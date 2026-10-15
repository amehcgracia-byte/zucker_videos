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

## Follow-up: frozen image, recorded voice and rainbow steam
The demo now freezes a selected resource frame, zooms into it for 1.2s, then plays
an adjustable interval of the resource's original audio while main narration is
paused. Main narration resumes afterwards. Mouth displacement follows an RMS audio
amplitude envelope with a silence threshold (not phoneme lip sync). Images are also
accepted as resources, with no voice attached. Frame time, voice start/length and
mouth region remain manual; freeze and voice are separate controls.
Rainbow steam is a procedural translucent particle preview on the main shot with
manual placement. No cup detection/tracking. No production renderer integration.
A short local excerpt of C0273 supplies the sample voice; its wording has not been
independently transcribed/approved in this demo. No media added to GitHub.

## Polygon tools and playback handling
Added unlimited polygon vertices, close/undo/reset, original-pixel masked patch
movement (mouth, nod, sideways, rotation), X/Y offsets and browser-local settings.
Black is exposed behind moved regions: background reconstruction is not provided.
Coordinates remain output-space and depend on zoom. Only one selection/resource
is supported; a per-shot approval queue remains a requested production feature.
Playback now primes the secondary media from the user gesture and reports rejected
play/decode errors instead of silently pausing. This addresses an observed code
failure path; the user's exact stall was not independently reproduced. Prototype
still has no video export. Do not present playback as a render.

## Simple selection restored
Rectangle drag is the default. Advanced points are explicitly selected from a
selector; click the first vertex or press Enter to close. Closure uses a convex
outer hull, removing interior vertices and crossings. This intentionally bridges
concavities: it is not precise concave segmentation. Source pixels are preserved.
Undo/redo cover selection edits, closing, clearing, cut and paste. Text inputs keep
native shortcuts. Focused canvas supports clipboard JSON copy/cut/paste via native
events; no global clipboard interception or HTML clipboard execution. No custom
right-click menu replaces the browser menu. Ctrl/Cmd+Z and shifted redo supported.

## Secondary black frame fix
Observed black secondary canvas in browser. Removed unconditional capture on
loadeddata/seeked (audio preparation could overwrite the frozen image). Capture is
now explicit, waits for current-frame readiness, uses a candidate canvas and rejects
near-black candidates while preserving the previous still. Four-second timeout
reports failure. Demo's independent JPEG cannot be overwritten by media events.
Browser verified visible wire figurine after reload and fixed-preview selection;
JavaScript syntax checked. Near-black rejection is a prototype heuristic and can
reject intentionally dark frames; production should offer an explicit override.

## Guided storyboard visualization
`tools/qa/show_storyboard_demo.html` is a new standalone interactive walkthrough:
identity/logo/title → optional imported intro → greeting → frozen character with
source audio → continuation → review. Example suggestions are authored, not AI
analysis. Accept/plain decisions are recorded per scene. Local images can be added
to the session library; overlays can be dragged/resized and edited/removed through
a context menu. Import source videos/images; prepare an editable drawing prompt
without sending it. Review plays the example sequence and downloads decision JSON.
Media use object URLs for the current session only; JSON excludes media and is not
project persistence. No video export. Logo is optional and no intro asset is fabricated.
The advanced editor opens in a dialog but adjustments are NOT transferred into the
storyboard; integration remains pending. Browser checked identity and greeting views.
Local sample: http://127.0.0.1:8792/storyboard.html?demo=1 . Private demo media/assets
stay outside git. For repository use, import local resources without demo query.

## Scene-specific element animation
Storyboard context menu now separates Animate element from mouth editing. Added
up/down, hop, grow, grow-and-return, looping scale and sway with intensity, in-place
preview and video-clock playback. Settings belong to each scene and enter decision
JSON. Removed the incorrect route that opened an unrelated demo video when editing
an overlay. Mouth integration is explicitly unavailable rather than opening the wrong
source. Scenes without overlays do not receive overlay motion tools.

Production analysis contract: transcribe main and resource audio with word timing;
separate spoken instructions from character dialogue; identify references and comedic
beats; match entity appearances with resource footage and voice intervals; propose
camera crop/cut and effect with evidence and confidence. Example: “this guy could
talk” should suggest the referenced character closeup and recorded voice, not coffee
steam. Ask for review when matching is uncertain. Emit a per-scene intervention plan
(entity/asset IDs, source/voice intervals, action, parameters, rationale, status).
Tools are determined by selected asset/action capabilities. Analysis is NOT implemented
in this prototype; no claims of automatic Whisper contextual direction.

