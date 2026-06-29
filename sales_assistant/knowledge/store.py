"""A small, dependency-free RAG store.

Documents are split into overlapping text chunks and indexed with TF-IDF;
queries are scored by cosine similarity. This runs anywhere with zero ML
dependencies. The interface (``add_document`` / ``search``) is deliberately the
same shape you would expose over a vector database, so swapping in embeddings
later is a drop-in change behind :meth:`search`.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import SourceRef, SourceType
from .ingest import IngestReport, RawDocument, load_directory

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "with", "at", "by", "be", "as", "it", "this", "that", "from", "we", "you",
    "your", "our", "will", "can", "has", "have", "if", "but", "not", "they",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _chunk(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    """Split text into ~``size``-char chunks on paragraph/sentence boundaries."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(buf) + len(para) + 2 <= size:
            buf = f"{buf}\n\n{para}".strip()
        else:
            if buf:
                chunks.append(buf)
            if len(para) <= size:
                buf = para
            else:
                # Hard-split an oversized paragraph with overlap.
                start = 0
                while start < len(para):
                    chunks.append(para[start:start + size])
                    start += size - overlap
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


@dataclass
class _Chunk:
    doc_title: str
    doc_path: str
    text: str
    tf: Counter = field(default_factory=Counter)


@dataclass
class SearchHit:
    score: float
    text: str
    source: SourceRef

    def to_dict(self) -> dict:
        return {"score": round(self.score, 4), "text": self.text, "source": self.source.to_dict()}


class KnowledgeStore:
    """In-memory TF-IDF document store with cosine search."""

    def __init__(self) -> None:
        self._chunks: list[_Chunk] = []
        self._df: Counter = Counter()        # document frequency per term
        self._idf: dict[str, float] = {}
        self._dirty = True

    # ----- ingestion -----

    def add_document(self, doc: RawDocument) -> int:
        added = 0
        for chunk_text in _chunk(doc.text):
            tokens = _tokenize(chunk_text)
            if not tokens:
                continue
            tf = Counter(tokens)
            self._chunks.append(_Chunk(doc.title, doc.path, chunk_text, tf))
            for term in tf:
                self._df[term] += 1
            added += 1
        self._dirty = True
        return added

    def add_text(self, title: str, text: str, path: str = "") -> int:
        return self.add_document(RawDocument(title=title, path=path or title, text=text, suffix=".txt"))

    def load_directory(self, directory: str) -> IngestReport:
        report = load_directory(directory)
        for doc in report.loaded:
            self.add_document(doc)
        return report

    # ----- indexing -----

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def _rebuild_idf(self) -> None:
        n = max(len(self._chunks), 1)
        self._idf = {term: math.log((n + 1) / (df + 1)) + 1.0 for term, df in self._df.items()}
        self._dirty = False

    def _vector(self, tf: Counter) -> dict[str, float]:
        return {term: count * self._idf.get(term, 0.0) for term, count in tf.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ----- retrieval -----

    def search(self, query: str, k: int = 4, min_score: float = 0.0) -> list[SearchHit]:
        if not self._chunks or not query.strip():
            return []
        if self._dirty:
            self._rebuild_idf()
        q_vec = self._vector(Counter(_tokenize(query)))
        scored: list[SearchHit] = []
        for chunk in self._chunks:
            score = self._cosine(q_vec, self._vector(chunk.tf))
            if score <= min_score:
                continue
            snippet = chunk.text[:300] + ("…" if len(chunk.text) > 300 else "")
            scored.append(SearchHit(
                score=score,
                text=chunk.text,
                source=SourceRef(SourceType.DOCUMENT, chunk.doc_title, chunk.doc_path, snippet, score),
            ))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]
