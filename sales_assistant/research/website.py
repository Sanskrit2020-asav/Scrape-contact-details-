"""Use the company website as a knowledge source.

Fetches a set of pages (trek pages, peak climbing, helicopter tours, blogs,
FAQs, guides), strips them to text, and indexes them into a
:class:`KnowledgeStore` tagged with ``WEBSITE`` provenance so answers can cite
the exact page. Network access is via the standard library; failures are
contained per-URL.
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field

from ..knowledge.ingest import RawDocument
from ..knowledge.store import KnowledgeStore
from ..models import SourceRef, SourceType

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)


def html_to_text(html: str) -> str:
    html = _TAG_RE.sub(" ", html)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", html, flags=re.IGNORECASE)
    text = _ANY_TAG_RE.sub(" ", html)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"'))
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _page_title(html: str, fallback: str) -> str:
    m = _TITLE_RE.search(html)
    if m:
        return html_to_text(m.group(1)) or fallback
    return fallback


@dataclass
class FetchReport:
    fetched: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return f"{len(self.fetched)} page(s) indexed, {len(self.failed)} failed."


class WebsiteIndex:
    """Fetches and indexes website pages into a dedicated KnowledgeStore."""

    def __init__(self, timeout: int = 20, user_agent: str = "NorthNepalSalesAssistant/0.1"):
        self.store = KnowledgeStore()
        self.timeout = timeout
        self.user_agent = user_agent
        self._indexed_urls: set[str] = set()

    def fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - controlled URLs
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")

    def index_url(self, url: str) -> bool:
        if url in self._indexed_urls:
            return True
        html = self.fetch(url)
        title = _page_title(html, url)
        text = html_to_text(html)
        if not text:
            return False
        # Override the document path with the URL so SourceRefs link back to the page.
        doc = RawDocument(title=title, path=url, text=text, suffix=".html")
        self.store.add_document(doc)
        self._indexed_urls.add(url)
        return True

    def index_urls(self, urls: list[str]) -> FetchReport:
        report = FetchReport()
        for url in urls:
            try:
                if self.index_url(url):
                    report.fetched.append(url)
                else:
                    report.failed.append((url, "no extractable text"))
            except Exception as exc:  # noqa: BLE001 - contain per-URL failures
                report.failed.append((url, f"{type(exc).__name__}: {exc}"))
        return report

    def search(self, query: str, k: int = 4, min_score: float = 0.0):
        hits = self.store.search(query, k=k, min_score=min_score)
        # Re-tag provenance as WEBSITE.
        for hit in hits:
            hit.source = SourceRef(
                SourceType.WEBSITE, hit.source.title, hit.source.locator, hit.source.snippet, hit.score
            )
        return hits
