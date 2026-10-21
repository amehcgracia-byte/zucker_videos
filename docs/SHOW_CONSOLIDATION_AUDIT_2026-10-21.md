# Show consolidation: acceptance ledger

Scope: user-requested six-step consolidation, starting from installed 2.6.15 / 2b29856b. No further installation until the reviewed work is complete. Existing project edits and approved assets are authoritative. A passing test does not replace a UI/render walkthrough.

Status vocabulary: **implemented** = source exists; **verified** = observed behaviour with named evidence; **gap** = concrete missing/broken path; **pending verification** = must not be described as finished.

| ID | Request / acceptance | Existing implementation | Status / next evidence |
|---|---|---|---|
| U1 | Same actions on canvas and timeline | show_review.effectItems / overlayMenu / timelineMenu | IMPLEMENTED shared overlay action provider and clipboard/history; VERIFIED timeline Animate → Spin → Undo restores Jump, and Copy → Paste creates two pieces on the same row in isolated UI fixture. Other media routes remain to audit |
| U2 | One common media editor, contextual specialist tools | editMedia / effectSettings / ShowVideoLayers / PhotoStudio / ClayEditor | PARTIAL: framing shared; independent specialist windows; keep useful tools behind a single action entry |
| U3 | Group and name tools consistently | mediaCategory / groupedEffects / groupAddOptions / addItems | PARTIAL: category definitions repeated; labels and menus need parity review |
| U4 | Native keyboard, context scroll, topmost dialogs | show_menu.js / show_review key handlers | PENDING: check narrow viewport, nested dialogs, keyboard and trackpad |
| M1 | Crop, scale beyond frame, rotate, stretch | show_geometry.py / ShowGeometry / editMedia | VERIFIED 2.6.15 targeted geometry renders; common pointer preview needs transformed-handle audit |
| M2 | Save edited image/video to Library | edited-copy endpoint / video catalog | VERIFIED immutable video recipe snapshots retain crop/offset/duration, deduplicate identical saves and reuse source bytes; background-composition library saving remains a gap |
| M3 | Auto-fit imports including portrait / embedded bars | media_framing.py, inserts, opaque layers | VERIFIED synthetic portrait render; PENDING all import/live-preview paths, explicit user framing precedence |
| B1 | Moving person over photo/video, reuse masks, export cutout | show_backgrounds.py / show_video_assets.py / ShowVideoLayers | PENDING end-to-end compositing and library reuse including silhouette |
| T1 | Attach image/video exactly to marked object | show_follow.py / steam mark handler | PENDING coordinate conversion and rendered vs clean-video selection; inspect before declaring fixed |
| T2 | Recover tracking after cuts / hide on loss / release to fixed position | ObjectTracker / Follow.until | PENDING discontinuous-shot render and release case |
| T3 | Texture replacement edits same effect; guides never baked | texture_footage / overlay lifecycle | VERIFIED previous targeted tests; PENDING all UI entry paths |
| A1 | Same mouth/eyes/part animation and strength on all access points | overlayMenu / proposalAnimationItems / pixel_layers | GAP menu parity U1; PENDING source-pixel render axes and no duplicate proposal |
| A2 | Character speech removable and mixed | removePiece / show_audio.overlay_tracks | PENDING UI and render audio proof |
| S1 | Local/link audio MP3, named editable bars | audio_import / show_audio / music_library | IMPLEMENTED, targeted tests pass; PENDING integrated library/mixer route |
| S2 | Censor profanity, noise reduction, balanced narrator/clip/music | show_audio.py / music_mix_plan.py | IMPLEMENTED; PENDING audible bounded fixture and timing evidence |
| P1 | Play always useful or explain why unavailable | preview transport / quick source player | PENDING all no-render/loading/error cases |
| P2 | Captions editable live and aligned to selected words | caption layout / clock / word placement | IMPLEMENTED; PENDING full UI-to-render parity |
| L1 | Approved art, Simón cap, sharp photos, no unwanted fallback | project/recurring/other/hidden libraries | PENDING inventory without deleting source/used assets |
| L2 | Video thumbnails, swapped versions, collapsible library | show_video_assets / drawLibrary | IMPLEMENTED; PENDING recipe/source preservation and grouped reuse |
| C1 | Clay clips editable; story-specific motion with pauses | show_clay / ShowClayEditor | IMPLEMENTED partly; PENDING placement and duration editing across routes; no unsupported video-generation claim |
| D1 | Planner uses actual tools and approved art, matches editorial reference | gag_director / show_proposals / show_research | PARTIAL: prompt improved; tool catalog and executable proposal contract need audit |
| R1 | Smaller/faster app measured, safe cleanup | bounded sprites / cached hashes / cache cleanup | PARTIAL: no substantial binary-size reduction demonstrated; collect measurements before removal |
| R2 | Full application + Windows verification | existing tests / CI | PENDING: 314 Show tests do not establish whole-app or Windows status |
| R3 | Preserve user work and installation rollback | previous verified install | VERIFIED 142 project JSON files unchanged during installation; no restore over later edits |

