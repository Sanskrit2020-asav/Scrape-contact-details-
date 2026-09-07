"""Comment density — deciding which posts deserve more effort.

When a history post takes off, its comments are where the real conversation is,
and they are usually the substantive ones: which peak, who climbed it, what
year. Those deserve deeper preparation than a compliment on a quiet post.

"Deeper preparation" here means **more of the approved knowledge base**, not web
research. The agent has no internet access by design: letting it search and then
paraphrase what it found is precisely how a wrong first-ascent date ends up
under the company's name. Widening the knowledge window gives the model more of
what a human has already approved, which is the safe version of the same idea.

High-density posts also get processed first and are flagged in the dashboard, so
a person reviewing the queue sees the busy conversation before the quiet one.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..database.repositories import Repositories
from ..observability import get_logger

log = get_logger(__name__)

#: Knowledge items sent to the model for an ordinary comment.
BASE_KNOWLEDGE_ITEMS = 8
#: ...and for one on a post that is clearly generating discussion.
DEEP_KNOWLEDGE_ITEMS = 14


@dataclass
class PostEngagement:
    """How busy a post's comment section is."""

    platform_post_id: str
    comment_count: int = 0
    threshold: int = 10

    @property
    def is_high(self) -> bool:
        return self.comment_count >= self.threshold

    @property
    def knowledge_budget(self) -> int:
        return DEEP_KNOWLEDGE_ITEMS if self.is_high else BASE_KNOWLEDGE_ITEMS

    def to_dict(self) -> dict:
        return {
            "platform_post_id": self.platform_post_id,
            "comment_count": self.comment_count,
            "threshold": self.threshold,
            "high_engagement": self.is_high,
            "knowledge_budget": self.knowledge_budget,
        }


class EngagementIndex:
    """Comment counts per post, computed once per cycle.

    Built up front rather than queried per comment: a cycle handling fifty
    comments would otherwise run fifty identical COUNT queries.
    """

    def __init__(self, repositories: Repositories, threshold: int = 10):
        self.repos = repositories
        self.threshold = max(1, int(threshold or 1))
        self._counts: dict[str, int] = {}
        self._loaded = False

    def refresh(self) -> dict[str, int]:
        rows = self.repos.db.query(
            """
            SELECT platform_post_id, COUNT(*) AS n
              FROM social_comments
             WHERE TRIM(COALESCE(platform_post_id, '')) <> ''
             GROUP BY platform_post_id
            """
        )
        self._counts = {r["platform_post_id"]: int(r["n"]) for r in rows}
        self._loaded = True
        return self._counts

    def _ensure_loaded(self) -> None:
        """Load counts on first use.

        Without this, forgetting ``refresh()`` reports "no busy posts" rather
        than failing — a silent wrong answer, which is the worst kind.
        """
        if not self._loaded:
            self.refresh()

    def for_post(self, platform_post_id: str) -> PostEngagement:
        self._ensure_loaded()
        return PostEngagement(
            platform_post_id=platform_post_id or "",
            comment_count=self._counts.get(platform_post_id or "", 0),
            threshold=self.threshold,
        )

    def sort_by_density(self, comments: list) -> list:
        """Busiest posts first; stable within a post so ordering stays sane."""
        self._ensure_loaded()
        return sorted(
            comments,
            key=lambda c: (-self._counts.get(c.platform_post_id or "", 0), c.id or 0),
        )

    def hot_posts(self) -> list[tuple[str, int]]:
        self._ensure_loaded()
        return sorted(
            ((pid, n) for pid, n in self._counts.items() if n >= self.threshold),
            key=lambda pair: pair[1],
            reverse=True,
        )
