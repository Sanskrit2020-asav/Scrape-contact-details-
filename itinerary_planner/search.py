"""Rank prepared itineraries against a client's stated requirements.

Pure-Python TF-IDF cosine similarity over the document text, plus a
duration-proximity boost so a "7 day" request favours ~7-day itineraries.
No external ML dependency, so it installs and runs anywhere.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .index import Itinerary

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "this", "that", "it", "as", "at", "by", "from",
    "day", "days", "night", "nights", "trip", "tour", "itinerary", "travel",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


@dataclass
class ClientBrief:
    destination: str = ""
    duration_days: int | None = None
    travelers: str = ""
    budget: str = ""
    interests: str = ""
    season: str = ""
    notes: str = ""

    def query_text(self) -> str:
        return " ".join(
            v for v in (
                self.destination, self.travelers, self.budget,
                self.interests, self.season, self.notes,
            ) if v
        )


@dataclass
class Match:
    itinerary: Itinerary
    score: float
    matched_terms: list[str]
    duration_note: str


def rank(
    itineraries: list[Itinerary],
    brief: ClientBrief,
    top_n: int = 5,
    semantic: dict[str, float] | None = None,
) -> list[Match]:
    semantic = semantic or {}
    docs_tokens = [_tokenize(it.text + " " + it.title) for it in itineraries]
    query_tokens = _tokenize(brief.query_text())
    if not itineraries or (not query_tokens and not semantic):
        return []

    n_docs = len(itineraries)
    df: Counter[str] = Counter()
    for tokens in docs_tokens:
        for term in set(tokens):
            df[term] += 1
    idf = {term: math.log((1 + n_docs) / (1 + d)) + 1 for term, d in df.items()}

    def tfidf_vec(tokens: list[str]) -> dict[str, float]:
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = len(tokens)
        return {t: (c / total) * idf.get(t, math.log(1 + n_docs) + 1) for t, c in counts.items()}

    q_vec = tfidf_vec(query_tokens)
    q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
    q_term_set = set(query_tokens)

    raw: list[tuple] = []
    for it, tokens in zip(itineraries, docs_tokens):
        d_vec = tfidf_vec(tokens)
        dot = sum(w * d_vec.get(t, 0.0) for t, w in q_vec.items())
        d_norm = math.sqrt(sum(v * v for v in d_vec.values())) or 1.0
        cosine = dot / (q_norm * d_norm)
        raw.append((it, tokens, cosine))

    kw_max = max((c for _, _, c in raw), default=0.0) or 1.0
    sem_max = max(semantic.values(), default=0.0) or 1.0
    blend_kw = 0.45 if semantic else 1.0

    matches: list[Match] = []
    for it, tokens, cosine in raw:
        kw_norm = cosine / kw_max
        sem_norm = semantic.get(it.filename, 0.0) / sem_max
        relevance = blend_kw * kw_norm + (1.0 - blend_kw) * sem_norm

        duration_note = ""
        duration_factor = 1.0
        if brief.duration_days and it.duration_days:
            diff = abs(brief.duration_days - it.duration_days)
            if diff == 0:
                duration_factor, duration_note = 1.30, f"exact {it.duration_days}-day length match"
            elif diff <= 2:
                duration_factor, duration_note = 1.15, f"close length ({it.duration_days} days)"
            elif diff >= 5:
                duration_factor, duration_note = 0.85, f"length differs ({it.duration_days} days)"
            else:
                duration_note = f"{it.duration_days} days"
        elif it.duration_days:
            duration_note = f"{it.duration_days} days"

        score = relevance * duration_factor
        matched = sorted(t for t in q_term_set if t in set(tokens))
        matches.append(Match(it, round(score, 4), matched, duration_note))

    matches.sort(key=lambda m: m.score, reverse=True)
    return [m for m in matches if m.score > 0][:top_n]