## Local original photo collection
Extracted 97 full-frame 1920x1080 JPEGs from the four resource clips: C0272 (22),
C0273 (14), C0274 (46), C0277 (15). Eight-second sampling plus extra character frames;
not exhaustive object detection or a curated sharpness selection. Some frames contain
hands/blur/transitions. RGB appearance is decoded from originals and JPEG encoded;
no generative reconstruction. Original videos/audio are untouched.
Local folder: /Users/macbookair/zucker_videos/work/show-motion-sample/photo-library
Contains manifest.json with source path/time, index.html gallery with source filter,
manual selection and JSON download, and extract.py. Photos remain local, excluded
from this PR. Next capture guidance should ask for photos of mentioned surroundings
and optional voice/video resources, not mandatory video for every object.

## Additional user-supplied Claude context
Claude reports a local crayon LoRA experiment (nine drawings), style improvements
but subject drift and paper backgrounds. These are reported results, not independently
validated here. A proposed Codex subscription-backed image generator integration is
UNVERIFIED: generating images in this conversation does not prove Zucker can invoke
it unattended, nor establish billing/usage guarantees. Validate supported invocation,
file delivery, transparency and failure behavior before advertising it. Keep local
painting/manual drawing import as explicit alternatives; no silent provider fallback.
Local transition comparison at /transitions.html offers cut, zoom and whole-photo
bounce, using private media. It is a timing/style demo, not semantic alignment proof.

## Character mouth restored in storyboard
Integrated same-scene still-image mouth canvas with rectangle selection, original
pixel patch movement, aperture slider, remove, demo motion and audio-amplitude
reaction. Per-scene coordinates survive scene navigation and enter decision JSON.
Browser manually verified Character → Edit mouth → drag rectangle → next → previous:
region still visible on original character. This replaces the disabled mouth action
for still scenes. Overlay mouths and polygon tools remain separate/unintegrated;
reload persistence, full project storage and real export remain unverified/pending.

## 2026-10-13 — Sonido y cámara en la maqueta

`tools/qa/show_storyboard_demo.html` añade controles por escena: voz de toma/importada/silencio, dos melodías sintetizadas de prueba, música importada, volumen y atenuación fija durante voz, intervalo con fundidos de música, muelle/campanita y censura manual con silenciamiento más pip. Zoom rápido de plano completo con centro, inicio, duración y escala. Escena de taza con vapor arcoíris y origen manual arrastrable. Ajustes incluidos en JSON de decisiones; URLs privadas de medios excluidas. El clip local cup.mp4 no se publica.

Validación: sintaxis JS comprobada; navegador muestra panel contextual, permite seleccionar música/censura y reproducir sin errores de consola. No se ha verificado render ni calidad sonora mediante captura. No es integración de exportación. No hay detección de palabras, caras o tazas, ni sincronización automática de narración entre tomas. La atenuación es fija, no detección de voz. Los archivos y ajustes viven en la sesión; JSON es traspaso, no proyecto recargable. Las melodías son osciladores locales, no una biblioteca comercial licenciada. Pendiente: continuidad de pista maestra y mezcla real en el exportador, detección con revisión del usuario, sincronía de zoom con capas y pruebas de audio exportado.

### Biblioteca sonora local ampliada

Generador reproducible `tools/qa/build_show_audio_library.py DESTINO`: 10 melodías cortas (13–26 s) y 6 efectos WAV mono a 24 kHz, sin muestras externas. Crea manifest y galería HTML con reproducción exclusiva y descarga. Generados en `work/show-motion-sample/audio-library/`, fuera de Git. La maqueta incorpora selector de pistas/efectos y carga de carpeta de audio (solo sesión). No descarga música de terceros. Verificados selector en navegador y transición del reproductor WAV a estado reproduciendo; no evaluación auditiva profesional. Ejecutar generador junto a storyboard como `audio-library` al trasladar la demo. No integra exportador de producción.

## 2026-10-13 — Piloto audiovisual exportado

