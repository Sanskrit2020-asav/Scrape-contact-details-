"""The structured context handed to OpenAI for one comment.

Building this is a separate, pure step so it can be asserted on in tests and
inspected in the dashboard without an API call. It is also the enforcement point
for §15 of the spec: the model only ever sees *approved* knowledge, never the
raw knowledge table.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

MAX_CAPTION_CHARS = 600
MAX_COMMENT_CHARS = 1200
MAX_KNOWLEDGE_ITEMS = 12
MAX_KNOWLEDGE_CHARS = 700
MAX_PREVIOUS_REPLIES = 10


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


@dataclass
class PostContext:
    id: str = ""
    caption: str = ""
    topic: str = ""
    url: str = ""


@dataclass
class CommentContext:
    id: str = ""
    author: str = ""
    text: str = ""
    created_at: str = ""
    is_reply_to_comment: bool = False


@dataclass
class KnowledgeContext:
    title: str
    content: str
    category: str = "general"


@dataclass
class AgentContext:
    """Exactly what the model is shown. Nothing else reaches it."""

    platform: str
    content_type: str = "comment"
    post: PostContext = field(default_factory=PostContext)
    comment: CommentContext = field(default_factory=CommentContext)
    previous_replies: list[str] = field(default_factory=list)
    approved_knowledge: list[KnowledgeContext] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "content_type": self.content_type,
            "post": asdict(self.post),
            "comment": asdict(self.comment),
            "previous_replies": self.previous_replies,
            "approved_knowledge": [asdict(k) for k in self.approved_knowledge],
        }

    def to_user_content(self) -> str:
        """Render the context as the user turn sent to OpenAI."""
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        guidance = (
            "Decide how North Nepal Travel & Trek should handle this social media "
            "comment. Return only the JSON object defined by the schema.\n\n"
            "`approved_knowledge` is the ONLY source of company-specific facts you "
            "may state. If a needed fact is not there, do not invent it.\n"
            "`previous_replies` are things we recently said — do not echo their "
            "wording, structure, opening or emoji.\n\n"
        )
        return f"{guidance}{payload}"


def build_context(
    *,
    platform: str,
    comment,
    post=None,
    previous_replies: list[str] | None = None,
    knowledge_items: list | None = None,
    content_type: str | None = None,
) -> AgentContext:
    """Assemble the model context from database rows.

    ``knowledge_items`` must already be filtered to active, valid, non-human-only
    entries — see :meth:`KnowledgeRepository.active_items`.
    """
    post_ctx = PostContext()
    resolved_type = content_type or "comment"
    if post is not None:
        post_ctx = PostContext(
            id=str(getattr(post, "platform_post_id", "") or ""),
            caption=_clip(getattr(post, "caption", "") or "", MAX_CAPTION_CHARS),
            topic=getattr(post, "topic", "") or "",
            url=getattr(post, "url", "") or "",
        )
        if content_type is None:
            base = getattr(post, "content_type", "") or "post"
            resolved_type = f"{base}_comment"

    comment_ctx = CommentContext(
        id=str(getattr(comment, "platform_comment_id", "") or ""),
        author=getattr(comment, "author_name", "") or "",
        text=_clip(getattr(comment, "comment_text", "") or "", MAX_COMMENT_CHARS),
        created_at=getattr(comment, "comment_created_at", "") or "",
        is_reply_to_comment=bool(getattr(comment, "parent_comment_id", "")),
    )

    knowledge = [
        KnowledgeContext(
            title=item.title,
            content=_clip(item.content, MAX_KNOWLEDGE_CHARS),
            category=item.category,
        )
        for item in (knowledge_items or [])[:MAX_KNOWLEDGE_ITEMS]
    ]

    return AgentContext(
        platform=platform,
        content_type=resolved_type,
        post=post_ctx,
        comment=comment_ctx,
        previous_replies=[r for r in (previous_replies or []) if r][:MAX_PREVIOUS_REPLIES],
        approved_knowledge=knowledge,
    )
