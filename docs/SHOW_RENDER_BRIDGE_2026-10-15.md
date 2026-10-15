# Show Studio in the real render — 15 October 2026

Branch `claude/show-app-build`, after merging PR #30 up to `7db8a015` (`d8fe9e9f`).
Answers `SHOW_RENDER_PARITY_REVIEW_2026-10-15.md` and the reconciliation brief.
Private recordings, frames, the clay sheets and the user's project stay out of Git;
every validation output named here lives in a private scratch folder.

## What changed structurally

`render_reviewed_show` used to read only `show_plan.json`, `show_proposals.json`
and `show_scenes.json`. It now also compiles every saved Show Studio section
(`show_studio/composition.json`, `show_studio/sections/*/composition.json`) and the
clay placements (`show_studio/clay.json`) onto the automatic edit
(`core/show_bridge.py`), and draws them frame by frame in the gag pass, under the
stickers, before captions. The automatic montage, framing, cutaways and captions
are untouched; Studio effects are drawn on top of them.

**Clocks.** Studio events are on their section's clock (seconds from the
section's start in the original: workshop 466 s, opening 0 s; 25 fps tracks).
The bridge maps them through the plan's kept shots by *time*, never by frame
number: a moment the edit removed has no output time, a gesture keeps its own
clock across cuts. Workshop 466–510 s → 20 kept parts, 32.83 s at
312.5667–345.40 s; clay 223.62–244.90 s → 11 kept parts, 19.0133 s at
121.7867–140.80 s (both as in Codex's review).

**Framing.** Each output frame shows a window of the source
(`window_at`, the same arithmetic as the render's zoompan). Drawings are laid out
in Studio pixels, exactly as the preview's zoomed canvas, and then mapped through
that window: a finger trail stays on the finger through punch-ins and pull-outs.

**Results.** Every Studio effect and every clay placement gets one result, shown
before rendering (last storyboard step, `GET /api/v1/show/studio-effects`) and
written to the export manifest (`studio_effects`):

| status | meaning |
|---|---|
| applied | drawn in the render (detail: kept parts, seconds, what it follows) |
| disabled | switched off, hidden by the automatic finger ink (as in the preview), or serious mode |
| superseded | on purpose: Studio zoom/cut and Studio captions yield to the automatic edit's |
| failed | cannot be drawn: its moment was cut, its data or picture is missing (named) |
| unsupported | a kind the render does not draw yet (named, see below) |

An applied effect with a problem (a mask whose voice track is missing: jaw still)
carries `warning`. Failed, unsupported and warned effects become export warnings.
Every storyboard proposal also gets a result by id (`show_effects`), so a drawing
the painter could not make is named with its time, not hidden in a subject list.

## Tools now in the render, and how close they are to the preview

`tools/qa/show_render_parity.py` renders a window of the real edit, then lets the
preview's own functions (extracted from the Studio files as they are) draw the
same frames in the browser and compares pixels. Measured on the pilot:

| Tool | Port | Preview vs render on real frames |
|---|---|---|
| Automatic finger ink (detected index runs, gaps kept) | `ShowPaint.timedTrails` + `pigment` (stamp sampled as Chrome does) | 92–97 % overlap of the ink, ~3/255 mean difference, across cuts and zooms |
| Reviewed / marked fingertip path, circle or spiral at the tip; spiral effect | editor `finger()`, `spiral()`, `stroke()` | geometry equal to Node reference values |
| Cup steam (positions, visibility, only while the cup is in the frame) | editor `coffee()`, including Chrome's dark gradient fade | same pixels (equal counts), hidden when a close-up leaves the cup out |
| Photo mask: original photo clipped, followed face (selected track or section track), turn, voice-driven jaw, mouse offsets | `show_mask_geometry`, `show_voice_envelope`, mask library drawing | 99.3 % overlap, < 1/255 mean difference |
| Keyframed paths (position, size, turn, opacity) for masks and library pictures | `show_motion_model.at` | 97.5 % overlap on a picture flying along its path |
| Hand painting (strokes revealed in order) | `drawPaint` / `reveal` | 99.3 % |
| Formulas and words | editor `formula()`, fonts as the preview (fallback font for missing glyphs) | same placement and font; text edges differ (anti-aliasing) |

The finger ink is not a `warp:swirl`, and nothing is substituted: a drawing that
cannot be made is reported.

## Clay v2 in the montage

`core/show_clay.py`, `tools/import_clay_story.py`. The approved character v2 and
`story-v3.json` are imported into a project: the pose sheets and the character
reference into the project library by SHA-256, the records and placements into
`show_studio/clay.json` (each replaced version kept in `show_studio/history/`).
Poses are cued once by the words and held (`show_clay_pose_model.js`); on all 532
frames of Codex's own render the same pose is chosen (mean difference ≤ 7/255,
the scaling filter). Placements are on the story's clock, which is the source
clock, so they follow the host's voice through the cuts; the host's voice and
captions are untouched.

Modes, all with in/out, position, size, opacity and fade, edited in the scene's
**Clay memory** block: full clay shot, small clay window (left by default), thought
bubble whose small circles lead to the host's head, brief blend over the host;
gaps return to the host. Window and bubble move aside when a framing would cover
the face (switchable). The first proposal uses each mode once along the story.

## Validation clips (private outputs)

1. Finger drawing through framing changes (edited 313.0–318.5): ink on the finger
   through 1.65× → 1× → push-in 1.65–1.8× → 1×; parity above.
2. Mask with face tracking and voice jaw, plus a moving picture, paint, formulas
   and words (validation copy of the project, edited 328.5–336.0): parity above.
3. Cup steam through zooms and cuts (edited 320.0–327.0): drawn in wide shots,
   absent where the close-up leaves the cup out, as in the preview.
4. Clay with holds, return to host, bubble, window, blend (edited 120.3–142.3):
   all modes present, 22.0 s video and audio, full decode clean.
5. Missing resource: missing finger data, an unmarked cup, a missing pose sheet,
   a missing voice track, a browser-only picture → each named in the report and
   the warnings; nothing drawn in its place (unit tests).

All clips: H.264 1280×720, audio present and equal in length, full decode clean.

## Not done yet (honestly)

- **The user's latest browser edits are not in the project.** The project's
  compositions are still revision 1 (workshop 9 events, opening 20). They live in
  the browser where they were made; "Save to project…" in the Studio editor copies
  them (hash-checked, revisions kept in `history/`). Until then the render shows
  the saved revision.
- **Not drawn by the render yet** (reported by name, never replaced): cut-out
  people and their background, moving body parts, added videos, added music and
  sounds. Pictures attached only in a browser are reported as failed.
- The thought bubble is a cv2-drawn cloud outlined with the ported crayon; it
  should be reviewed against the hand-drawn look the user wants.
- The clay story is imported only into the validation copy, not into the user's
  project; importing it there adds placements that change the next render, so it
  waits for the user's go-ahead.
- Not presented as finished: automatic generation, general segmentation, the
  hat/glasses gag. The drawing generator via ChatGPT/Codex is not integrated.
- No app rebuild or installation was done for this reconciliation, and the
  installed app was not touched.