## Execution order
1. Inventory and reproduce gaps; maintain this ledger with evidence.
2. Consolidate commands/entry points before removing duplicate controls.
3. Tracking, moving layers and preview parity.
4. Audio, captions, library and clay integrated paths.
5. Executable automatic-edit capability catalog and reference-based review.
6. Measurements, reversible cleanup, full validation and final package. Installation deferred.

## Findings
- U1 reproduced in code: timelineMenu special-cases overlays to one Edit media entry; overlayMenu separately supplies animation, tracking and clipboard actions. Outside the timeline, copy/paste uses layerClipboard; inside, timelineClipboard. This explains actions disappearing and copy/paste differing by location. Consolidate actions rather than add a third menu.
- M2 requires a saved recipe separate from source catalog identity. Saving placement cannot truthfully promise the same edited clip is reused if the place endpoint reconstructs only raw source parameters.

## 21 October checkpoint — not completion
- UI evidence: `/tmp/show-consolidation-menu.png`; isolated project `/tmp/Show cleanup lab.zuckervid`. No user project modified.
- 322 Show/framing/music-plan tests passed in 115.77 s before the subsequent short-duration regression fix. One existing Pillow deprecation warning.
- New render regression: a 0.12 s picture is absent at frame 4 of a 25 fps export. Removed renderer's implicit 0.3 s minimum; timeline timing is authoritative. Test passed.
- Initial regression fixture had invalid FFmpeg `d=.5`; corrected to `d=0.5`. This was a test-fixture failure, not a product fault.
- Saved video recipes use a separate content-derived identity and never overwrite the original catalog entry; endpoint rejects a stale project ID.
- Installed package baseline: 1.5 GB; web 11 MB; core 5.2 MB. Frameworks: torch 462 MB, cv2 144 MB, llvmlite 127 MB, numpy 82 MB, onnxruntime 65 MB, av 64 MB, ctranslate2 58 MB. These are measurements, not savings. No engine or user asset deleted.
- Full application suite is a separate check; targeted Show success does not establish its result or Windows compatibility.
- Remaining: common editor/library coverage for moving compositions, all tracking/coordinate cases, audio/captions/clay walkthroughs, planner capability coverage, full-suite findings and packaging. No new installation.

## Cross-platform findings
- Previous PR run https://github.com/amehcgracia-byte/zucker_videos/actions/runs/39532099501: fast job failed with 30 failures; slow advisory job succeeded. This predates this consolidation checkpoint. Do not describe CI as green.
- Two directly relevant failures addressed: older FFmpeg's blurred portrait composite lost its EOF frame (retain last picture to the requested frame boundary); MP3 encoder padding changed a 0.5 s recorded voice to 0.528 s (bound saved timing to the original and the 180 s recording limit).
- Local validation after these changes: 31 tests passed across media framing, pixel layers, clip editing and inserts. Remote confirmation still required.
- Whole local suite interrupted before completion when it reached heavy 360 renders; no completed full-suite result claimed. The runner and its child processes were stopped. Remote jobs remain the appropriate place for broad render verification.

