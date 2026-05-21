#!/usr/bin/env python3
"""Auto-generate a 9:16 social-media reel from a folder of raw clips.

Pipeline:
  1. Scan reel_input/clips/ for video files
  2. For each clip: extract sample frames, ask Claude (vision) to score it
     for the configured niche and pick the strongest segment
  3. Pick the top-scoring clips up to target duration
  4. ffmpeg: trim, scale/crop to 9:16, concat, overlay AI-written hook,
     mix background music
  5. Write the post caption + hashtags next to the .mp4

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 reel.py
"""

from __future__ import annotations

import base64
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
CLIPS_DIR = ROOT / "reel_input" / "clips"
MUSIC_DIR = ROOT / "reel_input" / "music"
OUTPUT_DIR = ROOT / "reel_output"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".hevc", ".mkv", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}


# ---------- data ----------

@dataclass
class ClipScore:
    path: Path
    duration: float
    score: int
    tags: list[str]
    seg_start: float
    seg_duration: float
    reason: str


# ---------- shell helpers ----------

def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        sys.exit(
            f"error: `{name}` not found on PATH. Install ffmpeg first.\n"
            f"  macOS:   brew install ffmpeg\n"
            f"  Ubuntu:  sudo apt install ffmpeg\n"
            f"  Windows: https://ffmpeg.org/download.html"
        )


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def ffprobe_duration(path: Path) -> float:
    out = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nokey=1:noprint_wrappers=1",
        str(path),
    ]).stdout.strip()
    return float(out) if out else 0.0


def extract_frames(path: Path, n: int, tmpdir: Path) -> list[Path]:
    duration = ffprobe_duration(path)
    if duration <= 0:
        return []
    stem = path.stem.replace(" ", "_")
    frames: list[Path] = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        out = tmpdir / f"{stem}_f{i}.jpg"
        try:
            run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{t:.3f}", "-i", str(path),
                "-frames:v", "1", "-q:v", "3",
                "-vf", "scale='min(1024,iw)':-2",
                str(out),
            ])
            frames.append(out)
        except subprocess.CalledProcessError:
            pass
    return frames


# ---------- claude scoring ----------

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "best_frame_index": {"type": "integer"},
        "best_segment_duration": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["score", "tags", "best_frame_index", "best_segment_duration", "reason"],
    "additionalProperties": False,
}


