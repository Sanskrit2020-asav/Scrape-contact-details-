"""Domain models shared across the platform.

Plain dataclasses keep the core import-light (no pydantic needed to run the
pricing engine or reasoning). The API layer adds request/response validation on
top of these.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Season(str, Enum):
    SPRING = "spring"        # Mar–May
    SUMMER = "summer"        # Jun–Aug (monsoon)
    AUTUMN = "autumn"        # Sep–Nov
    WINTER = "winter"        # Dec–Feb


class SourceType(str, Enum):
    EXCEL = "excel"
    DOCUMENT = "document"
    WEBSITE = "website"
    WEB = "web"
    REASONING = "reasoning"
    DEFAULTS = "defaults"


@dataclass
class SourceRef:
    """Where a piece of information came from, for internal provenance tracking."""

    type: SourceType
    title: str
    locator: str = ""          # file path, URL, sheet!cell, etc.
    snippet: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


@dataclass
class TripSpec:
    """Everything a customer can specify about a desired trip.

    All fields are optional; the assistant's job includes detecting what is
    missing and asking intelligent follow-up questions.
    """

    destination: str = ""
    trek: str = ""
    duration_days: int | None = None
    group_size: int | None = None
    start_date: str = ""               # ISO yyyy-mm-dd if known
    transportation: str = ""           # e.g. "private vehicle", "tourist bus"
    hotel_category: str = ""           # e.g. "3-star", "teahouse", "luxury"
    domestic_flights: int | None = None
    permits: list[str] = field(default_factory=list)
    guides: int | None = None
    porters: int | None = None
    meals_included: bool | None = None
    optional_activities: list[str] = field(default_factory=list)
    nationality: str = ""
    notes: str = ""
    customer_name: str = ""
    customer_email: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TripSpec":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class LineItem:
    label: str
    category: str            # transport / accommodation / permits / staff / meals / activities / misc
    unit_cost: float
    quantity: float
    days: float = 1.0
    source: SourceRef | None = None

    @property
    def total(self) -> float:
        return round(self.unit_cost * self.quantity * self.days, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "category": self.category,
            "unit_cost": self.unit_cost,
            "quantity": self.quantity,
            "days": self.days,
            "total": self.total,
            "source": self.source.to_dict() if self.source else None,
        }


@dataclass
class CostBreakdown:
    currency: str
    line_items: list[LineItem] = field(default_factory=list)
    markup_pct: float = 0.0

    @property
    def net_cost(self) -> float:
        return round(sum(li.total for li in self.line_items), 2)

    @property
    def profit(self) -> float:
        return round(self.net_cost * self.markup_pct / 100.0, 2)

    @property
    def total_price(self) -> float:
        return round(self.net_cost + self.profit, 2)

    def per_person(self, group_size: int | None) -> float | None:
        if not group_size or group_size <= 0:
            return None
        return round(self.total_price / group_size, 2)

    def by_category(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for li in self.line_items:
            out[li.category] = round(out.get(li.category, 0.0) + li.total, 2)
        return out

    def to_dict(self, group_size: int | None = None) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "line_items": [li.to_dict() for li in self.line_items],
            "by_category": self.by_category(),
            "net_cost": self.net_cost,
            "markup_pct": self.markup_pct,
            "profit": self.profit,
            "total_price": self.total_price,
            "per_person": self.per_person(group_size),
        }


@dataclass
class Finding:
    """A single observation produced by the reasoning engine."""

    severity: str            # info / suggestion / warning / blocker
    topic: str               # season / altitude / permits / flights / pricing / logistics / info
    message: str
    source: SourceRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "topic": self.topic,
            "message": self.message,
            "source": self.source.to_dict() if self.source else None,
        }


@dataclass
class Estimate:
    """The full result of estimating a trip — the platform's central artifact."""

    trip: TripSpec
    cost: CostBreakdown
    findings: list[Finding] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trip": self.trip.to_dict(),
            "cost": self.cost.to_dict(self.trip.group_size),
            "findings": [f.to_dict() for f in self.findings],
            "missing_fields": self.missing_fields,
            "follow_up_questions": self.follow_up_questions,
            "sources": [s.to_dict() for s in self.sources],
        }