Piloto local `work/show-motion-sample/pilot/index.html`: C0271, 466–510 s, voz continua, acercamientos dirigidos, fórmulas, breve congelado con silueta manual y espiral. Música producida Sneaky Snitch / Kevin MacLeod, CC BY 4.0 según ficha del autor; atribución incrustada, en la página y credits.txt. Ducking por sidechain, fundidos y limitador. Render/mix reproducibles conservados junto a los archivos privados (`render.py`, `mix.sh`), no publicados. Comparación original/montaje en la página. No es función integrada ni seguimiento automático.

Verificación: 1100 frames, 44.000 s, 1280×720 a 25 fps, H.264/AAC, 11.74 MB; decodificación completa sin errores FFmpeg. Revisados fotogramas de zoom, fórmulas y silueta. GrabCut inicial incluía nevera; sustituido por máscara manual del fotograma congelado. Falta valoración auditiva/creativa del usuario; no afirmar equivalencia a un editor profesional.

Seguimiento auditoría: ejecución 38218374953 completada con éxito en macos-15 y windows-latest. Estado consultado; no equivale a revisión de todos los artefactos ni cierra ronda de grabaciones reales. PR #32 sigue fc106448; #31 841d7a4; #30 remoto 42e6d0ab al consultar.

### Piloto v2 — gesto del dedo, no formas sobre la nevera

Sustituidos círculo/espiral en coordenadas fijas y fórmulas tipográficas por estela granulada de lápiz ocre, dibujada antes del zoom. Trayectoria en dos tramos (16–19.5 y 31–36.5 s del piloto), 227 fotogramas: anclas de punta de dedo revisadas manualmente cada medio segundo y flujo óptico bidireccional con corrección de deriva. No es detector automático general; oclusiones y movimiento rápido requieren ajuste adicional. Se omite el gesto fuera de cuadro en 29–30 s. Scripts y trayectoria privados junto al piloto (`track_gesture.py`, `finger-track.json`, `render.py`); v1 conservada como script. La estela sigue el gesto real, no fuerza un círculo geométrico. Ouroboros y variantes de pincel quedan pendientes.

## Mesa de montaje del piloto — 2026-10-13

`tools/qa/show_pilot_editor.html` (local `pilot/editor.html`) carga original.mp4, mix.m4a, recipe.json y finger-track.json privados. Tres pistas temporales: cámara, dibujo, entorno. Bloques arrastrables y tiempos editables; cortes directos y zooms; lápiz/cera/rotulador; espiral de tres vueltas hacia dentro/fuera con centro arrastrable; marcador de café; marcas de dedo por fotograma y trayectoria interpolada manual. Deshacer/rehacer, localStorage y descarga JSON. No genera MP4 de los ajustes desde UI ni integra el exportador principal. La pista de sonido está premezclada; slider cambia el volumen total, no balances independientes. Las técnicas se alternan en la propuesta; no hay planificador automático de variedad.

Validación: sintaxis JS, reproducción y salto a 30/32 s en navegador; selector de dirección, interfaz de tiempos y deshacer. El servidor simple no anunciaba rangos de bytes (seekable 0–0): el editor descarga vídeo/audio local como blobs para permitir navegación real. Previsualización de espiral revisada a 32 s. Piloto MP4 actualizado con espiral reconocible, cortes directos y vapor manual; decodificación completa sin errores. La espiral tiene centro elegido manualmente en zona del gesto, no detector de gesto ni ouroboros. Pendiente: seguimiento de taza, importación validada de decisiones y exportación compartida desde la app.

### Editing desk v2 — compact tracks and layer hierarchy

English editor UI. Video lanes are thick; each attached effect has a thin named row and an expand/collapse toggle. Separate audio lanes, global Add menu with file import and supported effects. Clip body drag moves; edge handles trim both ends, min 0.2 seconds and bounded to the 44-second pilot. Video layer ordering and opacity preview work; imported clips are muted by default. Imported audio mixes with the pre-mixed voice/music track. Master and per-track levels are separate. Source files need reattachment after reload; decisions, not blob URLs, persist.

Context menu Copy/Cut/Paste/Duplicate/Delete/Undo/Redo; Ctrl/Cmd shortcuts implemented, while text inputs keep browser editing. Clipboard serialization is JSON of the selected clip. Manual cup position keyframes interpolate; visibility toggles hide/show steam without splitting the long effect. Defaults assign a long coffee effect with a short manually reviewed visibility interval. No automatic object recognition.