def score_clip(
    client: anthropic.Anthropic,
    clip_path: Path,
    duration: float,
    frame_paths: list[Path],
    cfg: dict,
) -> ClipScore | None:
    if not frame_paths:
        return None

    content: list = []
    for fp in frame_paths:
        b64 = base64.standard_b64encode(fp.read_bytes()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    content.append({
        "type": "text",
        "text": (
            f"Clip: {clip_path.name}\n"
            f"Duration: {duration:.1f}s\n"
            f"Frames shown: {len(frame_paths)} (evenly spaced through the clip).\n\n"
            "Score this clip 0-10 for the niche described in your system prompt.\n"
            "Pick the frame index (0-based) marking the start of the strongest "
            f"segment, and recommend a segment duration between "
            f"{cfg['min_segment_seconds']} and {cfg['max_segment_seconds']} seconds."
        ),
    })

    system_prompt = (
        f"You are a social-media editor for a {cfg['niche']}.\n\n"
        f"{cfg['scoring_guidance']}\n\n"
        "For each clip you see sample frames from, return:\n"
        "- score (0-10, where 10 is scroll-stopping)\n"
        "- tags: short visual descriptors\n"
        "- best_frame_index: which 0-indexed frame marks the start of the strongest segment\n"
        "- best_segment_duration: recommended seconds for that segment\n"
        "- reason: one short sentence explaining the score\n\n"
        "Be honest about weak clips - a 3 is more useful than a generous 7."
    )

    try:
        resp = client.messages.create(
            model=cfg["scoring_model"],
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": SCORE_SCHEMA}},
        )
    except anthropic.APIError as e:
        print(f"  ! API error scoring {clip_path.name}: {e}", file=sys.stderr)
        return None

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    n_frames = len(frame_paths)
    idx = max(0, min(n_frames - 1, int(data["best_frame_index"])))
    seg_start = duration * (idx + 0.5) / n_frames - 1.0
    seg_start = max(0.0, seg_start)

    seg_dur = float(data["best_segment_duration"])
    seg_dur = max(cfg["min_segment_seconds"], min(cfg["max_segment_seconds"], seg_dur))
    if seg_start + seg_dur > duration:
        seg_dur = max(0.5, duration - seg_start)

    return ClipScore(
        path=clip_path,
        duration=duration,
        score=int(data["score"]),
        tags=list(data.get("tags", [])),
        seg_start=seg_start,
        seg_duration=seg_dur,
        reason=str(data.get("reason", "")),
    )


# ---------- selection ----------

def pick_best(scored: list[ClipScore], cfg: dict) -> list[ClipScore]:
    eligible = [s for s in scored if s.score >= cfg["min_score_to_include"]]
    eligible.sort(key=lambda s: s.score, reverse=True)

    target = float(cfg["target_duration_seconds"])
    max_clips = int(cfg["max_clips_in_reel"])

    chosen: list[ClipScore] = []
    total = 0.0
    for s in eligible:
        if len(chosen) >= max_clips:
            break
        if total + s.seg_duration > target + cfg["max_segment_seconds"]:
            continue
        chosen.append(s)
        total += s.seg_duration
        if total >= target:
            break

    # if we didn't reach target, top up regardless of cap
    if total < target * 0.7 and eligible:
        for s in eligible:
            if s in chosen or len(chosen) >= max_clips:
                continue
            chosen.append(s)
            total += s.seg_duration
            if total >= target:
                break

    # shuffle a bit so reel doesn't always lead with the same shot
    if len(chosen) > 2:
        head = chosen[0]
        rest = chosen[1:]
        random.shuffle(rest)
        chosen = [head, *rest]

    return chosen


# ---------- claude writing ----------

def write_hook_and_caption(
    client: anthropic.Anthropic,
    selected: list[ClipScore],
    cfg: dict,
) -> tuple[str, str]:
    tag_summary = ", ".join(sorted({t for s in selected for t in s.tags})[:25])
    reasons = "\n".join(f"- {s.reason}" for s in selected if s.reason)

    schema = {
        "type": "object",
        "properties": {
            "hook": {"type": "string"},
            "caption": {"type": "string"},
            "hashtags": {"type": "string"},
        },
        "required": ["hook", "caption", "hashtags"],
        "additionalProperties": False,
    }

    prompt = (
        f"Write the social copy for a 9:16 reel promoting a {cfg['niche']}.\n\n"
        f"Visual content tags: {tag_summary}\n\n"
        f"Editor notes per clip:\n{reasons}\n\n"
        "Return three fields:\n"
        "1. hook: a 3-5 word punchy overlay that appears in the first 1.5s. "
        "All caps, no quotes, no emoji, no punctuation except !\n"
        "2. caption: 2-4 sentences, conversational and warm, ending with a "
        "soft CTA inviting the reader to DM for trekking inquiries.\n"
        "3. hashtags: 18-25 mixed hashtags (Nepal-trekking specific + broader "
        "travel/adventure), space-separated, lowercase where idiomatic, "
        "no commas."
    )

    resp = client.messages.create(
        model=cfg["writing_model"],
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        return "ADVENTURE AWAITS", "Come trek with us in Nepal. DM to plan your trip."
    data = json.loads(text)
    return data["hook"], f"{data['caption']}\n\n{data['hashtags']}"


# ---------- ffmpeg assembly ----------

def find_font(cfg: dict) -> str | None:
    for cand in cfg["hook_font_path_candidates"]:
        if Path(cand).exists():
            return cand
    return None


def escape_drawtext(s: str) -> str:
    # drawtext needs : and ' and \ escaped
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")


def build_reel(
    selected: list[ClipScore],
    hook_text: str,
    cfg: dict,
    out_path: Path,
) -> None:
    music_files = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTS]
    music_path = random.choice(music_files) if music_files else None

    inputs: list[str] = []
    for s in selected:
        inputs += ["-i", str(s.path)]
    if music_path:
        inputs += ["-i", str(music_path)]

    res = cfg["output_resolution"]
    w, h = res.split("x")
    scale_crop = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1,fps={cfg['output_fps']}"
    )

    filter_parts: list[str] = []
    for i, s in enumerate(selected):
        filter_parts.append(
            f"[{i}:v]trim=start={s.seg_start:.3f}:duration={s.seg_duration:.3f},"
            f"setpts=PTS-STARTPTS,{scale_crop}[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(len(selected)))
    filter_parts.append(
        f"{concat_inputs}concat=n={len(selected)}:v=1:a=0[vc]"
    )

    font_path = find_font(cfg)
    if font_path and hook_text.strip():
        font_arg = f"fontfile='{font_path}'"
        text = escape_drawtext(hook_text.strip())
        hook_dur = cfg["hook_duration_seconds"]
        drawtext = (
            f"[vc]drawtext={font_arg}:text='{text}':"
            f"fontsize={cfg['hook_font_size']}:fontcolor=white:"
            f"borderw=6:bordercolor=black@0.85:"
            f"x=(w-text_w)/2:y=h*0.18:"
            f"enable='between(t,0,{hook_dur})'[vout]"
        )
        filter_parts.append(drawtext)
        v_label = "[vout]"
    else:
        v_label = "[vc]"
        if not font_path:
            print("  ! no font found, skipping hook overlay", file=sys.stderr)

    map_args = ["-map", v_label]
    if music_path:
        music_idx = len(selected)
        total_dur = sum(s.seg_duration for s in selected)
        filter_parts.append(
            f"[{music_idx}:a]atrim=duration={total_dur:.3f},"
            f"asetpts=PTS-STARTPTS,volume={cfg['music_volume']},"
            f"afade=t=out:st={max(0, total_dur - 1):.3f}:d=1[aout]"
        )
        map_args += ["-map", "[aout]"]

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-stats",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        *map_args,
        "-c:v", "libx264", "-preset", "medium",
        "-crf", str(cfg["video_bitrate_crf"]),
        "-pix_fmt", "yuv420p",
        "-r", str(cfg["output_fps"]),
    ]
    if music_path:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", str(out_path)]

    subprocess.run(cmd, check=True)


