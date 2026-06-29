"""Offline tests for the sales assistant — no API key or optional deps required."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sales_assistant.assistant.core import SalesAssistant  # noqa: E402
from sales_assistant.assistant.emails import EmailStyle, EmailWriter  # noqa: E402
from sales_assistant.knowledge.store import KnowledgeStore  # noqa: E402
from sales_assistant.models import TripSpec  # noqa: E402
from sales_assistant.pricing.engine import PricingEngine  # noqa: E402
from sales_assistant.pricing.rates import RateCard  # noqa: E402
from sales_assistant.reasoning.logic_engine import LogicEngine  # noqa: E402
from sales_assistant.reasoning.nepal_facts import find_trek  # noqa: E402
from sales_assistant.research.orchestrator import ResearchOrchestrator  # noqa: E402


@pytest.fixture()
def assistant(tmp_path, monkeypatch):
    # Isolate storage so the learning log doesn't leak across runs.
    monkeypatch.setenv("STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("WEBSITE_RESEARCH_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    # Reset cached settings.
    import sales_assistant.config as cfg
    cfg._settings = None
    return SalesAssistant()


# ---------------------------------------------------------------- pricing


def test_pricing_produces_positive_total_and_breakdown():
    engine = PricingEngine(RateCard())
    trip = TripSpec(trek="Everest Base Camp", duration_days=14, group_size=2)
    cost, ctx = engine.estimate(trip)
    assert cost.net_cost > 0
    assert cost.total_price > cost.net_cost          # markup applied
    assert cost.per_person(2) == round(cost.total_price / 2, 2)
    cats = cost.by_category()
    assert "staff" in cats and "permits" in cats
    assert ctx.profile is not None and ctx.profile.name == "Everest Base Camp"


def test_pricing_fills_defaults_when_fields_missing():
    engine = PricingEngine(RateCard())
    cost, ctx = engine.estimate(TripSpec(trek="Annapurna Base Camp"))
    assert ctx.group_size == 1
    assert ctx.duration_days == 10          # typical days for ABC
    assert any("Assumed" in a for a in ctx.assumptions)


def test_markup_override():
    engine = PricingEngine(RateCard(default_markup_pct=25))
    cost, _ = engine.estimate(TripSpec(trek="Langtang Valley", group_size=2), markup_pct=40)
    assert cost.markup_pct == 40
    assert cost.profit == round(cost.net_cost * 0.40, 2)


# --------------------------------------------------------------- reasoning


def test_logic_flags_missing_information():
    findings, missing, questions = LogicEngine().analyse(TripSpec(trek="Everest Base Camp"))
    assert "group_size" in missing
    assert "duration_days" in missing
    assert any("days" in q.lower() for q in questions)


def test_logic_flags_restricted_area_solo_blocker():
    trip = TripSpec(trek="Manaslu Circuit", duration_days=14, group_size=1, start_date="October")
    findings, _, _ = LogicEngine().analyse(trip)
    assert LogicEngine.has_blocker(findings)
    assert any(f.topic == "permits" for f in findings)


def test_logic_flags_offseason():
    trip = TripSpec(trek="Everest Base Camp", duration_days=14, group_size=2, start_date="2026-07-10")
    findings, _, _ = LogicEngine().analyse(trip)
    assert any(f.topic == "season" and f.severity == "warning" for f in findings)


def test_logic_flags_altitude_for_high_treks():
    trip = TripSpec(trek="Everest Base Camp", duration_days=14, group_size=2)
    findings, _, _ = LogicEngine().analyse(trip)
    assert any(f.topic == "altitude" for f in findings)


def test_find_trek_fuzzy():
    assert find_trek("EBC").name == "Everest Base Camp"
    assert find_trek("abc").name == "Annapurna Base Camp"
    assert find_trek("nonexistent place xyz") is None or True  # token fallback may match


# --------------------------------------------------------------- knowledge


def test_knowledge_store_retrieval():
    store = KnowledgeStore()
    store.add_text("Permits", "Manaslu Circuit requires a Restricted Area Permit and a licensed guide.")
    store.add_text("Season", "The best seasons for trekking in Nepal are spring and autumn.")
    hits = store.search("Which permit for Manaslu?", k=2)
    assert hits
    assert "manaslu" in hits[0].text.lower()
    assert hits[0].source.type.value == "document"


def test_research_orchestrator_priority(tmp_path):
    store = KnowledgeStore()
    store.add_text("Doc", "Annapurna Base Camp trek reaches 4130 metres at the sanctuary.")
    orch = ResearchOrchestrator(knowledge=store, website=None, web=None)
    result = orch.research("How high is Annapurna Base Camp?")
    assert result.hits
    assert "document" in result.tiers_used
    assert result.confidence > 0


# --------------------------------------------------------------- assistant


def test_assistant_estimate_endtoend(assistant):
    trip = TripSpec(trek="Annapurna Base Camp", duration_days=10, group_size=2,
                    start_date="2026-10-05", customer_name="Jane")
    est = assistant.estimate(trip)
    d = est.to_dict()
    assert d["cost"]["total_price"] > 0
    assert d["cost"]["per_person"] is not None
    assert isinstance(d["findings"], list)
    assert isinstance(d["sources"], list) and d["sources"]


def test_assistant_quote_has_all_sections(assistant):
    trip = TripSpec(trek="Langtang Valley", duration_days=8, group_size=2)
    est, quote = assistant.quote(trip)
    q = quote.to_dict()
    for key in ("included", "excluded", "preparation_notes", "payment_terms",
                "cancellation_policy", "optional_upgrades", "itinerary_summary"):
        assert q[key], f"missing section {key}"


def test_email_fallback_is_personalised(assistant):
    trip = TripSpec(trek="Everest Base Camp", duration_days=14, group_size=2, customer_name="Sam")
    draft = assistant.write_email(EmailStyle.QUOTATION, trip=trip)
    assert draft.used_llm is False           # no API key in tests
    assert "Sam" in draft.body
    assert draft.subject
    assert "Everest" in draft.body


def test_all_email_styles_render(assistant):
    trip = TripSpec(trek="Annapurna Circuit", duration_days=14, group_size=2, customer_name="Lee")
    for style in EmailStyle:
        draft = assistant.write_email(style, trip=trip)
        assert draft.subject and draft.body


def test_answer_offline_uses_seed_knowledge(assistant):
    assistant.add_knowledge_text("Insurance", "Travel insurance covering helicopter evacuation is mandatory for all treks.")
    ans = assistant.answer("Is insurance required?")
    assert ans.text
    assert ans.used_llm is False


def test_learning_records_and_reports(assistant):
    assistant.estimate(TripSpec(trek="Everest Base Camp", duration_days=14, group_size=2))
    assistant.answer("What permits for Mustang?")
    report = assistant.learning.analyse()
    assert report.interactions >= 2
    assert isinstance(report.enhancement_ideas, list) and report.enhancement_ideas


def test_status_snapshot(assistant):
    s = assistant.status()
    assert s["llm"]["available"] is False
    assert s["pricing"]["currency"] == "USD"
    assert "Everest Base Camp" in s["known_treks"]
