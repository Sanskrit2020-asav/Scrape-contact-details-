"""Local web app: ask client questions, suggest the best prepared itinerary."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Flask, abort, render_template, request, send_from_directory, url_for,
)

from .index import ITINERARIES_DIR, build_index
from .search import ClientBrief, rank

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    itineraries = build_index()
    return render_template("index.html", count=len(itineraries))


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
    itineraries = build_index()
    matches = rank(itineraries, brief, top_n=5)
    return render_template(
        "results.html", brief=brief, matches=matches, total=len(itineraries)
    )


@app.route("/reindex", methods=["POST"])
def reindex():
    build_index(force=True)
    return render_template("index.html", count=len(build_index()), reindexed=True)


@app.route("/download/<path:filename>")
def download(filename: str):
    safe_root = ITINERARIES_DIR.resolve()
    target = (safe_root / filename).resolve()
    if not str(target).startswith(str(safe_root)) or not target.is_file():
        abort(404)
    return send_from_directory(safe_root, target.relative_to(safe_root).as_posix())


def main() -> None:
    Path(ITINERARIES_DIR).mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True)


if __name__ == "__main__":
    main()
