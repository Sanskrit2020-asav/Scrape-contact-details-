#!/usr/bin/env python3
"""Generate a creative brief from raw clips.

Scans reel_input/clips/, runs deep visual analysis on each clip with
Claude vision, then asks Opus 4.7 (the creative director) to produce
multiple complete reel concepts with voiceover scripts, CapCut color
correction recipes, and effect suggestions.

Outputs to reel_output/:
  brief_YYYYMMDD_HHMMSS.md    - human-readable brief
  brief_YYYYMMDD_HHMMSS.json  - structured data (used by voiceover.py)

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 plan.py
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
PROMPT_PATH = ROOT / "prompts" / "editing_brief.md"
CLIPS_DIR = ROOT / "reel_input" / "clips"
OUTPUT_DIR = ROOT / "reel_output"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".hevc", ".mkv", ".avi"}


# ---------- ffmpeg helpers (shared with reel.py) ----------

def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        sys.exit(f"error: `{name}` not found on PATH. Install ffmpeg first.")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def ffprobe_duration(path: Path) -> float:
    out = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nokey=1:noprint_wrappers=1",
        str(path),
    ]).stdout.strip()
    return float(out) if out else 0.0


def extract_frames(path: Path, n: int, tmpdir: Path) -> list[tuple[float, Path]]:
    """Returns list of (timestamp_sec, frame_path) tuples."""
    duration = ffprobe_duration(path)
    if duration <= 0:
        return []
    stem = path.stem.replace(" ", "_")
    frames: list[tuple[float, Path]] = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        out = tmpdir / f"{stem}_f{i}.jpg"
        try:
            run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{t:.3f}", "-i", str(path),
                "-frames:v", "1", "-q:v", "3",
                "-vf", "scale='min(1280,iw)':-2",
                str(out),
            ])
            frames.append((t, out))
        except subprocess.CalledProcessError:
            pass
    return frames


# ---------- deep clip analysis ----------

CLIP_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "visual_elements": {"type": "array", "items": {"type": "string"}},
        "mood": {"type": "string"},
        "lighting_notes": {"type": "string"},
        "best_moment_start_sec": {"type": "number"},
        "best_moment_duration_sec": {"type": "number"},
        "best_moment_description": {"type": "string"},
        "story_potential": {"type": "string"},
        "usability_score": {"type": "integer"},
    },
    "required": [
        "scene", "visual_elements", "mood", "lighting_notes",
        "best_moment_start_sec", "best_moment_duration_sec",
        "best_moment_description", "story_potential", "usability_score",
    ],
    "additionalProperties": False,
}

ANALYSIS_SYSTEM = (
    "You are a senior video producer analyzing raw footage for a Nepal "
    "trekking business. For each clip you receive sample frames from, "
    "produce a structured analysis a creative director can build reels "
    "from.\n\n"
    "Be specific: name the peak, the trail, the village, the time of day "
    "if you can tell. Don't write 'beautiful mountains' — write "
    "'Annapurna South ridge at first light'.\n\n"
    "Fields:\n"
    "- scene: one-sentence visual description\n"
    "- visual_elements: 3-8 short tags (mountain, smile, prayer_flags, "
    "tea_house, etc.)\n"
    "- mood: one or two words (awe, tired-joy, calm, anticipation)\n"
    "- lighting_notes: state of light (harsh midday / overcast flat / "
    "golden hour / blue hour / mixed indoor) — used to plan color "
    "correction\n"
    "- best_moment_start_sec / best_moment_duration_sec / "
    "best_moment_description: the strongest 2-5 second window in the clip\n"
    "- story_potential: what kind of reel concept this clip serves "
    "(hook / reveal / emotion beat / cutaway / closing)\n"
    "- usability_score: 0-10 honest score. 3 is more useful than a "
    "generous 7."
)


def analyze_clip(
    client: anthropic.Anthropic,
    clip: Path,
    duration: float,
    frames: list[tuple[float, Path]],
    cfg: dict,
) -> dict | None:
    if not frames:
        return None

    content: list = []
    for t, fp in frames:
        b64 = base64.standard_b64encode(fp.read_bytes()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    timestamps = ", ".join(f"{t:.1f}s" for t, _ in frames)
    content.append({
        "type": "text",
        "text": (
            f"Clip: {clip.name}\n"
            f"Duration: {duration:.1f}s\n"
            f"Frames sampled at: {timestamps}\n\n"
            "Return the structured analysis."
        ),
    })

    try:
        resp = client.messages.create(
            model=cfg["deep_analysis_model"],
            max_tokens=1024,
            system=ANALYSIS_SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {"type": "json_schema", "schema": CLIP_ANALYSIS_SCHEMA}
            },
        )
    except anthropic.APIError as e:
        print(f"  ! API error on {clip.name}: {e}", file=sys.stderr)
        return None

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    data["filename"] = clip.name
    data["duration_sec"] = round(duration, 2)
    return data


# ---------- creative direction ----------

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "core_idea": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "hook_text": {"type": "string"},
                    "hook_visual_clip": {"type": "string"},
                    "storyboard": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "clip_filename": {"type": "string"},
                                "start_sec": {"type": "number"},
                                "duration_sec": {"type": "number"},
                                "on_screen_purpose": {"type": "string"},
                                "on_screen_text": {"type": "string"},
                                "color_correction": {"type": "string"},
                                "effect_or_transition": {"type": "string"},
                            },
                            "required": [
                                "clip_filename", "start_sec", "duration_sec",
                                "on_screen_purpose", "on_screen_text",
                                "color_correction", "effect_or_transition",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "voiceover_script": {"type": "string"},
                    "music_vibe": {"type": "string"},
                    "gear_callouts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "string"},
                                "context": {"type": "string"},
                            },
                            "required": ["item", "context"],
                            "additionalProperties": False,
                        },
                    },
                    "cta": {"type": "string"},
                    "hashtags": {"type": "string"},
                    "estimated_duration_sec": {"type": "number"},
                },
                "required": [
                    "title", "core_idea", "target_audience", "hook_text",
                    "hook_visual_clip", "storyboard", "voiceover_script",
                    "music_vibe", "gear_callouts", "cta", "hashtags",
                    "estimated_duration_sec",
                ],
                "additionalProperties": False,
            },
        },
        "unused_clip_notes": {"type": "string"},
        "series_opportunities": {
            "type": "array", "items": {"type": "string"},
        },
    },
    "required": ["concepts", "unused_clip_notes", "series_opportunities"],
    "additionalProperties": False,
}


def plan_brief(
    client: anthropic.Anthropic,
    catalog: list[dict],
    cfg: dict,
) -> dict:
    system = PROMPT_PATH.read_text()
    system = system.replace("{concepts_per_brief}", str(cfg["concepts_per_brief"]))

    catalog_text = json.dumps(catalog, indent=2)
    user_msg = (
        f"Footage catalog ({len(catalog)} clips):\n\n"
        f"```json\n{catalog_text}\n```\n\n"
        f"Produce {cfg['concepts_per_brief']} reel concepts following your "
        f"brief format. Be concrete and specific — name peaks and trails, "
        f"prescribe exact CapCut Adjust values per clip, name CapCut effects "
        f"by their actual UI labels. The voiceover scripts will be read by a "
        f"Cinematic British narrator via ElevenLabs."
    )

    resp = client.messages.create(
        model=cfg["planning_model"],
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        output_config={"format": {"type": "json_schema", "schema": BRIEF_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        sys.exit("error: planner returned no text")
    return json.loads(text)


# ---------- markdown formatter ----------

def to_markdown(brief: dict, catalog: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# Reel Brief — North Nepal Trek\n")
    lines.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M')} from "
                 f"{len(catalog)} source clips._\n")

    for i, c in enumerate(brief["concepts"], 1):
        lines.append(f"\n---\n\n## Concept {i}: {c['title']}\n")
        lines.append(f"**Core idea.** {c['core_idea']}\n")
        lines.append(f"**Target audience.** {c['target_audience']}\n")
        lines.append(f"**Estimated length.** {c['estimated_duration_sec']}s\n")
        lines.append(f"**Hook overlay.** `{c['hook_text']}` over "
                     f"`{c['hook_visual_clip']}` for the first 1.5s\n")
        lines.append(f"**Music vibe.** {c['music_vibe']}\n")
        lines.append(f"**CTA.** {c['cta']}\n")

        lines.append("\n### Storyboard\n")
        lines.append(
            "| # | Clip | In | Dur | On-screen text | "
            "On-screen purpose | Color (CapCut Adjust) | Effect |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for j, s in enumerate(c["storyboard"], 1):
            ost = s.get("on_screen_text", "").strip()
            ost_cell = f"`{ost}`" if ost else "—"
            lines.append(
                f"| {j} | `{s['clip_filename']}` | "
                f"{s['start_sec']:.1f}s | {s['duration_sec']:.1f}s | "
                f"{ost_cell} | {s['on_screen_purpose']} | "
                f"{s['color_correction']} | {s['effect_or_transition']} |"
            )

        lines.append("\n### Voiceover script\n")
        lines.append(f"```\n{c['voiceover_script']}\n```\n")

        gear = c.get("gear_callouts", [])
        if gear:
            lines.append("\n### Gear callouts\n")
            for g in gear:
                lines.append(f"- **{g['item']}** — {g['context']}")
            lines.append("")

        hashtags = c.get("hashtags", "").strip()
        if hashtags:
            lines.append("\n### Caption hashtags\n")
            lines.append(f"```\n{hashtags}\n```\n")

    lines.append("\n---\n\n## Unused footage notes\n")
    lines.append(brief.get("unused_clip_notes", "_(none)_") + "\n")

    lines.append("\n## Series opportunities\n")
    for s in brief.get("series_opportunities", []):
        lines.append(f"- {s}")

    lines.append("\n---\n\n## Source clip catalog\n")
    lines.append("| Clip | Score | Mood | Lighting | Best moment |")
    lines.append("|---|---|---|---|---|")
    for c in sorted(catalog, key=lambda x: -x["usability_score"]):
        lines.append(
            f"| `{c['filename']}` | {c['usability_score']}/10 | "
            f"{c['mood']} | {c['lighting_notes']} | "
            f"{c['best_moment_description']} |"
        )

    return "\n".join(lines) + "\n"


# ---------- main ----------

def main() -> None:
    require_binary("ffmpeg")
    require_binary("ffprobe")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("error: ANTHROPIC_API_KEY not set.")

    cfg = json.loads(CONFIG_PATH.read_text())

    clips = sorted(
        p for p in CLIPS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )
    if not clips:
        sys.exit(
            f"error: no clips in {CLIPS_DIR}\n"
            "Drop your iPhone clips there and re-run."
        )

    print(f"Analyzing {len(clips)} clip(s) (deep visual pass)...")

    client = anthropic.Anthropic()
    catalog: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="plan_frames_") as td:
        tmpdir = Path(td)
        for i, clip in enumerate(clips, 1):
            print(f"  [{i}/{len(clips)}] {clip.name}...", end=" ", flush=True)
            duration = ffprobe_duration(clip)
            if duration < cfg["min_segment_seconds"]:
                print(f"skip (too short: {duration:.1f}s)")
                continue
            frames = extract_frames(clip, cfg["frames_per_clip"], tmpdir)
            data = analyze_clip(client, clip, duration, frames, cfg)
            if data is None:
                print("skip (analysis failed)")
                continue
            catalog.append(data)
            print(f"score={data['usability_score']}  mood={data['mood']}")

    if not catalog:
        sys.exit("error: no clips analyzed successfully.")

    print(f"\nDirecting reel concepts (Opus 4.7)...")
    brief = plan_brief(client, catalog, cfg)
    print(f"  {len(brief['concepts'])} concept(s) returned")
    for c in brief["concepts"]:
        print(f"  - {c['title']} ({c['estimated_duration_sec']:.0f}s)")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    md_path = OUTPUT_DIR / f"brief_{stamp}.md"
    json_path = OUTPUT_DIR / f"brief_{stamp}.json"

    md_path.write_text(to_markdown(brief, catalog))
    json_path.write_text(json.dumps(
        {"brief": brief, "catalog": catalog}, indent=2,
    ))

    print(f"\nDone.")
    print(f"  brief:   {md_path}")
    print(f"  data:    {json_path}")
    print(f"\nNext: review the brief, then run:")
    print(f"  python3 voiceover.py {json_path.name}")


if __name__ == "__main__":
    main()
