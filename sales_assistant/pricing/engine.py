"""The cost-estimation engine.

Turns a :class:`TripSpec` into a :class:`CostBreakdown` using a :class:`RateCard`
and the curated trek profiles. It fills sensible defaults for anything the
customer didn't specify, and records each assumption so the assistant can show
its work and ask follow-up questions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..models import (
    CostBreakdown,
    LineItem,
    SourceRef,
    SourceType,
    TripSpec,
)
from ..reasoning.nepal_facts import TrekProfile, find_trek
from .rates import RateCard


@dataclass
class EstimateContext:
    """Everything the engine inferred while pricing — surfaced to the user."""

    profile: TrekProfile | None = None
    duration_days: int = 0
    group_size: int = 1
    assumptions: list[str] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)


# How many porters per trekker on a standard teahouse trek.
PORTERS_PER_TREKKER = 0.5
# City (Kathmandu/Pokhara) hotel nights bracketing the trek.
DEFAULT_CITY_NIGHTS = 2
# Airport transfers per trip (arrival + departure).
DEFAULT_TRANSFERS = 2


class PricingEngine:
    def __init__(self, rate_card: RateCard | None = None):
        self.rates = rate_card or RateCard()

    # The rate-card provenance attached to every generated line item.
    def _src(self) -> SourceRef:
        if self.rates.source:
            return self.rates.source
        return SourceRef(SourceType.DEFAULTS, "Built-in default rate card")

    def estimate(self, trip: TripSpec, markup_pct: float | None = None) -> tuple[CostBreakdown, EstimateContext]:
        ctx = EstimateContext()
        src = self._src()
        ctx.sources.append(src)

        profile = find_trek(trip.trek) or find_trek(trip.destination)
        ctx.profile = profile

        # --- Duration ---
        if trip.duration_days and trip.duration_days > 0:
            duration = trip.duration_days
        elif profile and profile.typical_days:
            duration = profile.typical_days
            ctx.assumptions.append(f"Assumed {duration} days (typical for {profile.name}).")
        else:
            duration = 10
            ctx.assumptions.append("Assumed 10 days (no duration given and trek not recognised).")
        ctx.duration_days = duration

        # --- Group size ---
        group = trip.group_size if (trip.group_size and trip.group_size > 0) else 1
        if not trip.group_size:
            ctx.assumptions.append("Assumed solo traveller (group size of 1).")
        ctx.group_size = group

        is_climb = bool(profile and not profile.teahouse)
        items: list[LineItem] = []

        # --- Staff: guide(s) ---
        guides = trip.guides if trip.guides is not None else 1
        if trip.guides is None:
            ctx.assumptions.append("Assumed 1 licensed guide.")
        if guides > 0:
            items.append(LineItem("Licensed guide", "staff", self.rates.guide_per_day, guides, duration, src))
        if is_climb:
            items.append(LineItem("Climbing guide (Sherpa)", "staff", self.rates.climbing_guide_per_day, 1, duration, src))

        # --- Staff: porters ---
        if trip.porters is not None:
            porters = trip.porters
        else:
            porters = math.ceil(group * PORTERS_PER_TREKKER)
            if porters:
                ctx.assumptions.append(f"Assumed {porters} porter(s) (~1 per 2 trekkers).")
        if porters > 0:
            items.append(LineItem("Porter", "staff", self.rates.porter_per_day, porters, duration, src))

        # --- Accommodation ---
        trek_pppn = self.rates.teahouse_pppn
        trek_nights = max(duration - DEFAULT_CITY_NIGHTS, 0) if not is_climb else duration
        if trek_nights:
            label = "Camping/teahouse on route" if is_climb else "Teahouse / lodge on trek"
            items.append(LineItem(label, "accommodation", trek_pppn, group, trek_nights, src))
        city_nights = DEFAULT_CITY_NIGHTS
        city_pppn = self.rates.accommodation_pppn(trip.hotel_category or "hotel")
        items.append(LineItem(
            f"City hotel ({trip.hotel_category or '3-star'})", "accommodation", city_pppn, group, city_nights, src
        ))
        if not trip.hotel_category:
            ctx.assumptions.append("Assumed standard 3-star city hotel and 2 city nights.")

        # --- Meals ---
        meals_on = trip.meals_included if trip.meals_included is not None else True
        if meals_on:
            items.append(LineItem("Meals (full board on trek)", "meals", self.rates.meals_per_day, group, duration, src))
            if trip.meals_included is None:
                ctx.assumptions.append("Assumed full-board meals included on trek.")

        # --- Flights ---
        if trip.domestic_flights is not None:
            n_flights = trip.domestic_flights
        elif profile and profile.requires_domestic_flight:
            n_flights = 1
            ctx.assumptions.append(f"Assumed 1 domestic round-trip flight ({profile.name} requires one).")
        else:
            n_flights = 0
        if n_flights > 0:
            items.append(LineItem("Domestic flight (round trip)", "transport",
                                  self.rates.domestic_flight_per_person, group, n_flights, src))

        # --- Ground transport ---
        transport = (trip.transportation or "").lower()
        if "bus" in transport:
            items.append(LineItem("Tourist bus", "transport", self.rates.tourist_bus_per_person, group, 1, src))
        elif transport:
            items.append(LineItem("Private vehicle", "transport", self.rates.private_vehicle_per_day, DEFAULT_TRANSFERS, 1, src))
        else:
            items.append(LineItem("Airport/road transfers", "transport", self.rates.airport_transfer_flat, DEFAULT_TRANSFERS, 1, src))

        # --- Permits ---
        permits = trip.permits or (list(profile.permits) if profile else [])
        if not trip.permits and profile:
            ctx.assumptions.append(f"Applied standard permits for {profile.name}: {', '.join(profile.permits)}.")
        for permit in permits:
            cost, known = self.rates.permit_cost(permit)
            li_src = src if known else SourceRef(SourceType.DEFAULTS, "Estimated permit fee (verify)")
            items.append(LineItem(permit, "permits", cost, group, 1, li_src))
            if not known:
                ctx.assumptions.append(f"Permit '{permit}' not in rate card — used conservative estimate ${cost:.0f}.")

        # --- Optional activities ---
        for activity in trip.optional_activities:
            cost, known = self.rates.activity_cost(activity)
            items.append(LineItem(f"Activity: {activity}", "activities", cost, group, 1, src))
            if not known:
                ctx.assumptions.append(f"Activity '{activity}' not in rate card — used estimate ${cost:.0f}.")

        # --- Misc buffer ---
        if self.rates.misc_per_person:
            items.append(LineItem("Misc (SIM, water, duffel, contingency)", "misc",
                                  self.rates.misc_per_person, group, 1, src))

        markup = markup_pct if markup_pct is not None else self.rates.default_markup_pct
        breakdown = CostBreakdown(currency=self.rates.currency, line_items=items, markup_pct=markup)
        return breakdown, ctx
