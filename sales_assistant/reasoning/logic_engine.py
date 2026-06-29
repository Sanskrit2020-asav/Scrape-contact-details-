"""The self-logic engine.

Before any response leaves the system, it reasons like an experienced Nepal
trekking consultant and produces structured :class:`Finding` objects:

  * Is information missing?
  * Is the itinerary realistic for the trek?
  * Are permits required?
  * Is the season suitable?
  * Are flights involved (and weather-risky)?
  * Is altitude risky (AMS / acclimatisation)?
  * Does the pricing look correct?
  * Would another route be better?

These deterministic checks complement the LLM; they always run, even offline.
"""
from __future__ import annotations

from datetime import date

from ..models import (
    CostBreakdown,
    Finding,
    Season,
    SourceRef,
    SourceType,
    TripSpec,
)
from .nepal_facts import (
    ALTITUDE_RISK_M,
    ALTITUDE_SERIOUS_M,
    TrekProfile,
    find_trek,
    season_for_month,
)

_REASON_SRC = SourceRef(SourceType.REASONING, "Consultant logic engine")

# Required-info fields and the questions to ask when they're absent.
_REQUIRED_QUESTIONS = {
    "destination_or_trek": "Which trek or destination are you interested in?",
    "duration_days": "How many days do you have for the trip?",
    "group_size": "How many people are travelling?",
    "start_date": "Roughly when would you like to travel (month is enough)?",
    "nationality": "What nationality are travellers? (affects visa and some permit fees)",
}


def _parse_month(start_date: str) -> int | None:
    if not start_date:
        return None
    for fmt_parts in (start_date.split("-"), start_date.split("/")):
        if len(fmt_parts) >= 2:
            try:
                # Accept yyyy-mm-dd or dd/mm/yyyy etc.
                for part in fmt_parts:
                    val = int(part)
                    if 1 <= val <= 12 and len(part) <= 2:
                        return val
            except ValueError:
                continue
    # Month name?
    months = ["january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december"]
    low = start_date.lower()
    for i, name in enumerate(months, 1):
        if name[:3] in low:
            return i
    return None


