"""Build and cache a searchable index of the prepared itineraries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from .parsing import SUPPORTED_SUFFIXES, extract_text

REPO_ROOT = Path(__file__).resolve().parent.parent
ITINERARIES_DIR = REPO_ROOT / "itineraries"
CACHE_FILE = REPO_ROOT / "output" / "itinerary_index.json"

_DURATION_RE = re.compile(r"(\d{1,2})\s*[-\s]?\s*(?:nights?|days?)", re.IGNORECASE)


@dataclass
class Itinerary:
    filename: str
    title: str
    duration_days: int | None
    text: str
    fingerprint: str  # mtime+size, so we can detect changes

    def snippet(self, length: int = 320) -> str:
        clean = re.sub(r"\s+", " ", self.text).strip()
        return clean[:length] + ("…" if len(clean) > length else "")


def _title_from(path: Path, text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 4:
            return line[:120]
    return path.stem.replace("_", " ").replace("-", " ").strip()


def _duration_from(text: str) -> int | None:
    matches = [int(m) for m in _DURATION_RE.findall(text)]
    plausible = [d for d in matches if 1 <= d <= 60]
    return max(plausible) if plausible else None


def _fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{int(st.st_mtime)}-{st.st_size}"


def build_index(force: bool = False) -> list[Itinerary]:
    """Scan the itineraries folder, reusing cached parse results when unchanged."""
    cache: dict[str, dict] = {}
    if CACHE_FILE.exists() and not force:
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache = {}

    results: list[Itinerary] = []
    if ITINERARIES_DIR.exists():
        for path in sorted(ITINERARIES_DIR.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            fp = _fingerprint(path)
            cached = cache.get(path.name)
            if cached and cached.get("fingerprint") == fp and not force:
                results.append(Itinerary(**cached))
                continue
            text = extract_text(path)
            results.append(
                Itinerary(
                    filename=path.name,
                    title=_title_from(path, text),
                    duration_days=_duration_from(text),
                    text=text,
                    fingerprint=fp,
                )
            )

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({it.filename: asdict(it) for it in results}, indent=2))
    return results


_MEM_CACHE: dict[str, object] = {"sig": None, "items": None}


def _dir_signature() -> tuple:
    if not ITINERARIES_DIR.exists():
        return ()
    return tuple(
        sorted(
            (p.name, _fingerprint(p))
            for p in ITINERARIES_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        )
    )


def get_index(force: bool = False) -> list[Itinerary]:
    """Fast accessor: rebuild only when files change (or force). Used per request."""
    sig = _dir_signature()
    if not force and _MEM_CACHE["sig"] == sig and _MEM_CACHE["items"] is not None:
        return _MEM_CACHE["items"]  # type: ignore[return-value]
    items = build_index(force=force)
    _MEM_CACHE["sig"] = sig
    _MEM_CACHE["items"] = items
    return items
