"""Structured logging with a per-cycle trace id.

Every processing cycle gets a request/trace id that is attached to each log
line, so one comment's journey (fetch → AI decision → guardrails → posting) can
be reconstructed from the log stream alone.

Never log full author names, profile URLs or message bodies at INFO. Comment
text is only ever logged at DEBUG and truncated.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def get_request_id() -> str:
    return _request_id.get()


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Bind a trace id for the duration of a processing cycle."""
    rid = request_id or new_request_id()
    token = _request_id.set(rid)
    try:
        yield rid
    finally:
        _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or _request_id.get(),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "request_id":
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None) or _request_id.get()
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _RESERVED and k != "request_id"
        }
        suffix = f" {json.dumps(extras, default=str)}" if extras else ""
        return f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} [{rid}] {record.name}: {record.getMessage()}{suffix}"


_configured = False


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Safe to call more than once."""
    global _configured
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root.handlers = [handler]
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def truncate(text: str, limit: int = 120) -> str:
    """Shorten user-generated text before it reaches a DEBUG log line."""
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
