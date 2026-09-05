"""The channel abstraction (spec §3).

The AI layer never learns that Apify exists. It receives :class:`FetchedPost`
and :class:`FetchedComment` values and hands back decisions; a
:class:`SocialPlatformAdapter` is what turns those into platform I/O.

Replacing Apify with the Meta Graph API later means writing a new adapter that
satisfies this interface. Nothing in ``ai/`` or ``agent/`` changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FetchedPost:
    """A post as seen on a platform, before it is stored."""

    platform: str
    platform_post_id: str
    url: str = ""
    caption: str = ""
    content_type: str = "post"
    topic: str = ""
    account_id: str = ""
    posted_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class FetchedComment:
    """A comment as seen on a platform, before it is stored."""

    platform: str
    platform_comment_id: str
    platform_post_id: str = ""
    author_id: str = ""
    author_name: str = ""
    comment_text: str = ""
    comment_url: str = ""
    comment_created_at: str | None = None
    parent_comment_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class CommentThreadContext:
    """Surrounding context for one comment — the post plus any existing replies."""

    post: FetchedPost | None = None
    existing_replies: list[FetchedComment] = field(default_factory=list)

    @property
    def already_answered(self) -> bool:
        return bool(self.existing_replies)


@dataclass
class ReplyResult:
    """Outcome of a publish attempt."""

    success: bool
    platform_reply_id: str | None = None
    dry_run: bool = False
    error: str = ""
    skipped_reason: str = ""


class PlatformNotConfiguredError(RuntimeError):
    """The adapter cannot act because required configuration is missing.

    Raised instead of guessing at an API contract — see
    :mod:`social_agent.apify.actors` on why reply actors have no default.
    """


class SocialPlatformAdapter(ABC):
    """One social channel. Implementations must be free of AI concerns."""

    #: "facebook" | "instagram"
    platform: str = ""

    @abstractmethod
    def fetch_posts(self, limit: int = 10) -> list[FetchedPost]:
        """Recent posts on the watched page/profile."""

    @abstractmethod
    def fetch_comments(self, posts: list[FetchedPost], limit: int = 50) -> list[FetchedComment]:
        """Comments on the supplied posts."""

    @abstractmethod
    def fetch_comment_context(self, comment: FetchedComment) -> CommentThreadContext:
        """The post a comment belongs to, plus replies already under it."""

    @abstractmethod
    def post_reply(self, comment: FetchedComment, reply_text: str) -> ReplyResult:
        """Publish a reply. Must never be called when dry-run is active."""

    @abstractmethod
    def check_existing_reply(self, comment: FetchedComment) -> bool:
        """True if this account has already replied to ``comment``.

        The last line of defence against duplicate replies: consulted against
        the live platform immediately before publishing, after the database
        checks have already passed.
        """

    def health(self) -> dict[str, Any]:
        """What is configured and what is missing, for the dashboard."""
        return {"platform": self.platform, "ready": True, "issues": []}
