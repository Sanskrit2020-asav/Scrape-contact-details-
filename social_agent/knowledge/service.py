"""The approved-knowledge layer (spec §15).

The rule this module exists to enforce: the model may only state
company-specific facts that a human has approved. It is therefore the *only*
path by which company information reaches a prompt, and it filters on three
things — status, validity window, and the ``human_only`` flag.

Items marked ``human_only`` (internal margins, escalation contacts, anything not
for the public) are never returned to the AI at all.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..database.models import KnowledgeItem
from ..database.repositories import KnowledgeRepository
from ..observability import get_logger

log = get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "with", "at", "by", "be", "as", "it", "this", "that", "from", "we", "you",
    "your", "our", "will", "can", "has", "have", "if", "but", "not", "they",
    "how", "much", "do", "does", "i", "my", "me", "what", "when", "where",
}

#: Which knowledge categories are worth surfacing for which intent. Keeps the
#: prompt focused instead of dumping the whole knowledge base into every call.
INTENT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "price_inquiry": ("pricing", "policy", "faq"),
    "itinerary_question": ("itinerary", "difficulty", "seasonal"),
    "trekking_difficulty": ("difficulty", "itinerary", "safety"),
    "permit_question": ("permits", "policy"),
    "availability": ("policy", "seasonal", "faq"),
    "weather": ("seasonal", "safety", "notice"),
    "safety": ("safety", "notice"),
    "booking_intent": ("policy", "faq", "pricing"),
    "lead": ("faq", "itinerary", "seasonal"),
    "travel_question": ("faq", "itinerary", "seasonal", "permits"),
}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 2}


class KnowledgeService:
    """Selects the approved knowledge shown to the AI for one comment."""

    def __init__(self, repository: KnowledgeRepository):
        self.repository = repository

    def active_items(self, *, now: str | None = None) -> list[KnowledgeItem]:
        """Every currently publishable item."""
        return self.repository.active_items(now=now)

    def for_comment(
        self,
        comment_text: str,
        *,
        post_caption: str = "",
        post_topic: str = "",
        limit: int = 8,
        now: str | None = None,
    ) -> list[KnowledgeItem]:
        """Rank approved items by relevance to this comment.

        Scored on simple token overlap. Current notices (``notice`` category)
        are always included regardless of score — an active "the road is closed"
        notice matters even when the comment does not use those words.
        """
        items = self.active_items(now=now)
        if not items:
            return []

        query = _tokens(f"{comment_text} {post_topic} {post_caption}")
        notices = [i for i in items if i.category == "notice"]
        scored: list[tuple[float, KnowledgeItem]] = []
        for item in items:
            if item.category == "notice":
                continue
            haystack = _tokens(f"{item.title} {item.content} {item.category}")
            if not haystack:
                continue
            overlap = len(query & haystack)
            if overlap:
                scored.append((overlap / (len(query) or 1), item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        chosen = notices + [item for _, item in scored[: max(0, limit - len(notices))]]
        log.debug(
            "knowledge selected",
            extra={"candidates": len(items), "selected": len(chosen)},
        )
        return chosen

    def seed_if_empty(self) -> int:
        """Install the starter knowledge base on a fresh install."""
        if self.repository.list(limit=1):
            return 0
        from .seed import SEED_ITEMS

        for item in SEED_ITEMS:
            self.repository.create(
                KnowledgeItem(
                    title=item["title"],
                    content=item["content"],
                    category=item["category"],
                    status=item.get("status", "active"),
                    human_only=bool(item.get("human_only", False)),
                )
            )
        log.info("knowledge seeded", extra={"count": len(SEED_ITEMS)})
        return len(SEED_ITEMS)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
