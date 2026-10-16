# Show render parity review — 16 October 2026

## Scope and evidence

Work starts at Claude's local `claude/show-app-build`, commit `77271072`, including its integration of `4320cdf7`. Changes are made in a separate checkout, `codex/show-preview-parity`; the installed app, active project and Claude checkout are untouched.

Input reviewed: `Prueba completa Show - ligera 480p.mp4`, 355.114 seconds, 854×480, 30 fps, audio present. Visual review used a contact sheet every 12 seconds across the file and a rendered four-second calculation example. This is a sampled visual review, not a claim that every frame or the entire audio was manually reviewed. Private frames, recordings and preview videos are not committed.

Observed examples: outlined word near 12 s; small multicoloured calculation symbols near 60 and 192 s; unrelated picture stickers later in the montage. These differ from the requested mixed art direction: deliberately rough drawings for illustrated jokes, serious dense formulas for thinking, and original photographic cutouts where requested.

## Verified causes

1. `94916d53` calls `/show/scenes/prefetch` when opening the storyboard; existing handlers also request FFmpeg previews after edits and when opening the timeline. Moving a saved image already works as a live browser layer; generating another MP4 is unnecessary for that operation.
2. Reviewed scene previews and the final reviewed render call `make_items` without requiring an already chosen image. An unbound drawing can invoke DreamShaper again. A subject or similar prompt is not the identity of an approved asset.
3. `math_items` used five coloured, outlined glyph stickers in a small face orbit. This is a separate implementation from Studio's serif formula treatment; their outputs were materially different.
4. `core/show_bridge.py` explicitly listed `cutout` as unsupported. The editor could show a person/background composition that the exported render omitted. This was a missing production route, not a prompting problem.
5. The local `Show Studio Pilot` project inspected has **no `show_studio/clay.json`**. The earlier private `Application Review` sample does have the imported Simón story. Merging source code does not import a project's private materials or decisions.
6. That inspected Pilot project's plan uses a recovered **381.767-second source which already contains effects and captions**, rather than the original 579.84-second recording. Its old workshop recipe still addresses original source time 466 s. No claim is made that this project metadata proves the supplied 355-second file's exact export provenance. It does demonstrate why reusing those recipes by old absolute seconds is unsafe.

## Changes

- Opening scenes/the timeline or editing image position/timing does not schedule a render. The live source/image layer continues to update. A baked **Preview with effects** remains an explicit action, and already requested jobs can still be polled. The prefetch endpoint remains available for deliberate callers; the UI no longer calls it automatically.
- Reviewed preview/export paths use `allow_generation=False`. An attached `params.picture` uses that exact PNG; a missing attachment produces an explicit missing-picture outcome. It is not silently replaced by a new local painting. Explicit image-generation tools and the legacy unattended pipeline remain separate. This does not ban a user from explicitly choosing a local generated result.
- Calculation gags use 16 real formulas from Studio's repertoire, serif lettering, monochrome ink, top/bottom bands and outer columns, avoiding a colourful orbit over the face. Formula sprites are rasterised once and retain their original source images through compositing.
- Emphasis words preserve case, use the handwritten note face, a thin outline and warm ink. This changes emphasis gags, not baked-in words or the separate subtitle renderer. It is font-based handwriting, not AI-generated custom lettering or an animated handwritten card.
- Saved polygon cutouts and bounded reviewed matte atlases can now reach the production compositor against built-in/project still backgrounds. Original subject pixels, alpha, movement, scale, rotation and opacity are composited; original voice is retained. Missing browser-only photos and a matte extended beyond its reviewed interval are reported as errors rather than substituted silently.
- Cache recipe versions are advanced; old files are retained. Existing cached scene previews are not mistaken for this new rendering policy. Static JS cache version is updated.

## Verification

Targeted pytest coverage: gags, Show review/scenes, clay, Studio bridge, cutout compositor, Studio API and live scheduling policy. The cutout integration test actually exports an MP4, decodes it, checks retained subject/background colours and verifies the audio stream. Additional tests cover transformed masks and refusal to reuse a bounded matte beyond its reviewed interval. A Node execution test exercises live-open/editor handlers and fails on unsolicited API work. Existing Show clock and edit-queue Node suites pass.

A private four-second calculation MP4 was rendered and inspected. The input already contains burned-in words: those remain visible, as expected. It is an effect-layout check, **not a clean before/after master comparison**.

Final directed run: **89 passed in 68.77 seconds**. No new skip/xfail is introduced. No full Windows package or installed-app UI validation is claimed for this change.

## Limits and next recording

This is not complete equivalence of the two editors. Live source + chosen images work without FFmpeg; steam, clay, cutout, voice interventions and the full production effects still need the explicit baked preview in the storyboard. A shared live compositor for all these effects remains further work.

Cutout support here does not implement generic automatic person segmentation, added foreground video tracks, moving background videos, or painted stripe fills. A fixed polygon does not follow a moving person. The existing reviewed moving matte is only a three-second pilot sample, not a general tracker. Cropped-away source pixels cannot be recovered when moving a cutout beyond the current camera window.

Do not automatically import the old clay timestamps into recovered or new footage. First align the relevant spoken passage to its **current source clock**, then import/retime the approved Simón story and verify its placements in a real MP4. Keep the user's green projecting cap, yellow coat, rough clay style and non-looping word-cued holds. Do not revive the retired bearded presenter.

For the new two-angle recording, register both originals and the static fridge/plant photographs as project materials. Synchronise the two angles and maintain a common source/edited-time map. Choose words for illustration timing. Bind approved assets by exact file/content hash; do not infer approval from an `approved/` folder or filename alone. Never use an already captioned/effected export as the clean source if clean footage is available.
