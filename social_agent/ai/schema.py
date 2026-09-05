"""The agent's structured-output contract and its validator.

The model is asked for strict JSON-schema output, but we never *trust* that it
complied: :func:`validate_decision` re-checks every field locally before the
result is allowed anywhere near a publishing decision. Structured output is a
strong hint, not a guarantee, and an unvalidated reply is one that could be
posted under the company's name.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..database.models import Action, RiskLevel

SCHEMA_NAME = "north_nepal_comment_decision"

#: JSON Schema sent to OpenAI as a strict structured-output format.
#: ``strict: true`` requires every property to be listed in ``required`` and
#: ``additionalProperties: false``, so ``reply`` is nullable rather than absent.
DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "intent", "confidence", "reply", "needs_human", "reason", "risk_level"],
    "properties": {
        "action": {
            "type": "string",
            "enum": ["reply", "ignore", "escalate"],
            "description": "Exactly one action for this comment.",
        },
        "intent": {
            "type": "string",
            "description": "Closest matching comment intent, e.g. compliment or price_inquiry.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "How safe this reply is to publish with no human review.",
        },
        "reply": {
            "type": ["string", "null"],
            "description": "The public reply text. Must be null for ignore and escalate.",
        },
        "needs_human": {
            "type": "boolean",
            "description": "True when a person should see this before anything is published.",
        },
        "reason": {
            "type": "string",
            "description": "One short internal line explaining the decision.",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Risk of publishing the wrong thing here.",
        },
    },
}


class DecisionValidationError(ValueError):
    """Raised when model output cannot be trusted as a decision."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


@dataclass
class AgentDecision:
    """A validated decision. Nothing reaches the pipeline unvalidated."""

    action: str
    intent: str
    confidence: float
    reply: str | None
    needs_human: bool
    reason: str
    risk_level: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "intent": self.intent,
            "confidence": self.confidence,
            "reply": self.reply,
            "needs_human": self.needs_human,
            "reason": self.reason,
            "risk_level": self.risk_level,
        }

    @property
    def is_reply(self) -> bool:
        return self.action == Action.REPLY.value


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of raw model text.

    Handles the two ways a model can wrap valid JSON in noise despite being
    asked not to: a markdown fence, or leading/trailing prose.
    """
    if not text or not text.strip():
        raise DecisionValidationError("model returned empty output")
    candidate = text.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise DecisionValidationError("model output was not JSON") from None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DecisionValidationError(f"model output was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DecisionValidationError("model output was not a JSON object")
    return parsed


def validate_decision(payload: dict[str, Any]) -> AgentDecision:
    """Validate a parsed decision against the contract.

    Raises :class:`DecisionValidationError` listing every problem found, so the
    caller can log precisely why a response was rejected.
    """
    errors: list[str] = []

    action = str(payload.get("action", "")).strip().lower()
    if action not in {a.value for a in Action}:
        errors.append(f"action must be reply|ignore|escalate, got {payload.get('action')!r}")

    risk = str(payload.get("risk_level", "")).strip().lower()
    if risk not in {r.value for r in RiskLevel}:
        errors.append(f"risk_level must be low|medium|high, got {payload.get('risk_level')!r}")

    intent = str(payload.get("intent", "")).strip().lower().replace(" ", "_")
    if not intent:
        errors.append("intent is required")

    raw_conf = payload.get("confidence")
    confidence = 0.0
    if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float)):
        errors.append(f"confidence must be a number, got {raw_conf!r}")
    else:
        confidence = float(raw_conf)
        if not 0.0 <= confidence <= 1.0:
            errors.append(f"confidence must be within 0.0–1.0, got {confidence}")

    needs_human = payload.get("needs_human")
    if not isinstance(needs_human, bool):
        errors.append(f"needs_human must be a boolean, got {needs_human!r}")

    reply_raw = payload.get("reply")
    reply: str | None
    if reply_raw is None:
        reply = None
    elif isinstance(reply_raw, str):
        reply = reply_raw.strip() or None
    else:
        errors.append(f"reply must be a string or null, got {type(reply_raw).__name__}")
        reply = None

    if action == Action.REPLY.value and not reply:
        errors.append("action 'reply' requires non-empty reply text")
    if action in {Action.IGNORE.value, Action.ESCALATE.value} and reply:
        # Not fatal — the text is simply discarded, since nothing is published
        # for these actions. Recorded so the operator can see it happened.
        reply = None

    reason = str(payload.get("reason", "")).strip()

    if errors:
        raise DecisionValidationError("invalid model decision: " + "; ".join(errors), errors)

    return AgentDecision(
        action=action,
        intent=intent,
        confidence=round(confidence, 4),
        reply=reply,
        needs_human=bool(needs_human),
        reason=reason,
        risk_level=risk,
        raw=payload,
    )


def fallback_decision(reason: str, *, intent: str = "unknown") -> AgentDecision:
    """The decision used when the AI could not be trusted or reached.

    Deliberately the most conservative possible outcome: no reply text, human
    required, escalated. A failure must never become a published comment.
    """
    return AgentDecision(
        action=Action.ESCALATE.value,
        intent=intent,
        confidence=0.0,
        reply=None,
        needs_human=True,
        reason=reason,
        risk_level=RiskLevel.HIGH.value,
        raw={"fallback": True, "reason": reason},
    )
