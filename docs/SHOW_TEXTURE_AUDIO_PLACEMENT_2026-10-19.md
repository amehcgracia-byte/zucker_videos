# Texture replacement, placement and audio — 19 October 2026

Continuation of PR #43. UI remains English. No private project media is committed.

## Changes

- A filmed texture replaces its existing texture proposal, preserving identity, timing, object mark and tracking template. It does not add a second placement bar.
- Selection guides are scoped to the selected, active effect in the paused scene editor; playback and the main preview hide them. Deleted effects cannot retain their guides.
- Placed pictures have crop handles on their edges and proportional corner scaling. Command/Ctrl-drag inside rotates about the centre (the user corrected an earlier pan choice); modified edge drags stretch. Pixel rendering applies crop before transforms, including original mouth/eye layers.
- Audio is inserted as an Audio timeline bar. Its displayed name comes from the source title or imported file name. Trimming the start also advances the audio source position; moving the bar does not. Undo restores source position.
- Music library offers music/jingle search shortcuts, project placement, catalog setup and MP3-only download from an existing video-link source interval. The Show audio tab links to this catalog.
- New audio imports and saved voice recordings are MP3. Existing source files are not deleted or rewritten.
- Downloaded video titles are saved in small local metadata sidecars and used on timeline bars. Source attribution is retained.

## Validation and limitations

70 targeted tests passed across clips, media library, music, texture replacement, tracking controls and pixel layers. The audio trim endpoint has an additional regression check. JavaScript syntax checks passed. Browser testing used a disposable synthetic project: cropping saved, playback had zero visible selection handles, and Add audio opened the catalog controls. No paid provider calls or private-media uploads were made.

This is not a claim that the entire legacy suite is green. Parent #43 has 28 known failures also present in #42. No skips or xfails were added.

Catalog searches require the user's provider credentials and permitted downloads; the test used mocks for external services. Automatic watermark detection/cropping is not included: a persistent corner graphic is insufficient evidence to crop someone's footage safely. Ordinary full-screen clip insert crop controls and live transformed video preview still need separate parity work; do not advertise these as verified for every clip type.

## Project recovery and handoff

The user explicitly chose the earlier 71-effect / 14-scene snapshot and resume at scene 7, excluding recent experiments. Back up the current project state before restoration, and refuse replacement while the app is running. Keep all media local. Install only the verified package with a verified rollback copy.

Claude: preserve this branch's same-ID replacement, selection lifecycle and audio source offsets. Do not reintroduce a new clip for a texture replacement or substitute unrelated drawings. Provider search and generated art are independent of placement and render approval.

## Photo background shortcut (20 October)

Remove background is now a direct context-menu action on placed photos and drawings. It uses the existing subject mask service and keeps the full canvas so replacement does not shift the element. Imported clear saturated green backings are removed automatically, only for border-connected green; enclosed green subject details survive. Originals stay in the library. Natural-photo masks remain approximate and can need manual correction.

## Follow-up 2026-10-20: cleanup and export consistency

- Photo background removal is available in the image context menu and project library; clear green-backed imports retain an original and create a transparent copy with the same canvas.
- Resuming composition from `cache/composition-base.mp4` now treats that file as a cache hit instead of copying it onto itself. Original provenance is preserved, including for filesystem aliases. Changed source exports still refresh the cache.
- Initial contain/letterbox behaviour was superseded by the automatic fit described below, following the subsequent user request. Intentional close-ups retain their crop and zoom.
- This is not yet a completed UI consolidation. Pending: shared media editor/action registry across canvas and timeline, consistent zoom/scale controls beyond the frame, background-video parity, and reviewed storage cleanup. No project media or useful libraries were removed.
- The previously restored 71-effect project must not be restored again: the user's subsequent edits are now authoritative. Resume scene 7 was already applied with the 2.6.14 installation.

## Unified framing and automatic clip fit (20 October, continuation)

- `Edit media…` is shared by the timeline and image context menus, with a pointer preview for photos and ordinary clips, crop/rotation/size, and links to specialist animation and moving-background controls. A saved edited image copy goes back into Library; video recipes remain in the video catalog.
- Placement is no longer clamped to the frame width or its edges. The image renderer bounds its working bitmap and transforms onto the visible output canvas, so a large placement does not allocate a correspondingly huge bitmap.
- Imported full-frame horizontal videos fill the output. Portrait clips retain their whole central picture with enlarged blurred sides. A bounded nine-frame inspection removes only consistently black border strips; dark shots and keyed texture footage are excluded. This is stable per clip, not zoom pumping frame by frame.
- Same geometry fields reach own-audio clip inserts, mixed/silent clips and moving-person backgrounds. Ordinary inserts and opaque layers use the same automatic framing policy.
- Effects and Add options are grouped; the redundant inline picture gallery is removed in favour of the searchable library. Picture deduplication reuses size/mtime-qualified content hashes instead of rereading every picture on each import.
- The director brief now asks for varied editorial techniques and reuse of approved art, with serious mathematical notation rather than cartoon equations. It cannot claim an asset exists before its tool succeeds.
- Cache cleanup now explains its scope before deletion. User projects, historical edits, source media and approved asset packs are not deleted by this change.

Remaining architectural limits: this consolidates entry points and framing; specialist rigging, clay and moving-person editors still exist behind those entry points. It does not replace them with a single universal compositor. Model dependencies remain bundled because transcription/segmentation/face tools use them. Automatic black-border detection is conservative and cannot prove that a black strip is unwanted in every artistic shot. Image/clip pointer preview is immediate; tracking, segmentation and animated effects still need their effects preview. No claim of perfect automatic editing or a completely green legacy suite is made.

## Verification on 21 October

The broader Show run reached 312 passing tests and exposed a draft-status snapshot race. The snapshot now reads the picture link and worker status consistently. Its targeted rerun, own-picture/library path checks, and real portrait framing renders pass (11 tests). The earlier clip/follow/scene geometry rerun passed 46 tests. A browser walkthrough in an isolated synthetic project verified Edit media above the timeline, 120% sizing, cropping and saving an edited Library copy while preserving the original. Equivalent filesystem paths no longer incorrectly reject a listed image; unlisted files remain inaccessible. Full-suite and installed-package results follow after packaging.

Final validation: **314 passed** in the Show/framing/composition-cache suite (one Pillow deprecation warning). Installed **2.6.15 / 2b29856b** after strict code-signature checks and verification of 5,666 packaged file/link entries. The previous app is preserved at `~/Applications Backups/Zucker Editor before 2.6.15 20261021-040405.app`. All 142 project JSON files checked were unchanged during installation. The running installed server serves the current review, media-preview and video-layer scripts byte-for-byte and opens the existing 14-scene project. Its current saved resume is scene-14; this was preserved rather than overwriting it with the older scene-7 recovery. No private media is committed.
