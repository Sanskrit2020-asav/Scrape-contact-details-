"""Free local semantic search over itineraries (sentence-transformers).

Embeds each itinerary once and caches the vectors on disk keyed by the
index fingerprint, so re-runs are fast. If sentence-transformers / torch
is not installed, every function degrades to "unavailable" and the app
falls back to keyword matching — nothing crashes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .index import Itinerary

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_VEC_FILE = Path(__file__).resolve().parent.parent / "output" / "embeddings.json"
_model = None
_model_failed = False


def available() -> bool:
    """True if the embedding model can be loaded."""
    global _model, _model_failed
    if _model is not None:
        return True
    if _model_failed:
        return False
    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_MODEL_NAME)
        return True
    except Exception:
        _model_failed = True
        return False


def _embed(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _model.encode(texts, normalize_embeddings=True)]  # type: ignore[union-attr]


def _doc_text(it: Itinerary) -> str:
    return (it.title + "\n" + it.text)[:4000]


def _load_cache() -> dict:
    if _VEC_FILE.exists():
        try:
            return json.loads(_VEC_FILE.read_text())
        except Exception:
            return {}
    return {}


def ensure_embeddings(itineraries: list[Itinerary]) -> dict[str, list[float]]:
    """Return {filename: vector}, embedding only new/changed itineraries."""
    if not available():
        return {}
    cache = _load_cache()
    vectors: dict[str, list[float]] = {}
    to_embed: list[tuple[str, str]] = []
    for it in itineraries:
        entry = cache.get(it.filename)
        if entry and entry.get("fp") == it.fingerprint:
            vectors[it.filename] = entry["vec"]
        else:
            to_embed.append((it.filename, _doc_text(it)))

    if to_embed:
        embedded = _embed([t for _, t in to_embed])
        for (name, _), vec in zip(to_embed, embedded):
            vectors[name] = vec

    fp_by_name = {it.filename: it.fingerprint for it in itineraries}
    _VEC_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VEC_FILE.write_text(
        json.dumps(
            {n: {"fp": fp_by_name[n], "vec": v} for n, v in vectors.items() if n in fp_by_name}
        )
    )
    return vectors


def semantic_scores(itineraries: list[Itinerary], query: str) -> dict[str, float]:
    """Cosine similarity (0..1) of the query against each itinerary, or {} if unavailable."""
    if not available() or not query.strip():
        return {}
    vectors = ensure_embeddings(itineraries)
    if not vectors:
        return {}
    q = _embed([query])[0]
    out: dict[str, float] = {}
    for name, vec in vectors.items():
        dot = sum(a * b for a, b in zip(q, vec))  # cosine; vectors are normalized
        out[name] = max(0.0, dot)
    return out
