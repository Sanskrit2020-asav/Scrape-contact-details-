"""Curated Nepal trekking domain knowledge.

This is the consultant's "15 years of experience" encoded as structured facts:
trek profiles, seasons, altitude, and permit rules. It is deliberately a small,
auditable seed — the RAG knowledge base and website research extend it, and the
real figures should always defer to uploaded company documents and the Excel
pricing sheet.

Every fact here is conservative and widely published; treat it as a sensible
default, not a substitute for company SOPs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Season


@dataclass(frozen=True)
class TrekProfile:
    name: str
    region: str
    aliases: tuple[str, ...] = ()
    typical_days: int = 0
    max_altitude_m: int = 0
    best_seasons: tuple[Season, ...] = ()
    permits: tuple[str, ...] = ()
    requires_domestic_flight: bool = False
    teahouse: bool = True
    notes: str = ""


# Months → season (Northern-hemisphere Nepal calendar)
_MONTH_SEASON = {
    1: Season.WINTER, 2: Season.WINTER, 3: Season.SPRING, 4: Season.SPRING,
    5: Season.SPRING, 6: Season.SUMMER, 7: Season.SUMMER, 8: Season.SUMMER,
    9: Season.AUTUMN, 10: Season.AUTUMN, 11: Season.AUTUMN, 12: Season.WINTER,
}


def season_for_month(month: int) -> Season | None:
    return _MONTH_SEASON.get(month)


# High-altitude threshold where AMS (acute mountain sickness) planning matters.
ALTITUDE_RISK_M = 3500
ALTITUDE_SERIOUS_M = 5000


TREKS: tuple[TrekProfile, ...] = (
    TrekProfile(
        name="Everest Base Camp",
        region="Everest (Khumbu)",
        aliases=("ebc", "everest base camp trek", "everest"),
        typical_days=14,
        max_altitude_m=5364,
        best_seasons=(Season.SPRING, Season.AUTUMN),
        permits=("Sagarmatha National Park Permit", "Khumbu Pasang Lhamu Rural Municipality Permit"),
        requires_domestic_flight=True,  # Lukla
        notes="Lukla flights are weather-sensitive; build buffer days. Acclimatisation at Namche & Dingboche.",
    ),
    TrekProfile(
        name="Annapurna Base Camp",
        region="Annapurna",
        aliases=("abc", "annapurna base camp trek", "annapurna sanctuary"),
        typical_days=10,
        max_altitude_m=4130,
        best_seasons=(Season.SPRING, Season.AUTUMN),
        permits=("ACAP (Annapurna Conservation Area Permit)", "TIMS Card"),
        notes="Avalanche-prone sections in heavy winter snow; check conditions Dec–Feb.",
    ),
    TrekProfile(
        name="Annapurna Circuit",
        region="Annapurna",
        aliases=("annapurna circuit trek", "thorong la"),
        typical_days=14,
        max_altitude_m=5416,  # Thorong La pass
        best_seasons=(Season.SPRING, Season.AUTUMN),
        permits=("ACAP (Annapurna Conservation Area Permit)", "TIMS Card"),
        notes="Thorong La pass (5416m) is the crux; do not cross in poor weather. Strong acclimatisation needed.",
    ),
    TrekProfile(
        name="Langtang Valley",
        region="Langtang",
        aliases=("langtang", "langtang trek", "langtang valley trek"),
        typical_days=8,
        max_altitude_m=3870,  # Kyanjin Gompa (Tserko Ri optional 4984m)
        best_seasons=(Season.SPRING, Season.AUTUMN),
        permits=("Langtang National Park Permit", "TIMS Card"),
        notes="Closest major trek to Kathmandu; no flight needed. Community rebuilt post-2015 earthquake.",
    ),
    TrekProfile(
        name="Manaslu Circuit",
        region="Manaslu",
        aliases=("manaslu", "manaslu circuit trek", "larke la"),
        typical_days=14,
        max_altitude_m=5106,  # Larke La
        best_seasons=(Season.SPRING, Season.AUTUMN),
        permits=(
            "Manaslu Restricted Area Permit",
            "MCAP (Manaslu Conservation Area Permit)",
            "ACAP (Annapurna Conservation Area Permit)",
        ),
        notes="Restricted area: minimum 2 trekkers + licensed guide mandatory. Permit cost varies by season.",
    ),
    TrekProfile(
        name="Upper Mustang",
        region="Mustang",
        aliases=("mustang", "upper mustang trek", "lo manthang"),
        typical_days=12,
        max_altitude_m=3840,
        best_seasons=(Season.SPRING, Season.SUMMER, Season.AUTUMN),
        permits=("Upper Mustang Restricted Area Permit (USD/10 days)", "ACAP (Annapurna Conservation Area Permit)"),
        requires_domestic_flight=True,  # via Jomsom typically
        notes="Rain-shadow region — trekkable in monsoon. Restricted area: licensed guide + min 2 trekkers.",
    ),
    TrekProfile(
        name="Ghorepani Poon Hill",
        region="Annapurna",
        aliases=("poon hill", "ghorepani", "poon hill trek"),
        typical_days=5,
        max_altitude_m=3210,
        best_seasons=(Season.SPRING, Season.AUTUMN, Season.WINTER),
        permits=("ACAP (Annapurna Conservation Area Permit)", "TIMS Card"),
        notes="Short, lower-altitude, family-friendly; good winter option.",
    ),
)

PEAK_CLIMBS: tuple[TrekProfile, ...] = (
    TrekProfile(
        name="Island Peak (Imja Tse)",
        region="Everest (Khumbu)",
        aliases=("island peak", "imja tse"),
        typical_days=18,
        max_altitude_m=6189,
        best_seasons=(Season.SPRING, Season.AUTUMN),
        permits=("NMA Climbing Permit (Island Peak)", "Sagarmatha National Park Permit"),
        requires_domestic_flight=True,
        teahouse=False,
        notes="Requires mountaineering experience, climbing guide, and technical gear. Combine with EBC acclimatisation.",
    ),
    TrekProfile(
        name="Mera Peak",
        region="Everest (Khumbu)",
        aliases=("mera peak",),
        typical_days=18,
        max_altitude_m=6476,
        best_seasons=(Season.SPRING, Season.AUTUMN),
        permits=("NMA Climbing Permit (Mera Peak)", "Makalu Barun National Park Permit"),
        requires_domestic_flight=True,
        teahouse=False,
        notes="Highest trekking peak; non-technical but serious altitude. Strong acclimatisation essential.",
    ),
)

_ALL = TREKS + PEAK_CLIMBS


def find_trek(query: str) -> TrekProfile | None:
    """Best-effort fuzzy lookup of a trek/peak by name or alias."""
    if not query:
        return None
    q = query.strip().lower()
    # exact name
    for t in _ALL:
        if t.name.lower() == q:
            return t
    # alias exact
    for t in _ALL:
        if q in {a.lower() for a in t.aliases}:
            return t
    # substring either direction
    for t in _ALL:
        hay = [t.name.lower(), *[a.lower() for a in t.aliases]]
        for h in hay:
            if q in h or h in q:
                return t
    # token overlap fallback
    q_tokens = set(q.split())
    best, best_score = None, 0
    for t in _ALL:
        t_tokens = set(t.name.lower().split())
        score = len(q_tokens & t_tokens)
        if score > best_score:
            best, best_score = t, score
    return best if best_score else None


def all_treks() -> tuple[TrekProfile, ...]:
    return _ALL
