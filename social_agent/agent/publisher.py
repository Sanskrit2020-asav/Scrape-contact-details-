"""Publishing, with duplicate protection as the primary concern (spec §21).

A retry must never produce a second reply on Facebook or Instagram. Four gates
are checked before anything is sent, in cheapest-first order:

1. The comment's status is one that may be published.
2. No ``ai_replies`` row for this comment is already marked posted.
3. The adapter reports no existing reply from us on the platform.
4. The reply row transitions ``posted 0 → 1`` under a partial unique index, so
   a race loses rather than duplicating.

A posting failure is **never** blindly retried here. The reply stays unposted
and visible in the dashboard, and a human decides — an automatic retry against
a network error that actually succeeded is exactly how duplicates happen.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..database.models import CommentStatus, Reply
from ..database.repositories import Repositories
from ..observability import get_logger
from ..social.base import (
    FetchedComment,
    PlatformNotConfiguredError,
    ReplyResult,
    SocialPlatformAdapter,
)

log = get_logger(__name__)

PUBLISHABLE_STATUSES = {
    CommentStatus.APPROVED.value,
    CommentStatus.PENDING_APPROVAL.value,
    CommentStatus.PROCESSING.value,
}


@dataclass
class PublishOutcome:
    published: bool = False
    dry_run: bool = False
    skipped: bool = False
    reason: str = ""
    platform_reply_id: str | None = None
    error: str = ""


class ReplyPublisher:
    """Publishes an approved reply exactly once, or explains why it didn't."""

    def __init__(self, repositories: Repositories, adapters: dict[str, SocialPlatformAdapter]):
        self.repos = repositories
        self.adapters = adapters

    def publish(
        self,
        *,
        comment_pk: int,
        reply_id: int,
        dry_run: bool,
        fetched: FetchedComment | None = None,
    ) -> PublishOutcome:
        comment = self.repos.comments.get(comment_pk)
        reply = self.repos.replies.get(reply_id)
        if comment is None or reply is None:
            return PublishOutcome(skipped=True, reason="comment or reply not found")

        text = (reply.final_reply_text or reply.reply_text or "").strip()
        if not text:
            return PublishOutcome(skipped=True, reason="reply text is empty")

        # Gate 1 — status.
        if comment.status not in PUBLISHABLE_STATUSES and comment.status != CommentStatus.APPROVED.value:
            return PublishOutcome(
                skipped=True, reason=f"comment status {comment.status!r} is not publishable"
            )

        # Gate 2 — already posted, per our own records.
        if self.repos.replies.has_posted_reply(comment_pk):
            log.info(
                "publish skipped: reply already posted",
                extra={"comment_pk": comment_pk, "platform": comment.platform},
            )
            self.repos.audit.log(
                "publish.duplicate_prevented", platform=comment.platform, comment_id=comment_pk,
                details={"stage": "database"},
            )
            return PublishOutcome(skipped=True, reason="a reply was already posted for this comment")

        # Dry run stops here: everything is recorded, nothing leaves the box.
        if dry_run:
            self.repos.replies.mark_posted(reply_id, None, dry_run=True)
            self.repos.comments.set_status(comment_pk, CommentStatus.DRY_RUN_REPLIED.value)
            self.repos.audit.log(
                "publish.dry_run", platform=comment.platform, comment_id=comment_pk,
                details={"reply_id": reply_id, "would_have_posted": text},
            )
            log.info(
                "dry run: reply not published",
                extra={"comment_pk": comment_pk, "platform": comment.platform},
            )
            return PublishOutcome(published=False, dry_run=True, reason="dry_run is on")

        adapter = self.adapters.get(comment.platform)
        if adapter is None:
            return PublishOutcome(skipped=True, reason=f"no adapter for {comment.platform}")

        target = fetched or FetchedComment(
            platform=comment.platform,
            platform_comment_id=comment.platform_comment_id,
            platform_post_id=comment.platform_post_id,
            comment_url=comment.comment_url,
            author_name=comment.author_name,
            comment_text=comment.comment_text,
        )

        # Gate 3 — ask the platform itself.
        try:
            if adapter.check_existing_reply(target):
                self.repos.comments.set_status(comment_pk, CommentStatus.REPLIED.value)
                self.repos.audit.log(
                    "publish.duplicate_prevented", platform=comment.platform, comment_id=comment_pk,
                    details={"stage": "platform"},
                )
                return PublishOutcome(skipped=True, reason="platform already shows a reply from us")
        except Exception as exc:  # noqa: BLE001 - a failed check must not publish blindly
            log.warning(
                "existing-reply check failed; not publishing",
                extra={"comment_pk": comment_pk, "error": str(exc)},
            )
            self.repos.replies.mark_error(reply_id, f"existing-reply check failed: {exc}")
            return PublishOutcome(skipped=True, reason=f"could not verify duplicates: {exc}")

        # Gate 4 — publish, then record under the unique index.
        try:
            result: ReplyResult = adapter.post_reply(target, text)
        except PlatformNotConfiguredError as exc:
            log.error("publishing not configured", extra={"platform": comment.platform, "error": str(exc)})
            self.repos.replies.mark_error(reply_id, str(exc))
            self.repos.audit.log(
                "publish.not_configured", level="error", platform=comment.platform,
                comment_id=comment_pk, details={"error": str(exc)},
            )
            return PublishOutcome(skipped=True, reason=str(exc), error=str(exc))
        except Exception as exc:  # noqa: BLE001 - adapters may raise anything
            log.exception("publishing failed", extra={"platform": comment.platform})
            self.repos.replies.mark_error(reply_id, str(exc))
            self.repos.comments.set_status(comment_pk, CommentStatus.FAILED.value)
            self.repos.audit.log(
                "publish.failed", level="error", platform=comment.platform,
                comment_id=comment_pk, details={"error": str(exc)},
            )
            return PublishOutcome(published=False, error=str(exc), reason="posting raised")

        if not result.success:
            reason = result.error or result.skipped_reason or "adapter reported failure"
            self.repos.replies.mark_error(reply_id, reason)
            # Not retried automatically: the reply may in fact have landed.
            self.repos.comments.set_status(comment_pk, CommentStatus.FAILED.value)
            self.repos.audit.log(
                "publish.failed", level="error", platform=comment.platform,
                comment_id=comment_pk, details={"error": reason},
            )
            log.error(
                "reply not published",
                extra={"comment_pk": comment_pk, "platform": comment.platform, "reason": reason},
            )
            return PublishOutcome(published=False, error=reason, reason=reason)

        recorded = self.repos.replies.mark_posted(reply_id, result.platform_reply_id, dry_run=False)
        if not recorded:
            # The index refused a second posted row — another worker won.
            return PublishOutcome(skipped=True, reason="a concurrent publish already recorded a reply")

        self.repos.comments.set_status(comment_pk, CommentStatus.REPLIED.value)
        self.repos.audit.log(
            "publish.succeeded", platform=comment.platform, comment_id=comment_pk,
            details={"reply_id": reply_id, "platform_reply_id": result.platform_reply_id},
        )
        log.info(
            "reply published",
            extra={
                "comment_pk": comment_pk,
                "platform": comment.platform,
                "platform_reply_id": result.platform_reply_id,
            },
        )
        return PublishOutcome(published=True, platform_reply_id=result.platform_reply_id)
