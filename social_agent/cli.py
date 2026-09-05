"""Command-line entry point.

    python -m social_agent status                # what is configured
    python -m social_agent init                  # migrate + seed
    python -m social_agent run-once              # one polling cycle
    python -m social_agent watch                 # poll on the configured interval
    python -m social_agent serve                 # dashboard
    python -m social_agent pending               # queue, in the terminal
    python -m social_agent approve <comment_id>  # approve and publish
    python -m social_agent demo                  # offline walkthrough (simulated AI)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .app import build_application
from .config import get_settings
from .observability import configure_logging


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def cmd_status(args) -> int:
    app = build_application()
    _print(app.health())
    return 0


def cmd_init(args) -> int:
    app = build_application()
    seeded = app.knowledge.seed_if_empty()
    print(f"Database ready at {app.settings.database_path}")
    print(f"Knowledge items seeded: {seeded}")
    settings = app.agent_settings
    print(
        f"Mode: dry_run={settings.dry_run} "
        f"human_approval_required={settings.human_approval_required} "
        f"auto_reply_enabled={settings.auto_reply_enabled}"
    )
    return 0


def cmd_run_once(args) -> int:
    app = build_application()
    report = app.agent.run_cycle(platforms=args.platforms or None)
    _print(report.to_dict())
    return 1 if report.errors else 0


def cmd_watch(args) -> int:
    app = build_application()
    interval = args.interval or app.agent_settings.polling_interval_seconds
    print(f"Polling every {interval}s. Ctrl-C to stop.")
    try:
        while True:
            report = app.agent.run_cycle(platforms=args.platforms or None)
            print(
                f"[{report.request_id}] fetched={report.comments_fetched} "
                f"new={report.comments_new} pending={report.pending_approval} "
                f"escalated={report.escalated} ignored={report.ignored} "
                f"errors={len(report.errors)}"
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_serve(args) -> int:
    from .dashboard import serve
    from .dashboard.auth import AuthNotConfiguredError

    app = build_application()
    try:
        serve(app, host=args.host, port=args.port)
    except AuthNotConfiguredError as exc:
        print(f"Refusing to start: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_pending(args) -> int:
    app = build_application()
    from .dashboard.api import pending

    items = pending(app, {"limit": str(args.limit)}, {})["items"]
    if not items:
        print("Nothing pending.")
        return 0
    for item in items:
        reply = (item.get("reply") or {}).get("text") or "(no reply generated)"
        print(f"\n#{item['id']}  [{item['platform']}]  {item['author_name']}")
        print(f"  comment : {item['comment_text']}")
        print(f"  intent  : {item['intent']}  confidence {item['confidence']:.2f}  risk {item['risk_level']}")
        print(f"  reply   : {reply}")
        print(f"  reason  : {item['reason']}")
    print(f"\n{len(items)} pending. Approve with: python -m social_agent approve <id>")
    return 0


def cmd_approve(args) -> int:
    app = build_application()
    result = app.agent.approve_reply(args.comment_id, text=args.text)
    _print(result)
    return 0 if result.get("ok") else 1


def cmd_ignore(args) -> int:
    app = build_application()
    _print(app.agent.ignore_comment(args.comment_id))
    return 0


def cmd_escalate(args) -> int:
    app = build_application()
    _print(app.agent.escalate_comment(args.comment_id))
    return 0


def cmd_demo(args) -> int:
    """Offline walkthrough of the whole pipeline.

    Uses the bundled mock comments and a *scripted stand-in* for OpenAI, so the
    pipeline, guardrails, routing, approval queue and dashboard can all be
    exercised with no network access. The replies it produces are canned, not
    generated — this proves the plumbing, not the model. Every audit entry from
    this run is tagged so a simulated result can never be mistaken for a real
    one.
    """
    from .testing import build_demo_application

    app = build_demo_application()
    app.repos.audit.log("demo.started", details={"ai": "SIMULATED — not a real OpenAI call"})
    report = app.agent.run_cycle()

    print("=" * 72)
    print("DEMO RUN — mock social data, SIMULATED AI (no OpenAI call was made)")
    print("=" * 72)
    _print(report.to_dict())

    from .dashboard.api import pending as pending_view

    print("\nQueue:")
    for item in pending_view(app, {"limit": "50"}, {})["items"]:
        reply = (item.get("reply") or {}).get("text") or "—"
        print(f"  #{item['id']} [{item['platform']}] {item['comment_text'][:44]!r}")
        print(f"      → {item['intent']} ({item['confidence']:.2f}, {item['risk_level']}) : {reply}")

    from .database.models import CommentStatus

    for label, status in (("Escalated", CommentStatus.ESCALATED.value),
                          ("Ignored", CommentStatus.IGNORED.value)):
        rows = app.repos.comments.list(status=status, limit=20)
        if rows:
            print(f"\n{label}:")
            for c in rows:
                print(f"  #{c.id} [{c.platform}] {c.comment_text[:44]!r} — {c.reason[:70]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="social_agent",
        description="North Nepal Travel & Trek — Social Engagement Agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show configuration and health").set_defaults(func=cmd_status)
    sub.add_parser("init", help="create the database and seed knowledge").set_defaults(func=cmd_init)
    sub.add_parser("demo", help="offline walkthrough with simulated AI").set_defaults(func=cmd_demo)

    run = sub.add_parser("run-once", help="run a single polling cycle")
    run.add_argument("--platforms", nargs="*", choices=["facebook", "instagram"])
    run.set_defaults(func=cmd_run_once)

    watch = sub.add_parser("watch", help="poll continuously")
    watch.add_argument("--interval", type=int, help="seconds between cycles")
    watch.add_argument("--platforms", nargs="*", choices=["facebook", "instagram"])
    watch.set_defaults(func=cmd_watch)

    serve_cmd = sub.add_parser("serve", help="run the admin dashboard")
    serve_cmd.add_argument("--host")
    serve_cmd.add_argument("--port", type=int)
    serve_cmd.set_defaults(func=cmd_serve)

    pending_cmd = sub.add_parser("pending", help="list replies awaiting approval")
    pending_cmd.add_argument("--limit", type=int, default=20)
    pending_cmd.set_defaults(func=cmd_pending)

    approve_cmd = sub.add_parser("approve", help="approve and publish a reply")
    approve_cmd.add_argument("comment_id", type=int)
    approve_cmd.add_argument("--text", help="replace the reply text before publishing")
    approve_cmd.set_defaults(func=cmd_approve)

    ignore_cmd = sub.add_parser("ignore", help="mark a comment ignored")
    ignore_cmd.add_argument("comment_id", type=int)
    ignore_cmd.set_defaults(func=cmd_ignore)

    escalate_cmd = sub.add_parser("escalate", help="mark a comment escalated")
    escalate_cmd.add_argument("comment_id", type=int)
    escalate_cmd.set_defaults(func=cmd_escalate)

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
