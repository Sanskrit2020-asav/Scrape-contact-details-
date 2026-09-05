"""Domain enums and row models shared across layers.

Plain dataclasses and string enums keep the core import-light — the AI layer,
the adapters and the dashboard all speak these types without depending on the
database module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Platform(str, Enum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"

    @classmethod
    def parse(cls, value: str) -> "Platform":
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError(f"Unsupported platform: {value!r}") from exc


class Action(str, Enum):
    REPLY = "reply"
    IGNORE = "ignore"
    ESCALATE = "escalate"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommentStatus(str, Enum):
    NEW = "new"                     # fetched, not yet sent to the AI
    PROCESSING = "processing"       # claimed by a cycle
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"           # approved, waiting for the publisher
    REPLIED = "replied"             # a reply exists on the platform
    DRY_RUN_REPLIED = "dry_run_replied"   # would have been posted; DRY_RUN=true
    IGNORED = "ignored"
    ESCALATED = "escalated"
    FAILED = "failed"               # AI or posting failure; needs a human
    NEEDS_REVIEW = "needs_review"   # invalid AI output / low confidence


#: Comment intents the agent understands. Extensible: the AI may return a value
#: outside this list, which is preserved verbatim and treated as ``unknown`` for
#: routing purposes rather than rejected.
INTENTS: tuple[str, ...] = (
    "compliment",
    "general_engagement",
    "travel_question",
    "price_inquiry",
    "itinerary_question",
    "availability",
    "permit_question",
    "trekking_difficulty",
    "weather",
    "safety",
    "booking_intent",
    "lead",
    "complaint",
    "negative_feedback",
    "refund",
    "urgent_safety",
    "spam",
    "irrelevant",
    "duplicate",
    "unknown",
)

#: Intents that must never be answered autonomously, whatever the model says.
ALWAYS_ESCALATE_INTENTS: frozenset[str] = frozenset(
    {"complaint", "negative_feedback", "refund", "urgent_safety", "safety"}
)

KNOWLEDGE_CATEGORIES: tuple[str, ...] = (
    "itinerary",
    "difficulty",
    "pricing",
    "permits",
    "transportation",
    "accommodation",
    "helicopter",
    "policy",
    "faq",
    "seasonal",
    "safety",
    "notice",
    "general",
)


@dataclass
class Post:
    platform: str
    platform_post_id: str
    account_id: str = ""
    url: str = ""
    caption: str = ""
    content_type: str = "post"
    topic: str = ""
    posted_at: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Comment:
    platform: str
    platform_comment_id: str
    platform_post_id: str = ""
    post_id: int | None = None
    parent_comment_id: str = ""
    author_id: str = ""
    author_name: str = ""
    comment_text: str = ""
    comment_url: str = ""
    comment_created_at: str | None = None
    intent: str = ""
    confidence: float = 0.0
    risk_level: str = ""
    action: str = ""
    status: str = CommentStatus.NEW.value
    reason: str = ""
    request_id: str = ""
    processed_at: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reply:
    comment_id: int
    reply_text: str = ""
    final_reply_text: str = ""
    model: str = ""
    confidence: float = 0.0
    approved: bool = False
    approved_by: str = ""
    approved_at: str | None = None
    posted: bool = False
    posted_at: str | None = None
    platform_reply_id: str | None = None
    auto: bool = False
    dry_run: bool = True
    error: str = ""
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeItem:
    title: str
    content: str
    category: str = "general"
    status: str = "active"
    valid_from: str | None = None
    valid_until: str | None = None
    human_only: bool = False
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentSettings:
    """The live operational configuration, backed by the ``agent_settings`` row."""

    dry_run: bool = True
    human_approval_required: bool = True
    auto_reply_enabled: bool = False
    minimum_confidence: float = 0.90
    max_reply_length: int = 240
    polling_interval_seconds: int = 300
    max_comments_per_run: int = 50
    max_posts_per_run: int = 10
    openai_model: str = "gpt-5"
    facebook_enabled: bool = True
    instagram_enabled: bool = True
    facebook_comments_actor: str = ""
    facebook_posts_actor: str = ""
    facebook_reply_actor: str = ""
    instagram_comments_actor: str = ""
    instagram_posts_actor: str = ""
    instagram_reply_actor: str = ""
    #: 0 disables the cap. Counted against tokens used in the last 24 hours.
    daily_token_budget: int = 0
    #: Per 1,000,000 tokens. Configuration, not constants: published rates
    #: change and differ per model, so a hardcoded number would go stale and be
    #: presented to the operator as fact.
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    id: int = 1
    created_at: str | None = None
    updated_at: str | None = None

    #: Fields an operator may change from the dashboard. Secrets are absent by
    #: design — API keys are environment-only and never round-trip the browser.
    EDITABLE: tuple[str, ...] = field(
        default=(
            "dry_run",
            "human_approval_required",
            "auto_reply_enabled",
            "minimum_confidence",
            "max_reply_length",
            "polling_interval_seconds",
            "max_comments_per_run",
            "max_posts_per_run",
            "openai_model",
            "facebook_enabled",
            "instagram_enabled",
            "facebook_comments_actor",
            "facebook_posts_actor",
            "facebook_reply_actor",
            "instagram_comments_actor",
            "instagram_posts_actor",
            "instagram_reply_actor",
            "daily_token_budget",
            "input_cost_per_million",
            "output_cost_per_million",
        ),
        repr=False,
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("EDITABLE", None)
        return data

    def actor_for(self, platform: str, kind: str) -> str:
        """Return the configured actor id for ``platform`` and ``kind``.

        ``kind`` is one of ``comments``, ``posts`` or ``reply``.
        """
        return str(getattr(self, f"{platform}_{kind}_actor", "") or "")


@dataclass
class UsageRecord:
    """One OpenAI call's token consumption."""

    model: str = ""
    platform: str = ""
    comment_id: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    attempts: int = 1
    request_id: str = ""
    response_id: str = ""
    succeeded: bool = True
    id: int | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditLog:
    event_type: str
    level: str = "info"
    platform: str = ""
    comment_id: int | None = None
    request_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