class LogicEngine:
    def analyse(
        self,
        trip: TripSpec,
        cost: CostBreakdown | None = None,
        profile: TrekProfile | None = None,
    ) -> tuple[list[Finding], list[str], list[str]]:
        """Return (findings, missing_fields, follow_up_questions)."""
        findings: list[Finding] = []
        missing: list[str] = []
        questions: list[str] = []

        profile = profile or find_trek(trip.trek) or find_trek(trip.destination)

        # --- Missing information ---
        if not (trip.trek or trip.destination):
            missing.append("destination_or_trek")
            questions.append(_REQUIRED_QUESTIONS["destination_or_trek"])
        if not trip.duration_days:
            missing.append("duration_days")
            questions.append(_REQUIRED_QUESTIONS["duration_days"])
        if not trip.group_size:
            missing.append("group_size")
            questions.append(_REQUIRED_QUESTIONS["group_size"])
        if not trip.start_date:
            missing.append("start_date")
            questions.append(_REQUIRED_QUESTIONS["start_date"])
        if not trip.nationality:
            missing.append("nationality")
            questions.append(_REQUIRED_QUESTIONS["nationality"])
        if missing:
            findings.append(Finding(
                "info", "info",
                f"{len(missing)} key detail(s) missing — asking follow-up questions before quoting.",
                _REASON_SRC,
            ))

        if profile is None:
            findings.append(Finding(
                "suggestion", "info",
                "Trek not recognised in the built-in profiles. Verify itinerary, permits and "
                "altitude against company documents before sending.",
                _REASON_SRC,
            ))
            return findings, missing, questions

        # --- Itinerary realism ---
        if trip.duration_days and profile.typical_days:
            if trip.duration_days < profile.typical_days - 2:
                findings.append(Finding(
                    "warning", "logistics",
                    f"{trip.duration_days} days is short for {profile.name} "
                    f"(typically ~{profile.typical_days}). Risk of rushed acclimatisation; "
                    "consider extending or a shorter alternative.",
                    _REASON_SRC,
                ))
            elif trip.duration_days > profile.typical_days + 4:
                findings.append(Finding(
                    "suggestion", "logistics",
                    f"{trip.duration_days} days is generous for {profile.name}; you could add "
                    "side trips, extra acclimatisation, or a cultural extension.",
                    _REASON_SRC,
                ))

        # --- Permits ---
        if profile.permits:
            findings.append(Finding(
                "info", "permits",
                f"Permits required for {profile.name}: {', '.join(profile.permits)}.",
                _REASON_SRC,
            ))
        if "Restricted Area Permit" in " ".join(profile.permits):
            findings.append(Finding(
                "warning", "permits",
                "Restricted-area trek: a licensed guide is mandatory and a minimum of 2 trekkers "
                "usually applies. Solo permits are not issued.",
                _REASON_SRC,
            ))
            if trip.group_size == 1:
                findings.append(Finding(
                    "blocker", "permits",
                    "Group size of 1 cannot be permitted for this restricted-area trek — "
                    "pair the traveller or adjust the route.",
                    _REASON_SRC,
                ))

        # --- Season suitability ---
        month = _parse_month(trip.start_date)
        if month:
            season = season_for_month(month)
            if season and profile.best_seasons and season not in profile.best_seasons:
                best = ", ".join(s.value for s in profile.best_seasons)
                sev = "warning"
                extra = ""
                if season == Season.SUMMER:
                    extra = " Monsoon brings rain, leeches, and flight delays."
                elif season == Season.WINTER:
                    extra = " Winter brings snow, cold, and possible high-pass closures."
                findings.append(Finding(
                    sev, "season",
                    f"{season.value.title()} is outside the ideal window for {profile.name} "
                    f"(best: {best}).{extra}",
                    _REASON_SRC,
                ))
            elif season:
                findings.append(Finding(
                    "info", "season",
                    f"{season.value.title()} is a good time for {profile.name}.",
                    _REASON_SRC,
                ))

        # --- Flights ---
        if profile.requires_domestic_flight:
            findings.append(Finding(
                "warning", "flights",
                "This trek depends on a weather-sensitive domestic flight (e.g. Lukla). "
                "Build in 1–2 buffer days and consider a helicopter contingency.",
                _REASON_SRC,
            ))

        # --- Altitude ---
        if profile.max_altitude_m >= ALTITUDE_SERIOUS_M:
            findings.append(Finding(
                "warning", "altitude",
                f"Max altitude {profile.max_altitude_m} m — serious AMS risk. Ensure graded "
                "acclimatisation, rest days, and that travel insurance covers helicopter evacuation.",
                _REASON_SRC,
            ))
        elif profile.max_altitude_m >= ALTITUDE_RISK_M:
            findings.append(Finding(
                "info", "altitude",
                f"Max altitude {profile.max_altitude_m} m — moderate AMS risk; plan acclimatisation days.",
                _REASON_SRC,
            ))

        # --- Pricing sanity ---
        if cost is not None and trip.group_size:
            pp = cost.per_person(trip.group_size)
            if pp is not None and trip.duration_days:
                per_day = pp / max(trip.duration_days, 1)
                if per_day < 60:
                    findings.append(Finding(
                        "warning", "pricing",
                        f"Per-person cost (~${per_day:.0f}/day) looks low for a guided Nepal trek — "
                        "check that permits, flights and staff insurance are all included.",
                        _REASON_SRC,
                    ))
                elif per_day > 500 and (profile.teahouse):
                    findings.append(Finding(
                        "suggestion", "pricing",
                        f"Per-person cost (~${per_day:.0f}/day) is high for a teahouse trek — "
                        "confirm the luxury/upgrade choices are intentional.",
                        _REASON_SRC,
                    ))

        return findings, missing, questions

    @staticmethod
    def has_blocker(findings: list[Finding]) -> bool:
        return any(f.severity == "blocker" for f in findings)
