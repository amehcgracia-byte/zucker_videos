# Show: Claude changes and clay-character reference — 15 October 2026

## Repository comparison

Reviewed Codex PR #30 at `32e7acbd` against local `claude/show-app-build` at `651164da`. The PR remains open at the former commit at review time. Claude's branch includes Show mode implementation and project integration absent from the PR; these are not merely new HTML files and must be merged with history, not copied over the prototype. No branches were merged and no installed application was replaced for this review.

| Change | Evidence in Claude branch | Validation / remaining limit |
|---|---|---|
| Project-backed Show Studio, per-source sections, own materials and revision conflict protection | `c52165bd`, `964f6001`; `core/show_studio_store.py`, `server/show_studio_api.py`, `show_studio_store.js` | Store/API tests pass. User browser edits still need the explicit, hash-checked import, once per section. Do not delete browser data. |
| Packaged Show Studio entry and return to Zucker | `655c8e56`; app entry and build scripts | Code present; current installed binary not verified this round. Packaging is not proof that every preview effect exports. |
| Wide-screen layout and OpenCV model packaging | `bc9de343`; `show_studio_layout.js`, styles/build scripts | Code present. Real wide/narrow UI interactions and installed package need validation. Missing face model returns no detections rather than a rendering crash; that does not prove correct framing. |
| Direct original-video scene playback; render effects only on request | `651164da`; `playQuick`, scene segments/source URL | Scene/review tests pass. Quick view explicitly excludes effects. Browser playback over several removed sections is still a required acceptance check. |
| Large-media range responses capped to 8 MiB | `651164da`; `server/media.py` | Range tests pass. It bounds a response allocation; it does not load the entire source into RAM. Long playback/seeking across successive responses needs browser/package validation. |
| Transcript on top, click word to seek, selected-word context menu | `651164da`; `saidSelection`, `sayMenu` | Implemented, not manually exercised this round. Menu creates effect proposals or opens a source picker; it is not proof of generated material or final rendered output. |
| Native export of the full Studio composition | Still pending according to code/handoff | Existing Show render and Studio's editable composition are different paths. Do not advertise MP4 parity until a composition containing these new tools exports and is compared with preview. |

Ran, without editing Claude's checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_show_review.py tests/test_show_scenes.py \
  tests/test_show_studio_store.py tests/test_show_studio_api.py tests/test_media.py
```

**40 passed in 86.11 s.** This is a targeted suite, not full CI, UI acceptance, Windows or installed-app certification. Disk connection, backup restoration and current installation in the pasted Claude message were not independently verified.

## Reference: Remembering Anthony Bourdain

Source: David Choe, https://www.youtube.com/watch?v=QpCd4BetnoQ (about 4:15).

Visual review used a private low-resolution copy, a six-second sampling pass and closer samples around animated passages. Timecodes below are approximate visual anchors, not exhaustive frame-accurate edit boundaries. No claim about the production software, actual fabrication technique, soundtrack timing or AI use is inferred from the pictures. Reference frames/video are not committed or bundled as library assets.

| Approximate anchor | Visible device | Candidate tool / use |
|---|---|---|
| 0:06, 0:42–0:44 | Archive photos/video placed beside or over the speaker | B-roll insert; picture-in-picture, preserved voice bed, explicit duration |
| 0:08–0:12 | Clay-like characters in a miniature group scene, recognisable costumes and poses | Persistent personal puppet plus supporting characters and a reusable set |
| 0:24 | Soft foreground/background and close view inside a miniature setting | Shot presets: establishing, two-shot, close-up, reaction; depth as part of the asset/scene |
| 0:46 | Cloud-like insert with a drawn spiral introducing the miniature scene | Short thought/recall transition, with editable timing |
| 0:48–0:54 | Conversation at a miniature table; hands, props, individual close-ups | A storyboard of actions, not just a talking head overlay; prop placement and pose variants |
| 1:06, 1:16–1:18, 1:30 | Same character in close-up, outside a small house and at a table | Reusable cast across several sets; identity must survive changes of viewpoint |
| 1:24 | Circular cutout of the speaker over archival footage | Masked picture-in-picture, independent layer movement |
| 1:42 | Close view of a real hand painting the portrait | Material-detail cutaway: reveal the making of a picture rather than only the finished picture |
| 1:48–1:54 | Miniature character with a megaphone, large pink cutout shape and drawn marks around a sculpture | Mixed-media collage: separate original/cutout/drawing layers, scale changes and graphic emphasis |
| 1:58–2:06 | Archive imagery inside a physical-looking monitor frame | Reusable screen/frame matte with video inside |
| 2:18 | A grid of small circular pictures behind the speaker | Repeated collage / contact-sheet backdrop |
| 2:24–2:30, 3:24–3:30 | Archival footage and painting/action cutaways between present-day shots | Contextual B-roll and time shifts with the narration retained |
| 3:02–3:06 | Painting in the miniature world and group character reaction | Props that carry the story; reaction shots for recurring cast |
| 3:48–3:50 | Character in an armchair in a lit miniature room | A sustained little scene with body pose and set lighting, not constant effect changes |
| 4:06–4:12 | Plain end message, portrait and hand-drawn identity | Quiet ending / reusable credits and visual identity |

## Recommended next step

1. **Consolidate and validate the real app first.** Merge the Claude branch through a reviewable PR, exercise transcript selection/context menu, wide/narrow layout, browser-to-project migration and source switching. Keep the current browser edits. Do not build another disconnected demo.
2. **Close the preview/export gap with one vertical slice.** A short saved scene containing a trim, original voice, close-up, imported image/mask and music should reload and export with matching timing and composition. Add capabilities to the composition renderer through that path.
3. **Make one canonical personal clay puppet.** Use approved frames/photos of the user to agree face, hair, clothing, proportions and handmade clay texture. Save a character id, version, approved reference sheet, transparent views, expressions and mouth/hand variants in the project library. The same id is reused across scenes; do not independently regenerate identity for each shot. No clay identity asset has been made by this review.
4. **Prove a 5–8 second miniature memory.** One set, the personal puppet, one prop, a close-up/reaction and the user's voice. Start with layered 2.5D/cutout animation if needed, explicitly labelled as such. Rotating a flat picture is not a true 3D puppet; arbitrary body acting and new viewpoints require a rigged model, captured stop-motion or separately reviewed generated animation.
5. **Expose it in the existing scene editor.** Suggested English commands: `Clay memory`, `Character`, `Set`, `Action`, `Voice`, `Camera`, `Preview`. Use existing contextual controls, tracks, project library and approval/revision flow. Store approved reusable assets separately from disposable previews; keep origin and character version.

The app can propose a memory scene from the transcript with source-time evidence, but should ask for missing visual details and leave uncertain interpretations editable. Generated animation is a candidate until reviewed; a reusable sheet helps consistency but does not guarantee it in an external generator. No video generation provider, automatic 3D reconstruction or arbitrary character acting is implemented by this document.