## Remote confirmation — 21 October follow-up
- Commit b1fed79a, run https://github.com/amehcgracia-byte/zucker_videos/actions/runs/39533601357: the portrait-clip EOF and MP3 voice-length failures no longer appear; remaining failures are still blocking the fast job. Slow advisory job succeeded. This confirms the two codec fixes remotely, not the entire application.
- The follow-up automation now targets the current PR #44 and this six-phase ledger, rather than the obsolete PR #30 checkout. Installation stays deferred.

## Common media pointer checkpoint
- Fixed shared preview selection geometry: the visible outline, crop edges and resize corner now use the same rotation, mirror and stretching as the image. Pointer coordinates are inverted through that transform; clicks outside the cropped image no longer move it.
- The common editor's “Animation & other tools” opens the same contextual action provider as the timeline. Errors saving a video library copy remain visible even after the edit dialog closes.
- Validation: 15 tests passed (pointer coordinates, Show review and preview transport), plus syntax checks for both changed scripts. Coordinate regression covers 90-degree and free rotation, mirror, unequal stretch, cropped corners and outside clicks. Interactive verification of this new checkpoint remains pending; this is not a completed six-phase audit or an installed build.

## Library entry into the common editor
- Added explicit “Place & edit” for pictures, video and audio library cards, reusing the existing placement and media/settings editor. The action creates a scene piece; it does not silently edit the source asset. Pictures now use the playhead or selected-word interval when placed through this route.
- Placement returns the persisted piece identity. A failed image save rolls back the new overlay; a changed project cannot open another project's newly placed clip in the current editor.
- Interactive isolated-project proof: Library → Place & edit → rotation 90° → Save → timeline right-click → Edit media retains 90°. The timeline exposes the shared animation, tracking, background removal and geometry actions. Screenshot: `/tmp/show-library-common-editor.png`. User projects unchanged.
- Eight targeted tests passed (library placement, transformed pointer coordinates and preview transport); script syntax and diff checks passed. Tests cover short selected-word intervals, saved item identity, failed persistence and stale project responses. Video/audio visual walkthrough and clay/moving-composition parity remain pending. No installation.

## Tracking selection checkpoint
- Object marking now pauses the source video before the drag and records the project, scene and frame time. A moved playhead invalidates the selection rather than capturing a different object appearance. A scene/project change during template upload cannot apply the old selection to the new scene. The temporary selection rectangle is removed on pointer release, including invalid selections.
- 18 follow-control tests passed, including clean-source tracking/render movement, loss/reacquisition and short-picture duration; one new browser-handler regression passed for pausing, ownership and a right-side marker coordinate. Script syntax passed. This does not establish tracking accuracy on all user footage, across all camera cuts or moving-background preview parity; those remain open.

## Audio, captions and clay checkpoint
- Fixed the common editor's audio dispatch: a jingle/audio now opens Sound mixer instead of the generic timing-only settings. Mixer entry from clips, character voices and talking stickers targets the selected track, scrolls it into view and focuses its volume. Missing active audio is explained explicitly.
- 31 existing audio/caption/clay tests passed: recorded character mixing and persistence, narrator ducking/muting, caption position reaching cue export, insertion clock alignment, clay timing through cuts and reviewed-draft placement. One new JS handler regression passed for audio editor routing, selected-track focus and missing-track feedback. Both scripts pass syntax checks.
- These checks validate specific stored recipes and rendering paths, not a completed visual/audible walkthrough of every combined editor route. Full mixer/captions/clay interaction and preview parity remain pending. No installation or user-project mutation.

## Planner and package checkpoint
- Removed compulsory gag cadence and the instruction to replace the background for every named place. The brief now asks for natural unadorned stretches and contextually justified background changes. This is a prompt correction, not evidence of editorial equivalence to the user's montage; that comparison and complete kanji recovery remain open.
- Diagnosed the version CI failure: its regex accepted only one patch digit, incorrectly rejecting 2.6.15. Corrected the test to accept multi-digit patch versions, without changing the product version or skipping the check.
- 35 targeted project-preview/gag tests passed. The last complete remote result remains 28 failures until a new full run establishes the current count. No claim of package-size savings or full validation.
- Final sequence explicitly includes phase 7: install the verified package after the outstanding fixes and validation, preserving the user's project and a rollback copy. Installation has not happened at this checkpoint.
