"""SalesAssistant — the single entry point that behaves like a 15-year Nepal
trekking consultant.

It composes the pricing engine, knowledge base (RAG), website + web research,
the self-logic reasoning engine, the LLM, the quotation builder, the email
writer, and the learning engine into one coherent workflow.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings, get_settings
from ..knowledge.store import KnowledgeStore
from ..llm.client import LLMClient
from ..learning.engine import Interaction, LearningEngine
from ..models import Estimate, SourceRef, TripSpec
from ..pricing.engine import PricingEngine
from ..pricing.excel_loader import ExcelLoadResult, load_rate_card_from_excel
from ..pricing.rates import RateCard
from ..reasoning.logic_engine import LogicEngine
from ..reasoning.nepal_facts import all_treks
from ..research.orchestrator import ResearchOrchestrator, ResearchResult
from ..research.web import WebResearcher
from ..research.website import WebsiteIndex
from .emails import EmailDraft, EmailStyle, EmailWriter
from .quotation import Quotation, build_quotation


@dataclass
class Answer:
    question: str
    text: str
    used_llm: bool
    confidence: float
    sources: list[SourceRef]

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "text": self.text,
            "used_llm": self.used_llm,
            "confidence": round(self.confidence, 4),
            "sources": [s.to_dict() for s in self.sources],
        }


class SalesAssistant:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

        # Pricing — start from defaults; load Excel if configured.
        self.rate_card = RateCard(
            currency=self.settings.currency,
            default_markup_pct=self.settings.default_markup_pct,
        )
        self._excel_result: ExcelLoadResult | None = None
        if self.settings.pricing_xlsx_path:
            try:
                self.load_pricing_excel(self.settings.pricing_xlsx_path)
            except Exception:  # noqa: BLE001 - fall back to defaults on any load issue
                pass
        self.pricing = PricingEngine(self.rate_card)

        # Knowledge & research.
        self.knowledge = KnowledgeStore()
        self.website = WebsiteIndex() if self.settings.website_research_enabled else None
        self.web = WebResearcher(
            enabled=self.settings.web_research_enabled,
            trusted_domains=self.settings.trusted_web_domains,
        )
        self.research = ResearchOrchestrator(
            knowledge=self.knowledge,
            website=self.website,
            web=self.web,
            min_score=self.settings.min_retrieval_score,
        )
        # Auto-load any knowledge already sitting in the configured dir.
        self.knowledge.load_directory(self.settings.knowledge_dir)

        # Reasoning, LLM, generation, learning.
        self.logic = LogicEngine()
        self.llm = LLMClient(self.settings)
        self.emailer = EmailWriter(self.settings, self.llm)
        self.learning = LearningEngine(self.settings)

    # ---------------------------------------------------------------- setup

    def load_pricing_excel(self, path: str) -> ExcelLoadResult:
        result = load_rate_card_from_excel(path, base=self.rate_card)
        self.rate_card = result.rate_card
        self._excel_result = result
        # Rebind the engine to the updated card.
        self.pricing = PricingEngine(self.rate_card)
        return result

    def load_knowledge_dir(self, path: str):
        return self.knowledge.load_directory(path)

    def add_knowledge_text(self, title: str, text: str) -> int:
        return self.knowledge.add_text(title, text)

    def index_website(self, urls: list[str]):
        if self.website is None:
            self.website = WebsiteIndex()
            self.research.website = self.website
        return self.website.index_urls(urls)

    # ------------------------------------------------------------- features

    def estimate(self, trip: TripSpec, markup_pct: float | None = None) -> Estimate:
        """Feature 1 + 4: cost estimate with reasoning, missing-info detection,
        and research-backed context."""
        cost, ctx = self.pricing.estimate(trip, markup_pct=markup_pct)
        findings, missing, questions = self.logic.analyse(trip, cost, ctx.profile)

        # Enrich with research provenance (so the estimate cites company knowledge).
        sources = list(ctx.sources)
        query = trip.trek or trip.destination
        research: ResearchResult | None = None
        if query:
            research = self.research.research(query, k=3)
            sources.extend(research.sources)

        # Surface engine assumptions as info findings.
        from ..models import Finding, SourceType
        for assumption in ctx.assumptions:
            findings.append(Finding("info", "info", assumption,
                                    SourceRef(SourceType.DEFAULTS, "Pricing assumption")))

        estimate = Estimate(
            trip=trip,
            cost=cost,
            findings=findings,
            missing_fields=missing,
            follow_up_questions=questions,
            sources=sources,
        )
        self.learning.record(Interaction(
            kind="estimate",
            query=query,
            trek=(ctx.profile.name if ctx.profile else query),
            confidence=research.confidence if research else 1.0,
            missing_fields=missing,
            unresolved=ctx.profile is None,
            used_llm=False,
        ))
        return estimate

    def quote(self, trip: TripSpec, markup_pct: float | None = None) -> tuple[Estimate, Quotation]:
        """Feature 2: full quotation package."""
        estimate = self.estimate(trip, markup_pct=markup_pct)
        quotation = build_quotation(estimate, self.settings)
        self.learning.record(Interaction(kind="quote", trek=trip.trek or trip.destination))
        return estimate, quotation

    def write_email(
        self,
        style: EmailStyle | str,
        trip: TripSpec | None = None,
        estimate: Estimate | None = None,
        extra_instructions: str = "",
    ) -> EmailDraft:
        """Feature 3: AI email writer with retrieval grounding."""
        if isinstance(style, str):
            style = EmailStyle(style)
        if estimate is None and trip is not None:
            estimate = self.estimate(trip)
        context = ""
        if estimate is not None:
            q = estimate.trip.trek or estimate.trip.destination
            if q:
                context = self.research.research(q, k=3).context_block()
        draft = self.emailer.write(style, estimate=estimate, context=context,
                                   extra_instructions=extra_instructions)
        self.learning.record(Interaction(kind="email", query=style.value, used_llm=draft.used_llm))
        return draft

    def answer(self, question: str, k: int = 4) -> Answer:
        """Feature 5: research a question (docs → website → web) and answer with sources."""
        research = self.research.research(question, k=k)
        context = research.context_block()

        text = ""
        used_llm = False
        if self.llm.available:
            system = (
                f"You are a Nepal trekking expert at {self.settings.company_name}. Answer using the "
                "provided company knowledge first. If the knowledge is insufficient, say what you'd "
                "need to confirm. Be concise and practical. Never invent prices or permit rules."
            )
            result = self.llm.complete(question, system=system, context=context)
            if result.used_llm and result.text:
                text, used_llm = result.text, True
        if not text:
            # Offline fallback: stitch the top retrieved snippets.
            if research.hits:
                text = "Based on our knowledge base:\n\n" + "\n\n".join(
                    f"• {h.text.strip()[:280]}" for h in research.hits[:3]
                )
            else:
                text = ("I don't have enough in our knowledge base to answer that confidently yet. "
                        "Please upload relevant documents or enable web research.")

        self.learning.record(Interaction(
            kind="answer", query=question, confidence=research.confidence,
            unresolved=not research.hits, used_llm=used_llm,
        ))
        return Answer(question, text, used_llm, research.confidence, research.sources)

    # --------------------------------------------------------------- status

    def status(self) -> dict:
        """Feature 8: system health snapshot for the admin dashboard."""
        return {
            "company": self.settings.company_name,
            "llm": self.llm.status(),
            "pricing": {
                "currency": self.rate_card.currency,
                "source": (self.rate_card.source.to_dict() if self.rate_card.source else "defaults"),
                "excel_summary": self._excel_result.summary() if self._excel_result else None,
                "default_markup_pct": self.rate_card.default_markup_pct,
            },
            "knowledge": {
                "chunks_indexed": self.knowledge.chunk_count,
                "knowledge_dir": self.settings.knowledge_dir,
            },
            "website_research": self.settings.website_research_enabled,
            "web_research": self.web.enabled,
            "known_treks": [t.name for t in all_treks()],
        }
