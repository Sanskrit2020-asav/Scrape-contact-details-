"""Reply variation (spec §14).

Three cheap signals catch the way an LLM repeats itself, without needing
embeddings:

* **Token overlap** — the same words in a different order.
* **Shared opening** — "Thank you so much!" after "Thank you! 😊". Different
  enough by token overlap, identical in feel.
* **Skeleton match** — the same sentence with one noun swapped.

Recent replies are also fed back into the prompt so the model avoids repeating
itself in the first place; this module is the check on whether it did.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9']+")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤️]+"
)

#: Jaccard similarity at or above this counts as repetition.
SIMILARITY_THRESHOLD = 0.6
#: How many leading words count as "the same opening". Two, because short
#: social replies share a stock opener long before their token sets converge:
#: "Thank you so much!" and "Thank you! 😊" are the same reply in a reader's
#: eyes while scoring only 0.5 on token overlap.
OPENING_WORDS = 2

_FILLER = {"the", "a", "an", "is", "it", "to", "of", "and", "so", "we", "you", "your"}


def stem(word: str) -> str:
    """Crude plural/verb-form stripping so "thanks" matches "thank".

    Deliberately not a real stemmer: this only needs to collapse the handful of
    variants that make two stock replies look different to a token comparison.
    """
    if len(word) > 3:
        for suffix in ("ing", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[: -len(suffix)]
    return word


def normalize(text: str) -> str:
    text = _EMOJI_RE.sub("", text or "").lower()
    return " ".join(stem(w) for w in _WORD_RE.findall(text))


def tokens(text: str) -> set[str]:
    return set(normalize(text).split())


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of the two token sets (0.0–1.0)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def opening(text: str, words: int = OPENING_WORDS) -> str:
    return " ".join(normalize(text).split()[:words])


def skeleton(text: str) -> str:
    """The sentence with filler removed — catches one-noun-swapped repeats."""
    return " ".join(w for w in normalize(text).split() if w not in _FILLER)


def is_repetitive(
    candidate: str,
    previous: list[str],
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[bool, str]:
    """Is ``candidate`` too close to anything in ``previous``?

    Returns ``(is_repetitive, the_reply_it_resembles)``.
    """
    if not candidate or not previous:
        return False, ""

    candidate_opening = opening(candidate)
    candidate_skeleton = skeleton(candidate)

    for earlier in previous:
        if not earlier:
            continue
        if normalize(candidate) == normalize(earlier):
            return True, earlier
        if similarity(candidate, earlier) >= threshold:
            return True, earlier
        if candidate_opening and candidate_opening == opening(earlier):
            return True, earlier
        if candidate_skeleton and candidate_skeleton == skeleton(earlier):
            return True, earlier
    return False, ""


def variation_report(candidate: str, previous: list[str]) -> dict:
    """Diagnostics for the dashboard: how close to each recent reply."""
    scores = [
        {"reply": earlier, "similarity": round(similarity(candidate, earlier), 3)}
        for earlier in previous if earlier
    ]
    scores.sort(key=lambda s: s["similarity"], reverse=True)
    repetitive, similar_to = is_repetitive(candidate, previous)
    return {"repetitive": repetitive, "closest": scores[:3], "similar_to": similar_to}
