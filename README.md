# Zucker Editor

Simple three-step app for turning Zucker Mixer audio plus raw camera clips into a finished video.

## Uso

1. **Drop everything**
   - Escribe `Nombre del vídeo`, or keep the default `Jam YYYY-MM-DD`.
   - Drag videos, master audio, and optionally `songs.json` into `Arrastra aquí tus archivos`.
   - Files already in `~/ZuckerVideos/Inbox/` are imported automatically.
   - You can continue once there is at least one video and one master audio. Without `songs.json`, Zucker Editor makes one continuous video.

2. **Choose edit type**
   - `YouTube`: full-length 16:9 edit.
   - `Instagram`: vertical 9:16 short, about 45 seconds.
   - `TikTok`: vertical 9:16 short, about 20 seconds.
   - If `songs.json` has multiple songs, choose the song. YouTube also allows `Todas`.

3. **Wait for the result**
   - The app runs ingest, sync, cut, edit, and export automatically.
   - Progress is shown in plain Spanish, including ffmpeg export progress and ETA.
   - On success, preview the video, reveal it in Finder, or start another.
   - On failure, use `Ver detalles técnicos` for the log tail.

Creative logic in this version:

- YouTube builds a first real multicam edit: bar-aligned cuts, 2-4 bar segment lengths, confidence-weighted camera rotation, and no silent fallback to bad sync.
- Clips below the sync confidence threshold, clips with unstable second-pass sync verification, clips without usable audio, and invalid videos are excluded from multicam. If no clip qualifies, the job fails with per-clip diagnostics.
- Manual sync overrides in `/advanced` still rescue a clip when the user has verified it by hand.
- Instagram/TikTok use a center crop and fixed short excerpt sourced from the best-covered synced stretch.
- All exports include the Zucker watermark, intro/outro fades, subtle per-source color normalization, and title overlays when the active ffmpeg build supports `drawtext`.
- Instagram/TikTok highlight selection, automatic subject tracking, and more advanced creative pacing are still placeholders.

The old technical UI is still available for debugging at:

```text
/advanced
```

## Development

This version implements the architecture, persistence, pipeline contracts, Flask API, media range serving, a real audio-based sync stage, the three-step wizard, YouTube beat-cut multicam v1, and ffmpeg export with streamed progress.

### Definition Of Done

For any task that changes app behavior, finish by rebuilding `dist/Zucker Editor.app` and `dist/Zucker Editor.dmg`, then verify the bundled `build_info.json` reports the current git commit.

### Quick Start

```bash
cd /Users/macbookair/zucker_videos
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python app.py --dev
```

Desktop mode:

```bash
.venv/bin/python app.py
```

Open an existing project on startup:

```bash
.venv/bin/python app.py --project /path/to/MyJam_2026-07-13.zuckervid
```

`--dev` runs Flask only with CORS enabled. It prefers port `5179` and falls back automatically if that port is already in use. Desktop mode binds an OS-assigned free port and passes the actual URL into pywebview.

The app creates `~/ZuckerVideos/Inbox/` on first launch. The path lives in the global config:

```text
~/ZuckerVideos/config.json
```

This is global app configuration, not project state.

## Input Workflows

Zucker Editor supports three input methods at the same time.

Watched Inbox:

- `GET /api/v1/inbox` scans `~/ZuckerVideos/Inbox/`.
- The scan runs on app launch, project open/create, the Inputs `Rescan` button, and every few seconds while the Inputs screen is visible.
- `.wav`, `.mp3`, `.flac`, `.aiff`, and `.aif` are master candidates.
- `.json` files are accepted as songs only when they contain a top-level `songs` array.
- Only common camera containers (`.mp4`, `.mov`, `.m4v`, `.mts`, `.m2ts`, `.avi`, `.mkv`, `.3gp`, `.3g2`, `.mpg`, `.mpeg`, `.ts`, `.mxf`) can become video clips.
- Video candidates must also pass `ffprobe` validation: a real camera-video codec, duration over 2 seconds, and at least 320x240 resolution.
- `.txt`, `.lrv`, `.thm`, `.xml`, `.srt`, hidden files, and `.DS_Store` are ignored with a visible reason; `.lrv` is treated as a low-resolution camera sidecar, not a usable clip.
- Raw `.insv`/`.insp` Insta360 files are ignored with instructions to export an MP4 from Insta360 Studio first.
- 2:1 equirectangular MP4/MOV exports are accepted and prepared as a fixed 16:9 virtual camera view.
- Unsupported files are shown under Ignored with a note.
- When multiple master candidates are present, the wizard defaults to the longest audio file and shows a small selector so you can change it.

Ingest normalization:

