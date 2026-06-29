"""Controlled web research — the lowest-priority knowledge source.

This is a pluggable backend. By default it is disabled and returns nothing, so
the assistant never lets random internet content override company knowledge. To
enable real web research, either:

  * register a callable backend with :meth:`WebResearcher.set_backend`, or
  * let the LLM client perform it via Claude's server-side ``web_search`` tool
    (see ``llm.client``), restricted to trusted domains.

Results are always tagged ``WEB`` and ranked below documents and website hits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..knowledge.store import SearchHit
from ..models import SourceRef, SourceType

# A backend takes (query, k, trusted_domains) and returns a list of (title, url, snippet).
WebBackend = Callable[[str, int, tuple[str, ...]], list[tuple[str, str, str]]]


@dataclass
class WebResearcher:
    enabled: bool = False
    trusted_domains: tuple[str, ...] = ()
    _backend: WebBackend | None = None

    def set_backend(self, backend: WebBackend) -> None:
        self._backend = backend
        self.enabled = True

    def search(self, query: str, k: int = 3) -> list[SearchHit]:
        if not self.enabled or self._backend is None:
            return []
        try:
            raw = self._backend(query, k, self.trusted_domains)
        except Exception:  # noqa: BLE001 - web is best-effort, never fatal
            return []
        hits: list[SearchHit] = []
        for i, (title, url, snippet) in enumerate(raw[:k]):
            # Filter to trusted domains when configured.
            if self.trusted_domains and not any(d in url for d in self.trusted_domains):
                continue
            score = max(0.3 - i * 0.05, 0.05)  # web ranked below internal sources
            hits.append(SearchHit(
                score=score,
                text=snippet,
                source=SourceRef(SourceType.WEB, title, url, snippet[:300], score),
            ))
        return hits
