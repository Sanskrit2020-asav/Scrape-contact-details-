"""The orchestration layer.

One cycle:

    fetch posts → fetch comments → store new ones (duplicates dropped)
      → for each: build context → OpenAI → validate → guardrails → route
      → publish, queue for approval, ignore, or escalate

This is the only module that knows about both Apify and OpenAI, and it knows
each of them only through its service interface. Failures are contained per
comment: one bad comment does not stop the cycle, and one platform being down
does not stop the other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..ai import AIDecisionService, build_context
from ..apify import ApifyError
from ..config import Settings, get_settings
from ..database.models import AgentSettings, Comment, CommentStatus, Post, Reply
from ..database.repositories import Repositories
from ..knowledge import KnowledgeService
from ..observability import get_logger, request_context, truncate
from ..social.base import (
    FetchedComment,
    FetchedPost,
    PlatformNotConfiguredError,
    SocialPlatformAdapter,
)
from .guardrails import apply_guardrails
from .policy import route
from .publisher import ReplyPublisher

log = get_logger(__name__)


@dataclass
class CycleReport:
    """What one polling cycle did, for the CLI and the dashboard."""

    request_id: str = ""
    dry_run: bool = True
    platforms: list[str] = field(default_factory=list)
    posts_fetched: int = 0
    comments_fetched: int = 0
    comments_new: int = 0
    comments_duplicate: int = 0
    processed: int = 0
    replied: int = 0
    auto_replied: int = 0
    dry_run_replies: int = 0
    pending_approval: int = 0
    ignored: int = 0
    escalated: int = 0
    needs_review: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "dry_run": self.dry_run,
            "platforms": self.platforms,
            "posts_fetched": self.posts_fetched,
            "comments_fetched": self.comments_fetched,
            "comments_new": self.comments_new,
            "comments_duplicate": self.comments_duplicate,
            "processed": self.processed,
            "replied": self.replied,
            "auto_replied": self.auto_replied,
            "dry_run_replies": self.dry_run_replies,
            "pending_approval": self.pending_approval,
            "ignored": self.ignored,
            "escalated": self.escalated,
            "needs_review": self.needs_review,
            "failed": self.failed,
            "errors": self.errors,
        }


class SocialEngagementAgent:
    """The North Nepal social engagement agent."""

    def __init__(
        self,
        repositories: Repositories,
        adapters: dict[str, SocialPlatformAdapter],
        ai_service: AIDecisionService,
        knowledge: KnowledgeService,
        settings: Settings | None = None,
    ):
        self.repos = repositories
        self.adapters = adapters
        self.ai = ai_service
        self.knowledge = knowledge
        self.settings = settings or get_settings()
        self.publisher = ReplyPublisher(repositories, adapters)

    # -- one cycle -------------------------------------------------------

    def run_cycle(self, *, platforms: list[str] | None = None) -> CycleReport:
        """Fetch and process one batch of comments across enabled platforms."""
        agent_settings = self.repos.settings.get()
        with request_context() as rid:
            report = CycleReport(request_id=rid, dry_run=agent_settings.dry_run)
            targets = platforms or list(self.adapters)
            report.platforms = [p for p in targets if p in self.adapters]

            log.info(
                "cycle started",
                extra={"platforms": report.platforms, "dry_run": agent_settings.dry_run},
            )
            self.repos.audit.log(
                "cycle.started",
                details={"platforms": report.platforms, "dry_run": agent_settings.dry_run},
            )

            for platform in report.platforms:
                try:
                    self._ingest_platform(platform, agent_settings, report)
                except (PlatformNotConfiguredError, ApifyError) as exc:
                    message = f"{platform}: {exc}"
                    report.errors.append(message)
                    log.error("platform ingest failed", extra={"platform": platform, "error": str(exc)})
                    self.repos.audit.log(
                        "fetch.failed", level="error", platform=platform,
                        details={"error": str(exc), "error_type": type(exc).__name__},
                    )
                except Exception as exc:  # noqa: BLE001 - one platform must not sink the cycle
                    message = f"{platform}: unexpected {type(exc).__name__}: {exc}"
                    report.errors.append(message)
                    log.exception("platform ingest crashed", extra={"platform": platform})
                    self.repos.audit.log(
                        "fetch.failed", level="error", platform=platform,
                        details={"error": str(exc), "error_type": type(exc).__name__},
                    )

            self._process_pending(agent_settings, report)

            log.info("cycle finished", extra=report.to_dict())
            self.repos.audit.log("cycle.finished", details=report.to_dict())
            return report

    # -- ingestion -------------------------------------------------------

    def _ingest_platform(
        self, platform: str, agent_settings: AgentSettings, report: CycleReport
    ) -> None:
        adapter = self.adapters[platform]
        posts = adapter.fetch_posts(limit=agent_settings.max_posts_per_run)
        report.posts_fetched += len(posts)

        stored: dict[str, Post] = {}
        for fetched in posts:
            stored[fetched.platform_post_id] = self.repos.posts.upsert(
                Post(
                    platform=fetched.platform,
                    platform_post_id=fetched.platform_post_id,
                    account_id=fetched.account_id,
                    url=fetched.url,
                    caption=fetched.caption,
                    content_type=fetched.content_type,
                    topic=fetched.topic,
                    posted_at=fetched.posted_at,
                )
            )

        comments = adapter.fetch_comments(posts, limit=agent_settings.max_comments_per_run)
        report.comments_fetched += len(comments)

        for fetched in comments:
            post = stored.get(fetched.platform_post_id)
            record = Comment(
                platform=fetched.platform,
                platform_comment_id=fetched.platform_comment_id,
                platform_post_id=fetched.platform_post_id,
                post_id=post.id if post else None,
                parent_comment_id=fetched.parent_comment_id,
                author_id=fetched.author_id,
                author_name=fetched.author_name,
                comment_text=fetched.comment_text,
                comment_url=fetched.comment_url,
                comment_created_at=fetched.comment_created_at,
            )
            if self.repos.comments.insert_if_new(record) is None:
                report.comments_duplicate += 1
                log.debug(
                    "duplicate comment skipped",
                    extra={"platform": platform, "comment_id": fetched.platform_comment_id},
                )
            else:
                report.comments_new += 1

    # -- processing ------------------------------------------------------

    def _process_pending(self, agent_settings: AgentSettings, report: CycleReport) -> None:
        claimed = self.repos.comments.claim_new(agent_settings.max_comments_per_run)
        for comment in claimed:
            try:
                self.process_comment(comment, agent_settings, report)
            except Exception as exc:  # noqa: BLE001 - never let one comment stop the batch
                report.failed += 1
                report.errors.append(f"comment {comment.id}: {exc}")
                log.exception("comment processing crashed", extra={"comment_pk": comment.id})
                self.repos.comments.set_status(comment.id, CommentStatus.FAILED.value)
                self.repos.audit.log(
                    "process.crashed", level="error", platform=comment.platform,
                    comment_id=comment.id, details={"error": str(exc)},
                )

    def process_comment(
        self,
        comment: Comment,
        agent_settings: AgentSettings,
        report: CycleReport | None = None,
    ) -> dict[str, Any]:
        """Decide and act on a single stored comment."""
        report = report or CycleReport()
        report.processed += 1

        post = self.repos.posts.get_by_id(comment.post_id) if comment.post_id else None
        knowledge_items = self.knowledge.for_comment(
            comment.comment_text,
            post_caption=post.caption if post else "",
            post_topic=post.topic if post else "",
        )
        previous_replies = self.repos.replies.recent_reply_texts(platform=comment.platform)

        context = build_context(
            platform=comment.platform,
            comment=comment,
            post=post,
            previous_replies=previous_replies,
            knowledge_items=knowledge_items,
        )

        log.debug(
            "processing comment",
            extra={
                "comment_pk": comment.id,
                "platform": comment.platform,
                "text": truncate(comment.comment_text),
            },
        )

        ai_result = self.ai.decide(context, model=agent_settings.openai_model)

        guarded = apply_guardrails(
            ai_result.decision,
            comment_text=comment.comment_text,
            previous_replies=previous_replies,
            max_reply_length=agent_settings.max_reply_length,
            approved_knowledge_text=" ".join(i.content for i in knowledge_items),
        )
        decision = guarded.decision

        if guarded.violations or guarded.modified:
            self.repos.audit.log(
                "guardrails.applied", platform=comment.platform, comment_id=comment.id,
                details={
                    "violations": guarded.violations,
                    "notes": guarded.notes,
                    "blocked": guarded.blocked,
                    "final_action": decision.action,
                },
            )

        already_replied = self.repos.replies.has_posted_reply(comment.id)
        routing = route(
            decision, agent_settings,
            duplicate_ok=not already_replied,
            ai_valid=ai_result.valid,
        )

        self.repos.comments.save_decision(
            comment.id,
            intent=decision.intent,
            confidence=decision.confidence,
            risk_level=decision.risk_level,
            action=decision.action,
            status=routing.status,
            reason=decision.reason,
        )
        self.repos.audit.log(
            "ai.decision", platform=comment.platform, comment_id=comment.id,
            details={
                "action": decision.action,
                "intent": decision.intent,
                "confidence": decision.confidence,
                "risk_level": decision.risk_level,
                "needs_human": decision.needs_human,
                "model": ai_result.model,
                "valid": ai_result.valid,
                "attempts": ai_result.attempts,
                "routing": routing.status,
                "blockers": routing.blockers,
                "error": ai_result.error,
            },
        )

        # A reply row is stored whenever text exists — including in dry run and
        # while pending approval — so the dashboard can show what would be sent.
        reply_id: int | None = None
        if decision.reply:
            reply = self.repos.replies.create(
                Reply(
                    comment_id=comment.id,
                    reply_text=decision.reply,
                    final_reply_text=decision.reply,
                    model=ai_result.model,
                    confidence=decision.confidence,
                    approved=routing.auto,
                    approved_by="agent" if routing.auto else "",
                    auto=routing.auto,
                    dry_run=agent_settings.dry_run,
                )
            )
            reply_id = reply.id

        outcome: dict[str, Any] = {
            "comment_id": comment.id,
            "status": routing.status,
            "action": decision.action,
            "intent": decision.intent,
            "confidence": decision.confidence,
            "risk_level": decision.risk_level,
            "reply": decision.reply,
            "reply_id": reply_id,
            "blockers": routing.blockers,
            "guardrail_violations": guarded.violations,
            "ai_valid": ai_result.valid,
        }

        # Tally.
        if routing.status == CommentStatus.IGNORED.value:
            report.ignored += 1
        elif routing.status == CommentStatus.ESCALATED.value:
            report.escalated += 1
        elif routing.status == CommentStatus.NEEDS_REVIEW.value:
            report.needs_review += 1
        elif routing.status == CommentStatus.PENDING_APPROVAL.value:
            report.pending_approval += 1

        # Publish only when policy said so.
        if routing.publish_now and reply_id is not None:
            result = self.publisher.publish(
                comment_pk=comment.id, reply_id=reply_id, dry_run=agent_settings.dry_run
            )
            outcome["publish"] = {
                "published": result.published,
                "dry_run": result.dry_run,
                "skipped": result.skipped,
                "reason": result.reason,
            }
            if result.published:
                report.replied += 1
                report.auto_replied += 1
            elif result.dry_run:
                report.dry_run_replies += 1
        elif agent_settings.dry_run and decision.reply and routing.status == CommentStatus.PENDING_APPROVAL.value:
            report.dry_run_replies += 1

        return outcome

    # -- approval actions (dashboard) ------------------------------------

    def approve_reply(
        self, comment_pk: int, *, text: str | None = None, approved_by: str = "operator"
    ) -> dict[str, Any]:
        """Approve (optionally editing) and publish, respecting dry-run."""
        agent_settings = self.repos.settings.get()
        comment = self.repos.comments.get(comment_pk)
        if comment is None:
            return {"ok": False, "error": "comment not found"}
        reply = self.repos.replies.latest_for_comment(comment_pk)
        if reply is None:
            return {"ok": False, "error": "no generated reply for this comment"}
        if self.repos.replies.has_posted_reply(comment_pk):
            return {"ok": False, "error": "a reply has already been posted for this comment"}

        edited = text.strip() if text else None
        if edited:
            # Operator-edited text still gets the length guardrail applied.
            from .guardrails import trim_to_length
            edited = trim_to_length(edited, agent_settings.max_reply_length)

        self.repos.replies.approve(reply.id, text=edited, approved_by=approved_by)
        self.repos.comments.set_status(comment_pk, CommentStatus.APPROVED.value)
        self.repos.audit.log(
            "approval.granted", platform=comment.platform, comment_id=comment_pk,
            details={"reply_id": reply.id, "edited": bool(edited), "by": approved_by},
        )

        result = self.publisher.publish(
            comment_pk=comment_pk, reply_id=reply.id, dry_run=agent_settings.dry_run
        )
        return {
            "ok": result.published or result.dry_run,
            "published": result.published,
            "dry_run": result.dry_run,
            "reason": result.reason,
            "error": result.error,
            "platform_reply_id": result.platform_reply_id,
        }

    def ignore_comment(self, comment_pk: int, *, by: str = "operator") -> dict[str, Any]:
        comment = self.repos.comments.get(comment_pk)
        if comment is None:
            return {"ok": False, "error": "comment not found"}
        self.repos.comments.set_status(comment_pk, CommentStatus.IGNORED.value)
        self.repos.audit.log(
            "approval.ignored", platform=comment.platform, comment_id=comment_pk, details={"by": by}
        )
        return {"ok": True, "status": CommentStatus.IGNORED.value}

    def escalate_comment(self, comment_pk: int, *, by: str = "operator", note: str = "") -> dict[str, Any]:
        comment = self.repos.comments.get(comment_pk)
        if comment is None:
            return {"ok": False, "error": "comment not found"}
        self.repos.comments.set_status(comment_pk, CommentStatus.ESCALATED.value)
        self.repos.audit.log(
            "approval.escalated", platform=comment.platform, comment_id=comment_pk,
            details={"by": by, "note": note},
        )
        return {"ok": True, "status": CommentStatus.ESCALATED.value}
