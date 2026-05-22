#!/usr/bin/env python3
"""Generate ElevenLabs voiceovers from a brief.

Reads a brief JSON (produced by plan.py), pulls the voiceover script
for each concept, and renders it as an MP3 using the configured
ElevenLabs voice. Pacing marks in the script ([pause 0.5s], em-dashes,
*emphasis*) are translated for ElevenLabs.

Usage:
  export ELEVENLABS_API_KEY=...
  python3 voiceover.py reel_output/brief_20260521_143022.json
  python3 voiceover.py brief_*.json --concept 1   # just concept 1
  python3 voiceover.py brief_*.json --dry-run     # show what would render

  # First-time setup — list available voices and find an ID:
  python3 voiceover.py --list-voices
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_DIR = ROOT / "reel_output"


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[-\s]+", "_", s)[:60]


def clean_script_for_tts(script: str) -> str:
    """Translate brief pacing marks into ElevenLabs-friendly SSML-ish text.

    - `[pause Xs]` → break tag (ElevenLabs reads `<break time="Xs"/>`)
    - `*word*`     → strip asterisks (ElevenLabs reads punctuation/caps
                     for stress; markdown emphasis isn't supported)
    - Smart quotes and em-dashes are kept; they read naturally.
    """
    script = re.sub(
        r"\[pause\s+([\d.]+)\s*s?\]",
        lambda m: f' <break time="{m.group(1)}s"/> ',
        script,
        flags=re.IGNORECASE,
    )
    script = re.sub(r"\*([^*]+)\*", r"\1", script)
    return re.sub(r"\s+", " ", script).strip()


def load_voice_id(client, cfg_voice: dict) -> tuple[str, str]:
    """Resolve voice_id from explicit ID or by name search."""
    explicit = cfg_voice.get("voice_id", "").strip()
    if explicit:
        return explicit, cfg_voice.get("voice_name", "(by id)")

    target = cfg_voice.get("voice_name", "Daniel").lower()
    page = client.voices.search()
    for v in page.voices:
        if target in v.name.lower():
            print(f"  matched voice: {v.name} ({v.voice_id})")
            return v.voice_id, v.name

    sys.exit(
        f"error: no voice matched name '{cfg_voice['voice_name']}'.\n"
        f"Run `python3 voiceover.py --list-voices` to see options, "
        f"then set voice_id in config.json."
    )


def list_voices() -> None:
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        sys.exit("error: pip install elevenlabs")
    if not os.environ.get("ELEVENLABS_API_KEY"):
        sys.exit("error: ELEVENLABS_API_KEY not set.")
    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    page = client.voices.search()
    print(f"{'Name':<25} {'Voice ID':<25} {'Labels'}")
    print("-" * 90)
    for v in page.voices:
        labels = v.labels or {}
        label_str = ", ".join(f"{k}={v}" for k, v in labels.items())
        print(f"{v.name:<25} {v.voice_id:<25} {label_str}")


def generate(brief_path: Path, only_concept: int | None, dry_run: bool) -> None:
    cfg = json.loads(CONFIG_PATH.read_text())
    vcfg = cfg["voiceover"]

    data = json.loads(brief_path.read_text())
    concepts = data["brief"]["concepts"]

    if only_concept is not None:
        if not 1 <= only_concept <= len(concepts):
            sys.exit(f"error: --concept must be 1..{len(concepts)}")
        concepts = [concepts[only_concept - 1]]
        offset = only_concept
    else:
        offset = 1

    if dry_run:
        print(f"[dry-run] would render {len(concepts)} voiceover(s):\n")
        for i, c in enumerate(concepts, offset):
            cleaned = clean_script_for_tts(c["voiceover_script"])
            word_count = len(cleaned.split())
            est_sec = word_count / 2.5  # ~150 wpm for documentary VO
            print(f"  [{i}] {c['title']}")
            print(f"      ~{word_count} words, ~{est_sec:.1f}s narrated")
            print(f"      script: {cleaned[:120]}...\n")
        return

    if not os.environ.get("ELEVENLABS_API_KEY"):
        sys.exit(
            "error: ELEVENLABS_API_KEY not set.\n"
            "Get one at https://elevenlabs.io/, then:\n"
            "  export ELEVENLABS_API_KEY=..."
        )

    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        sys.exit("error: pip install elevenlabs")

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    voice_id, voice_name = load_voice_id(client, vcfg)
    print(f"Using voice: {voice_name} ({voice_id})\n")

    stem = brief_path.stem  # e.g. brief_20260521_143022
    for i, c in enumerate(concepts, offset):
        cleaned = clean_script_for_tts(c["voiceover_script"])
        out_path = OUTPUT_DIR / f"{stem}_c{i}_{slugify(c['title'])}.mp3"
        print(f"  [{i}] {c['title']}")
        print(f"      → {out_path.name}", end=" ", flush=True)

        try:
            audio = client.text_to_speech.convert(
                text=cleaned,
                voice_id=voice_id,
                model_id=vcfg.get("model_id", "eleven_multilingual_v2"),
                output_format="mp3_44100_128",
                voice_settings={
                    "stability": vcfg.get("stability", 0.5),
                    "similarity_boost": vcfg.get("similarity_boost", 0.85),
                    "style": vcfg.get("style", 0.3),
                    "use_speaker_boost": True,
                },
            )
            with open(out_path, "wb") as f:
                for chunk in audio:
                    f.write(chunk)
            print("ok")
        except Exception as e:  # noqa: BLE001
            print(f"FAILED: {e}", file=sys.stderr)

        time.sleep(0.5)  # be polite to the API

    print(f"\nDone. Drop the .mp3 files into CapCut as your voiceover track.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("brief", nargs="?", help="brief_*.json from plan.py")
    ap.add_argument("--concept", type=int, help="only render concept N (1-based)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would render, no API calls")
    ap.add_argument("--list-voices", action="store_true",
                    help="list available ElevenLabs voices")
    args = ap.parse_args()

    if args.list_voices:
        list_voices()
        return

    if not args.brief:
        ap.error("brief path required (or use --list-voices)")

    brief_path = Path(args.brief)
    if not brief_path.is_absolute():
        # allow `voiceover.py brief_*.json` from anywhere
        candidate = OUTPUT_DIR / brief_path.name
        if candidate.exists():
            brief_path = candidate
    if not brief_path.exists():
        sys.exit(f"error: {brief_path} not found")

    generate(brief_path, args.concept, args.dry_run)


if __name__ == "__main__":
    main()
