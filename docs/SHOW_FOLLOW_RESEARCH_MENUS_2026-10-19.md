# Show 2.6.12 — object following, reference drafts and editing controls

## What changed

- Pictures and moving clip cutouts have **Follow an object: mark it…**. Mark a tight rectangle at the effect's start. A project-owned pixel template is retained, so the renderer can locate the same appearance even when camera framing changes before the effect starts. Tracking uses clean frames, before overlays, and hides a lost target rather than learning the wall. This is visual matching, not semantic hand recognition: heavy occlusion, rotations and different angles can still need another mark. The effects preview/export perform the tracking; quick source playback shows placement controls.
- Pointer cancellation/window blur releases unfinished move/mark tools instead of leaving a stuck drag. The exact reported mouse failure has not been conclusively reproduced.
- The layer editor also initializes when its cached sample becomes ready before the enhancement script loads; the Save button no longer depends on catching a one-time event.
- Selected moving clip/person cutouts have a visible rectangle, mouse movement and corner resizing. **Colour & light…** changes only the foreground's brightness, saturation and hue. The video-layer editor also has a visible frame and live colour controls.
- **Eye direction** now offers horizontal, vertical and both diagonals. **Mouth direction** is separate. **Pivot** remains the turning/scaling point; it does not pretend to define the direction of an eye glance. The saved rig, preview, close-up and export carry the selected directions.
- Zero-length **Character speaks** placeholders and their row labels open the same timeline menu, including **Delete**. Clipboard/timeline behavior stays available.
- The video-layer background picker includes **From library…**, for existing pictures and videos. Other projects' pictures are copied in once; an arbitrary filesystem path is rejected.
- Cascading menus use the browser's top layer when available, fit the visual viewport, scroll independently, and attach to the active modal or fullscreen owner. Submenus keep their own scroll and keyboard navigation. They close when the viewport changes, instead of retaining stale positions.

## Stock textures and YouTube access

Procedural textures remain available; they are not relabelled as photorealistic footage. **Textures → Filmed texture from a link…**, and **Replace with filmed texture…** on a procedural texture, create reusable video layers with muted audio and the same placement/follow tools. Green-backed soft smoke/rain/light has a separate soft key: the hard opaque-object key erased faint rain. Black-backed light uses screen blending. Both match the layer editor's blend. Completed downloads are kept in the video library. Texture clips can loop when their timeline interval exceeds the source.

Two external source examples were actually downloaded and inspected locally:

