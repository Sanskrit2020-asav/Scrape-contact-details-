"""Build a professional quotation from an :class:`Estimate`.

Produces the structured sections the brief asks for: itinerary summary, what's
included / excluded, preparation notes, payment terms, cancellation policy, and
optional upgrades. Deterministic and offline-safe; the email writer turns this
into prose.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..models import Estimate
from ..reasoning.nepal_facts import find_trek

DEFAULT_INCLUDED = [
    "Airport pick-up and drop-off",
    "Licensed, experienced trekking guide (government-registered)",
    "Porter support as specified",
    "All trekking permits and conservation/TIMS cards listed below",
    "Teahouse/lodge accommodation on trek and hotel nights in the city as specified",
    "Meals as specified during the trek",
    "Domestic flights where listed",
    "Staff wages, insurance, food and lodging",
    "Government taxes and service charges",
]

DEFAULT_EXCLUDED = [
    "International airfare and Nepal visa fees",
    "Travel and high-altitude rescue/evacuation insurance (mandatory)",
    "Personal trekking equipment and clothing",
    "Meals in the city (unless specified)",
    "Drinks, hot showers, battery charging, and Wi-Fi on the trek",
    "Tips for guide and porters",
    "Expenses caused by events beyond our control (weather, flight delays, illness)",
]

DEFAULT_PREP_NOTES = [
    "Travel insurance covering trekking up to your maximum altitude AND helicopter evacuation is mandatory.",
    "Break in your trekking boots before arrival.",
    "Pack layers; temperatures swing from warm days to freezing nights at altitude.",
    "Carry some Nepali rupees in cash for drinks, charging and tips on the trail.",
    "Acclimatise — follow your guide's pace and hydration advice.",
]

DEFAULT_PAYMENT_TERMS = [
    "20% non-refundable deposit to confirm the booking (covers permits and flight reservations).",
    "Balance payable on arrival in Kathmandu before the trek departs.",
    "Bank transfer, major cards (surcharge may apply), or cash accepted.",
]

DEFAULT_CANCELLATION = [
    "More than 30 days before departure: deposit retained, remainder refundable.",
    "15–30 days: 30% of trip cost charged.",
    "7–14 days: 50% of trip cost charged.",
    "Less than 7 days or no-show: 100% charged.",
    "Permits and domestic flights are non-refundable once issued.",
]

DEFAULT_UPGRADES = [
    "Helicopter return from altitude to save days and reduce fatigue",
    "Upgrade to deluxe/boutique hotels in Kathmandu and Pokhara",
    "Private guide and 1:1 porter ratio",
    "Add-on: Chitwan jungle safari or Pokhara relaxation extension",
    "Everest scenic mountain flight",
]


@dataclass
class Quotation:
    title: str
    summary: str
    itinerary_summary: list[str] = field(default_factory=list)
    cost: dict = field(default_factory=dict)
    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    preparation_notes: list[str] = field(default_factory=list)
    payment_terms: list[str] = field(default_factory=list)
    cancellation_policy: list[str] = field(default_factory=list)
    optional_upgrades: list[str] = field(default_factory=list)
    permits: list[str] = field(default_factory=list)
    important_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def build_quotation(estimate: Estimate, settings: Settings | None = None) -> Quotation:
    settings = settings or get_settings()
    trip = estimate.trip
    profile = find_trek(trip.trek) or find_trek(trip.destination)
    name = trip.trek or trip.destination or (profile.name if profile else "Custom Nepal Trek")
    days = trip.duration_days or (profile.typical_days if profile else None)
    pax = trip.group_size or 1

    title = f"{name} — {days}-Day Itinerary" if days else f"{name} Itinerary"

    summary_bits = [f"A {days}-day" if days else "A tailor-made", name, "experience"]
    if profile:
        summary_bits.append(f"in the {profile.region} region")
    summary_bits.append(f"for {pax} traveller{'s' if pax != 1 else ''}.")
    summary = " ".join(summary_bits)

    itinerary_summary: list[str] = []
    if profile:
        itinerary_summary.append(f"Region: {profile.region}")
        if profile.max_altitude_m:
            itinerary_summary.append(f"Maximum altitude: {profile.max_altitude_m} m")
        if profile.best_seasons:
            itinerary_summary.append("Best seasons: " + ", ".join(s.value for s in profile.best_seasons))
        if profile.notes:
            itinerary_summary.append(profile.notes)
    if days:
        itinerary_summary.append(f"Trip length: {days} days (incl. arrival/departure and acclimatisation).")

    permits = list(profile.permits) if profile else trip.permits

    important = [f.message for f in estimate.findings if f.severity in {"warning", "blocker"}]

    return Quotation(
        title=title,
        summary=summary,
        itinerary_summary=itinerary_summary,
        cost=estimate.cost.to_dict(pax),
        included=list(DEFAULT_INCLUDED),
        excluded=list(DEFAULT_EXCLUDED),
        preparation_notes=list(DEFAULT_PREP_NOTES),
        payment_terms=list(DEFAULT_PAYMENT_TERMS),
        cancellation_policy=list(DEFAULT_CANCELLATION),
        optional_upgrades=list(DEFAULT_UPGRADES),
        permits=permits,
        important_notes=important,
    )
