# Actual Show render vs Show Studio tools — 15 October 2026

## Exact reviewed output and scope

User identified `Show-Studio-Pilot-show-20261015-141337_composed-captions.mp4`, in the local Show Studio Pilot project's exports. Probe: **381.7667 s, 1920×1080, H.264 at 30 fps, AAC, 118,337,560 bytes**. Export manifest rounds duration to 381.8 s. Reviewed visual samples every four seconds across the whole output, plus its plan/proposals/scenes and both stored Studio compositions. This is a visual/structural review, not a claim of listening to every second or inspecting every frame.

Read-only comparison used local `claude/show-app-build` at `651164da` (clean checkout); user application/project were not closed, changed or re-rendered. Codex preview work is in PR #30. Screenshots, reference sheets, project data and personal footage stay private and are not committed.

## Preserve what the user likes

The current automatic editor's montage, punch-ins, captions and contextual cutaways are the base to keep. The plan contains **231 shots / 230 cuts**, 55 approved proposals (23 cutaways, 22 drawing requests, 6 maths, 4 warps), and six character proposals that remain unapproved. The export reports 446 captions, 23 close-ups, 46 generated picture layers and zero speaking character inserts. Picture layers are not a one-for-one count of approved drawing requests: maths generates multiple layers.

The source shots remain in ascending source order. The 23 cutaways and changing framing create visual changes without globally reordering the main speech. Do not remove those decisions while integrating Studio controls. Examples visible in the render: punch-in/caption changes throughout, maths around 0:32, 1:20, 2:36 and 5:00, contextual images around 1:32/1:36 and 4:36, and a pronounced swirl around 5:31–5:33. These are approximate review anchors.

## What is different, and why

| Capability | Actual render / project evidence | Required integration |
|---|---|---|
| Automatic cuts, framing and captions | Present and liked. `core/show.py:render_reviewed_show` calls `render_show`, approved proposals and the composition/caption pass. | Keep this base and its clock. Manual effects must compile onto it, not replace it with the demo edit. |
| Handmade drawings | Requests exist; several visible outputs are polished icons or anime-like stickers (e.g. lightbulb around 1:08, character around 2:00 / 3:28 / 4:12), unlike the approved rough drawings/clay. | Preserve useful placements/gags, but resolve their assets through the approved library/style. No silent substitution of unrelated characters. |
| Missing drawing requests | Manifest warning lists six failed clean-background requests: lightbulb twice, thumbs-up, camera, spiral and guitar. A guitar/lightbulb can still appear elsewhere because repeated requests are distinct. | Report result **per effect id**: applied, failed asset, unsupported, disabled. Current subject-only warning is insufficient to locate each omitted request. Do not advertise every approved request as rendered. |
| Maths | Present as multiple orbiting formula pictures from `core/gags.py:math_items`. | Keep the useful effect; expose placement/scale/amount and compare with the Studio version before promising parity. |
| Finger-following ink / painted circle / drawn spiral | No finger proposal kind or Studio event compiler in this render path. The spiral region uses an approved full-picture `warp:swirl`; its drawing request was among failed backgrounds. | Native source-coordinate fingertip path must be imported, mapped through cuts and camera framing, and actually rasterized. A swirl distortion is not an equivalent fallback. |
| Coffee steam | No steam proposal was approved or even present in this project's proposal list. Server does support a manually placed steam proposal. | Compile Studio's coffee event/anchor and visibility; test after zoom/cut. Absence here is not proof the server steam implementation is broken. |
| Speaking original-photo characters | Six are merely proposed, duration zero; region/voice are unset. Export has zero character inserts. `show_proposals.check` requires region and (for video sources) voice. | Surface missing setup and permit deliberate approval. Do not auto-approve instruction-track audio or guess a mouth. Reuse Studio's saved photo/mouth/voice controls through an explicit adapter. |
| Bill Gates / photo masks, chosen face, voice-driven jaw | Not in this project's stored Studio event list, nor compiled into the approved Show proposals. Server render does not consume `maskAsset`, `faceTrack` or the new mouth mode. | Both import the actual user edits and implement the renderer; storage alone does not deliver these. Preserve the original-photo pixels, selected tracking and actual voice cue. |
| Moving cutouts / custom backgrounds / keyframed paths | Browser preview supports them; no production bridge from these Studio fields is used by the render. | Compile the supported layer recipe, with mask/video alpha and keyframes; reject unsupported operations explicitly. |
| Clay characters / story / timed gestures | Created after this render; private samples and renderer are newer than Claude's branch. | Import approved v2 and the source-clock story; do not regenerate the polished v1, invent band portraits, or loop a default gesture. |
| Music / sound effects | No approved jingle proposal in the inspected list. | Keep host voice continuous under visual replacements; add only chosen music/SFX with explicit levels/ducking and provenance. |

### Two concrete integration gaps

1. **Different recipes.** Production `render_reviewed_show` reads `show_plan.json`, `show_proposals.json` and `show_scenes.json`; it never reads `StudioStore` or the Studio composition. `server/show_studio_api.py` persists/serves the Studio work, but does not export it. There is no production consumer of `motionKeys`, `maskAsset`, `faceTrack` or `autoHands` outside the store/routes. This is the structural reason preview and render diverge.
2. **The user's richer browser edits have not been migrated into this project.** Stored workshop composition is the 44-second proposal with one video, audio, zoom, two cuts, two fingers, spiral and coffee; opening has 88 seconds, original/audio, one finger and 17 captions. Neither stored event list includes the photo masks or backgrounds the user edited in the standalone browser. Do not call this data loss: browser-to-project import exists and needs to be run/verified in the browser where the edits live. Preserve both before merging; never replace either with a default proposal.

