"""Apify actor configuration.

Actor ids and their input payloads live in configuration rather than code
because actor input schemas differ between actors and change over time. Each
operation is described by an :class:`ActorSpec` whose ``input_template`` is
rendered with the runtime values (page url, post ids, limits).

The reply actors are intentionally **unset by default**. Posting a reply to
Facebook or Instagram through Apify requires an actor with authenticated write
access; there is no universal public actor we can name with confidence, so the
adapter raises a clear configuration error instead of guessing at an API
contract. Nothing is silently faked — see docs/social_agent.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import PACKAGE_ROOT
from ..observability import get_logger

log = get_logger(__name__)

ACTOR_INPUTS_PATH = PACKAGE_ROOT / "apify" / "actor_inputs.json"


@dataclass
class ActorSpec:
    """One configured Apify operation."""

    actor_id: str
    input_template: dict[str, Any] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.actor_id)

    def render(self, **values: Any) -> dict[str, Any]:
        """Fill ``{placeholders}`` in the template with runtime values.

        String values are substituted with ``str.format``; lists and numbers are
        replaced wholesale when the entire value is a single placeholder, so a
        template can express both ``"directUrls": ["{post_url}"]`` and
        ``"resultsLimit": "{limit}"`` correctly typed.
        """
        return _render(self.input_template, values)


def _render(node: Any, values: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: _render(v, values) for k, v in node.items()}
    if isinstance(node, list):
        rendered = [_render(v, values) for v in node]
        # A one-element list whose placeholder resolved to a list flattens,
        # so "urls": ["{post_urls}"] yields the list itself.
        if len(rendered) == 1 and isinstance(rendered[0], list):
            return rendered[0]
        return [r for r in rendered if r not in ("", None)]
    if isinstance(node, str):
        stripped = node.strip()
        if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1:
            key = stripped[1:-1]
            if key in values:
                return values[key]
            return ""
        try:
            return node.format(**values)
        except (KeyError, IndexError):
            return node
    return node


#: Fallback input templates, used when actor_inputs.json has no entry.
#: Deliberately minimal: the fields below are the ones the named default actors
#: document. Override per-deployment in actor_inputs.json rather than editing.
DEFAULT_INPUT_TEMPLATES: dict[str, dict[str, Any]] = {
    "facebook_posts": {"startUrls": ["{page_urls}"], "resultsLimit": "{limit}"},
    "facebook_comments": {"startUrls": ["{post_urls}"], "resultsLimit": "{limit}"},
    "instagram_posts": {"directUrls": ["{page_urls}"], "resultsLimit": "{limit}"},
    "instagram_comments": {"directUrls": ["{post_urls}"], "resultsLimit": "{limit}"},
    "facebook_reply": {"commentUrl": "{comment_url}", "replyText": "{reply_text}"},
    "instagram_reply": {"commentUrl": "{comment_url}", "replyText": "{reply_text}"},
}


def load_input_templates(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Merge on-disk actor input overrides over the built-in defaults."""
    templates = {k: dict(v) for k, v in DEFAULT_INPUT_TEMPLATES.items()}
    source = path or ACTOR_INPUTS_PATH
    if source.is_file():
        try:
            overrides = json.loads(source.read_text(encoding="utf-8"))
            for key, value in (overrides or {}).items():
                if isinstance(value, dict):
                    templates[key] = value
        except (json.JSONDecodeError, OSError) as exc:
            log.error("could not read actor inputs", extra={"path": str(source), "error": str(exc)})
    return templates


class ActorRegistry:
    """Resolves ``(platform, operation)`` to a configured :class:`ActorSpec`."""

    def __init__(self, agent_settings, templates: dict[str, dict[str, Any]] | None = None):
        self.settings = agent_settings
        self.templates = templates or load_input_templates()

    def spec(self, platform: str, operation: str) -> ActorSpec:
        """``operation`` is one of ``posts``, ``comments`` or ``reply``."""
        key = f"{platform}_{operation}"
        return ActorSpec(
            actor_id=self.settings.actor_for(platform, operation),
            input_template=self.templates.get(key, {}),
        )
