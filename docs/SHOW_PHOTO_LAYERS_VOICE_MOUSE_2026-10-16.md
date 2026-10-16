# Photo layers, voice-over and contextual mouse tools

## What changed

- Photo Studio has **Save image to gallery** directly after the automatic cutout. No mouth, voice or pivot is required. **Keep background instead** reverses the mask. Both modes retain the photo's original RGB values; only alpha changes. New assets stay in the project. Legacy shared characters remain readable.
- **Animate original parts** lets the user mark eyes as separate photographic regions. **Test glance** renders one glance left, a hold, a glance right and a return. The exported `look` animation uses the same original-pixel compositor. Selected pixels are translated by integer coordinates; only the small vacated holes are filled from the surrounding photograph. No generated replacement eyes. Eye regions can be reopened from the selected character's context menu.
- Automatic selection is macOS Vision when available, with GrabCut and a box/keep/erase brushes as fallback. This does not promise automatic detection of every arbitrary object. These are still-photo layers; continuous person/background segmentation and tracking in arbitrary footage are not implemented by this change.
- Character scene editors and the photo-character rig share an actual **Voice-over** recorder/drop target. Record explicitly requests microphone permission, displays REC and Stop, releases tracks when stopped/closed, and has a three-minute limit. Audio can also be imported or dropped. It is decoded to project-local WAV and assigned to that control; it is not treated as dictation/subtitle text. Project changes prevent an old recording being attached to the new project.
- Stage and image context menus group parameters instead of exposing one long list. Layers have Copy, Cut, Paste, Duplicate, Undo and Remove. Paste/Undo no longer require a current layer selection, including after cutting. Video menus group Effects and Cuts/audio; the existing cutting desk handles actual linked video/audio cuts. Show Studio groups its existing commands into Cuts/audio, Effects/animation and selected-element tools.
- Wheel events over the video affect its playhead, not an unrelated inspector. Studio Ctrl/Cmd+wheel adjusts the selected camera scale; ordinary wheel scrubs. Scrollable panels contain their own scroll chain. Native text/input menus stay available; selected transcript words have an explicit **Animation for selection** button (Alt-right-click remains a shortcut). App tool menus are HTML contextual menus, not additions to the browser's native OS menu.
- Cutout cache keys include the full photo content digest, so replacing an early blurry file with a new close-up cannot silently reuse its old mask. Concurrent requests cannot replace the latest preview with an older result.

## Evidence

46 focused tests passed across photo characters, pixel layers, scene models, Show review and Studio API. The JS timeline and clay cue tests also pass, as do JS syntax checks. New tests cover exact original-pixel translation, held glances without looping, invalid selections, project-local gallery storage, reopenable eye rigs, export recipe propagation, actual rendered eye previews, valid/invalid audio decoding and stale-project rejection.

Using the real UI on an isolated project at port 8794: chose C0274's 216-second photograph, obtained a macOS automatic cutout, saved it to the gallery, opened the eye/mouth rig, imported an existing M4A, verified the new voice selected automatically, and saved the character with that voice. The stored voice file exists and the placed overlay is in the project JSON. Then cut the layer, opened the video context menu with no selected layer, pasted it back, and verified the editable clip persisted again. A copied fixture initially retained an asset path belonging to its source project; rebinding that fixture resolved placement rejection. This was not classified as a product rendering failure. The UI did expose a real false-success bug: `addToScene` previously ignored failed saves. It now reports that the gallery save succeeded but scene placement failed instead of claiming success. The user's running app and active project were not restarted or replaced.

The physical microphone/OS permission flow has **not** been exercised by recording the user's room. Windows microphone/WebView behavior and the contextual mouse interactions have been exercised on this Mac but still need Windows validation; the focused suite is not a full green application audit. The isolated source project's older media preview state was not used as evidence of correct full-show playback.

## Exact sample character and private material

The eye sample is the **red-bodied lightbulb-shaped character on an orange background on the fridge**, not Simón's green-cap plant figurine. Source: recovered C0274 frame at 216 seconds, with both eyes unobscured. Its private clip is:

`work/show-motion-sample/pilot/clay-memory/fridge-eyes-preview.mp4`

Review page (while the local sample server is running): `http://127.0.0.1:8793/fridge-eyes.html`.

`work/show-motion-sample/FRIDGE_EYES_HANDOFF_2026-10-16.json` records source, regions and the project-local character path. Do not publish these recordings/photos/voice clips. The earlier clay asset pack stays separate in `assets/clay/simon/`.

## Claude integration checklist

Read this document and the current handoff. Preserve the exact original photographic cutouts; do not substitute the clay character or an AI-generated face for the fridge character. Integrate `web/show_voice.js` with `web/show_characters.js`, `web/show_review.js`, the API endpoints and `core/show_pixel_layers.py` together. New voice controls use `data-voice-input` and dispatch `show-voice-ready` with the saved audio reference. New eyes use the saved character rig and the `look` action through `core/show_scenes.py` and `core/gag_render.py`; showing rectangles alone is not the animation.

Keep the project-recoverable deletion fix from `aa16eb1f`. Do not reconstruct from a commit older than these changes or restore an old flat context menu. Run the focused tests, then test microphone permissions, drag/drop, cut/paste on an empty lane and the actual rendered result in each desktop package before claiming those platform flows are verified. Do not install or close the user's running application as part of this handoff.
