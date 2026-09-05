"""Turning raw Apify dataset items into our own types.

Scraper actors disagree about field names and change them between versions, so
every field is read through a list of candidate keys rather than one hardcoded
name. An item missing an id is dropped rather than guessed at — a comment with
a fabricated id would defeat duplicate protection.

All text is sanitised here, at the boundary, since everything downstream (the
AI prompt, the database, the dashboard) treats it as untrusted user input.
"""
from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

from .base import FetchedComment, FetchedPost

MAX_TEXT_CHARS = 4000
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """Normalise and de-fang externally supplied text."""
    if value is None:
        return ""
    text = str(value)
    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL_RE.sub("", text)
    text = text.replace("​", "").strip()
    return text[:limit]


def _first(item: dict[str, Any], keys: Iterable[str], default: Any = "") -> Any:
    """First non-empty value among ``keys``, supporting ``a.b`` paths."""
    for key in keys:
        node: Any = item
        for part in key.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if node not in (None, "", [], {}):
            return node
    return default


def _iso(value: Any) -> str | None:
    """Best-effort ISO-8601 timestamp; unparseable values are dropped."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return text[:64] or None


POST_ID_KEYS = ("postId", "post_id", "id", "pk", "shortCode", "shortcode", "facebookId")
POST_URL_KEYS = ("url", "postUrl", "post_url", "link", "permalink", "displayUrl")
POST_CAPTION_KEYS = ("caption", "text", "message", "title", "description")
POST_TIME_KEYS = ("timestamp", "time", "publishedAt", "date", "takenAt", "createdTime")
POST_TYPE_KEYS = ("type", "productType", "mediaType", "postType")

COMMENT_ID_KEYS = ("commentId", "comment_id", "id", "pk", "fbid")
COMMENT_TEXT_KEYS = ("text", "message", "comment", "commentText", "body")
COMMENT_URL_KEYS = ("commentUrl", "url", "permalink", "link")
COMMENT_TIME_KEYS = ("timestamp", "time", "createdAt", "date", "commentDate", "createdTime")
COMMENT_PARENT_KEYS = ("parentCommentId", "parentId", "replyToCommentId", "threadId")
AUTHOR_ID_KEYS = ("ownerId", "authorId", "owner.id", "user.id", "profileId", "from.id", "owner.pk")
AUTHOR_NAME_KEYS = (
    "ownerUsername", "authorName", "owner.name", "user.username", "username",
    "profileName", "from.name", "owner.username", "name",
)
PARENT_POST_KEYS = ("postId", "post_id", "postUrl", "post_url", "parentPostId", "mediaId")


def normalize_post(item: dict[str, Any], platform: str, account_id: str = "") -> FetchedPost | None:
    post_id = sanitize_text(_first(item, POST_ID_KEYS), 200)
    if not post_id:
        return None
    content_type = sanitize_text(_first(item, POST_TYPE_KEYS, "post"), 40).lower() or "post"
    if content_type in {"video", "clips", "igtv"}:
        content_type = "reel" if platform == "instagram" else "video"
    return FetchedPost(
        platform=platform,
        platform_post_id=post_id,
        url=sanitize_text(_first(item, POST_URL_KEYS), 500),
        caption=sanitize_text(_first(item, POST_CAPTION_KEYS)),
        content_type=content_type,
        topic=sanitize_text(_first(item, ("topic", "hashtag")), 120),
        account_id=account_id,
        posted_at=_iso(_first(item, POST_TIME_KEYS, None)),
        raw=item,
    )


def normalize_comment(
    item: dict[str, Any],
    platform: str,
    fallback_post_id: str = "",
) -> FetchedComment | None:
    comment_id = sanitize_text(_first(item, COMMENT_ID_KEYS), 200)
    if not comment_id:
        # No stable id means no duplicate protection. Drop it rather than
        # invent one and risk replying to the same person twice.
        return None
    text = sanitize_text(_first(item, COMMENT_TEXT_KEYS))
    return FetchedComment(
        platform=platform,
        platform_comment_id=comment_id,
        platform_post_id=sanitize_text(_first(item, PARENT_POST_KEYS, fallback_post_id), 200),
        author_id=sanitize_text(_first(item, AUTHOR_ID_KEYS), 200),
        author_name=sanitize_text(_first(item, AUTHOR_NAME_KEYS), 200),
        comment_text=text,
        comment_url=sanitize_text(_first(item, COMMENT_URL_KEYS), 500),
        comment_created_at=_iso(_first(item, COMMENT_TIME_KEYS, None)),
        parent_comment_id=sanitize_text(_first(item, COMMENT_PARENT_KEYS), 200),
        raw=item,
    )