- [Electricity, Green Screen Garf](https://www.youtube.com/watch?v=JBXG0HfrXlA), 20–26 seconds; the opening seconds are an intro, so the example starts later.
- [Rain green-screen stock footage](https://www.youtube.com/watch?v=1LaoMlzr5wg), 0–6 seconds; subtle/faint rain, useful for testing soft keying, not a claim of a large finished realistic texture library.

Only links are stored in source control. Third-party footage is not redistributed in the repository. Choosing a source downloads it into the project. Other nature textures still need chosen footage; this release does not claim every procedural preset has been replaced by reviewed stock footage.

YouTube's “confirm you’re not a bot” is an access check, not an exhausted Zucker allowance. **Browser session · YouTube access** explicitly lets the person choose their local browser. yt-dlp reads that browser's current session at download time; Zucker stores only its name, never exports a cookie file. A signed-out browser/CAPTCHA still needs the person. There is no promised silent account renewal. Public example downloads used no browser session in QA. The downloader also resolves an ffmpeg executable name to its actual path before passing it to yt-dlp.

Official behavior: [yt-dlp cookie FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp), [YouTube extractor guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies).

## Automatic editing direction

The supplied final video is a reference for the level of editing expected from automatic mode, not merely a request to copy individual effects. The director can now request **video_reference**, **internet_photo** and **texture_reference**, with a concrete search query, in addition to drawings, formulas, words, close-ups and backgrounds. No invented URLs are accepted from the planner.

During preparation for the reviewed Show flow, up to three selected external references are researched and prepared as drafts. Public text queries are sent to YouTube/Wikimedia; source footage is not uploaded. A short video result is downloaded into project assets with its source link and title; existing approved artwork is not replaced. A search result is not automatically approved as the best joke. Its visual/timing still needs review. The initial Show screen exposes an opt-out checkbox. Keyword/offline mode makes no research requests unless it contains an explicit external-reference proposal.

If search or download fails, the card keeps the error and needs a chosen reference. Missing reference photos cannot fall through to a painter or silently produce substitute artwork. Export and effects-preview rendering never perform this research or invent new resources. Existing already-prepared projects are not reanalysed or overwritten automatically. This is an initial bounded reference-draft workflow, not full autonomous understanding of every scene or guaranteed contextually correct film selection.

## Save safety and existing project

A concurrent draft test reproduced an actual empty-JSON read while `show_proposals.json` was being rewritten. Proposal and scene saves now use the existing atomic fsync/replace writer. No test was skipped or marked xfail to hide this race.

The user's current project was inspected read-only: 14 scenes, 14 placed overlays and 45 approved proposals were present. The supplied export is 356.488 seconds, H.264/AAC, 1920×1080. A contact sheet was inspected, not a claim of having watched every frame or listened to the whole show. The edit JSON files were copied into `~/Documents/Zucker recovery/La prueba edits 20261019-032241`; this snapshot contains edit data, not a duplicate of the original video files. No real project was modified by QA.

## Reconciliation of the pasted audit

The public `main` really remains `321fdb45` (9 October); the working fixes are in the stacked Show PRs, so auditing only main cannot evaluate them.

| Point | Current finding/action |
|---|---|
| Reveal folder on Windows | Confirmed macOS-only command and false success. Uses OS-specific commands and reports launch failure; English label is Show in folder. Native Windows execution still needs a Windows package run. |
| Windows hardware encode | Current helper still qualifies VideoToolbox only. NVENC/QSV quality/performance remains unqualified; no unmeasured replacement enabled in this change. |
| main CI / 360 tests | CI workflow already exists on all branches/PRs. No skips/xfails or 360 expectation changes in this work. |
| Step counts | All six main headers already say “of 6”. |
| Project shelf thumbnails/exports shortcut | Separate follow-up; video-library thumbnails do not prove project-shelf thumbnails. |
| Language | New UI is English; no blanket claim that every historic string in every mode has been audited. |
| New Jam | Default new name now includes the local calendar date. |
| Keyboard | Existing scoped video/clipboard controls retained. No global Escape that would unexpectedly cancel an export while closing a dialog. |
| Backstage paper-edit preview | Separate Backstage work; not claimed from Show validation. |
| Reconnection | Status polling now shows one reconnection notice and backs off rather than creating one toast each second. Cache-cleaning behavior is not changed here. |

## Validation

- Complete local Show/gag/desktop suite: **336 passed**, one existing Pillow deprecation (`/tmp/show-2612-release-verified.log`).
- Final focused controls/library/atomic-save group: **15 passed** (`/tmp/show-2612-final-controls-library.log`); counts overlap the complete suite and include an additional library-path security/selection test.
- Chromium and WebKit: deletion of the zero-duration speech placeholder, saved target pixels, mouse-to-frame coordinates inside the resized scene editor, viewport-bound menus and isolated menu wheel scrolling, library background selection, key-mode retention and foreground-colour persistence all pass (`/tmp/show-2612-ui-complete.log`). These use a synthetic project. Async photo loading and save completion are awaited; earlier harness reads made before completion were not treated as product failures.
- Source JavaScript and embedded compositor scripts parse successfully. New real-FFmpeg tests verify that an added picture follows the clean moving target; target-template recovery across a camera zoom is covered separately. No real user project was rendered or modified during these tests.

Package verification and installation evidence follows below after completion. Source-level browser checks and native Windows behavior are distinct. No claim of zero remaining application bugs.

## Packaged macOS validation

The 2.6.12 bundle is built from code commit `4333d689`. Strict deep signature verification passes, and the packaged HTML/JS/CSS/compositor hashes equal the reviewed source (`/tmp/show-2612-package-assets.json`). The new follow/research modules are present in the frozen archive; no post-signing source replacement was used.

The mandatory frozen self-test passes: instrument model, intro encoding, faster-whisper/Silero dependency path, person segmentation availability, exact Metal remap and CLIP scene tagging (`/tmp/show-2612-selftest.json`). Its transcription input is synthetic; this is not a speech-recognition accuracy measurement. Windows native UI is not tested here.

The complete browser flow also passes in Chromium and WebKit when served by the frozen executable: delete the speech placeholder, save a marked target/template at the correct resized-screen coordinates, independently scroll a viewport-bounded menu, choose a library background, retain the selected key, and save foreground colour (`/tmp/show-2612-frozen-ui-proof.log`). A real frozen scene effects preview completed with no omitted effects and preserved its four-second duration (`/tmp/show-2612-frozen-scene.json`). These fixtures are isolated; no user project was rendered during package QA.

## Installation

Installed and opened **2.6.12** (`4333d689`) in `/Applications/Zucker Editor.app`. The previous installed version was copied to `/Users/macbookair/Applications Backups/Zucker Editor before 2.6.12 20261019-035425.app`, verified by every file hash and symlink, and retained before replacement. The staged/pending copy and signature were also verified. User projects and source media were not modified by the installer. Evidence: `/tmp/show-2612-install-result.json`.
