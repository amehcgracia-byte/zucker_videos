# Zucker Editor 2.4.6

Zucker Editor combines a master audio track and synchronized camera videos into an automatic edit. It supports flat cameras and equirectangular 360 sources.

## Download and install

Packages are published under [GitHub Releases](https://github.com/amehcgracia-byte/zucker_videos/releases).

- macOS: open the normal `Zucker Editor.dmg` and copy `Zucker Editor.app` into Applications. FFmpeg and FFprobe must be installed; the app checks their availability.
- Windows: extract the complete ZIP, then run `Zucker Editor 2.4.6.exe`. Keep `_internal` and the other resources beside the EXE. The package includes FFmpeg and FFprobe.

Version and source commit appear in the app footer and wizard report. See [README_APP.md](README_APP.md) for more user guidance.

## Editing

Load a master audio file and camera videos, choose a format and the audio interval, then review the selected frames before export. Recheck the interval after replacing the master audio: its timestamps can differ from the previous file.

- YouTube renders horizontal 16:9 and goes from frame review to final rendering.
- Reel and Backstage have their own short-form and caption/composition flows.
- 360 supports an equirectangular source, including a source with embedded audio.

YouTube uses measured energy per musical bar to choose Tranquilo (normally 5–7 seconds), Animado (2.5–4 seconds) and Frenético (1–2 seconds at clear peaks). Movements change too: gentle holds and zooms, faster bounded pans and accelerating zooms. Planet reveals continuously zoom and tilt into the calibrated 360 stage view over up to 3.2 seconds, with at least 40 seconds between effects. Missing subject evidence keeps flat-camera framing conservative. Camera quotas, performer rotation and source coverage still constrain the edit.

`Make another` generates a new creative seed. Cut placement, camera tie-breaks and movement choices can change; rerendering the same saved plan remains reproducible. Available footage limits how many distinct alternatives are possible.

The selected master span starts with the video content; bookends outside that span are silent. Video ends at available footage coverage, which can be earlier than the requested audio end. Cuts are clean by default. Failed exports do not publish an incomplete MP4 as a completed result.

Frame alternatives persist per project and timeline interval. Replacing one frame preserves the others. A changed source, pose, interval or relevant configuration invalidates the affected reserve. Loading and replacement report errors instead of polling indefinitely.

## Development and packaging

Use Python 3.11 with `requirements.txt` and `requirements-build.txt`. Create a virtual environment, install dependencies and run `python app.py --dev`; `--project /absolute/path/project.zuckervid` explicitly reopens a project. FFmpeg/FFprobe are required for analysis and rendering.

- macOS: `tools/build_app.sh`, with `ZUCKER_SELFTEST_AUDIO` pointing to real media. The script builds and signs the app, runs the frozen self-test, verifies build metadata and creates `dist/Zucker Editor.dmg`.
- Windows: `tools/build_windows.ps1`, with `ZUCKER_SELFTEST_AUDIO` and actual FFmpeg/FFprobe binaries available. It builds the EXE and resources, runs the frozen self-test and checks version/commit before creating the ZIP.
- `.github/workflows/build-release.yml` builds and verifies Windows artifacts. It does not automatically publish them. Releases contain verified macOS and Windows packages from the same source commit.

The directed checks for this release cover musical pacing and variation, projection geometry, native motion, camera distribution, frame review and atomic export completion. The general API suite has ten known failures that also reproduce on 2.1.35; see the release validation notes. Passing the directed checks does not claim that entire suite is green.

## Version numbering

The previous 2.1.35 is renumbered as 2.4.5. This release is 2.4.6. Every ten patch revisions increments the middle component: 2.4.9 → 2.5.0.
