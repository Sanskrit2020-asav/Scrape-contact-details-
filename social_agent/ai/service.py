"""The single AI service for the whole application.

One centralized service, one OpenAI call per comment, channel-aware through the
``platform`` field of the context rather than through separate agents. No other
module in this package imports :mod:`openai_client` directly.

Responsibilities: comment understanding, classification, risk assessment, reply
generation, structured output and confidence scoring — spec §4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..observability import get_logger, truncate
from .context import AgentContext
from .openai_client import CompletionResult, OpenAIClient, OpenAIError
from .schema import (
    DECISION_SCHEMA,
    SCHEMA_NAME,
    AgentDecision,
    DecisionValidationError,
    extract_json,
    fallback_decision,
    validate_decision,
)

log = get_logger(__name__)

RETRY_NUDGE = (
    "\n\nYour previous response was rejected: {errors}\n"
    "Return ONLY a JSON object with exactly these keys: action, intent, "
    "confidence, reply, needs_human, reason, risk_level. No prose, no code fence."
)


@dataclass
class DecisionResult:
    """A decision plus how it was reached, for auditing and the dashboard."""

    decision: AgentDecision
    model: str = ""
    valid: bool = True
    attempts: int = 0
    error: str = ""
    response_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        """True when the AI could not be trusted, so a human must look."""
        return not self.valid


class AIDecisionService:
    """Turns a comment context into a validated :class:`AgentDecision`."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: OpenAIClient | None = None,
        system_prompt: str | None = None,
    ):
        self.settings = settings or get_settings()
        self.client = client or OpenAIClient(self.settings.openai)
        self._system_prompt = system_prompt
        self._prompt_mtime: float | None = None

    # -- prompt ----------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        """The agent instructions, read from disk and hot-reloaded on change.

        Kept in ``ai/prompts/system_prompt.md`` so the brand voice can be tuned
        without touching application code (spec §25).
        """
        if self._system_prompt is not None:
            return self._system_prompt
        path = Path(self.settings.system_prompt_path)
        if not path.is_file():
            raise FileNotFoundError(f"System prompt not found at {path}")
        mtime = path.stat().st_mtime
        if self._prompt_mtime != mtime or not getattr(self, "_cached_prompt", ""):
            self._cached_prompt = path.read_text(encoding="utf-8")
            self._prompt_mtime = mtime
        return self._cached_prompt

    @property
    def configured(self) -> bool:
        return self.client.configured

    # -- decisions -------------------------------------------------------

    def decide(self, context: AgentContext, *, model: str | None = None) -> DecisionResult:
        """Classify a comment and draft a reply.

        The contract is that this never raises and never returns something
        unvalidated. On any failure — network, rate limit, malformed output —
        it returns the conservative fallback (escalate / needs_human), because
        an AI failure must never become a published comment.
        """
        resolved_model = model or self.settings.openai.model
        if not self.configured:
            return DecisionResult(
                decision=fallback_decision("OpenAI is not configured; routed to a human"),
                model=resolved_model,
                valid=False,
                error="OPENAI_API_KEY is not configured",
            )

        user_content = context.to_user_content()
        attempts = 0
        last_errors = ""
        last_completion: CompletionResult | None = None

        # One retry with the validation errors fed back (spec §24).
        for round_index in range(2):
            attempts += 1
            prompt_input = user_content if round_index == 0 else (
                user_content + RETRY_NUDGE.format(errors=last_errors)
            )
            try:
                completion = self.client.complete_json(
                    instructions=self.system_prompt,
                    user_content=prompt_input,
                    json_schema=DECISION_SCHEMA,
                    schema_name=SCHEMA_NAME,
                    model=resolved_model,
                )
                last_completion = completion
            except OpenAIError as exc:
                log.error(
                    "openai call failed",
                    extra={
                        "platform": context.platform,
                        "comment_id": context.comment.id,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                return DecisionResult(
                    decision=fallback_decision(f"AI unavailable: {exc}"),
                    model=resolved_model,
                    valid=False,
                    attempts=attempts,
                    error=str(exc),
                )

            try:
                decision = validate_decision(extract_json(completion.text))
            except DecisionValidationError as exc:
                last_errors = "; ".join(exc.errors)
                log.warning(
                    "invalid model output",
                    extra={
                        "comment_id": context.comment.id,
                        "attempt": attempts,
                        "errors": exc.errors,
                        "raw": truncate(completion.text, 200),
                    },
                )
                continue

            log.info(
                "ai decision",
                extra={
                    "platform": context.platform,
                    "comment_id": context.comment.id,
                    "action": decision.action,
                    "intent": decision.intent,
                    "confidence": decision.confidence,
                    "risk_level": decision.risk_level,
                    "needs_human": decision.needs_human,
                    "attempts": attempts,
                },
            )
            return DecisionResult(
                decision=decision,
                model=completion.model or resolved_model,
                valid=True,
                attempts=attempts,
                response_id=completion.response_id,
                usage=completion.usage,
            )

        # Two rounds of invalid output: mark for human review, publish nothing.
        return DecisionResult(
            decision=fallback_decision(f"AI output failed validation: {last_errors}"),
            model=(last_completion.model if last_completion else resolved_model),
            valid=False,
            attempts=attempts,
            error=last_errors,
        )
