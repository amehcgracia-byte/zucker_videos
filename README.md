# Zucker Editor

## Download and install

Public packages are listed under [GitHub Releases](https://github.com/amehcgracia-byte/zucker_videos/releases). macOS uses the normal `Zucker Editor.dmg`; copy the app into Applications. Windows uses a ZIP containing `Zucker Editor 2.4.6.exe` and its resource folder: extract everything, then run the EXE. See [README_APP.md](README_APP.md) for requirements and user guidance. A release is published only after the corresponding frozen package checks.


Zucker Editor 2.4.6 turns master audio plus synchronized camera clips into a finished video.

The user-facing UI is English-only in this build. User strings are centralized in `core/messages.py` for backend/status text and `web/strings.js` for frontend dynamic text.

## Current product rules

- **YouTube** is horizontal 16:9 and skips captions and flyer composition.
- **Reel**, **Backstage**, and **360** remain separate modes; 360 may run with one equirectangular video carrying its own audio.
- A new wizard submission always starts a new `.zuckervid` project. Opening an old project is explicit; matching file paths never select one automatically.
- 360 preview controls and final rendering must preserve the selected yaw/pitch/roll/FOV relationship and use smooth, bounded motion.

## Uso

1. **Drop everything**
   - Enter `Video name`, or keep the default `Jam YYYY-MM-DD`.
   - Drag videos, master audio, and optionally `songs.json` into `Drop your files here`.
   - Files already in `~/ZuckerVideos/Inbox/` are imported automatically.
   - You can continue once there is at least one video and one master audio. Without `songs.json`, Zucker Editor makes one continuous video.

2. **Choose edit type**
   - `YouTube`: full-length horizontal 16:9 edit, without captions or flyer composition.
   - `Reel`: vertical 9:16 short for Instagram/TikTok.
   - `Backstage`: documentary edit with its own review/caption flow.
   - `360`: equirectangular source workflow, including the single-video-plus-embedded-audio case.
   - If `songs.json` has multiple songs, choose the song. YouTube also allows `All`.

3. **Wait for the result**
   - The app runs ingest, sync, cut, edit, and export automatically.
   - Progress is shown in plain Spanish, including ffmpeg export progress and ETA.
   - On success, preview the video, reveal it in Finder, or start another.
   - On failure, use `Show technical details` for the log tail.

Creative logic in this version:

- YouTube uses music-aware Tranquilo / Animado / Frenético pacing and movement, bounded subject-safe zooms, musician rotation and camera quotas. Make another changes the run seed and creative decisions while rerendering the same plan stays reproducible.
- Clips below the sync confidence threshold, clips with unstable second-pass sync verification, clips without usable audio, and invalid videos are excluded from multicam. If no clip qualifies, the job fails with per-clip diagnostics.
- Manual sync overrides in `/advanced` still rescue a clip when the user has verified it by hand.
- Instagram/TikTok use a center crop and fixed short excerpt sourced from the best-covered synced stretch.
- Exports use clean cuts by default, a watermark and intro/outro. The selected master span starts with video content; logo windows outside that span remain silent.
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
- Common camera containers (`.mp4`, `.mov`, `.m4v`, `.mts`, `.m2ts`, `.avi`, `.mkv`, `.3gp`, `.3g2`, `.mpg`, `.mpeg`, `.ts`, `.mxf`) and raw Insta360 `.insv`/`.insp` containers can become video clips.
- Video candidates must also pass `ffprobe` validation: a real camera-video codec, duration over 2 seconds, and at least 320x240 resolution.
- `.txt`, `.lrv`, `.thm`, `.xml`, `.srt`, hidden files, and `.DS_Store` are ignored with a visible reason; `.lrv` is treated as a low-resolution camera sidecar, not a usable clip.
- Raw `.insv`/`.insp` Insta360 files are accepted as raw 360 clips and show a visible quality note. Zucker Editor can stitch them automatically; an Insta360 Studio export is still preferred when present because it includes better stabilization.
- 2:1 equirectangular MP4/MOV exports are accepted and preferred over matching raw `.insv` files when the durations are within 2 seconds.
- Unsupported files are shown under Ignored with a note.
- When multiple master candidates are present, the wizard defaults to the longest audio file and shows a small selector so you can change it.

Ingest proxies:

- Ingest prepares low-resolution analysis proxies in the global cache at `~/ZuckerVideos/Cache/proxies/{cache_key}.mp4`.
- Proxy generation runs two clips at a time by default (`settings.ingest.proxy_workers`) and reports aggregate per-clip progress.
- Hardware is attempted first with `-hwaccel videotoolbox` plus `h264_videotoolbox`; each clip falls back to software `libx264` if the hardware path fails, and the chosen path is logged.
- Proxies are H.264 `yuv420p`, no larger than 1280x720, even dimensions, constant frame rate at the detected dominant FPS, with rotation baked in.
- HDR/10-bit sources use `zscale -> tonemap=hable -> bt709 -> yuv420p`; regular SDR sources use even-dimension scale plus `yuv420p`.
- Equirectangular clips use the fixed analysis proxy `v360=input=equirect:output=flat:yaw=0:pitch=0:h_fov=100:v_fov=67.673:w=1280:h=720`. Raw `.insv` clips first use `v360=input=dfisheye:output=e` with `settings.ingest.insv_fov` (default `190`; One X2 often needs about `204`, X3/X4 usually about `190`), then the same flat analysis reframe.
- The fixed flat proxy is analysis-only: sync, beat/coverage analysis and other point-of-view-independent work may use it. It is never the source for a view of a 360 shot.
- Review shots, selector thumbnails, and authored 360 previews use a lazy equirectangular analysis proxy: each 360 source is decoded and resized once to `960x480` under the project cache, then each requested thumbnail applies that shot's saved yaw, pitch, FOV, and source projection with the canonical `core.spherical_view.spherical_view_filter`. The proxy key includes the source signature and INSV FOV, so changed media or calibration creates a fresh proxy; changing a saved landmark changes the pose-keyed thumbnail.
- Final export still renders the used segments from the original camera files with the same `view_parameters` contract. Therefore a review/selector/preview frame and its export use the same signed yaw, pitch, horizontal/vertical FOV pair, and flat-vs-stereographic projection.
- Already-compliant originals skip proxy transcoding. The predicate is: H.264, constant frame rate, SDR 8-bit or lower, no rotation metadata, not equirectangular, and no larger than 1920x1080.
- The generic sync preview remains an analysis preview unless a shot pose is explicitly requested; it does not change the proxy used for audio/sync analysis. Final export renders only the used segments lazily from the original camera files and caches rendered segments globally under `~/ZuckerVideos/Cache/segments/`.
- To audit a real project end to end, run `python -m tools.audit_360_projection "/Users/macbookair/ZuckerVideos/Projects/Chrome Midnight.zuckervid"`. It prints the fixed proxy parameters, renders the singer/stage/audience thumbnails, compares their pixels, verifies pose-cache invalidation, and prints the saved-vs-thumbnail-vs-export angle table.

YouTube camera allocation and framing:

- The edit plan allocates the configured camera percentages against physical camera identities, not the shared `Edited` or session directory. Explicit ingest camera IDs win; otherwise common filename families (`C0185`, `IMG_0043`, and `VID_...`) are kept in separate, stable buckets.
- `edit_plan.json` records `camera_target_weights`, `camera_distribution.actual_percent`, and `quota_gap_percent`, so the requested mix can be audited after every run.
- Fixed/phone-camera Ken Burns moves are subject-anchored and deliberately limited to a maximum `1.38x` zoom with a central composition envelope. Legacy 3x corner/feet recipes are invalidated by the YouTube selection and edit-plan versions and clamped again by the exporter.
- Nikon sources receive a quality tie-breaker, but never override an explicitly zero-weight camera or the configured quota.

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
- `export` renders video-only MP4 intermediates, concatenates them, then muxes one continuous master-audio stream over the finished video and keeps output under 1.9 GB by computing a target bitrate from duration.
- Export tries `h264_videotoolbox` first on macOS and falls back to `libx264` when hardware encoding is unavailable.
- Export applies a bottom-right watermark from `assets/watermark.png` when present, otherwise `logo_editor_green.png`.
- Export color matching samples five short windows per clip and caches the profile globally under `~/ZuckerVideos/Cache/color/`; color-measure failures are warnings, not export failures.
- Full-clip high-quality mezzanines are no longer required for export; segment renders seek into the original camera files and fall back to the analysis proxy only if an original fragment cannot be decoded.
- Text overlays use `band_name` and `handle` from `~/ZuckerVideos/config.json`; if ffmpeg lacks `drawtext`, export skips text rather than failing.
- Frontend JavaScript runtime errors are logged to `~/ZuckerVideos/logs/frontend.log`.

Proxy benchmark:

- Benchmark command path: add one ~10 minute 4K HEVC camera clip, run ingest once with the previous full-mezzanine build, then run ingest with the current proxy build and compare the per-clip log lines in `~/ZuckerVideos/logs/`.
- This checkout does not include a representative 10 minute 4K HEVC sample, so no local speedup number is recorded here. Expected improvement comes from 720p proxies, hardware decode/encode, two-clip parallelism, compliant-source skip, and lazy full-quality segment rendering at export time.

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

## Storage cleanup and project isolation

The app never treats original media, registered inputs, or valid exports as disposable cache. The read-only storage inventory is available at `GET /api/v1/maintenance/storage`. The explicit CLI audit/cleanup is:

```bash
.venv/bin/python tools/cleanup_storage.py report
.venv/bin/python tools/cleanup_storage.py clean
```

Cleanup only proposes recoverable generated leftovers: stale temp files in generated folders, old verification projects, excess backups, and historical exports beyond the current result plus two previous families. Global cache pruning remains a separate explicit cache operation. The CLI asks for `CLEANUP` and moves planned items to the system Trash. Do not run it during an active render.

A fresh wizard run does not search for a project with matching input paths. Use the project shelf and **Open** when you intentionally want to resume an existing project.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Coverage includes project roundtrip and atomic-write behavior, engine dependency/cache/staleness/failure behavior, API project and stage polling, the stage readiness matrix, inbox classification, register-from-inbox, upload fallback and upload-size errors, songs-json suggestions, missing input detection, sync confidence and offset math, manual overrides, error envelopes, media `Range` responses, and a marked slow generated-media sync integration test.

### Auditing 360 Motion

Automatic 360 motion cannot be trusted to instrumentation: several fixes looked
correct in the sendcmd stream handed to ffmpeg and still shipped motion that
read as wild. `tools/audit_360.py` renders a real export and measures the
delivered pixels with dense optical flow, reporting apparent motion per segment
as a percentage of frame width per second.

```bash
.venv/bin/python -m tools.audit_360 --synthetic   # never touches real media
.venv/bin/python -m tools.audit_360               # uses the last project's 360 clip
```

It carries two controls and fails loudly rather than reporting numbers it
cannot trust: a static camera that must read ~0%/s (otherwise the measurement
is inventing motion) and a deliberate 80° pan that must read high (otherwise
the measurement is blind, and a still hold proves nothing). It also verifies
the landmarks rendered as genuinely different framings, which catches the silent
failure where a segment's source does not match its input record, the v360
reframing is dropped, and the export is a flat passthrough that measures as
perfectly still.

Run it on a **real** 360 clip to judge hold magnitudes. On the synthetic source
the hold reading is dominated by the test texture's spatial frequency rather
than by the motion (the same render measures 11.2%/s or 0.017%/s depending on
how coarse the noise is), so those rows are reported as `(info)` and excluded
from the verdict; the structural checks still apply. The authored motion budget
is guaranteed exactly and cheaply by
`tests/test_edit.py::test_automatic_360_motion_never_exceeds_the_fov_fraction_budget`,
which checks the sendcmd stream ffmpeg is handed. `tests/test_audit_360.py` runs
the synthetic audit as a slow test.

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
- Generated green logo: `assets/logo_editor_green.png`
- Generated app icon: `assets/icon.icns`

Build:

```bash
tools/build_app.sh
```

Outputs:

```text
dist/Zucker Editor.dmg
```

The app bundle is staged temporarily under `build/release/`, ad-hoc signed,
self-tested, copied into the DMG, and removed from the hand-off directory. The
`dist/` folder is deliberately left with only the installer so it cannot be
mistaken for an installable app folder.

During the build the app is ad-hoc signed with:

```bash
codesign --force --deep -s - "build/release/Zucker Editor.app"
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

The wizard can always move back and forward with the top navigation. `New project` clears the current wizard view without deleting the existing project folder, so auto-resume never traps you in a previous run.

Troubleshooting:

- If media probing fails immediately, install ffmpeg with `brew install ffmpeg`, then relaunch.
- If a dropped file appears under Ignored, the note explains why. Text reports and camera sidecars are intentionally excluded from sync/export.
- If a raw Insta360 file looks poor after automatic stitching, export a stabilized 360 MP4 from Insta360 Studio and drop that exported MP4 into Zucker Editor; the Studio export will be preferred automatically when it matches the raw clip duration.
- If Finder blocks a local unsigned build, rerun `codesign --force --deep -s - "dist/Zucker Editor.app"`.
- If the UI opens but appears blank, verify the build command included `--add-data "$ROOT/web:web"` and rebuild.
- If native picker buttons do nothing in `/advanced`, wait for the window to finish loading. A missing bridge shows a toast; rebuild with the current `app.py` if the toast says the desktop picker bridge is unavailable.
- If browser drag/drop reports the 512 MB limit, move large videos to `~/ZuckerVideos/Inbox/` and press Rescan, or use the bundled app's `Add video folder...` picker.
- In the wizard, `songs.json` is optional. Without it, Zucker Editor exports one continuous video. In `/advanced`, `songs.json` is only required for manual cut/edit/export debugging.

### Native fixed-camera and phone motion

Flat fixed cameras and recognized legacy phone inputs use a native catalogue of
20 bounded hold, zoom, horizontal, vertical, diagonal and subject-reframing
recipes. Selection is seeded from the edit window, source identities and cut
index, excludes the previous recipe, and scales travel to the shot duration.
A cached, independent subject inside the safe framing area is required for
subject-directed moves. Without that evidence, use a full-frame hold or a tiny
central zoom. No new detection dependency is required. Both authored and stale
Ken Burns recipes are capped at 1.38x during export; automatic 360 remains static.

YouTube pacing prefers 5–7 seconds for low intensity/tempo, 3–5 seconds for
medium, and 2–4 seconds for high. When explicit intensity is unavailable, tempo
is used as a pacing proxy. Tail boundaries must never create a one-second shot.
Transitions require an explicit `enabled: true` setting; saved legacy durations
alone cannot enable a hidden crossfade.

The macOS build reads the version from `core.build_info.APP_VERSION`, embeds the
full HEAD commit, names the image and volume `Zucker Editor <version>`, retains
the tested `.app`, and backs up existing build products. A locally edited
PyInstaller spec is restored on success or failure. Unrelated installers and
user environments are preserved.
