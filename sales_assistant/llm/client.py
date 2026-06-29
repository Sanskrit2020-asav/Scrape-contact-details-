"""Thin Anthropic Claude wrapper.

Centralises model choice and request shape so the rest of the platform never
talks to the SDK directly. Designed to degrade gracefully: if the ``anthropic``
package or an API key is missing, :meth:`available` is ``False`` and callers
fall back to deterministic templates instead of crashing.

Defaults follow current Claude guidance: model ``claude-opus-4-8`` with adaptive
thinking. Optionally grounds generation on retrieved context and can be given
trusted-domain web search.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings, get_settings


@dataclass
class LLMResult:
    text: str
    model: str
    used_llm: bool


class LLMClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = None
        self._import_error: str | None = None
        if self.settings.llm_available:
            try:
                import anthropic  # type: ignore

                self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
            except Exception as exc:  # noqa: BLE001 - keep platform usable without the SDK
                self._import_error = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self._client is not None

    def status(self) -> dict:
        return {
            "available": self.available,
            "model": self.settings.llm_model,
            "has_api_key": self.settings.llm_available,
            "error": self._import_error,
        }

    def complete(
        self,
        prompt: str,
        system: str = "",
        context: str = "",
        max_tokens: int | None = None,
        web_search: bool = False,
    ) -> LLMResult:
        """Generate a completion, optionally grounded on ``context``.

        Falls back to returning the prompt's context unchanged (clearly marked)
        when the LLM is unavailable, so pipelines remain testable offline.
        """
        if not self.available:
            return LLMResult(text="", model=self.settings.llm_model, used_llm=False)

        user_content = prompt
        if context:
            user_content = (
                "Use the following retrieved company knowledge. Prefer it over prior "
                "knowledge, and do not contradict it:\n\n"
                f"{context}\n\n---\n\n{prompt}"
            )

        kwargs: dict = {
            "model": self.settings.llm_model,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "messages": [{"role": "user", "content": user_content}],
            # Adaptive thinking is the recommended mode for current Claude models.
            "thinking": {"type": "adaptive"},
        }
        if system:
            kwargs["system"] = system
        if web_search:
            kwargs["tools"] = [{
                "type": "web_search_20260209",
                "name": "web_search",
                "allowed_domains": list(self.settings.trusted_web_domains) or None,
            }]

        try:
            resp = self._client.messages.create(**kwargs)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - never crash the caller on an API hiccup
            return LLMResult(text="", model=self.settings.llm_model, used_llm=False)

        text = "".join(
            getattr(block, "text", "") for block in resp.content if getattr(block, "type", "") == "text"
        )
        return LLMResult(text=text.strip(), model=self.settings.llm_model, used_llm=True)
