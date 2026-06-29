"""The rate card: every price the engine uses, in one place.

These default figures are PLACEHOLDERS modelled on typical Nepal market rates so
the engine produces realistic numbers out of the box. They are explicitly meant
to be replaced by the company's real Excel estimator via
``RateCard.from_excel(...)`` (see ``excel_loader``). Never treat these defaults
as authoritative quotes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..models import SourceRef, SourceType


@dataclass
class RateCard:
    currency: str = "USD"

    # Per-day staff rates (incl. wage, insurance, food/lodging allowance).
    guide_per_day: float = 35.0
    porter_per_day: float = 22.0
    climbing_guide_per_day: float = 80.0

    # Per-person-per-night accommodation.
    teahouse_pppn: float = 12.0
    hotel_3star_pppn: float = 35.0
    hotel_luxury_pppn: float = 110.0

    # Meals per person per day (full board on trek).
    meals_per_day: float = 30.0

    # Transport.
    domestic_flight_per_person: float = 410.0   # e.g. KTM–Lukla round trip
    private_vehicle_per_day: float = 90.0
    tourist_bus_per_person: float = 25.0
    airport_transfer_flat: float = 20.0

    # Permits & cards (per person unless noted). Extend freely.
    permit_costs: dict[str, float] = field(default_factory=lambda: {
        "TIMS Card": 17.0,
        "ACAP (Annapurna Conservation Area Permit)": 25.0,
        "Sagarmatha National Park Permit": 25.0,
        "Khumbu Pasang Lhamu Rural Municipality Permit": 17.0,
        "Langtang National Park Permit": 25.0,
        "Manaslu Restricted Area Permit": 100.0,
        "MCAP (Manaslu Conservation Area Permit)": 25.0,
        "Makalu Barun National Park Permit": 25.0,
        "Upper Mustang Restricted Area Permit (USD/10 days)": 500.0,
        "NMA Climbing Permit (Island Peak)": 250.0,
        "NMA Climbing Permit (Mera Peak)": 250.0,
    })

    # Per-person buffers / extras.
    equipment_per_trip: float = 0.0
    insurance_per_trip: float = 0.0
    misc_per_person: float = 40.0       # SIM, water purification, duffel, tips buffer
    activity_costs: dict[str, float] = field(default_factory=lambda: {
        "everest scenic flight": 220.0,
        "helicopter return": 1100.0,
        "chitwan safari (2n/3d)": 280.0,
        "paragliding pokhara": 90.0,
        "white water rafting": 75.0,
        "city tour kathmandu": 55.0,
    })

    # Default markup (%) if the trip doesn't specify one.
    default_markup_pct: float = 25.0

    # Provenance — set when loaded from Excel so estimates can cite the sheet.
    source: SourceRef | None = field(default=None)

    # ----- (de)serialisation -----

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("source", None)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RateCard":
        known = {f for f in cls.__dataclass_fields__ if f != "source"}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    @classmethod
    def from_json(cls, path: str | Path) -> "RateCard":
        path = Path(path)
        card = cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        card.source = SourceRef(SourceType.DOCUMENT, "Rate card (JSON)", str(path))
        return card

    def save_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    # ----- lookups with graceful fallback -----

    def permit_cost(self, permit: str) -> tuple[float, bool]:
        """Return (cost, known). Unknown permits fall back to a conservative flat fee."""
        if permit in self.permit_costs:
            return self.permit_costs[permit], True
        # Case-insensitive match
        for name, cost in self.permit_costs.items():
            if name.lower() == permit.lower():
                return cost, True
        return 30.0, False

    def activity_cost(self, activity: str) -> tuple[float, bool]:
        key = activity.strip().lower()
        if key in self.activity_costs:
            return self.activity_costs[key], True
        for name, cost in self.activity_costs.items():
            if name in key or key in name:
                return cost, True
        return 50.0, False

    def accommodation_pppn(self, hotel_category: str) -> float:
        c = (hotel_category or "").lower()
        if any(w in c for w in ("lux", "5", "boutique", "resort")):
            return self.hotel_luxury_pppn
        if any(w in c for w in ("hotel", "3", "4", "star", "city")):
            return self.hotel_3star_pppn
        return self.teahouse_pppn  # default to teahouse on trek