## Source time is not edited time

The production plan is on a **30 fps** frame grid. The local workshop/landmark preview uses **25 fps**, with its 44-second excerpt starting at full-source 466 s. Use seconds/source identity to bridge them; never reuse a frame number at the other fps.

In this exact frozen plan:

- Full-source **223.62–244.90 s** (our 21.28-second raw-voice clay study) retains **19.0133 s** after the automatic cuts, at edited **121.7867–140.8000 s** (about **2:01.79–2:20.80**). It intersects 11 kept shot pieces.
- Full-source **466–510 s** (44-second workshop) retains **32.8334 s**, at edited **312.5667–345.4000 s**. Some source timestamps lie in removed gaps and have no direct output timestamp.

These clocks currently have no opening-title/character insert offset (empty identity title/logo, no intro, zero character inserts). Any future intro/voice insert also needs its time mapping. For clay integration, retain the edited host audio and trim/retime the visual source pieces through the same map. Do **not** overlay an unmodified 21.28-second audio/video insert at 2:01.79: that would undo automatic silence cuts or desync captions.

## Compositing choices requested by the user

Expose these as views of the **same** clay scene/layer, with consistent clock and captions:

- `Full scene`: short full-screen clay cutaway, return to the host at a chosen thought/question/reaction;
- `Picture in picture`: clay scene smaller on the left, host remains visible;
- `Thought bubble`: rounded cloud mask with small thought circles anchored toward the host; position/size editable, avoids face and captions;
- optional short `Blend` entering/leaving, with duration and opacity control.

Host voice continues by default; mute the imported scene's audio unless deliberately chosen. User wants the automatic editor to alternate these meaningfully, not leave a long uninterrupted clay block or fire all effects together. No thought-bubble/blend render is claimed delivered by this review.

## Narrative motion correction delivered in the private study

The first study's repeating `floor(time*3) % poseCount` was mechanical. It is replaced by **one-off source-word-timed pose cues with held pauses** (`show_clay_pose_model.js`, shared concept in the Python renderer). Cues use the newer project's cached word timings: dream 224.23, everyone 227.85, coming 228.79, saying 229.87, question 231.07, city 235.55, forever 237.31, alternative 238.17, village/100 people 240.79 and little town 242.87. These source times are relative to the study's 223.62 s start in `story-v3.json`.

The presenter gestures once during the dream and rests; neighbours approach during “everyone … coming”, then think; the city question gets a small open-hand reaction and a reflective pause; the village gets one step/welcome followed by a hold. Last pose holds indefinitely, never wraps. This is still a small set of generated poses, not full acted video or phonetic lips.

Node tests cover exact cue edges, long holds, no implicit repeat and source-clock behaviour after cut changes. Full preview render checks pass. Mean decoded pixel difference over quiet intervals is 0.102 (2.0 vs 3.8 s), 0.039 (9.2 vs 11.5 s) and 0.053 (19.6 vs 21.0 s), versus 9.684 across a real pose change (0.3 vs 1.1 s), on the 0–255 scale; tiny static variation is encode noise. The v2 loop clips remain private for comparison; v3 is the new preview.

## Handoff prompt for Claude

> Read this review and `docs/SHOW_CLAY_CHARACTER_WORKFLOW.md` on `codex/crossplatform-ui-audit` / PR #30 before changing the real Show. Keep the automatic montage, captions and good cutaways from `Show-Studio-Pilot-show-20261015-141337_composed-captions.mp4`. First reconcile branch history with your `claude/show-app-build`; do not overwrite either worktree. Import the user's actual browser edits with hashes/revision checks, retaining both originals. Implement a versioned composition bridge between Studio source-clock events and the automatic edited plan. Publish per-effect applied/failed/unsupported/disabled results; never silently replace a finger drawing with swirl or a missing original object with an anime sticker. Resolve approved materials from the project library. Clay's approved character is v2; current motion is word-cued with quiet holds, not loops. Private story sources remain outside Git. Support full-scene / return-to-host / PiP / thought-bubble views of the same visual layer, keeping the edited host voice and captions. The source 223.62–244.90 excerpt maps to 19.0133 edited seconds at 121.7867–140.8 in the frozen plan; respect all 11 kept pieces, not a single offset. Validate short representative clips against preview before any full render, keeping the good current export. Clearly separate implemented render support from UI-only tools or prepared generation requests. Do not regenerate/upload personal footage, add a paid provider, delete browser edits or install/close the user app as part of this reconciliation without a separate request.

## Next acceptance clips (not a full hour-long rerender)

1. Original-photo character with actual approved mouth/voice; compare preview/export.
2. Finger circle/spiral through a camera crop and removed silence; no substitute warp.
3. Cup steam through visibility and zoom changes.
4. Clay on the 11-piece edited source interval: word-cued holds, one full-screen segment, return to host, then PiP/thought bubble.
5. Missing asset: clear effect-id warning and preserved clean base, not a silent unrelated drawing.

No production bridge or new composition mode was installed by this review. Private screenshot review, plans and renderer-call tracing support these findings; it is not a claim of zero errors or a full cross-platform rerun.