- Ingest transcodes each valid camera clip once into `cache/normalized/{clip_id}.mp4`.
- Downstream sync, thumbnails, previews, cut, and export consume only the normalized MP4 path.
- Normalized video is H.264 `yuv420p`, even dimensions, constant frame rate at the detected dominant FPS, with rotation baked in.
- Audio is normalized to AAC stereo 48 kHz.
- HDR/10-bit sources use `zscale -> tonemap=hable -> bt709 -> yuv420p`; regular SDR sources use even-dimension scale plus `yuv420p`.
- Equirectangular clips use ffmpeg `v360` equirect-to-flat with yaw `0`, pitch `0`, h_fov `100`, output `1920x1080`. TODO: expose per-clip yaw/pitch controls later.

Native Pickers:

- Desktop mode exposes pywebview bridge methods for `Choose master...`, `Choose songs.json...`, `Add videos...`, `Add video folder...`, and `Reveal in Finder`.
- These dialogs run in the app process, not through HTTP.
- Picker buttons surface a toast if the pywebview bridge is missing, still loading, or returns an invalid result.
- File type filters are intentionally not passed to pywebview; files are validated by the registration/classification path after selection. This avoids macOS/pywebview filter syntax failures that can make native dialogs silently fail.
- In `--dev` browser mode, picker buttons are disabled and explain that they are desktop-only.

Drag And Drop:

- The Inputs screen has one drop zone.
- In desktop mode, dropped local paths are classified like inbox files; dropped folders are recursed.
- In browser dev mode, dropped files and folder entries are recursively traversed with `webkitGetAsEntry()`, supported media/JSON files are uploaded to `inputs/uploads/` through `POST /api/v1/inputs/upload`, then classified.
- Browser uploads show per-request progress and have a 512 MB per-file/server request limit. For large phone videos, put files in `~/ZuckerVideos/Inbox/` or use the desktop app's folder picker so the app registers local paths instead of uploading bytes through the browser.
- Global drag/drop default navigation is prevented so stray drops do not navigate away.

By default, inputs are referenced by absolute path plus size and mtime. Turn on `Copy into project` per project to copy selected files into `inputs/` before registering them.

If a referenced file is later moved or deleted, `GET /api/v1/project` marks that input record with `missing: true`; the Inputs screen shows a missing-file badge instead of crashing.

Input requirements by stage:

- `ingest` needs at least one video. Master audio and `songs.json` are optional at this point.
- `sync` needs ingest done and master audio registered.
- In `/advanced`, manual `cut`, `edit`, and `export` debugging expect `songs.json`. Export it from Zucker Mixer; it must be JSON with a top-level `songs` array.
- When a master is registered, Zucker Editor scans the master's folder and the Inbox for valid songs JSON files and suggests candidates in the Inputs screen. Suggestions require one click; they are never registered silently.

## Architecture

```text
app.py
  | parses args, chooses ports, starts Flask or pywebview
  v
server/api.py
  | thin /api/v1 JSON routes
  | owns current Project + PipelineEngine process state
  v
core/project.py
  | project.json schema, atomic writes, input file signatures
  v
core/engine.py
  | dependency planning, cache hits, stale/blocked propagation
  | one background worker, per-stage progress
  v
core/stages/*
  | Stage interface implementations
  | ingest is real validation/probe
  | sync is real onset-correlation matching
  | cut plans usable synced coverage
  | edit builds beat-aligned YouTube multicam plans
  | export renders edit-plan segments with ffmpeg progress

server/media.py
  | Range-aware file serving for browser audio/video seeking

web/
  | plain HTML/CSS/JS shell
  | renders project, inputs, stages, and progress from API state only
```

Plain browser JavaScript is used deliberately: the current UI is a shell for pipeline development, and no frontend framework decision needs to become architecture. A framework can be introduced later behind the same `/api/v1` contract.

## Project Folder

```text
MyJam_2026-07-13.zuckervid/
├── project.json
├── inputs/
│   ├── master.wav
│   ├── songs.json
│   └── videos/
├── cache/
│   └── logs/
├── artifacts/
└── exports/
```

`project.json` is the single source of truth. It stores schema version, timestamps, input records with `path`, `size`, and `mtime`, stage state, stage outputs, errors, fingerprints, and default settings.

Writes use temp-file plus `os.replace`, so a crash before replace leaves the previous `project.json` intact.

## Stage Contract

Every stage implements:

```python
name: str
dependencies: list[str]
inputs_fingerprint(project) -> str
run(project, progress_callback) -> dict[str, Any]
outputs(project) -> dict[str, str]
```

The engine runs dependencies first, skips stages whose stored fingerprint matches the current fingerprint, marks downstream stages stale when upstream output changes, and marks downstream stages blocked when an upstream stage fails.

Progress callbacks use:

```python
progress_callback(percent: int, message: str)
```

