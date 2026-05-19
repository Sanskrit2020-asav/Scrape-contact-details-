"""Claude refinement layer: reason over the top local matches and ask follow-ups.

Given the client's brief and the top candidates from local (keyword +
semantic) ranking, Claude re-ranks by genuine fit, explains each pick, and
proposes follow-up questions when the brief is underspecified.

Degrades gracefully: if `anthropic` is not installed or ANTHROPIC_API_KEY
is unset, `refine()` returns None and the app shows local results only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .search import ClientBrief, Match

_MODEL = "claude-opus-4-7"

_SYSTEM = """You are an expert travel itinerary matcher for a Nepal trekking agency.
You are given a client brief and a shortlist of the agency's existing prepared
itineraries (title, trip length, and a content excerpt). Your job:

1. Re-rank the shortlist by how well each itinerary genuinely fits the client's
   needs — reason about meaning, not keyword overlap (e.g. "relaxed pace for
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


def available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401

        return True
    except Exception:
        return False


def refine(brief: ClientBrief, matches: list[Match]) -> AIResult | None:
    if not available() or not matches:
        return None
    try:
        import anthropic
        from pydantic import BaseModel

        class RankedItem(BaseModel):
            filename: str
            reason: str

        class Schema(BaseModel):
            summary: str
            ranked: list[RankedItem]
            questions: list[str]

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

        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=_MODEL,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"CLIENT BRIEF\n{brief_text}\n\n"
                        f"SHORTLIST ({len(matches)} itineraries)\n{candidates}\n\n"
                        "Return the re-ranked itineraries (best first), each with a "
                        "reason, plus any follow-up questions."
                    ),
                }
            ],
            output_format=Schema,
        )
        parsed = response.parsed_output
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
        return AIResult(
            summary=f"(AI refinement unavailable: {exc})",
            ranked=[],
            questions=[],
        )