Browser verification: English labels, effect expansion, right-edge coffee trim from 3.8 to 14.85 seconds, duplicate and undo via context menu; audio WAV import; second video import and visible upper layer during playback; no console errors. Browser automation intercepts OS copy/paste, so those shortcuts were not established end-to-end through that tool; context actions were verified. Undo of imported clips pauses orphan media; delete retains session sources for undo. Main application/exporter untouched. Pending real renderer integration, general source duration beyond this pilot, full project file persistence and automatic visibility detection.

### Thinking gag and composable resources

Added Thinking / maths, custom Words / thought, imported image layers, and orchestral soundtrack presets. Formulas appear progressively around a movable head centre; quantity, typography, spread, opacity and motion are adjustable. Images and text compose within the selected video layer. No face detector, generated characters or 3D deformation. A Try thinking gag button adds a reviewed 10.8–16.7-second proposal plus Investigations soundtrack. Source speech can replace the premixed pilot audio, avoiding stacking music over existing music.

Local licensed recordings: Investigations (USUAN1100646) and Oppressive Gloom (USUAN1100885), Kevin MacLeod, attribution CC BY 4.0 per current author pages. Source links and credit are carried in soundtrack JSON. MP3 files stay outside Git. Packaged tracks reload as seekable blobs. These are produced strings/orchestral recordings, not the earlier synthesized notes; no choral track added yet.

Verified formulas around protagonist at 13.2 seconds, playback without console errors, image import with size/opacity controls. Not an exported new MP4; only the live preview changed. Fix: clicking a timeline block must not add a no-op undo item; history is now recorded only once dragging actually changes timing. Undo/redo remain recipe operations; source file persistence and renderer integration still pending.

### Range editing and steam repair

Explicit Mark In / Mark Out, selected range shade, Split at playhead (Ctrl/Cmd K), Lift range (keep gap), Extract range (close gap across all tracks), and context ripple-delete. Selected clips have a stronger highlight. Original and imported video/audio use sourceIn distinct from timeline time; the preview clock is independent of a source decoder. Splits stay in the same video/audio lane. Extract preserves linked speech, source offsets and keyframe timing. The new pure model has meaningful source continuity tests in `tools/qa/test_show_timeline_model.cjs` (Node); they pass.

Browser: removed 2–4 seconds, duration changed from 44 to 42, voice/video split in the same rows and subsequent effects shifted together. Original split at 6 seconds produced two pieces; hidden right decoder currentTime was 6, not a restart at source zero. Changes undone after testing. Renderer integration still pending.

Steam had a hidden default off keyframe at 3.8 seconds despite a 44-second clip. Legacy default is migrated to visible throughout this reviewed cup shot; manual cup-rim anchor samples added from 12 reviewed source frames. Controls explicitly show VISIBLE/HIDDEN/CUP OUTSIDE CROP and the visibility keyframes; whole-clip enable/disable available. Steam is transformed with its video and suppressed if the cup rim falls outside a close-up crop. This is manual keyframe tracking, not object detection; sparse samples are approximate.

Professional workflow references reviewed (current official pages, not claims of watching entire courses):
- https://helpx.adobe.com/ca/premiere/desktop/edit-projects/change-clip-sequence/remove-clips-from-a-sequence.html — delete versus ripple-delete.
- https://helpx.adobe.com/uk/premiere/desktop/get-started/tour-the-workspace/tools-panel-and-options-panel.html — selection, ripple/rolling/slip/slide, razor, pen.
- https://www.blackmagicdesign.com/products/davinciresolve/training — editing, sound, compositing and finishing training.
These support separating editorial operations from effects, linked media, and source time. Still pending: frame thumbnails/waveforms, snapping, locks, finer trim tools, text-driven edits, persistent media and shared export rather than endlessly adding effects.

## Independent finger effects, linked A/V, compact modes and captions

Each new finger effect starts with its own points and explicit trail/circle/spiral choice. A single mark is visible rather than silently falling back to the global pilot path. Mark operations capture the effect ID; colour and copied gesture data remain independent. Reviewed paths are explicitly copied into an effect. Local loopback QA helper `tools/qa/show_pilot_tracker.py SOURCE --port 8793` offers point tracking only on the fixed original pilot, with forward/backward flow confidence checks, bounded interval, two workers and a 20-second browser timeout. It stops and exposes a correction point when confidence drops. Imported source tracking is not connected. Actual pilot mark at 16 s tracked 7 frames then stopped; synthetic translating texture tracked successfully. This is not reliable automatic hand recognition and must be reviewed. No private media or track JSON is committed.

