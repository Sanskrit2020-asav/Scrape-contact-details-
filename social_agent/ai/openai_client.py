"""Transport-level OpenAI client.

OpenAI is the only runtime AI provider in this application. Everything that
talks to it goes through here, so the model, endpoint, retry policy and timeout
are all in one place.

The client speaks the REST API directly over ``urllib`` rather than depending on
the SDK. That keeps the package installable anywhere Python runs, and — more
usefully — makes the transport a single injectable seam, so tests exercise the
real request-building and response-parsing code with a fake HTTP layer instead
of mocking a third-party object graph.

Two API styles are supported and selected by ``OPENAI_API_STYLE``:

* ``responses`` (default) — ``POST /v1/responses``, the current agent-oriented
  API, with structured output supplied via ``text.format``.
* ``chat_completions`` — ``POST /v1/chat/completions`` with
  ``response_format``, for accounts or gateways pinned to the older surface.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..config import OpenAISettings
from ..observability import get_logger

log = get_logger(__name__)


class OpenAIError(RuntimeError):
    """Any failure talking to OpenAI."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class OpenAITimeoutError(OpenAIError):
    def __init__(self, message: str = "OpenAI request timed out"):
        super().__init__(message, retryable=True)


class OpenAIRateLimitError(OpenAIError):
    def __init__(self, message: str = "OpenAI rate limit exceeded", retry_after: float | None = None):
        super().__init__(message, status=429, retryable=True)
        self.retry_after = retry_after


class Transport(Protocol):
    """The HTTP seam. Tests substitute this; production uses urllib."""

    def post(self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
        ...


class UrllibTransport:
    def post(self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:800]
            except Exception:  # noqa: BLE001 - the status matters more than the body
                pass
            if exc.code == 429:
                retry_after = exc.headers.get("retry-after") if exc.headers else None
                raise OpenAIRateLimitError(
                    f"OpenAI rate limit: {detail or exc.reason}",
                    retry_after=float(retry_after) if retry_after else None,
                ) from exc
            raise OpenAIError(
                f"OpenAI HTTP {exc.code}: {detail or exc.reason}",
                status=exc.code,
                retryable=exc.code >= 500,
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                raise OpenAITimeoutError() from exc
            raise OpenAIError(f"OpenAI network error: {reason}", retryable=True) from exc
        except TimeoutError as exc:
            raise OpenAITimeoutError() from exc


@dataclass
class CompletionResult:
    """A raw text completion plus the metadata we record for auditing."""

    text: str
    model: str
    response_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1


class OpenAIClient:
    """Builds requests, applies the retry policy, extracts the text payload."""

    def __init__(
        self,
        settings: OpenAISettings,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.transport = transport or UrllibTransport()
        self._sleep = sleep

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.settings.organization:
            headers["OpenAI-Organization"] = self.settings.organization
        return headers

    def _url(self) -> str:
        base = self.settings.base_url.rstrip("/")
        path = "/responses" if self.settings.api_style == "responses" else "/chat/completions"
        return f"{base}{path}"

    def _build_payload(
        self,
        *,
        instructions: str,
        user_content: str,
        model: str,
        json_schema: dict[str, Any] | None,
        schema_name: str,
    ) -> dict[str, Any]:
        if self.settings.api_style == "responses":
            payload: dict[str, Any] = {
                "model": model,
                "instructions": instructions,
                "input": [
                    {"role": "user", "content": [{"type": "input_text", "text": user_content}]}
                ],
                "max_output_tokens": self.settings.max_output_tokens,
            }
            if json_schema is not None:
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": json_schema,
                    }
                }
        else:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_content},
                ],
                "max_completion_tokens": self.settings.max_output_tokens,
            }
            if json_schema is not None:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
                }
        # Only sent when explicitly configured: several current models accept
        # the default temperature only.
        if self.settings.temperature is not None:
            payload["temperature"] = self.settings.temperature
        return payload

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        """Pull assistant text out of either API shape."""
        # Responses API convenience field, when a gateway provides it.
        direct = data.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        if isinstance(direct, list) and direct:
            joined = "".join(part for part in direct if isinstance(part, str))
            if joined.strip():
                return joined

        # Responses API canonical shape.
        chunks: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in (None, "message"):
                continue
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                    text = block.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
        if chunks:
            return "".join(chunks)

        # Chat Completions shape.
        for choice in data.get("choices") or []:
            message = (choice or {}).get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                joined = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
                if joined.strip():
                    return joined

        # A truncated response is a distinct, actionable failure.
        if data.get("status") == "incomplete":
            detail = (data.get("incomplete_details") or {}).get("reason", "unknown")
            raise OpenAIError(f"OpenAI response incomplete: {detail}")
        return ""

    def complete_json(
        self,
        *,
        instructions: str,
        user_content: str,
        json_schema: dict[str, Any],
        schema_name: str,
        model: str | None = None,
    ) -> CompletionResult:
        """Request one structured-output completion, retrying transient errors.

        Retries cover timeouts, rate limits and 5xx responses with exponential
        backoff plus jitter. A 4xx other than 429 is a request problem and is
        raised immediately — retrying it only burns quota.
        """
        if not self.configured:
            raise OpenAIError("OPENAI_API_KEY is not configured")

        resolved_model = model or self.settings.model
        payload = self._build_payload(
            instructions=instructions,
            user_content=user_content,
            model=resolved_model,
            json_schema=json_schema,
            schema_name=schema_name,
        )
        url, headers = self._url(), self._headers()
        last_error: OpenAIError | None = None

        for attempt in range(1, self.settings.max_retries + 2):
            try:
                data = self.transport.post(url, payload, headers, self.settings.timeout_seconds)
                text = self._extract_text(data)
                return CompletionResult(
                    text=text,
                    model=data.get("model", resolved_model),
                    response_id=str(data.get("id", "")),
                    usage=data.get("usage") or {},
                    attempts=attempt,
                )
            except OpenAIError as exc:
                last_error = exc
                if not exc.retryable or attempt > self.settings.max_retries:
                    raise
                delay = getattr(exc, "retry_after", None) or min(
                    2 ** (attempt - 1) + random.uniform(0, 0.4), 20.0
                )
                log.warning(
                    "openai call failed, retrying",
                    extra={"attempt": attempt, "delay_seconds": round(delay, 2), "error": str(exc)},
                )
                self._sleep(delay)

        raise last_error or OpenAIError("OpenAI call failed")
