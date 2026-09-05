"""Social channel abstraction (Apify-backed in V1)."""
from .apify_adapter import ApifySocialAdapter
from .base import (
    CommentThreadContext,
    FetchedComment,
    FetchedPost,
    PlatformNotConfiguredError,
    ReplyResult,
    SocialPlatformAdapter,
)
from .facebook import FacebookAdapter
from .instagram import InstagramAdapter
from .mock import MockAdapter
from .normalize import normalize_comment, normalize_post, sanitize_text
from .registry import FUTURE_PLATFORMS, build_adapters

__all__ = [
    "FUTURE_PLATFORMS",
    "ApifySocialAdapter",
    "CommentThreadContext",
    "FacebookAdapter",
    "FetchedComment",
    "FetchedPost",
    "InstagramAdapter",
    "MockAdapter",
    "PlatformNotConfiguredError",
    "ReplyResult",
    "SocialPlatformAdapter",
    "build_adapters",
    "normalize_comment",
    "normalize_post",
    "sanitize_text",
]