Stage logs are written to:

```text
cache/logs/{stage}.log
```

## Add A New Stage

1. Create `core/stages/my_stage.py`.
2. Subclass `Stage`.
3. Set `name` and `dependencies`.
4. Include every relevant input, setting, and dependency fingerprint in `inputs_fingerprint`.
5. Write artifacts with `write_artifact_json` or another atomic write.
6. Return artifact paths from `run`.
7. Return expected paths from `outputs`.
8. Register the stage in `PipelineEngine.__init__`.
9. Add a focused engine/API test.

## Porting The Existing Prototypes

Sync stage:

- Extracts clip audio to `cache/audio/{clip}.wav` as mono 22050 Hz WAV.
- Reuses extracted audio when the cached WAV is newer than the source video.
- Computes onset-strength envelopes with `librosa.onset.onset_strength`, `hop_length=512`.
- Normalizes each envelope to zero mean and unit standard deviation.
- Caches master and clip envelopes under `cache/envelopes/`.
- Correlates `master_env` and `clip_env` with `scipy.signal.correlate`.
- Uses `mode="valid"` when the clip envelope is no longer than the master envelope.
- Uses `mode="full"` for clips longer than the master and clamps negative offsets to zero.
- Writes `artifacts/sync_map.json` atomically.

Confidence is a robust z-score against the correlation curve:

```text
confidence = (peak - median(correlation)) / (1.4826 * MAD(correlation) + 1e-9)
```

`MAD` is median absolute deviation. Larger values mean the best match stands out more clearly from the rest of the possible offsets. The default `settings.sync.confidence_threshold` is `6.0`; clips below it are marked `low_confidence: true`.

Sync map shape:

```json
{
  "schema_version": 1,
  "master_duration_sec": 3600.0,
  "clips": {
    "clip_id": {
      "offset_sec": 1823.412,
      "duration_sec": 95.3,
      "confidence": 8.4,
      "low_confidence": false,
      "manual_override": false
    }
  }
}
```

Per-clip failures do not fail the stage. A clip with no audio gets `no_audio: true`; unreadable clips get an `error` string in their clip entry.

Manual overrides:

- `POST /api/v1/stages/sync/override` with `clip_id` and `offset_sec`.
- `POST /api/v1/stages/sync/override/clear` with `clip_id`.
- Overrides keep `detected_offset_sec`, set `manual_override: true`, leave sync itself done, and mark `cut`, `edit`, and `export` stale.
- Re-running sync preserves overrides for clips whose file signature is unchanged while refreshing the detected value.

Sync Review UI:

- Shows thumbnails, filenames, offsets, confidence, and badges.
- Draws a master timeline with clip blocks and optional song boundaries parsed from `songs.json`.
- Generates 10-second verification previews on demand under `cache/previews/`.
- Serves previews through `/api/v1/media/cache/...`, which uses the same Range-capable media responder as source media.

Porting the existing sync prototype:

- Move onset extraction and cross-correlation code into `core/stages/sync.py`.
- Keep the public artifact as `artifacts/sync_map.json`.
- Include master file signature, video file signatures, extracted audio cache signatures, and sync settings in `inputs_fingerprint`.
- Use `progress_callback` around expensive phases: extraction, envelope calculation, correlation, confidence scoring, write artifact.
- Store regenerable extracted audio/envelopes under `cache/`.

Cut prototype:

- Move coverage planning into `core/stages/cut.py`.
- Keep the public artifact as `artifacts/coverage.json`; put generated segments under `artifacts/cut/`.
- Include `sync` fingerprint, songs file signature, video signatures, and cut settings in `inputs_fingerprint`.
- Leave final rendering decisions for `edit` and `export`.

Edit/export prototype:

- `sync` verifies each detected offset with independent first-third/last-third correlations. A disagreement above 150 ms marks the clip `unstable_sync`.
- `edit` writes `artifacts/beats.json` and `artifacts/edit_plan.json`.
- YouTube uses `librosa.beat.beat_track` plus onset/spectral novelty to estimate bars and section changes, then plans bar-aligned multicam cuts.
- Instagram/TikTok still use the current fixed middle excerpt as placeholder creative logic.
- `export` renders per-segment MP4 intermediates with matching master-audio slices, concatenates them, and keeps output under 1.9 GB by computing a target bitrate from duration.
- Export tries `h264_videotoolbox` first on macOS and falls back to `libx264` when hardware encoding is unavailable.
- Export applies a bottom-right watermark from `assets/watermark.png` when present, otherwise `logo_editor_blue.png`.
- Text overlays use `band_name` and `handle` from `~/ZuckerVideos/config.json`; if ffmpeg lacks `drawtext`, export skips text rather than failing.
- Frontend JavaScript runtime errors are logged to `~/ZuckerVideos/logs/frontend.log`.

