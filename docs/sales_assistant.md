# North Nepal Travel & Trek — Intelligent Sales Assistant

An AI sales-consultant platform for North Nepal Travel & Trek. It estimates trek
costs, retrieves company knowledge (RAG), researches the website and the wider
web, reasons like a 15-year Nepal trekking expert, and drafts quotations and
human-quality emails — improving itself as it goes.

The platform lives in the `sales_assistant/` package and is independent of the
existing Facebook contact-scraper in `scripts/`.

## Design principles

- **Modular & API-first.** Every capability is a focused module and is reachable
  over HTTP. The `SalesAssistant` class composes them; nothing else talks to the
  SDK or the spreadsheet directly.
- **Graceful degradation.** The core runs on the Python standard library with no
  API key. Claude, document parsers, the web server, and web research activate
  automatically when their dependencies/credentials are present, and fall back
  to deterministic behaviour otherwise. This keeps it testable offline.
- **Company knowledge first.** Research always prefers uploaded documents, then
  the website, then trusted web sources — random internet content never
  overrides company knowledge.
- **Excel is the pricing source of truth.** Default rates are placeholders;
  loading the company `.xlsx` replaces them and preserves formulas for audit.
- **Provenance everywhere.** Estimates, answers and quotations carry `SourceRef`s
  so you always know whether a fact came from Excel, a document, the website,
  the web, or the reasoning engine.

## Architecture

```
sales_assistant/
  config.py              # env-driven settings (no hardcoded secrets/prices)
  models.py              # TripSpec, CostBreakdown, Estimate, Finding, SourceRef …
  pricing/
    rates.py             # RateCard — every price in one place (Excel-overridable)
    excel_loader.py      # read .xlsx estimator → RateCard (+ formulas, extras)
    engine.py            # TripSpec → CostBreakdown with assumptions
  knowledge/
    ingest.py            # load .txt/.md/.pdf/.docx/.xlsx into documents
    store.py             # dependency-free TF-IDF RAG store (embeddings-ready)
  research/
    website.py           # fetch + index company website pages
    web.py               # controlled, pluggable web research (trusted domains)
    orchestrator.py      # priority: documents → website → web, de-duped
  reasoning/
    nepal_facts.py       # curated trek/peak profiles, seasons, altitude, permits
    logic_engine.py      # self-logic checks before responding (Feature 4)
  llm/
    client.py            # Anthropic Claude wrapper (claude-opus-4-8, adaptive)
  assistant/
    quotation.py         # full quotation package (Feature 2)
    emails.py            # 8-style AI email writer with template fallback (F3)
    core.py              # SalesAssistant — orchestrates everything
  learning/
    engine.py            # interaction memory + gap detection + ideas (F9)
  api/
    app.py               # FastAPI surface incl. admin endpoints (F8)
  cli.py                 # command-line interface
  data/
    knowledge/           # drop trek itineraries, SOPs, PDFs, DOCX, XLSX here
    store/               # interaction log / learning memory (generated)
```

### Feature → module map

| # | Feature | Where |
|---|---------|-------|
| 1 | AI cost estimator | `pricing/engine.py`, `assistant/core.py:estimate` |
| 2 | Smart sales assistant (quotation) | `assistant/quotation.py` |
| 3 | AI email writer (8 styles) | `assistant/emails.py` |
| 4 | Self logic engine | `reasoning/logic_engine.py` |
| 5 | Research skill | `research/orchestrator.py` |
| 6 | Memory | `learning/engine.py` (JSONL store) |
| 7 | Source referencing | `models.SourceRef`, surfaced in every result |
| 8 | Admin dashboard (API) | `api/app.py` (`/admin/*`) |
| 9 | Learning system | `learning/engine.py:analyse` |

## Quick start

```bash
# (optional) install integrations — the core works without them
pip install -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY and PRICING_XLSX_PATH if you have them

# Estimate a trip
python -m sales_assistant.cli estimate --trek "Everest Base Camp" --days 14 --pax 2 --start 2026-10-05

# Build a full quotation
python -m sales_assistant.cli quote --trek "Annapurna Base Camp" --pax 2 --name "Jane"

# Draft an email (uses Claude if a key is set, else a personalised template)
python -m sales_assistant.cli email --style quotation --trek "Langtang Valley" --pax 2 --name "Sam"

# Ask a knowledge question (documents → website → web)
python -m sales_assistant.cli answer "What permits do I need for Manaslu?"

# Load the company pricing spreadsheet
python -m sales_assistant.cli load-excel /path/to/cost-estimator.xlsx

# See what the system has learned
python -m sales_assistant.cli learn

# Run the API (needs fastapi + uvicorn)
python -m sales_assistant.cli serve   # → http://127.0.0.1:8000/docs
```

### Python usage

```python
from sales_assistant.assistant.core import SalesAssistant
from sales_assistant.assistant.emails import EmailStyle
from sales_assistant.models import TripSpec

a = SalesAssistant()
a.load_pricing_excel("cost-estimator.xlsx")           # real prices
a.load_knowledge_dir("company_docs/")                 # itineraries, SOPs, PDFs
a.index_website(["https://www.northnepaltrek.com/everest-base-camp-trek"])

trip = TripSpec(trek="Everest Base Camp", duration_days=14, group_size=2,
                start_date="2026-10-05", customer_name="Jane")

estimate = a.estimate(trip)            # cost + reasoning + missing-info questions
est, quote = a.quote(trip)             # full quotation package
draft = a.write_email(EmailStyle.QUOTATION, estimate=est)
answer = a.answer("Is travel insurance required?")
```

## Connecting the existing resources

- **Cost estimator (.xlsx):** set `PRICING_XLSX_PATH` or call
  `load_pricing_excel`. The loader maps labelled `rate: value` rows to the
  `RateCard`, captures formulas for audit, and reports anything it couldn't map
  as `extras` so nothing is lost. Extend `_FIELD_KEYWORDS` in
  `pricing/excel_loader.py` to teach it your sheet's labels.
- **Knowledge base:** drop files in `sales_assistant/data/knowledge/` (or set
  `KNOWLEDGE_DIR`) and they're indexed on startup. PDF/DOCX/XLSX need the
  optional parser libs.
- **Website:** call `index_website([...])` with your trek/peak/blog/FAQ URLs.
- **Claude project & skills:** the LLM layer uses Claude (`claude-opus-4-8`,
  adaptive thinking) and is the natural home for porting prompt logic from the
  existing Claude project. Set `ANTHROPIC_API_KEY` to enable it.

## Extensibility (future integrations)

The architecture leaves clear seams for the roadmap items in the brief:

- **CRM (Zoho), WhatsApp, payments:** add a `sales_assistant/integrations/`
  package and call it from `assistant/core.py` after `estimate`/`quote`/`email`.
- **Embeddings RAG:** replace the scoring inside `KnowledgeStore.search` — the
  interface is already the shape of a vector store.
- **Live web research:** register a backend via `WebResearcher.set_backend`, or
  let Claude's server-side `web_search` tool handle it (restricted to
  `TRUSTED_WEB_DOMAINS`).

## Tests

```bash
pip install pytest
pytest tests/test_sales_assistant.py
```

All tests run fully offline (no API key, no optional dependencies).
