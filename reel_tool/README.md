# Auto Reel Tool

Generate a 9:16 Instagram/TikTok reel from a folder of raw clips. Claude
vision scores each clip for the configured niche, picks the strongest
moments, and ffmpeg stitches them into a polished video with a punchy
text hook, background music, and an AI-written caption + hashtags.

Built for **North Nepal Trek** but the niche is configurable — edit
`config.json` to retarget for fitness, food, real estate, etc.

## What you get per run

- `reel_output/reel_YYYYMMDD_HHMMSS.mp4` — 1080×1920, ready to upload
- `reel_output/reel_YYYYMMDD_HHMMSS.txt` — caption + hashtags to paste

## One-time setup

### 1. Install ffmpeg

```sh
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows: download from https://ffmpeg.org/download.html and add to PATH
```

### 2. Install Python deps

```sh
cd reel_tool
pip3 install -r requirements.txt
```

### 3. Get an Anthropic API key

Sign up at <https://console.anthropic.com/>, create a key, then:

```sh
export ANTHROPIC_API_KEY=sk-ant-...
```

(Add that line to your `~/.zshrc` or `~/.bashrc` to make it persistent.)

### 4. Drop in background music

Copy 1+ royalty-free `.mp3` / `.m4a` tracks into `reel_input/music/`.
Suggested sources: YouTube Audio Library, Epidemic Sound (paid),
Pixabay Music. The tool picks one at random per run.

## Every-time workflow

### 1. Get clips off your iPhone

The tool reads from `reel_input/clips/`. You have to put videos there
yourself — iPhone local storage doesn't auto-sync without iCloud.

**Easiest path (Mac):**

1. Plug iPhone into Mac with cable, unlock phone, tap "Trust" if prompted
2. Open the **Image Capture** app (built into macOS)
3. Select your iPhone in the sidebar
4. Select the videos you want (sort by date to find recent ones)
5. Set "Import To" to `reel_tool/reel_input/clips/`
6. Click "Import" (or "Import All")

**Mac alternative — AirDrop:**

Select videos in the Photos app on your iPhone, tap Share → AirDrop →
your Mac. Then move them from `~/Downloads` into `reel_input/clips/`.

**Windows path:**

1. Install [Apple Devices](https://apps.microsoft.com/detail/9np83lwlpz9k) from the Microsoft Store
2. Plug iPhone in, trust the computer
3. In File Explorer: `This PC → Apple iPhone → Internal Storage → DCIM`
4. Copy `.MOV` files into `reel_tool\reel_input\clips\`

### 2. Run it

```sh
cd reel_tool
python3 reel.py
```

You'll see something like:

```
Found 23 clip(s) in clips/
  [1/23] scoring IMG_0421.MOV... score=8  tags=mountain,sunrise,scenic
  [2/23] scoring IMG_0422.MOV... score=4  tags=indoor,blurry
  ...

Selected 7 clip(s), total 27.4s:
  9/10  IMG_0418.MOV  [2.3s +4.0s]  Stunning sunrise over Annapurna
  8/10  IMG_0421.MOV  [1.1s +3.5s]  Smiling group at viewpoint
  ...

Writing hook + caption...
  hook: WAKE UP HERE

Assembling reel → reel_20260521_143022.mp4
Done.
  video:   /path/to/reel_output/reel_20260521_143022.mp4
  caption: /path/to/reel_output/reel_20260521_143022.txt
```

### 3. Post it

Open the `.txt` next to the video, copy the caption + hashtags, then
upload the `.mp4` to Instagram/TikTok and paste the caption.

## Tuning

All knobs live in `config.json`. The ones you'll touch most:

| Setting | What it does |
| --- | --- |
| `niche` | One-line description of your business |
| `scoring_guidance` | What makes a "good" clip for you — rewrite this if Claude picks the wrong stuff |
| `target_duration_seconds` | Reel length (Instagram caps at 90s, sweet spot is ~25-35s) |
| `min_score_to_include` | Raise to 7-8 if too many mediocre clips slip in; lower to 4-5 if you don't have many great clips |
| `max_clips_in_reel` | Cap on how many cuts. More cuts = punchier, but harder to follow |
| `music_volume` | 0.0 to 1.0. Lower if music drowns out hook impact |
| `hook_font_size` | Bump to 100 if hook looks small on phone |

## Cost

Each run hits the Claude API:

- ~$0.001 per clip scored (Haiku 4.5 with 4 frames)
- ~$0.005 per reel for hook + caption (Opus 4.7, one call)

So a run with 30 clips ≈ $0.03 in API costs. Negligible.

## Troubleshooting

**"no font found, skipping hook overlay"** — The tool tries a list of
common system font paths. Add your font's full path to
`hook_font_path_candidates` in `config.json`. On macOS, look in
`/System/Library/Fonts/` or `/Library/Fonts/`.

**Clips look washed-out / wrong colors** — Likely HDR / Dolby Vision
footage from iPhone. Toggle iPhone Camera settings →
**Record Video → HDR Video OFF**, then re-shoot. Existing HDR clips
can be tonemapped — ask Claude to add a tonemap filter to `scale_crop`.

**Reel is silent** — Drop a `.mp3` into `reel_input/music/`.

**"too short" skips on otherwise-good clips** — Reduce
`min_segment_seconds` in `config.json` (default 2.0).

**Want different orientation** — Change `output_resolution` to
`1080x1080` (square) or `1920x1080` (landscape).
