"""Fixture-backed adapter for dry runs and tests (spec §30).

Set ``USE_MOCK_SOCIAL_DATA=true`` to exercise the whole pipeline — fetch,
classify, decide, guardrails, approval, publish — with no Apify token, no
OpenAI-side platform credentials and no real accounts.

Publishing through this adapter never touches a social network; it records what
*would* have been posted and returns a synthetic reply id. Any run that used it
is labelled as such in the audit log so a simulated result can never be mistaken
for a real one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import MOCK_DIR
from ..observability import get_logger
from .base import (
    CommentThreadContext,
    FetchedComment,
    FetchedPost,
    ReplyResult,
    SocialPlatformAdapter,
)
from .normalize import normalize_comment, normalize_post

log = get_logger(__name__)


class MockAdapter(SocialPlatformAdapter):
    """Reads ``data/mock/<platform>.json`` instead of calling Apify."""

    def __init__(self, platform: str, fixture_path: Path | None = None, account_id: str = ""):
        self.platform = platform
        self.fixture_path = fixture_path or (MOCK_DIR / f"{platform}.json")
        self.account_id = account_id
        self.published: list[dict[str, str]] = []
        self._data = self._load()
        self._posts = [
            p for p in (normalize_post(i, platform, account_id) for i in self._data.get("posts", []))
            if p
        ]
        self._post_index = {p.platform_post_id: p for p in self._posts}

    def _load(self) -> dict[str, Any]:
        if not self.fixture_path.is_file():
            log.warning("mock fixture missing", extra={"path": str(self.fixture_path)})
            return {"posts": [], "comments": []}
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))

    def fetch_posts(self, limit: int = 10) -> list[FetchedPost]:
        return self._posts[:limit]

    def fetch_comments(self, posts: list[FetchedPost], limit: int = 50) -> list[FetchedComment]:
        wanted = {p.platform_post_id for p in posts} or set(self._post_index)
        out: list[FetchedComment] = []
        for item in self._data.get("comments", []):
            comment = normalize_comment(item, self.platform)
            if comment and comment.platform_post_id in wanted:
                out.append(comment)
        return out[:limit]

    def fetch_comment_context(self, comment: FetchedComment) -> CommentThreadContext:
        return CommentThreadContext(post=self._post_index.get(comment.platform_post_id))

    def check_existing_reply(self, comment: FetchedComment) -> bool:
        return any(p["comment_id"] == comment.platform_comment_id for p in self.published)

    def post_reply(self, comment: FetchedComment, reply_text: str) -> ReplyResult:
        if self.check_existing_reply(comment):
            return ReplyResult(
                success=False,
                skipped_reason="mock adapter already recorded a reply for this comment",
            )
        self.published.append(
            {"comment_id": comment.platform_comment_id, "text": reply_text}
        )
        log.info(
            "mock reply recorded (nothing was published to a real account)",
            extra={"platform": self.platform, "comment_id": comment.platform_comment_id},
        )
        return ReplyResult(
            success=True,
            platform_reply_id=f"mock_reply_{comment.platform_comment_id}",
        )

    def health(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "ready": self.fixture_path.is_file(),
            "can_publish": True,
            "issues": [] if self.fixture_path.is_file() else ["mock fixture file missing"],
            "mode": "mock",
        }
