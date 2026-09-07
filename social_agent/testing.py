"""Test doubles and offline fixtures.

Nothing here is a runtime AI provider. :class:`ScriptedOpenAITransport` is an
HTTP test double that returns pre-written responses shaped like the OpenAI API,
so the *real* client, validator, guardrails and pipeline all run unchanged with
no network. It is used by the test suite and by ``python -m social_agent demo``.

Never wire this into production: the application's only AI provider is OpenAI,
reached through :class:`social_agent.ai.openai_client.OpenAIClient`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .ai.openai_client import OpenAIError, OpenAIRateLimitError, OpenAITimeoutError
from .apify.client import ApifyError


def responses_payload(decision: dict[str, Any], model: str = "gpt-5") -> dict[str, Any]:
    """Wrap a decision the way the OpenAI Responses API would."""
    return {
        "id": "resp_test_0001",
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": json.dumps(decision, ensure_ascii=False)}
                ],
            }
        ],
        "usage": {"input_tokens": 400, "output_tokens": 60, "total_tokens": 460},
    }


def decision(
    action: str = "reply",
    *,
    intent: str = "general_engagement",
    confidence: float = 0.9,
    reply: str | None = "Thanks for the kind words.",
    needs_human: bool = False,
    reason: str = "test",
    risk_level: str = "low",
) -> dict[str, Any]:
    return {
        "action": action,
        "intent": intent,
        "confidence": confidence,
        "reply": reply,
        "needs_human": needs_human,
        "reason": reason,
        "risk_level": risk_level,
    }


# --------------------------------------------------------------------------
# OpenAI doubles
# --------------------------------------------------------------------------


@dataclass
class QueuedOpenAITransport:
    """Returns (or raises) queued items in order. Records every request."""

    queue: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    default: Any = None

    def post(self, url, payload, headers, timeout):
        self.requests.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        item = self.queue.pop(0) if self.queue else self.default
        if isinstance(item, Exception):
            raise item
        if item is None:
            raise OpenAIError("QueuedOpenAITransport ran out of queued responses")
        return item


#: Canned decisions keyed by a regex over the comment text. First match wins.
DEMO_SCRIPT: list[tuple[str, dict[str, Any]]] = [
    (r"how many|number of|in total", decision(
        "escalate", intent="mountain_history", confidence=0.88, reply=None, needs_human=True,
        risk_level="medium", reason="Asks for a count that changes — never stated publicly.")),
    (r"first person to climb|who climbed|first ascent|first to climb", decision(
        "reply", intent="mountain_history", confidence=0.93, needs_human=False, risk_level="low",
        reply="Edmund Hillary and Tenzing Norgay Sherpa, on 29 May 1953.",
        reason="First-ascent fact present in approved knowledge.")),
    (r"what year|which year", decision(
        "reply", intent="mountain_history", confidence=0.86, needs_human=False, risk_level="low",
        reply="Annapurna I went in 1950 — three years before Everest, which surprises people.",
        reason="Date present in approved knowledge.")),
    (r"\bk2\b", decision(
        "reply", intent="peak_identification", confidence=0.9, needs_human=False, risk_level="low",
        reply="That's Machhapuchhre — K2 is over in Pakistan, a long way from here.",
        reason="Common mix-up; corrected from approved knowledge.")),
    (r"mallory", decision(
        "reply", intent="mountain_history", confidence=0.84, needs_human=False, risk_level="low",
        reply="Nobody knows for certain — his body turned up decades later, and the question is still open.",
        reason="Genuinely disputed history; said so rather than picking a side.")),
    (r"vote|election|political", decision(
        "ignore", intent="irrelevant", confidence=0.95, reply=None, needs_human=False,
        risk_level="low", reason="Not our subject.")),
    (r"tenzing|sherpa|norgay", decision(
        "reply", intent="mountain_history", confidence=0.91, needs_human=False, risk_level="low",
        reply="He'd already been high on the mountain the year before with the Swiss — that experience mattered.",
        reason="Story engagement, named the climber.")),
    (r"scam|refund|cheated|fraud", decision(
        "escalate", intent="complaint", confidence=0.94, reply=None, needs_human=True,
        risk_level="high", reason="Accusation against the company — a human must answer.")),
    (r"trail open|road open|is it open|landslide|flood|earthquake|rescue", decision(
        "escalate", intent="urgent_safety", confidence=0.92, reply=None, needs_human=True,
        risk_level="high", reason="Asks about a current condition — never answered by the agent.")),
    (r"followers|likes|dm me|crypto|www\.|http", decision(
        "ignore", intent="spam", confidence=0.97, reply=None, needs_human=False,
        risk_level="low", reason="Promotional spam.")),
    (r"how much|cost|price", decision(
        "reply", intent="price_inquiry", confidence=0.82, needs_human=True, risk_level="low",
        reply="Happy to help with that — send us a message and we'll share the current details.",
        reason="Price question; no approved public pricing, so pointing to a DM.")),
    (r"permit", decision(
        "reply", intent="permit_question", confidence=0.78, needs_human=True, risk_level="low",
        reply="Manaslu is a restricted area so there are a few extra requirements — message us and we'll walk you through the current ones.",
        reason="Permit rules change; kept general and moved to a DM.")),
    (r"difficult|hard|tough", decision(
        "reply", intent="trekking_difficulty", confidence=0.88, needs_human=False, risk_level="low",
        reply="It's a challenging one, but with good preparation and proper acclimatisation it's very achievable.",
        reason="General difficulty question answerable from approved knowledge.")),
    (r"which mountain|what mountain|where is this", decision(
        "reply", intent="travel_question", confidence=0.86, needs_human=False, risk_level="low",
        reply="That's the Annapurna Sanctuary — the caption has the details.",
        reason="Answerable from the post itself.")),
    (r"want to (come|visit)|next year|hope to visit", decision(
        "reply", intent="lead", confidence=0.91, needs_human=False, risk_level="low",
        reply="Hope you make it here — message us anytime and we'd be glad to help you plan it.",
        reason="Genuine travel intent; warm, no push.")),
    (r"visited nepal|been to nepal|i was there", decision(
        "reply", intent="general_engagement", confidence=0.93, needs_human=False, risk_level="low",
        reply="2019 was a good year up there — hope you get back one day.",
        reason="Friendly recollection, easy to engage with.")),
    (r"^\W*$|😍|❤️|🔥", decision(
        "reply", intent="compliment", confidence=0.95, needs_human=False, risk_level="low",
        reply="Those Himalayan mornings are something else.",
        reason="Emoji-only appreciation.")),
    (r"@\w+", decision(
        "ignore", intent="irrelevant", confidence=0.9, reply=None, needs_human=False,
        risk_level="low", reason="Tagging a friend; a reply from us would be noise.")),
    (r"beautiful|amazing|wow|stunning|gorgeous", decision(
        "reply", intent="compliment", confidence=0.96, needs_human=False, risk_level="low",
        reply="It really is… Nepal never gets old.",
        reason="Simple compliment.")),
]


class ScriptedOpenAITransport:
    """Answers according to :data:`DEMO_SCRIPT`, matched on the comment text.

    A stand-in for the model, not a model. It lets the pipeline, guardrails,
    routing and dashboard be exercised offline; it does not evaluate whether the
    real model behaves this way.
    """

    def __init__(self, script: list[tuple[str, dict[str, Any]]] | None = None,
                 fallback: dict[str, Any] | None = None):
        self.script = script or DEMO_SCRIPT
        self.fallback = fallback or decision(
            "escalate", intent="unknown", confidence=0.4, reply=None, needs_human=True,
            risk_level="medium", reason="No scripted match — routed to a human.")
        self.requests: list[dict[str, Any]] = []

    @staticmethod
    def _comment_text(payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, ensure_ascii=False)
        match = re.search(r'\\"text\\":\s*\\"(.*?)\\"', blob)
        if match:
            return match.group(1)
        match = re.search(r'"text":\s*"(.*?)"', blob)
        return match.group(1) if match else ""

    def post(self, url, payload, headers, timeout):
        self.requests.append({"url": url, "payload": payload})
        text = self._comment_text(payload).lower()
        for pattern, canned in self.script:
            if re.search(pattern, text, re.IGNORECASE):
                return responses_payload(canned, payload.get("model", "gpt-5"))
        return responses_payload(self.fallback, payload.get("model", "gpt-5"))


# --------------------------------------------------------------------------
# Apify doubles
# --------------------------------------------------------------------------


@dataclass
class QueuedApifyTransport:
    """Serves queued Apify API responses; raises queued exceptions."""

    queue: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def request(self, method, url, body, timeout):
        self.requests.append({"method": method, "url": url, "body": body})
        if not self.queue:
            raise ApifyError("QueuedApifyTransport ran out of queued responses")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def apify_run_started(run_id: str = "run_1", dataset_id: str = "ds_1", status: str = "SUCCEEDED"):
    return {"data": {"id": run_id, "defaultDatasetId": dataset_id, "status": status}}


def apify_run_status(status: str = "SUCCEEDED", dataset_id: str = "ds_1"):
    return {"data": {"id": "run_1", "defaultDatasetId": dataset_id, "status": status}}


# --------------------------------------------------------------------------
# Application builders
# --------------------------------------------------------------------------


def build_test_application(
    *,
    transport: Any = None,
    adapters: dict | None = None,
    settings=None,
    db=None,
    seed_knowledge: bool = True,
):
    """An application wired to in-memory storage and a fake OpenAI transport."""
    from .ai import AIDecisionService, OpenAIClient
    from .app import build_application
    from .config import Settings
    from .database import Database
    from .social import MockAdapter

    resolved_settings = settings or Settings()
    if not resolved_settings.openai.api_key:
        resolved_settings.openai.api_key = "sk-test-not-a-real-key"

    client = OpenAIClient(
        resolved_settings.openai,
        transport=transport or ScriptedOpenAITransport(),
        sleep=lambda _: None,
    )
    ai = AIDecisionService(resolved_settings, client=client)

    return build_application(
        resolved_settings,
        db=db or Database(":memory:"),
        ai_service=ai,
        adapters=adapters if adapters is not None else {
            "facebook": MockAdapter("facebook"),
            "instagram": MockAdapter("instagram"),
        },
        seed_knowledge=seed_knowledge,
        configure_logs=False,
    )


def build_demo_application():
    """The offline demo: mock social data plus the scripted AI stand-in."""
    from .config import Settings

    settings = Settings()
    settings.platforms.use_mock_data = True
    return build_test_application(settings=settings)
