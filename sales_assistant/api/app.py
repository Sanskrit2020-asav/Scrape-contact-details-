"""API-first surface for the sales assistant.

Every capability is reachable over HTTP (Feature 8: admin dashboard). The app is
created lazily so importing the package never hard-requires FastAPI; install
``fastapi`` and ``uvicorn`` to run it.

Run:  python -m sales_assistant.cli serve
"""
from __future__ import annotations

from typing import Any

try:
    from fastapi import Body, FastAPI, HTTPException
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "FastAPI is required for the API. Install with `pip install fastapi uvicorn`."
    ) from exc

from ..assistant.core import SalesAssistant
from ..assistant.emails import EmailStyle
from ..models import TripSpec

app = FastAPI(
    title="North Nepal Travel & Trek — Sales Assistant API",
    version="0.1.0",
    description="Cost estimation, RAG knowledge, research, quotations, AI emails, and self-improving logic.",
)

# A single long-lived assistant (loads knowledge/pricing once).
_assistant: SalesAssistant | None = None


def assistant() -> SalesAssistant:
    global _assistant
    if _assistant is None:
        _assistant = SalesAssistant()
    return _assistant


# --------------------------------------------------------------- schemas


class TripModel(BaseModel):
    destination: str = ""
    trek: str = ""
    duration_days: int | None = None
    group_size: int | None = None
    start_date: str = ""
    transportation: str = ""
    hotel_category: str = ""
    domestic_flights: int | None = None
    permits: list[str] = []
    guides: int | None = None
    porters: int | None = None
    meals_included: bool | None = None
    optional_activities: list[str] = []
    nationality: str = ""
    notes: str = ""
    customer_name: str = ""
    customer_email: str = ""

    def to_spec(self) -> TripSpec:
        return TripSpec.from_dict(self.model_dump())


class EstimateRequest(BaseModel):
    trip: TripModel
    markup_pct: float | None = None


class EmailRequest(BaseModel):
    style: str
    trip: TripModel
    extra_instructions: str = ""


class AnswerRequest(BaseModel):
    question: str
    k: int = 4


class KnowledgeTextRequest(BaseModel):
    title: str
    text: str


class WebsiteRequest(BaseModel):
    urls: list[str]


# ----------------------------------------------------------------- routes


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict[str, Any]:
    return assistant().status()


@app.post("/estimate")
def estimate(req: EstimateRequest) -> dict[str, Any]:
    est = assistant().estimate(req.trip.to_spec(), markup_pct=req.markup_pct)
    return est.to_dict()


@app.post("/quote")
def quote(req: EstimateRequest) -> dict[str, Any]:
    est, quotation = assistant().quote(req.trip.to_spec(), markup_pct=req.markup_pct)
    return {"estimate": est.to_dict(), "quotation": quotation.to_dict()}


@app.post("/email")
def email(req: EmailRequest) -> dict[str, Any]:
    try:
        style = EmailStyle(req.style)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown email style '{req.style}'. "
                                 f"Valid: {[s.value for s in EmailStyle]}") from exc
    draft = assistant().write_email(style, trip=req.trip.to_spec(),
                                    extra_instructions=req.extra_instructions)
    return draft.to_dict()


@app.post("/answer")
def answer(req: AnswerRequest) -> dict[str, Any]:
    return assistant().answer(req.question, k=req.k).to_dict()


# ---- Admin / knowledge management (Feature 8) ----


@app.post("/admin/knowledge/text")
def add_knowledge_text(req: KnowledgeTextRequest) -> dict[str, Any]:
    chunks = assistant().add_knowledge_text(req.title, req.text)
    return {"added_chunks": chunks, "total_chunks": assistant().knowledge.chunk_count}


@app.post("/admin/knowledge/reload")
def reload_knowledge(path: str | None = Body(default=None, embed=True)) -> dict[str, Any]:
    a = assistant()
    report = a.load_knowledge_dir(path or a.settings.knowledge_dir)
    return {"summary": report.summary(), "skipped": report.skipped}


@app.post("/admin/website/index")
def index_website(req: WebsiteRequest) -> dict[str, Any]:
    report = assistant().index_website(req.urls)
    return {"summary": report.summary(), "fetched": report.fetched, "failed": report.failed}


@app.post("/admin/pricing/excel")
def load_excel(path: str = Body(..., embed=True)) -> dict[str, Any]:
    result = assistant().load_pricing_excel(path)
    return {
        "summary": result.summary(),
        "populated_fields": result.populated_fields,
        "permits_loaded": result.permits_loaded,
        "formulas": result.formulas,
        "extras": result.extras,
        "warnings": result.warnings,
    }


@app.get("/admin/pricing/rates")
def get_rates() -> dict[str, Any]:
    return assistant().rate_card.to_dict()


@app.get("/admin/learning/report")
def learning_report() -> dict[str, Any]:
    return assistant().learning.analyse().to_dict()


@app.get("/admin/learning/history")
def learning_history(limit: int = 50) -> dict[str, Any]:
    return {"history": assistant().learning.history(limit=limit)}
