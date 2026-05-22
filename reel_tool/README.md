# Reel Tool — North Nepal Trek

Two workflows, same `reel_input/clips/` folder:

| Tool | What it does | When to use |
| --- | --- | --- |
| **`reel.py`** | Auto-renders a finished 9:16 MP4 + IG caption | Fast: post-and-go, no manual editing |
| **`plan.py` + `voiceover.py`** | Produces a creative brief (multiple reel concepts, color-correction recipes, CapCut effect suggestions, voiceover scripts) + generates the voiceover MP3s | Polish: you finish in CapCut yourself, AI does the thinking |

Both tools target **North Nepal Trek** content. Tune `config.json` for
other niches.

## What you get from `plan.py`

- `reel_output/brief_YYYYMMDD_HHMMSS.md` — full production brief with
  4 reel concepts, each containing:
  - Hook (visual + text overlay)
  - Storyboard table (clip / in-point / duration / on-screen purpose /
    CapCut Adjust values / suggested effect)
  - Voiceover script with pacing marks
  - Music vibe, CTA, target audience
- `reel_output/brief_YYYYMMDD_HHMMSS.json` — structured data for
  `voiceover.py`
- A footage catalog showing every clip's score, mood, lighting, and
  best moment — useful for spotting what content you have

Then `voiceover.py` reads the brief and renders one MP3 per concept,
ready to drop into CapCut.

## What you get from `reel.py`

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

### 4. Get an ElevenLabs API key (only for voiceovers)

Sign up at <https://elevenlabs.io/> — free tier gives 10k chars/month
(~10 reels of voiceover). Get the key from Profile → API Keys, then:

```sh
export ELEVENLABS_API_KEY=...
```

The default voice is set to **Daniel** (Cinematic British narrator) in
`config.json`. To pick a different voice:

```sh
python3 voiceover.py --list-voices
# copy a voice_id, paste into config.json → voiceover.voice_id
```

### 5. Drop in background music

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

### 2. Pick a workflow

#### Workflow A — Creative brief + voiceover (recommended for polish)

```sh
cd reel_tool
python3 plan.py
```

This analyzes every clip (Haiku 4.5 vision), then calls Opus 4.7 as
your creative director to produce 4 reel concepts. Each concept comes
with a storyboard, per-clip CapCut color-correction recipe, suggested
effects/transitions by name, voiceover script, and music brief.

Review the markdown brief in `reel_output/`. When you've picked a
concept, render its voiceover:

```sh
python3 voiceover.py reel_output/brief_*.json --concept 1
```

(Drop `--concept N` to render all of them. Use `--dry-run` first to see
word counts and previews without burning ElevenLabs credits.)

Then open CapCut, import your clips + the voiceover MP3, and follow the
brief.

The prompt that drives this lives at `prompts/editing_brief.md` — edit
it to retune the creative direction. You can also paste it directly
into claude.ai if you want to iterate interactively before committing
to a render.

#### Workflow B — Auto-rendered reel

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
