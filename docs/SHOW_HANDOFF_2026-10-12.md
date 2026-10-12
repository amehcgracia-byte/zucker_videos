# Show: visual direction and prototype handoff — 12 October

## Accepted direction
The user approved rough pencil, crayon and marker drawings: imperfect contours,
unfilled patches, expressive silly characters, no glossy sticker finish or white
sticker rim. The approved concept sheet was generated with Codex image generation,
NOT the local Hugging Face model. Local uncommitted `core/gag_paint.py` uses
DreamShaper 8 LCM, four steps, 512px, guidance 1.0; its negative prompt is only
passed when guidance >1. The director rotates across 22 styles. Port and validate
the approved art direction on that actual model; do not claim it already matches.

## Corrections from the user
- Existing objects must retain their exact source pixels. Pixelation is acceptable.
  Generated reconstructions were rejected. Optional closeups improve detail.
- Zoom means reframing the whole video, including the host, with real cuts to
  closeup/B-roll footage while the principal speech audio continues. Enlarging a
  floating object alone is not the requested edit.
- Talking: move a rectangular original mouth/jaw patch up/down and expose black
  behind it. Preserve the smiling mouth shape, no outlined circle or electronic
  ring. Real talking lips may be an optional future gag. Voiceover may be supplied
  by the user. Do not pretend rhythmic demo motion is lip sync.
- Position and scale must be editable by dragging and sliders. Hide adjustment
  outlines during playback. Camera movement requires separate controls from object
  motion. Track moving subjects or request adjustment; never claim a fixed mask tracks.

## Delivered prototypes (not production integration)
`tools/qa/show_direction_demo.html`: self-contained local browser proof of concept.
Loads two local videos via file inputs, cuts at 3s to closeup until 8s, retaining
main audio; animates full-frame crop zoom; adjustable original-pixel jaw patch with
black cavity. No files uploaded, no export, tracking, voice sync or automatic semantic
selection. Draw a mouth rectangle in closeup adjustment view. Coordinates are output
space and need readjustment when zoom/composition changes.

Earlier local `advanced.html` demo supports draggable exact RGB-preserving cutouts,
size/motion controls and JSON direction download, but its object-only zoom and
synthetic mouth were rejected. Kept locally for reference, not the implementation
target. Actual media and generated assets are intentionally excluded from GitHub.

## New footage inspected (sparse frames, not full transcription)
User supplied C0272, C0273, C0274, C0277 alongside C0271. C0272 (130.08s) includes
fridge figure closeup; C0273 (85.44s) includes host/detail views; C0274 (364.32s)
moves through room/fridge details; C0277 (118.56s) includes host and plant. Do not
assume each clip is a single clean insert or already matched to transcript.
Files remain inside the user's local Channel Prueba project, not in this PR.

## Requested next work
1. Integrate Show with main talking video plus optional environment/character
   closeups; select useful intervals and match inserts to spoken references.
2. Guided optional steps: record talking video (suggest up to ten minutes, not a new
   hard limit), capture closeups of mentioned things, optionally record their voices.
3. Host closeups, wider reveals and camera reframes; original-pixel mouth animation;
   reviewed subject coordinates and timing before rendering.
4. Rainbow steam from mugs as an optional recurring signature. Thinking/math overlays,
   face drawings and reactions during pauses, context-sensitive rather than random.
5. Persist reusable characters, masks, mouth regions, style recipe, provenance and
   user placement in a library. New objects still need new assets/regions.
6. Validate actual DreamShaper prompts; avoid automatic rotation into unrelated styles.
7. Production render, audio sync, cancellation, cache recipe/versioning, Windows and
   macOS tests remain necessary. This prototype does not establish those guarantees.

## Repository boundaries
PR #32 at fc106448 contains Show phase 1. The main checkout has additional uncommitted
Claude changes (gag modules, UI and Show fixes). This work does not overwrite or
commit those changes. Audit PR #30 remains separate. No private footage, thumbnails,
transcripts or voice recordings should be added to public git/Actions artifacts.
