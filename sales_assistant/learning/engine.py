"""Continuous-learning engine.

Logs every interaction to an append-only JSONL store (the platform's memory),
then mines that history to:

  * detect missing knowledge (low research confidence),
  * recommend documents to upload,
  * identify repeated customer questions,
  * recommend new email templates,
  * and generate concrete enhancement ideas.

It is storage-backed and dependency-free, so it works in any deployment.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings, get_settings


@dataclass
class Interaction:
    kind: str                      # estimate / email / answer / quote
    query: str = ""
    trek: str = ""
    confidence: float = 1.0        # research/answer confidence, 1.0 if N/A
    missing_fields: list[str] = field(default_factory=list)
    unresolved: bool = False       # true when we couldn't answer well
    used_llm: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class LearningReport:
    interactions: int
    low_confidence_topics: list[tuple[str, int]]
    repeated_questions: list[tuple[str, int]]
    common_missing_fields: list[tuple[str, int]]
    recommended_uploads: list[str]
    recommended_templates: list[str]
    enhancement_ideas: list[str]

    def to_dict(self) -> dict:
        return {
            "interactions": self.interactions,
            "low_confidence_topics": self.low_confidence_topics,
            "repeated_questions": self.repeated_questions,
            "common_missing_fields": self.common_missing_fields,
            "recommended_uploads": self.recommended_uploads,
            "recommended_templates": self.recommended_templates,
            "enhancement_ideas": self.enhancement_ideas,
        }


_LOW_CONFIDENCE = 0.15


class LearningEngine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._log_path = Path(self.settings.store_dir) / "interactions.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ----- memory (append-only log) -----

    def record(self, interaction: Interaction) -> None:
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(interaction.to_dict(), ensure_ascii=False) + "\n")

    def history(self, limit: int | None = None) -> list[dict]:
        if not self._log_path.exists():
            return []
        rows = [json.loads(line) for line in self._log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[-limit:] if limit else rows

    # ----- analysis -----

    def analyse(self) -> LearningReport:
        rows = self.history()
        n = len(rows)

        low_conf_topics = Counter()
        repeated = Counter()
        missing = Counter()
        for r in rows:
            conf = r.get("confidence", 1.0)
            unresolved = r.get("unresolved", False)
            topic = (r.get("trek") or r.get("query") or "").strip().lower()
            if (conf < _LOW_CONFIDENCE or unresolved) and topic:
                low_conf_topics[topic] += 1
            q = (r.get("query") or "").strip().lower()
            if q and r.get("kind") == "answer":
                repeated[q] += 1
            for f in r.get("missing_fields", []):
                missing[f] += 1

        low_topics = low_conf_topics.most_common(8)
        repeated_qs = [(q, c) for q, c in repeated.most_common(8) if c >= 2]
        common_missing = missing.most_common(8)

        uploads: list[str] = []
        for topic, count in low_topics:
            uploads.append(f"Add company docs / itinerary covering '{topic}' (asked {count}× with weak answers).")
        if not uploads and n == 0:
            uploads.append("Upload trek itineraries, SOPs and the pricing Excel to seed the knowledge base.")

        templates: list[str] = []
        for q, c in repeated_qs:
            templates.append(f"Consider a saved reply/FAQ for the recurring question: \"{q}\" ({c}×).")

        ideas = self._enhancement_ideas(rows, common_missing, low_topics)

        return LearningReport(
            interactions=n,
            low_confidence_topics=low_topics,
            repeated_questions=repeated_qs,
            common_missing_fields=common_missing,
            recommended_uploads=uploads,
            recommended_templates=templates,
            enhancement_ideas=ideas,
        )

    def _enhancement_ideas(self, rows, common_missing, low_topics) -> list[str]:
        ideas: list[str] = []
        if common_missing:
            top = ", ".join(f"{f} ({c})" for f, c in common_missing[:3])
            ideas.append(f"Customers most often omit: {top}. Add these as required fields in the enquiry form.")
        llm_uses = sum(1 for r in rows if r.get("used_llm"))
        if rows and llm_uses == 0:
            ideas.append("LLM was never used — set ANTHROPIC_API_KEY to enable richer emails and answers.")
        if low_topics:
            ideas.append("Several topics returned weak research — expand the knowledge base or enable website indexing.")
        if not ideas:
            ideas.append("No gaps detected yet. Keep logging interactions to surface improvement opportunities.")
        return ideas