Video/audio are linked by default, including imported clips. Split, trim, move, delete and copy/paste operate on linked pairs; context Unlink makes them independent. Audio is shown as a narrow decorative strip inside the video block (not a measured waveform). Unlinked audio stays in separate lanes. Node model tests pass for linked split/delete, unlink, trim/move, source offsets and global range edits. Browser verified original split at 6 s produced linked pieces, unlink exposed audio, and undo restored the project. New independent pink finger effect retained its colour while the previous green effect remained green after reload.

Effect rows are name-only until expanded. Captions share a compact row. Serious / Animated / Mixed control preview and pilot camera proposals; proposals use reviewed pilot timings, not new AI analysis. Serious hides playful overlays/preset music and uses speech-only base audio. Existing transcript captions are loaded from local cached faster-whisper output (22 cues), mapped through source edits and editable in a review panel. Timed words allow partial caption trims; manual correction clears stale word alignment. Re-loading does not overwrite corrected captions. Manual caption creation and browser dictation UI are present; dictation depends on browser support/permission and may use the browser's online speech service. Microphone was not activated/tested. Review repetition candidates is conservative and requires a human decision; no automatic best-take selection yet.

Original application and checkout remain untouched. No fresh Whisper job for imported media, generic tracking, new project-wide ingestion or MP4 export integration is claimed. QA assets required alongside editor: original.mp4, mix.m4a, voice.m4a, recipe.json, finger-track.json, captions.json, licensed score MP3s and timeline-model.js. Only code/documentation are published.

## 15 October — on-picture cutout and pigment prototype

User approved testing Choe-reference techniques on the existing 44-second pilot,
with a compact single-image workspace. This remains a **local prototype**, not
production Show or an installed release. The main checkout is untouched.

New source files:
- `tools/qa/show_paint_tools.js`: deterministic pigment stamps and path sampling;
  fixed gesture bounds for circle/spiral reveal (no moving-centre orbit).
- `tools/qa/show_studio_tools.js`: Select/Paint/Cutout/Camera/+Add dock, contextual
  controls below the picture, options drawer, manual rectangle/closed polygon
  masks, and examples on the original pilot. Tools on a selected sample inset
  return to the primary shot; general nested inset-coordinate editing is not
  established by this sample.
- `tools/qa/build_show_cutout_sample.py`: a reviewed outline at source 21.32 s
  propagated with backward optical flow through 75 frames / 3 seconds at 25 fps.
  Produces a private alpha atlas and metadata locally; does not reconstruct the
  person's pixels, run general person segmentation or use image generation.
- `tools/qa/test_show_paint_tools.cjs`: fixed shape/reveal geometry, resampling,
  deterministic seed checks. Existing timeline tests still pass.

Private local assets at `work/show-motion-sample/pilot/`:
`cutout-atlas.webp` (548,620 bytes) and `cutout-sample.json` are **not in Git**.
The 75 alpha masks are nonempty, with foreground area fractions 0.2627–0.2674.
These are integrity checks, not ground-truth segmentation accuracy measurements.
Inspect cap/hair, fingers, cup and occluding edges before treating the mask as
finished. Reviewed assets remain bounded to 21.32–24.32 s of this pilot.

Demonstrated looks: original person against paper/dark/imported-image backdrop;
black silhouette; crayon-pattern fill inside the matte; and a second muted,
independently timed moving cutout over the primary video. The inset starts from
the reviewed source window and keeps the underlying speech. It can be dragged
without jumping its anchor, and resized below the picture. This is not generated
clothing replacement. Image backgrounds must be reattached after reload.

Painting works directly on the paused canvas, with pencil/crayon/felt-tip,
independent colour and width, progressive reveal on playback and immediate full
stroke display while editing. Deterministic pigment texture avoids per-frame
random flicker. Finger trails retain their painted path; circle and spiral shapes
are anchored to the entire gesture, not re-centred on the current fingertip.
Shape fitting is an approximation; it is not a guarantee that a synthetic circle
matches every actual fingertip sample. Reviewed paths remain manually correctable.

