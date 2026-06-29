"""Priority research orchestrator.

Enforces the brief's strict source priority:

    1. Uploaded documents (RAG knowledge base)
    2. Company website
    3. Trusted web sources

Merges findings, removes duplicates, and returns a single ranked answer with
full provenance. Company knowledge always outranks the open web.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..knowledge.store import KnowledgeStore, SearchHit
from ..models import SourceRef
from .web import WebResearcher
from .website import WebsiteIndex

# Priority weights applied on top of raw similarity scores.
_TIER_WEIGHT = {"document": 1.0, "website": 0.8, "web": 0.5}


@dataclass
class ResearchResult:
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    confidence: float = 0.0
    tiers_used: list[str] = field(default_factory=list)

    @property
    def sources(self) -> list[SourceRef]:
        return [h.source for h in self.hits]

    def context_block(self, max_chars: int = 2400) -> str:
        """Concatenate the top hits into a citation-tagged context block for the LLM."""
        parts: list[str] = []
        total = 0
        for i, hit in enumerate(self.hits, 1):
            s = hit.source
            entry = f"[{i}] ({s.type.value}) {s.title} — {s.locator}\n{hit.text.strip()}"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "confidence": round(self.confidence, 4),
            "tiers_used": self.tiers_used,
            "hits": [h.to_dict() for h in self.hits],
        }


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    seen: set[str] = set()
    out: list[SearchHit] = []
    for hit in hits:
        key = " ".join(hit.text.lower().split())[:160]
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


class ResearchOrchestrator:
    def __init__(
        self,
        knowledge: KnowledgeStore | None = None,
        website: WebsiteIndex | None = None,
        web: WebResearcher | None = None,
        min_score: float = 0.05,
    ):
        self.knowledge = knowledge or KnowledgeStore()
        self.website = website
        self.web = web
        self.min_score = min_score

    def research(self, query: str, k: int = 4, web_fallback_threshold: float = 0.15) -> ResearchResult:
        """Gather evidence in priority order; only escalate when confidence is low."""
        result = ResearchResult(query=query)
        collected: list[SearchHit] = []

        # Tier 1 — documents.
        doc_hits = self.knowledge.search(query, k=k, min_score=self.min_score)
        for h in doc_hits:
            h.score *= _TIER_WEIGHT["document"]
        collected.extend(doc_hits)
        if doc_hits:
            result.tiers_used.append("document")

        best_so_far = max((h.score for h in collected), default=0.0)

        # Tier 2 — website (always consulted; complements documents).
        if self.website is not None:
            site_hits = self.website.search(query, k=k, min_score=self.min_score)
            for h in site_hits:
                h.score *= _TIER_WEIGHT["website"]
            collected.extend(site_hits)
            if site_hits:
                result.tiers_used.append("website")
            best_so_far = max(best_so_far, *(h.score for h in site_hits)) if site_hits else best_so_far

        # Tier 3 — web, only when company knowledge is weak.
        if self.web is not None and self.web.enabled and best_so_far < web_fallback_threshold:
            web_hits = self.web.search(query, k=k)
            for h in web_hits:
                h.score *= _TIER_WEIGHT["web"]
            collected.extend(web_hits)
            if web_hits:
                result.tiers_used.append("web")

        merged = _dedupe(collected)
        merged.sort(key=lambda h: h.score, reverse=True)
        result.hits = merged[:k]
        result.confidence = result.hits[0].score if result.hits else 0.0
        return result