## API

All JSON routes are under `/api/v1`.

```text
POST /api/v1/project
GET  /api/v1/project
POST /api/v1/project/open
POST /api/v1/inputs/videos
POST /api/v1/inputs/master
GET  /api/v1/inbox
POST /api/v1/inbox/register
POST /api/v1/inputs/upload
POST /api/v1/inputs/classify-paths
GET  /api/v1/inputs/suggestions/songs
POST /api/v1/wizard/upload
POST /api/v1/wizard/songs
POST /api/v1/wizard/prepare
POST /api/v1/wizard/start
GET  /api/v1/wizard/status
GET  /api/v1/wizard/result
POST /api/v1/settings/inputs
GET  /api/v1/app/config
POST /api/v1/stages/{name}/run
GET  /api/v1/stages/status
GET  /api/v1/artifacts/{stage}
POST /api/v1/stages/sync/override
POST /api/v1/stages/sync/override/clear
GET  /api/v1/stages/sync/preview/{clip_id}
GET  /api/v1/stages/sync/thumbnail/{clip_id}
GET  /api/v1/media/videos/{index}
GET  /api/v1/media/master/0
GET  /api/v1/media/cache/{relative_path}
```

Errors use:

```json
{"error": {"code": "bad_request", "message": "message"}}
```

Media endpoints support HTTP `Range` requests and return `206 Partial Content` with `Content-Range`, which is required for browser seeking.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Coverage includes project roundtrip and atomic-write behavior, engine dependency/cache/staleness/failure behavior, API project and stage polling, the stage readiness matrix, inbox classification, register-from-inbox, upload fallback and upload-size errors, songs-json suggestions, missing input detection, sync confidence and offset math, manual overrides, error envelopes, media `Range` responses, and a marked slow generated-media sync integration test.

## Building The macOS App

PyInstaller is used for packaging because this app is already a Python Flask + pywebview process. Briefcase would add an app-template layer without improving the current runtime model.

Build prerequisites:

```bash
cd /Users/macbookair/zucker_videos
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-build.txt
```

Icon generation:

- Source/swap point: `assets/logo_mixer.png`
- Generated blue logo: `assets/logo_editor_blue.png`
- Generated app icon: `assets/icon.icns`

Build:

```bash
tools/build_app.sh
```

Outputs:

```text
dist/Zucker Editor.app
dist/Zucker Editor.dmg
```

The build script ad-hoc signs the local app with:

```bash
codesign --force --deep -s - "dist/Zucker Editor.app"
```

Distribution signing and notarization are out of scope.

ffmpeg and ffprobe are not bundled. On startup Zucker Editor searches `PATH`, `/opt/homebrew/bin`, and `/usr/local/bin`, stores resolved paths in `~/ZuckerVideos/config.json`, and shows an in-app dialog when missing:

```bash
brew install ffmpeg
```

Bundle-safe paths:

- Static UI resources are loaded from `sys._MEIPASS` when running from the PyInstaller bundle.
- Logs, config, and the Inbox remain under `~/ZuckerVideos/`.
- First launch creates `~/ZuckerVideos/Inbox/` and opens on the Project screen.

Bundle smoke checklist:

1. Launch `dist/Zucker Editor.app` from Finder, without a terminal.
2. Confirm `~/ZuckerVideos/Inbox/` exists.
3. Create or open a `.zuckervid` project.
4. Drop a folder of videos plus the master audio into the wizard.
5. Confirm and pick YouTube, Instagram, or TikTok.
6. Wait for the progress screen to finish.
7. Play the exported preview.

Troubleshooting:

- If media probing fails immediately, install ffmpeg with `brew install ffmpeg`, then relaunch.
- If a dropped file appears under Ignored, the note explains why. Text reports and camera sidecars are intentionally excluded from sync/export.
- If a raw Insta360 file is ignored, export a normal MP4 from Insta360 Studio and drop that exported MP4 into Zucker Editor.
- If Finder blocks a local unsigned build, rerun `codesign --force --deep -s - "dist/Zucker Editor.app"`.
- If the UI opens but appears blank, verify the build command included `--add-data "$ROOT/web:web"` and rebuild.
- If native picker buttons do nothing in `/advanced`, wait for the window to finish loading. A missing bridge shows a toast; rebuild with the current `app.py` if the toast says the desktop picker bridge is unavailable.
- If browser drag/drop reports the 512 MB limit, move large videos to `~/ZuckerVideos/Inbox/` and press Rescan, or use the bundled app's `Add video folder...` picker.
- In the wizard, `songs.json` is optional. Without it, Zucker Editor exports one continuous video. In `/advanced`, `songs.json` is only required for manual cut/edit/export debugging.