The sample buttons reuse their own demo layers rather than growing duplicates.
Solo preview isolates the example but preserves existing edits. Manual rectangle
and polygon masks are fixed source-coordinate outlines: they do **not** track a
new arbitrary subject automatically. Polygon Finish closes the mask; Back point
and Ctrl/Cmd Z undo draft points. Escape cancels drafting and closes options.
The existing timeline range tools are folded, not removed. More options and
captions remain accessible; all visible UI is English.

Browser observations: native direct URLs against the simple local HTTP server
became black/stuck at source time 0 during seeks, despite readyState 4. Restoring
one shared Blob per media source restored seeks to 8.4 s for the primary clip and
21.32 s for the inset. Initial native src attributes were removed, avoiding a
native preload plus a second fetch of each file. This observation is specific to
the prototype serving/browser path; it is not a diagnosed production export bug.
Matte indexing follows presented video-frame timestamps where available, rather
than only the independent preview clock. The draw loop reports errors visibly.

Verified interactively: original-pixel cutout, black and patterned looks, freehand
canvas stroke, undo/redo of that stroke, primary/inset frame seeking, and dragging
the inset while a primary close-up is active. Existing saved edits/captions were
retained. PNG/video sources and saved private decisions must not be uploaded to
public CI artifacts. MP4 export from these new editor decisions remains
unconnected; the older `show-pilot.mp4` does not contain these new live edits.

Playback and source seeking were retested with the shared Blobs: primary time
8.40 s, inset time 21.32 s, both readyState 4; no browser warnings/errors reported.
Dragging the inset moved it from lower-right to upper-right without changing the
primary close-up. Solo filtering now applies only during the selected example,
so it cannot silently hide later, unrelated drawings. Freehand test strokes were
undone after verification. The original source has no initial src, so Blob
preparation is the sole fetch instead of racing a native preload of the same file.

### Second Choe reference and head/region tool

User supplied `https://www.youtube.com/watch?v=pYOJ6NzoM9o` while the preceding
work was finishing. Inventory and approximate timecodes are in
`docs/SHOW_REFERENCE_PYOJ6NZOM9O.md`; 303 visual samples cover the five-minute
reference, with no claim of full frame-by-frame or audio analysis.

Added `Big head` on the reviewed 3-second sample, using the original head pixels
and an anatomical source anchor, without scaling the room/body. `+ Add > Move /
enlarge a part` allows a manual rectangle/closed outline, independent size,
rotation and dragging. This copies a region and does not remove its original.
Fixed manual masks do not acquire tracking automatically. Added affine anchor
checks to `test_show_paint_tools.cjs`. Other reference techniques are catalogued,
not claimed implemented. No reference footage is in Git or public artifacts.

Interactive follow-up: selecting a fresh rectangle around the cup replaced the
head region, enlarged that selected source region, and a 12-degree rotation was
visibly applied. The selected source view is shown during region drafting so an
already enlarged copy cannot contaminate selection coordinates. These manual
region edits remain fixed-coordinate copies, not automatic object removal.

## 2026-10-15 · Automatic finger ink and six-scene workshop

The pilot now has a six-scene workshop, implemented in `tools/qa/show_scene_workshop.js` and loaded by `show_pilot_editor.html`:

1. Coffee (source 0–5 s): cup-rim placement, width and visibility controls on-picture.
2. Thinking (7.2–10.5 s): formula cloud and close-up comparison.
3. Circle (12.8–16 s): detected fingertip path; optional tiny pencil clock gag.
4. Awkward gesture (16–23 s): preserve the actual imperfect motion.
5. Later spiral (30.5–39.2 s): actual path, not the old fixed-position spiral.
6. Surreal character (21.32–24.32 s): existing reviewed head/cutout sample.

Thumbnails navigate to different source times. Previous/Next, scene-bounded Play, optional Loop, persistent review decisions and effect removal with Undo are included. The six-step English tutorial explicitly describes the prototype's limits. Existing user edits remain; enabling automatic ink hides earlier finger/spiral layers during its interval, without deleting them. “Show earlier drawings” disables automatic ink for comparison. A generic ink layer can be disabled or removed independently of the original linked video/audio.

### Genuine automatic detection, not manual anchors

`tools/qa/build_show_hand_sample.py` uses MediaPipe Hands 0.10.21 to detect the extended index tip. It requires no marked starting point. Palm detection is restarted every second to limit tracking drift. Missing detections, excessive jumps and short isolated detections split/discard strokes. The renderer preserves separate runs, original source coordinates, source offsets, and the same parent camera transform as the video. Four seconds of recent pigment remain visible; no line bridges a missing-hand interval. Brush/colour/width remain editable. `ShowPaint.timedTrails` has regression checks for source scaling, future samples and disconnected runs.

