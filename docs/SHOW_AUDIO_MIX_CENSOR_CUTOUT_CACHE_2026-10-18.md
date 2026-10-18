# Show 2.6.11 — sound mixer, automatic censorship, noise cleanup and persistent cutouts

## Workflow

Open **Sound mixer** above the Show, from **Tools → Sound mixer…**, from a clip's settings, or from **Cuts and audio** in the preview context menu.

Narration and every imported clip, music item or character voice have independent 0–200% volume controls. Clips offer **Clip sound · pause narration**, **Mix with narration**, and **Mute clip · keep narration**. These modes reach the render recipe; they are not just player-volume controls.

**Mix & balance audio** samples each source, sets bounded independent gains, switches available clip audio to mix mode and enables narration-priority sidechain compression. The added sound dips while the narrator speaks. It does not lower the narrator to make a clip louder. Missing audio is reported and left unchanged; source files are not edited. Changes made to a track while balancing are retained instead of overwritten.

**Preview scene mix** prepares the current scene with its actual mix. Quick source playback remains available while that preview is prepared. The source view previews narration volume and word censorship; the complete multi-source mix and cleaned narration are heard in the effects preview and export.

## Automatic strong-language censorship

The default **Soft error** is an original two-note sound (490 Hz then 330 Hz, rounded attack/release), not a continuous piercing beep. An audition player is in the mixer. Matching uses whole transcript tokens from a conservative English/Spanish strong-language list. In “what the fuck”, only the timestamp for “fuck” is replaced. A small outside-word padding covers timing uncertainty; audio duration and picture frames remain unchanged. Narration volume zero also mutes the replacement sound.

The option is on by default, can be disabled, and has an editable exception list. Word times are mapped through the kept cuts, scene offsets, inserted clips and the opening. Rendered previews and export use the same PCM replacement. Quick source playback uses the same transcript windows with a lightweight player mute/sound preview.

Limits: this depends on Whisper recognizing the word and aligning it correctly. Legacy multiword transcript records use estimated subword timing, labelled as such. Untranscribed speech in a downloaded movie/character voice is not silently claimed to have been checked. Subtitles are not rewritten by this audio setting.

## Noise reduction in initial analysis

Show preparation now checks background noise before Whisper. It measures a short sample, and only when there is a distinguishable quiet noise floor and speech contrast does it prepare conservative high-pass, spectral reduction and a gentle gate. The prepared narration is lossless FLAC, keyed by full source SHA-256 and the algorithm recipe, saved under the project assets and reused. Whisper and the later render can use this prepared audio without changing the original video or sample timing.

Existing projects can use **Analyze & reduce noise** in the mixer. Turning cleanup off restores original narration for subsequent effects previews/exports. If the sample is already quiet, no filter is applied. If a reliable quiet floor cannot be identified, the UI says so and keeps the source rather than guessing an aggressive gate. The current detector samples the first 12 seconds; it does not guarantee removal of changing background sounds throughout a recording. Noise cleanup here belongs to the Show narration pipeline, not every unrelated editor mode.

## Persistent moving-person cutouts

Imported person cutouts use compressed two-second source-mask chunks under `assets/video-cutouts`. The same native-source-frame/cover recipe is shared by the layer editor, scene previews and export. A shorter, longer or re-timed use of the same source reuses completed chunks and processes only uncovered chunks. View arrays remain in the normal project cache. Masks are resampled for other output frame rates without segmenting the source again. Different source bytes or geometry require a valid new recipe.

Also corrected: person-only video layers were not always entering the segmentation-preparation path; missing person masks must now fail explicitly instead of falling through to green-screen keying. Editor and renderer now agree on cover geometry, numeric fps identity and source sampling.

The library preserves cutout and complete-video recipes separately. It stores originals plus reusable masks/settings, rather than duplicating the entire video for every background. Native library video playback is a source preview; placing its saved cutout recipe applies its masks in the scene. Existing old cache files are preserved; recipes without content identity can be rebuilt once to establish the new verified key. No cache emptying or mtime/ctime hash shortcut is used.

## Evidence

- Full Show/desktop-menu pass before the final packaging checks: **280 passed** (`/tmp/show-2611-release-suite.log`).
- New audio/cutout tests prove actual PCM replacement, preserved neighboring words, zero-volume mute, a real encoded censored video with unchanged duration, clip ducking while narration stays level, measurable noise reduction without removing a speech-band test tone, sample-count preservation, and source-mask reuse without a second segmenter call.
- Final audio/background/clip checks: **24 passed** (`/tmp/show-2611-cutout-audio-final.log`); audio plus storage/logging/menu checks: **34 passed** (`/tmp/show-2611-final-targets.log`). Counts overlap the full suite.
- Chromium and WebKit: faders, mix mode, zero mute, asynchronous balance, live source word muting/resume and persistence after reload pass (`/tmp/show-audio-ui-proof.log`). Synthetic footage only; no user material uploaded.
- The storage first-launch test's minimal `webview` double had no `webview.menu`. Its storage-only fixture now stubs menu construction; separate desktop-menu tests still construct and dispatch the real menu objects. No product error is hidden and no skip/xfail was added.

Package verification and installation are recorded below when complete. The storage/logging/menu regression group also passes: 27 tests (`/tmp/show-2611-native-regressions.log`). Browser checks are not a physical trackpad or Windows native-package certification.

## Sound design reference

[Kenney Interface Sounds](https://kenney.nl/assets/interface-sounds) was considered as a reusable interface-sound source. This implementation uses its own synthesized Soft error; no Kenney audio or additional external sound asset is bundled.
