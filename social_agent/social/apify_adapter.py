"""Apify-backed implementation of :class:`SocialPlatformAdapter`.

Facebook and Instagram differ only in configuration — which actor to call and
which page/profile to watch — so both share this base class. Anything genuinely
platform-specific belongs in the subclass, not in ``if platform == ...``
branches here.
"""
from __future__ import annotations

from typing import Any

from ..apify import ActorRegistry, ApifyClient, ApifyError
from ..observability import get_logger
from .base import (
    CommentThreadContext,
    FetchedComment,
    FetchedPost,
    PlatformNotConfiguredError,
    ReplyResult,
    SocialPlatformAdapter,
)
from .normalize import normalize_comment, normalize_post

log = get_logger(__name__)


class ApifySocialAdapter(SocialPlatformAdapter):
    """Fetches through Apify actors; publishes only when a reply actor exists."""

    def __init__(
        self,
        platform: str,
        *,
        client: ApifyClient,
        registry: ActorRegistry,
        page_urls: list[str],
        account_id: str = "",
        account_username: str = "",
    ):
        self.platform = platform
        self.client = client
        self.registry = registry
        self.page_urls = [u for u in page_urls if u]
        self.account_id = account_id
        self.account_username = (account_username or "").lower()
        self._post_cache: dict[str, FetchedPost] = {}

    # -- reads -----------------------------------------------------------

    def fetch_posts(self, limit: int = 10) -> list[FetchedPost]:
        if not self.page_urls:
            raise PlatformNotConfiguredError(
                f"No page/profile URL configured for {self.platform}. "
                f"Set {self.platform.upper()}_PAGE_URL or {self.platform.upper()}_PROFILE_URL."
            )
        spec = self.registry.spec(self.platform, "posts")
        if not spec.configured:
            raise PlatformNotConfiguredError(
                f"No Apify posts actor configured for {self.platform} "
                f"(APIFY_{self.platform.upper()}_POSTS_ACTOR)."
            )
        result = self.client.run_actor(
            spec.actor_id, spec.render(page_urls=self.page_urls, limit=limit)
        )
        posts: list[FetchedPost] = []
        for item in result.items[:limit]:
            post = normalize_post(item, self.platform, self.account_id)
            if post:
                posts.append(post)
                self._post_cache[post.platform_post_id] = post
        log.info("fetched posts", extra={"platform": self.platform, "count": len(posts)})
        return posts

    def fetch_comments(self, posts: list[FetchedPost], limit: int = 50) -> list[FetchedComment]:
        if not posts:
            return []
        spec = self.registry.spec(self.platform, "comments")
        if not spec.configured:
            raise PlatformNotConfiguredError(
                f"No Apify comments actor configured for {self.platform} "
                f"(APIFY_{self.platform.upper()}_COMMENTS_ACTOR)."
            )
        for post in posts:
            self._post_cache[post.platform_post_id] = post

        post_urls = [p.url for p in posts if p.url]
        if not post_urls:
            log.warning("no post URLs to scrape comments from", extra={"platform": self.platform})
            return []

        result = self.client.run_actor(
            spec.actor_id, spec.render(post_urls=post_urls, limit=limit)
        )
        fallback = posts[0].platform_post_id if len(posts) == 1 else ""
        comments: list[FetchedComment] = []
        for item in result.items[:limit]:
            comment = normalize_comment(item, self.platform, fallback)
            if comment and comment.comment_text:
                comments.append(comment)
        log.info("fetched comments", extra={"platform": self.platform, "count": len(comments)})
        return comments

    def fetch_comment_context(self, comment: FetchedComment) -> CommentThreadContext:
        """Context from what the fetch already returned.

        Deliberately does not start another actor run per comment: that would
        multiply cost and runtime for a batch. Adapters with a cheap per-comment
        read (the Graph API, later) can override this.
        """
        post = self._post_cache.get(comment.platform_post_id)
        return CommentThreadContext(post=post, existing_replies=[])

    def check_existing_reply(self, comment: FetchedComment) -> bool:
        """Whether our account already replied, judged from fetched data.

        Scraper actors do not reliably return the reply thread under a comment,
        so this returns ``False`` unless the raw item explicitly shows a reply
        authored by our account. The authoritative duplicate guards are the
        database ones in :mod:`social_agent.database.repositories`; this is a
        best-effort extra check, and it never reports a false "already replied"
        that would silently drop a genuine reply.
        """
        raw = comment.raw or {}
        replies = raw.get("replies") or raw.get("commentReplies") or []
        if not isinstance(replies, list) or not self.account_username:
            return False
        for reply in replies:
            if not isinstance(reply, dict):
                continue
            author = str(
                reply.get("ownerUsername")
                or reply.get("authorName")
                or (reply.get("owner") or {}).get("name", "")
            ).lower()
            if author and author == self.account_username:
                return True
        return False

    # -- writes ----------------------------------------------------------

    def post_reply(self, comment: FetchedComment, reply_text: str) -> ReplyResult:
        """Publish through the configured reply actor.

        No reply actor is configured by default, and none is invented: posting a
        reply needs an authenticated write path, and guessing at an actor's
        input schema would either fail loudly or — worse — silently post the
        wrong thing. Configure ``APIFY_<PLATFORM>_REPLY_ACTOR`` plus a matching
        entry in ``apify/actor_inputs.json`` to enable publishing.
        """
        spec = self.registry.spec(self.platform, "reply")
        if not spec.configured:
            raise PlatformNotConfiguredError(
                f"No Apify reply actor configured for {self.platform}. Set "
                f"APIFY_{self.platform.upper()}_REPLY_ACTOR and add a "
                f"'{self.platform}_reply' input template in apify/actor_inputs.json. "
                "Until then the agent runs read-only."
            )
        try:
            result = self.client.run_actor(
                spec.actor_id,
                spec.render(
                    comment_url=comment.comment_url,
                    comment_id=comment.platform_comment_id,
                    post_id=comment.platform_post_id,
                    reply_text=reply_text,
                ),
            )
        except ApifyError as exc:
            log.error(
                "reply publish failed",
                extra={
                    "platform": self.platform,
                    "comment_id": comment.platform_comment_id,
                    "error": str(exc),
                },
            )
            return ReplyResult(success=False, error=str(exc))

        reply_id = ""
        for item in result.items:
            candidate = item.get("replyId") or item.get("commentId") or item.get("id")
            if candidate:
                reply_id = str(candidate)
                break
        return ReplyResult(success=True, platform_reply_id=reply_id or result.run_id)

    # -- diagnostics -----------------------------------------------------

    def health(self) -> dict[str, Any]:
        issues: list[str] = []
        if not self.client.configured:
            issues.append("APIFY_API_TOKEN is not set")
        if not self.page_urls:
            issues.append(f"No {self.platform} page/profile URL configured")
        for operation in ("posts", "comments"):
            if not self.registry.spec(self.platform, operation).configured:
                issues.append(f"No {operation} actor configured")
        can_publish = self.registry.spec(self.platform, "reply").configured
        return {
            "platform": self.platform,
            "ready": not issues,
            "can_publish": can_publish,
            "issues": issues,
            "mode": "apify",
        }