Measured on the private 44 s, 1280×720, 25 fps pilot: 1,100 frames analysed in **33.908 s**, 661 extended-index detections before dropping short runs, 635 usable frames. Usable coverage: circle 51/80 frames; awkward gesture 175/175; later spiral 172/218. These numbers do **not** imply that all detections are correct or all gestures are recognised. Contact-sheet review exposed drift onto the cup in the first continuous-tracking pass; periodic palm resets improved the inspected later-gesture samples. Static detection on every frame was also tried (582 detections / 31.724 s) but missed more blurred frames. No claim of perfect tracking.

The private `automatic-hands.json`, source video, review sheets and six thumbnails stay in the local demo directory, outside Git. MediaPipe was installed in an isolated temporary directory, not the application's environment. Reproduce with an isolated Python 3.11 environment, `mediapipe==0.10.21`, then:

```sh
python tools/qa/build_show_hand_sample.py SOURCE.mp4 PRIVATE_OUTPUT/automatic-hands.json
```

The demo serves precomputed landmarks once; it does not run inference on each playback. New uploads still need integration with the real ingest pipeline, job progress/cancellation and source-keyed cache validation. Prototype scripts and tests are in PR #30; private media are not.

### Scope / editorial limits

“Spain” / bullring is recorded as a possible contextual background gag, not a detected cue: the available cached transcript contains no matching Spain phrase, and the current 44 s excerpt discusses circles/spirals. No unrelated image has been inserted as though it matched the speech. A user-provided background can already be used with the reviewed cutout sample. General contextual gag selection remains pending.

This is an editable browser preview, not a finished MP4 export path. Generic automatic cup/person detection and full-length effect rendering remain separate integration work. The existing 3-second mask is still a reviewed sample, not general segmentation.

## 2026-10-15 · Context menus and clips sharing an effect lane

The pilot now groups effect fragments by parent video, effect kind and optional explicit independent-layer ID. Automatically detected finger ink keeps one shared lane across its copies/splits; manually drawn finger layers remain distinguishable from automatic detection. Copy/paste does not create a new lane. Only “Duplicate as independent lane” assigns a new layer ID. “Duplicate in this lane” inserts immediately after the selected clip. Pasted effects use the same pure source-time retiming as split/extract; automatic finger rendering now honours that content offset rather than replaying the wrong time range.

Timeline gaps accept right-click and mark the exact insertion time. The menu offers Paste here, Copy, Cut, Split, Delete and Undo/Redo; clips expose a small ×. Effect clips now support Split, as well as linked video/audio. Lane expansion is keyed by the lane, not the selected fragment. Timeline reconstruction and inspector updates preserve scrolling, clamped to the remaining content bounds. No existing user fragments are deleted or silently consolidated.

Right-click → Effects & animations opens a second menu of currently implemented tools. “Edit selected effect” moves the real contextual controls into an on-screen floating panel; it does not create inert copies of the controls. Escape, outside click and × dismiss it. The automatic finger inspector no longer exposes manual-tracker controls that do not affect the automatic rendering route.

“Focus editor” keeps the picture, compact controls and timeline together in the tested 732×810 viewport. The original linked video row stays at the top while effect rows scroll below; “Scenes & overview” returns to the six-scene overview. “Editing guide” is a seven-page English tutorial covering grouped pieces, gap paste, trim/move/split, delete/undo, contextual tools, independent layers and prototype limits.

Validation: browser exercised same-lane gap paste (four existing automatic fragments → five, one lane), × removal, Undo, ordinary duplication, explicit independent duplication, effect split, a live width change through the floating panel, and all tutorial pages. Test edits were undone, preserving the four user-created automatic fragments. A paste and inspector rebuild retained document scroll 603 and timeline scroll 303 exactly; content deletion clamps scrolling when the document becomes shorter. Pure timeline tests now cover source-time preservation through copied gestures, split grouping and explicit independent layers; existing linked-audio and pigment tests pass. Syntax checks cover the inline script and all modified modules.

The current changes are still prototype preview tooling; MP4 export integration remains pending. No application installation, user-app shutdown or primary-checkout modification occurred.
