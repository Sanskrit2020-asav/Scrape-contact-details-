"""Command-line interface for the sales assistant.

Examples:
    python -m sales_assistant.cli status
    python -m sales_assistant.cli estimate --trek "Everest Base Camp" --days 14 --pax 2 --start 2026-10-05
    python -m sales_assistant.cli quote --trek "Annapurna Base Camp" --pax 2 --name "Jane"
    python -m sales_assistant.cli email --style quotation --trek "Langtang Valley" --pax 2 --name "Sam"
    python -m sales_assistant.cli answer "What permits do I need for Manaslu?"
    python -m sales_assistant.cli learn
    python -m sales_assistant.cli load-excel path/to/estimator.xlsx
"""
from __future__ import annotations

import argparse
import json
import sys

from .assistant.core import SalesAssistant
from .assistant.emails import EmailStyle
from .models import TripSpec


def _trip_from_args(args) -> TripSpec:
    return TripSpec(
        destination=getattr(args, "destination", "") or "",
        trek=getattr(args, "trek", "") or "",
        duration_days=getattr(args, "days", None),
        group_size=getattr(args, "pax", None),
        start_date=getattr(args, "start", "") or "",
        hotel_category=getattr(args, "hotel", "") or "",
        nationality=getattr(args, "nationality", "") or "",
        optional_activities=[a.strip() for a in (getattr(args, "activities", "") or "").split(",") if a.strip()],
        customer_name=getattr(args, "name", "") or "",
        customer_email=getattr(args, "email", "") or "",
    )


def _add_trip_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--trek", default="")
    p.add_argument("--destination", default="")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--pax", type=int, default=None)
    p.add_argument("--start", default="", help="Travel date or month, e.g. 2026-10-05 or October")
    p.add_argument("--hotel", default="", help="Hotel category, e.g. 3-star, luxury")
    p.add_argument("--nationality", default="")
    p.add_argument("--activities", default="", help="Comma-separated optional activities")
    p.add_argument("--name", default="", help="Customer name")
    p.add_argument("--email", default="", help="Customer email")
    p.add_argument("--markup", type=float, default=None, help="Markup %% override")


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sales_assistant", description="North Nepal Travel & Trek — sales assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show system health")

    p_est = sub.add_parser("estimate", help="Estimate trip cost")
    _add_trip_args(p_est)

    p_quote = sub.add_parser("quote", help="Build a full quotation")
    _add_trip_args(p_quote)

    p_email = sub.add_parser("email", help="Draft an email")
    p_email.add_argument("--style", required=True, choices=[s.value for s in EmailStyle])
    p_email.add_argument("--instructions", default="")
    _add_trip_args(p_email)

    p_answer = sub.add_parser("answer", help="Answer a question via research")
    p_answer.add_argument("question")

    sub.add_parser("learn", help="Show the learning report")

    p_xlsx = sub.add_parser("load-excel", help="Load a pricing .xlsx and show what was parsed")
    p_xlsx.add_argument("path")

    p_know = sub.add_parser("load-knowledge", help="Ingest a knowledge directory")
    p_know.add_argument("path")

    p_serve = sub.add_parser("serve", help="Run the API server (requires fastapi+uvicorn)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    assistant = SalesAssistant()

    if args.command == "status":
        _print(assistant.status())

    elif args.command == "estimate":
        est = assistant.estimate(_trip_from_args(args), markup_pct=args.markup)
        _print(est.to_dict())

    elif args.command == "quote":
        est, quote = assistant.quote(_trip_from_args(args), markup_pct=args.markup)
        _print({"estimate": est.to_dict(), "quotation": quote.to_dict()})

    elif args.command == "email":
        draft = assistant.write_email(args.style, trip=_trip_from_args(args),
                                      extra_instructions=args.instructions)
        print(f"Subject: {draft.subject}\n")
        print(draft.body)
        print(f"\n[used_llm={draft.used_llm}]", file=sys.stderr)

    elif args.command == "answer":
        _print(assistant.answer(args.question).to_dict())

    elif args.command == "learn":
        _print(assistant.learning.analyse().to_dict())

    elif args.command == "load-excel":
        result = assistant.load_pricing_excel(args.path)
        print(result.summary())
        _print({"populated_fields": result.populated_fields, "extras": result.extras,
                "formulas": result.formulas, "warnings": result.warnings})

    elif args.command == "load-knowledge":
        report = assistant.load_knowledge_dir(args.path)
        print(report.summary())
        if report.skipped:
            _print({"skipped": report.skipped})

    elif args.command == "serve":
        try:
            import uvicorn
        except ImportError:
            print("uvicorn is not installed. Run: pip install 'fastapi' 'uvicorn'", file=sys.stderr)
            return 1
        uvicorn.run("sales_assistant.api.app:app", host=args.host, port=args.port, reload=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
