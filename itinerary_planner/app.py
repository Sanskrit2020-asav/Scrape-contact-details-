"""Local web app: ask client questions, suggest the best prepared itinerary."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Flask, abort, render_template, request, send_from_directory, url_for,
)

from . import ai as ai_layer
from .embeddings import available as embeddings_available, semantic_scores
from .index import ITINERARIES_DIR, get_index
from .search import ClientBrief, rank

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    itineraries = get_index()
    return render_template(
        "index.html",
        count=len(itineraries),
        semantic=embeddings_available(),
        ai=ai_layer.available(),
    )


@app.route("/plan", methods=["POST"])
def plan():
    form = request.form
    raw_days = form.get("duration_days", "").strip()
    try:
        duration = int(raw_days) if raw_days else None
    except ValueError:
        duration = None

    brief = ClientBrief(
        destination=form.get("destination", "").strip(),
        duration_days=duration,
        travelers=form.get("travelers", "").strip(),
        budget=form.get("budget", "").strip(),
        interests=form.get("interests", "").strip(),
        season=form.get("season", "").strip(),
        notes=form.get("notes", "").strip(),
    )
    itineraries = get_index()
    sem = semantic_scores(itineraries, brief.query_text())
    # Local stage: take a wider shortlist so Claude has room to re-rank.
    shortlist = rank(itineraries, brief, top_n=12, semantic=sem)

    ai_result = ai_layer.refine(brief, shortlist)
    matches = shortlist[:5]
    if ai_result and ai_result.ranked:
        by_name = {m.itinerary.filename: m for m in shortlist}
        reordered = [by_name[r["filename"]] for r in ai_result.ranked if r["filename"] in by_name]
        matches = reordered[:5] or matches
        reasons = {r["filename"]: r["reason"] for r in ai_result.ranked}
    else:
        reasons = {}

    return render_template(
        "results.html",
        brief=brief,
        matches=matches,
        total=len(itineraries),
        ai=ai_result,
        reasons=reasons,
        semantic_used=bool(sem),
    )


def _home(**extra):
    return render_template(
        "index.html",
        count=len(get_index()),
        semantic=embeddings_available(),
        ai=ai_layer.available(),
        **extra,
    )


@app.route("/reindex", methods=["POST"])
def reindex():
    get_index(force=True)
    return _home(reindexed=True)


@app.route("/sync-drive", methods=["POST"])
def sync_drive_route():
    import os

    folder = request.form.get("folder", "").strip() or os.environ.get(
        "GDRIVE_FOLDER_ID", ""
    )
    if not folder:
        return _home(drive_msg="Set GDRIVE_FOLDER_ID or enter a Drive folder ID/URL.")
    try:
        from .sync_drive import sync

        pulled = sync(folder)
        get_index(force=True)
        msg = f"Synced from Drive: {pulled} new/updated file(s) pulled and indexed."
    except SystemExit as exc:
        msg = f"Drive sync not configured: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface any Drive/auth error to the UI
        msg = f"Drive sync failed: {exc}"
    return _home(drive_msg=msg)


@app.route("/download/<path:filename>")
def download(filename: str):
    safe_root = ITINERARIES_DIR.resolve()
    target = (safe_root / filename).resolve()
    if not str(target).startswith(str(safe_root)) or not target.is_file():
        abort(404)
    return send_from_directory(safe_root, target.relative_to(safe_root).as_posix())


def main() -> None:
    import os

    Path(ITINERARIES_DIR).mkdir(parents=True, exist_ok=True)
    # Default 5050: macOS hijacks port 5000 with the AirPlay Receiver.
    port = int(os.environ.get("PORT", "5050"))
    print(f"\nItinerary planner running → http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=True)


if __name__ == "__main__":
    main()
