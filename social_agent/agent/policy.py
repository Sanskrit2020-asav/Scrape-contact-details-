"""Routing policy — what happens to a comment once guardrails have run.

Auto-reply requires *every* one of these to hold (spec §8):

    confidence >= configured threshold
    AND risk_level == low
    AND needs_human == false
    AND action == reply
    AND auto_reply_enabled
    AND NOT human_approval_required
    AND duplicate check passes
    AND dry_run == false

Any single failure routes to human approval instead. The decision is returned as
a structured :class:`RoutingDecision` rather than a bare boolean so the
dashboard and the audit log can show exactly which condition stopped a reply.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ai.schema import AgentDecision
from ..database.models import Action, AgentSettings, CommentStatus, RiskLevel


@dataclass
class RoutingDecision:
    """Where a comment goes, and why."""

    status: str
    publish_now: bool = False
    auto: bool = False
    blockers: list[str] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        return "; ".join(self.blockers) if self.blockers else "all auto-reply conditions met"


def auto_reply_blockers(
    decision: AgentDecision,
    settings: AgentSettings,
    *,
    duplicate_ok: bool = True,
) -> list[str]:
    """Every reason this decision may not be published autonomously."""
    blockers: list[str] = []
    if decision.action != Action.REPLY.value:
        blockers.append(f"action is {decision.action}, not reply")
    if decision.needs_human:
        blockers.append("model flagged needs_human")
    if decision.risk_level != RiskLevel.LOW.value:
        blockers.append(f"risk_level is {decision.risk_level}, not low")
    if decision.confidence < settings.minimum_confidence:
        blockers.append(
            f"confidence {decision.confidence:.2f} below threshold {settings.minimum_confidence:.2f}"
        )
    if not settings.auto_reply_enabled:
        blockers.append("auto_reply_enabled is off")
    if settings.human_approval_required:
        blockers.append("human_approval_required is on")
    if not duplicate_ok:
        blockers.append("a reply already exists for this comment")
    if settings.dry_run:
        blockers.append("dry_run is on")
    return blockers


def route(
    decision: AgentDecision,
    settings: AgentSettings,
    *,
    duplicate_ok: bool = True,
    ai_valid: bool = True,
) -> RoutingDecision:
    """Decide the comment's status and whether to publish immediately."""
    if not ai_valid:
        # Unvalidated AI output never becomes a published reply (spec §24).
        return RoutingDecision(
            status=CommentStatus.NEEDS_REVIEW.value,
            blockers=["AI output failed validation"],
        )

    if decision.action == Action.IGNORE.value:
        return RoutingDecision(status=CommentStatus.IGNORED.value)

    if decision.action == Action.ESCALATE.value:
        return RoutingDecision(status=CommentStatus.ESCALATED.value)

    blockers = auto_reply_blockers(decision, settings, duplicate_ok=duplicate_ok)

    if not blockers:
        return RoutingDecision(status=CommentStatus.APPROVED.value, publish_now=True, auto=True)

    # In dry-run the reply is still generated and stored — it just isn't sent.
    # Everything else waits for a human.
    return RoutingDecision(status=CommentStatus.PENDING_APPROVAL.value, blockers=blockers)
