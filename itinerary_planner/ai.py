"""AI refinement layer: reason over the top local matches and ask follow-ups.

Given the client's brief and the top candidates from local (keyword +
semantic) ranking, an LLM re-ranks by genuine fit, explains each pick, and
proposes follow-up questions when the brief is underspecified.

Provider is auto-detected:
  - OPENAI_API_KEY set   -> OpenAI  (model from OPENAI_MODEL, default gpt-4o)
  - ANTHROPIC_API_KEY set -> Claude (model from ANTHROPIC_MODEL, default
                                      claude-opus-4-7)
Degrades gracefully: if neither key/SDK is available, refine() returns None
and the app shows local results only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_SYSTEM = """You are an expert travel itinerary matcher for a Nepal trekking agency.
You are given a client brief and a shortlist of the agency's existing prepared
itineraries (title, trip length, and a content excerpt). Your job:

1. Re-rank the shortlist by how well each itinerary genuinely fits the client's
   needs - reason about meaning, not keyword overlap (e.g. "relaxed pace for
   older travellers" fits a gentle short trek even if it never says "easy").
2. For each ranked itinerary give a one-sentence reason grounded in the brief.
3. If the brief is missing information that would materially change the
   recommendation (group fitness, budget, dates/season, trek vs tour, altitude
   tolerance), list up to 3 specific follow-up questions for the agent to ask
   the client. If the brief is already clear, return an empty list.

Only choose from the provided itineraries. Never invent itineraries or files."""


@dataclass
class AIResult:
    summary: str
    ranked: list[dict]  # [{"filename": str, "reason": str}]
    questions: list[str]


def _provider() -> str | None:
    if os.environ.get("OPENAI_API_KEY"):
        try:
            import openai  # noqa: F401

            return "openai"
        except Exception:
            return None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401

            return "anthropic"
        except Exception:
            return None
    return None


def available() -> bool:
    return _provider() is not None


def _prompt(brief, matches) -> tuple[str, str]:
    candidates = "\n\n".join(
        f"[{i+1}] filename: {m.itinerary.filename}\n"
        f"title: {m.itinerary.title}\n"
        f"length: {m.itinerary.duration_days or 'unknown'} days\n"
        f"excerpt: {m.itinerary.snippet(500)}"
        for i, m in enumerate(matches)
    )
    brief_text = (
        f"destination: {brief.destination or '-'}\n"
        f"trip length (days): {brief.duration_days or '-'}\n"
        f"travelers: {brief.travelers or '-'}\n"
        f"budget: {brief.budget or '-'}\n"
        f"season: {brief.season or '-'}\n"
        f"interests: {brief.interests or '-'}\n"
        f"notes: {brief.notes or '-'}"
    )
    user = (
        f"CLIENT BRIEF\n{brief_text}\n\n"
        f"SHORTLIST ({len(matches)} itineraries)\n{candidates}\n\n"
        "Return the re-ranked itineraries (best first), each with a reason, "
        "plus any follow-up questions."
    )
    return _SYSTEM, user


def _schema():
    from pydantic import BaseModel

    class RankedItem(BaseModel):
        filename: str
        reason: str

    class Schema(BaseModel):
        summary: str
        ranked: list[RankedItem]
        questions: list[str]

    return Schema


def _refine_openai(system: str, user: str):
    from openai import OpenAI

    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    client = OpenAI()
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=_schema(),
    )
    return completion.choices[0].message.parsed


def _refine_anthropic(system: str, user: str):
    import anthropic

    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7")
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=_schema(),
    )
    return response.parsed_output


def refine(brief, matches) -> AIResult | None:
    provider = _provider()
    if provider is None or not matches:
        return None
    system, user = _prompt(brief, matches)
    try:
        parsed = (
            _refine_openai(system, user)
            if provider == "openai"
            else _refine_anthropic(system, user)
        )
        if parsed is None:
            return None
        valid = {m.itinerary.filename for m in matches}
        ranked = [
            {"filename": r.filename, "reason": r.reason}
            for r in parsed.ranked
            if r.filename in valid
        ]
        return AIResult(
            summary=parsed.summary.strip(),
            ranked=ranked,
            questions=[q.strip() for q in parsed.questions if q.strip()][:3],
        )
    except Exception as exc:  # noqa: BLE001 - AI is optional; never break the page
        return AIResult(summary=f"(AI refinement unavailable: {exc})", ranked=[], questions=[])