# ---------- main ----------

def main() -> None:
    require_binary("ffmpeg")
    require_binary("ffprobe")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "error: ANTHROPIC_API_KEY not set.\n"
            "Get one at https://console.anthropic.com/ then:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )

    cfg = json.loads(CONFIG_PATH.read_text())

    clips = sorted(
        p for p in CLIPS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )
    if not clips:
        sys.exit(
            f"error: no video files in {CLIPS_DIR}\n"
            "Drop .mov / .mp4 files there and re-run."
        )

    print(f"Found {len(clips)} clip(s) in {CLIPS_DIR.name}/")

    client = anthropic.Anthropic()
    scored: list[ClipScore] = []

    with tempfile.TemporaryDirectory(prefix="reel_frames_") as td:
        tmpdir = Path(td)
        for i, clip in enumerate(clips, 1):
            print(f"  [{i}/{len(clips)}] scoring {clip.name}...", end=" ", flush=True)
            duration = ffprobe_duration(clip)
            if duration < cfg["min_segment_seconds"]:
                print(f"skip (too short: {duration:.1f}s)")
                continue
            frames = extract_frames(clip, cfg["frames_per_clip"], tmpdir)
            s = score_clip(client, clip, duration, frames, cfg)
            if s is None:
                print("skip (no score)")
                continue
            scored.append(s)
            print(f"score={s.score}  tags={','.join(s.tags[:3])}")

    if not scored:
        sys.exit("error: no clips could be scored.")

    selected = pick_best(scored, cfg)
    if not selected:
        sys.exit(
            f"error: no clips met the min_score_to_include threshold "
            f"({cfg['min_score_to_include']}). Lower it in config.json or "
            f"add better clips."
        )

    total = sum(s.seg_duration for s in selected)
    print(f"\nSelected {len(selected)} clip(s), total {total:.1f}s:")
    for s in selected:
        print(f"  {s.score}/10  {s.path.name}  "
              f"[{s.seg_start:.1f}s +{s.seg_duration:.1f}s]  {s.reason}")

    print("\nWriting hook + caption...")
    hook, caption = write_hook_and_caption(client, selected, cfg)
    print(f"  hook: {hook}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    video_path = OUTPUT_DIR / f"reel_{stamp}.mp4"
    caption_path = OUTPUT_DIR / f"reel_{stamp}.txt"

    print(f"\nAssembling reel → {video_path.name}")
    build_reel(selected, hook, cfg, video_path)

    caption_path.write_text(f"{hook}\n\n{caption}\n")

    print(f"\nDone.")
    print(f"  video:   {video_path}")
    print(f"  caption: {caption_path}")


if __name__ == "__main__":
    main()
