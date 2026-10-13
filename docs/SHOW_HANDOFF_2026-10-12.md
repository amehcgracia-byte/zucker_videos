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
