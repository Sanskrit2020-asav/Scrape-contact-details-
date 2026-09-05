"""JSON API behind the dashboard.

Handlers are plain functions of ``(app, params, body) -> dict``, so they are
testable without an HTTP server. Secrets never appear in any response: the
settings endpoint reports whether a key is configured, never its value.
"""
from __future__ import annotations

from typing import Any

from ..database.models import AgentSettings, CommentStatus, KnowledgeItem
from ..observability import get_logger

log = get_logger(__name__)

PENDING_STATUSES = (CommentStatus.PENDING_APPROVAL.value, CommentStatus.NEEDS_REVIEW.value)


def _int(params: dict[str, Any], key: str, default: int) -> int:
    try:
        return max(0, int(params.get(key, default)))
    except (TypeError, ValueError):
        return default


def overview(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Counts for the overview screen (spec §22)."""
    statuses = app.repos.comments.counts_by_status()
    platforms = app.repos.comments.counts_by_platform()
    replies = app.repos.replies.counts()
    agent_settings = app.repos.settings.get()

    return {
        "totals": {
            "comments": app.repos.comments.total(),
            "replies": replies.get("total", 0),
            "posted_replies": replies.get("posted", 0),
            "auto_replies": replies.get("auto", 0),
            "dry_run_replies": replies.get("dry_run_posted", 0),
            "ignored": statuses.get(CommentStatus.IGNORED.value, 0),
            "escalated": statuses.get(CommentStatus.ESCALATED.value, 0),
            "pending_approval": statuses.get(CommentStatus.PENDING_APPROVAL.value, 0),
            "needs_review": statuses.get(CommentStatus.NEEDS_REVIEW.value, 0),
            "failed": statuses.get(CommentStatus.FAILED.value, 0),
        },
        "by_status": statuses,
        "by_platform": {
            "facebook": platforms.get("facebook", 0),
            "instagram": platforms.get("instagram", 0),
        },
        "mode": {
            "dry_run": agent_settings.dry_run,
            "human_approval_required": agent_settings.human_approval_required,
            "auto_reply_enabled": agent_settings.auto_reply_enabled,
            "minimum_confidence": agent_settings.minimum_confidence,
        },
        "health": app.health(),
    }


def _comment_view(app, comment) -> dict[str, Any]:
    reply = app.repos.replies.latest_for_comment(comment.id)
    post = app.repos.posts.get_by_id(comment.post_id) if comment.post_id else None
    return {
        "id": comment.id,
        "platform": comment.platform,
        "platform_comment_id": comment.platform_comment_id,
        "author_name": comment.author_name,
        "comment_text": comment.comment_text,
        "comment_url": comment.comment_url,
        "comment_created_at": comment.comment_created_at,
        "intent": comment.intent,
        "confidence": comment.confidence,
        "risk_level": comment.risk_level,
        "action": comment.action,
        "status": comment.status,
        "reason": comment.reason,
        "post": {
            "topic": post.topic if post else "",
            "caption": (post.caption[:160] if post else ""),
            "url": post.url if post else "",
            "content_type": post.content_type if post else "",
        },
        "reply": None if reply is None else {
            "id": reply.id,
            "text": reply.final_reply_text or reply.reply_text,
            "original_text": reply.reply_text,
            "model": reply.model,
            "confidence": reply.confidence,
            "approved": reply.approved,
            "posted": reply.posted,
            "dry_run": reply.dry_run,
            "auto": reply.auto,
            "error": reply.error,
        },
    }


def inbox(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    status = params.get("status") or None
    platform = params.get("platform") or None
    comments = app.repos.comments.list(
        status=status, platform=platform,
        limit=_int(params, "limit", 50), offset=_int(params, "offset", 0),
    )
    return {"items": [_comment_view(app, c) for c in comments]}


def pending(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    comments = app.repos.comments.list(
        status=PENDING_STATUSES, limit=_int(params, "limit", 50), offset=_int(params, "offset", 0)
    )
    return {"items": [_comment_view(app, c) for c in comments]}


def history(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return {
        "items": app.repos.replies.history(
            limit=_int(params, "limit", 50), offset=_int(params, "offset", 0)
        )
    }


def logs(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    entries = app.repos.audit.list(
        level=params.get("level") or None,
        limit=_int(params, "limit", 100), offset=_int(params, "offset", 0),
    )
    return {"items": [e.to_dict() for e in entries]}


# -- actions ---------------------------------------------------------------


def approve(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    comment_id = body.get("comment_id")
    if not comment_id:
        return {"ok": False, "error": "comment_id is required"}
    return app.agent.approve_reply(
        int(comment_id), text=body.get("text"), approved_by=body.get("by") or "operator"
    )


def ignore(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    comment_id = body.get("comment_id")
    if not comment_id:
        return {"ok": False, "error": "comment_id is required"}
    return app.agent.ignore_comment(int(comment_id), by=body.get("by") or "operator")


def escalate(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    comment_id = body.get("comment_id")
    if not comment_id:
        return {"ok": False, "error": "comment_id is required"}
    return app.agent.escalate_comment(
        int(comment_id), by=body.get("by") or "operator", note=body.get("note", "")
    )


def run_cycle(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    platforms = body.get("platforms") or None
    return app.agent.run_cycle(platforms=platforms).to_dict()


# -- knowledge -------------------------------------------------------------


def knowledge_list(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    items = app.repos.knowledge.list(status=params.get("status") or None)
    return {"items": [i.to_dict() for i in items]}


def knowledge_create(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title or not content:
        return {"ok": False, "error": "title and content are required"}
    item = app.repos.knowledge.create(
        KnowledgeItem(
            title=title, content=content,
            category=(body.get("category") or "general").strip(),
            status=(body.get("status") or "active").strip(),
            valid_from=body.get("valid_from") or None,
            valid_until=body.get("valid_until") or None,
            human_only=bool(body.get("human_only")),
        )
    )
    app.repos.audit.log("knowledge.created", details={"id": item.id, "title": item.title})
    return {"ok": True, "item": item.to_dict()}


def knowledge_update(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    item_id = body.get("id")
    if not item_id:
        return {"ok": False, "error": "id is required"}
    item = app.repos.knowledge.update(int(item_id), **{
        k: v for k, v in body.items() if k != "id"
    })
    if item is None:
        return {"ok": False, "error": "knowledge item not found"}
    app.repos.audit.log("knowledge.updated", details={"id": item.id})
    return {"ok": True, "item": item.to_dict()}


def knowledge_delete(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    item_id = body.get("id")
    if not item_id:
        return {"ok": False, "error": "id is required"}
    app.repos.knowledge.delete(int(item_id))
    app.repos.audit.log("knowledge.deleted", details={"id": int(item_id)})
    return {"ok": True}


# -- settings --------------------------------------------------------------


def settings_get(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return {
        "settings": app.repos.settings.get().to_dict(),
        "editable": list(AgentSettings.EDITABLE),
        "environment": app.settings.redacted(),
    }


def settings_update(app, params: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Update runtime settings. Only whitelisted fields; never a secret."""
    rejected = [k for k in body if k not in AgentSettings.EDITABLE]
    updated = app.repos.settings.update(**body)
    app.repos.audit.log(
        "settings.updated",
        details={"fields": [k for k in body if k in AgentSettings.EDITABLE], "rejected": rejected},
    )
    log.info("settings updated", extra={"fields": list(body), "rejected": rejected})
    return {"ok": True, "settings": updated.to_dict(), "rejected": rejected}


#: ``(method, path) -> handler``
ROUTES: dict[tuple[str, str], Any] = {
    ("GET", "/api/overview"): overview,
    ("GET", "/api/inbox"): inbox,
    ("GET", "/api/pending"): pending,
    ("GET", "/api/history"): history,
    ("GET", "/api/logs"): logs,
    ("GET", "/api/knowledge"): knowledge_list,
    ("GET", "/api/settings"): settings_get,
    ("POST", "/api/approve"): approve,
    ("POST", "/api/ignore"): ignore,
    ("POST", "/api/escalate"): escalate,
    ("POST", "/api/run-cycle"): run_cycle,
    ("POST", "/api/knowledge"): knowledge_create,
    ("POST", "/api/knowledge/update"): knowledge_update,
    ("POST", "/api/knowledge/delete"): knowledge_delete,
    ("POST", "/api/settings"): settings_update,
}
