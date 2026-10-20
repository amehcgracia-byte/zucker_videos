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
- Imported clips with mixed or disabled source audio now use contain/letterbox, matching clips inserted with their own audio. Intentional close-ups retain their crop and zoom.
- This is not yet a completed UI consolidation. Pending: shared media editor/action registry across canvas and timeline, consistent zoom/scale controls beyond the frame, background-video parity, and reviewed storage cleanup. No project media or useful libraries were removed.
- The previously restored 71-effect project must not be restored again: the user's subsequent edits are now authoritative. Resume scene 7 was already applied with the 2.6.14 installation.
