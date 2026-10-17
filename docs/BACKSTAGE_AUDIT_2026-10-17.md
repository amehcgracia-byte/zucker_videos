# Backstage audit — 17 October 2026

Based on Claude's installed `98d0971f`, including Show preview fixes from PR39 and subsequent Show work. Do not rebuild from the older PR34/35 branches and lose those features. Work is in `codex/backstage-audit-polish`; no edits to the main checkout or Claude checkout.

## Eleven requested changes

Each numbered item has its own commit.

1. Bundle DejaVu Sans with its licence; portable lookup prefers bundled assets before Windows Arial, Linux DejaVu or macOS Arial. PIL subtitle cards/tracks and parchment use scalable fonts. FFmpeg paths escape Windows drive separators. Source: https://raw.githubusercontent.com/matplotlib/matplotlib/v3.10.7/lib/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf . Licence beside the file. macOS/Windows build recipes include assets/fonts.
2. Mark summaries exclude dropped cuts. Shared selection logic places closings last; approval and export compute the same kept duration and actual joins. Mark edits address stable segment IDs, not row indexes alone. The written list remains available for restoring dropped cuts.
3. Backstage input text no longer promises image support.
4. Removed unused BACKSTAGE_AUDIO_STATES and backstage_audio defaults. Glossary and language/model overrides remain.
5. English fallback requires two words, rejects accented Spanish and clear Spanish markers/endings. It remains a conservative heuristic, not language identification or translation.
6. Narrative key settings use macOS Keychain, Windows Credential Manager or Linux Secret Service via keyring. No plaintext fallback, key in project/config JSON, API echo or new account signup. Environment keys take precedence. The UI exposes active/limited state and the storyboard's unavailable reason. A configured key means available configuration, not a verified balance or successful provider request.
7. Small/medium/large-v3 selector, default medium, saved in project and included in analysis fingerprint. Listening occupies 0–25%, transcription 25–90%, narrative work the remainder; faster-whisper reports each yielded segment and each completed source. Other backends still report source boundaries. Existing model/language overrides remain.
8. One temporary mono PCM extraction per source for candidate RMS windows; per-window FFmpeg remains the failure fallback. PCM stays int16 in memory, only measured slices use float64. Temporary audio is deleted after extraction. Music detection and later boundary checks are separate costs, so no whole-project speedup is claimed.
9. Intro/outro logo overlays last three seconds; the intro fade-out starts at two seconds rather than seven. Source content duration is preserved.
10. Saving text/marks updates header and existing row states without replacing the list or the active text input.
11. Keep/drop/closing buttons with pressed state; no raw LLM score JSON. English storyboard labels and message placeholders. Button saves are queued so rapid choices retain order.

Analysis/export recipes and web cache keys are bumped. No segment cache was emptied. No new skip/xfail was added.

## Verification and manual checks

Targeted suite before final packaging: 32 tests passed (3.76 s), including the Backstage stage suite, new audit tests, secret-store tests and Node-based paper-row preservation test. Syntax checks cover app.js. Tests simulate the frozen Windows font location and confirm a true scalable font; this is not a real Windows package run.

- Windows: open Backstage with no macOS fonts, render an English subtitle and parchment card, inspect readable glyphs at output resolution. Confirm the package contains assets/fonts/DejaVuSans.ttf and its licence. A real Windows visual/package check remains outstanding.
- Paper edit: drop a six-second clip, retain a four- and three-second clip, mark the first as closing. Summary must show seven seconds and one join; exported sequence must be three then four seconds. Logo overlays add no time. Type in another row while saving; focus and scroll must stay.
- Narrative settings: save a test key using the UI, reopen settings, confirm status without exposing the key; remove it. If the OS vault is unavailable, save must fail explicitly. No paid provider request is made by saving. Confirm missing-key storyboard reason is visible.
- Whisper: choose small, reopen project, confirm selection. Change to medium and rerun: analysis recipe must change, existing source caches stay on disk. Watch per-video progress. Language overrides should still apply.
- Volume: compare representative dialogue/music recordings with the old volumedetect route, especially stereo material with phase cancellation. Mono/downsampling makes this approximate; the automated sine comparison is within 0.2 dB, not proof for every recording.
- Logo: inspect first/last three seconds of an actual export; no intro fade scheduled at seven seconds remains.

## Previous Show review evidence

The original full fast run started from 2b6cc5f4 while the Show preview fixes were being developed: 25 failed, 1018 passed, 2 skipped, 16 deselected, 1 xfailed (355.38 s). It is not a frozen before/after performance comparison. The targeted preview/sticker/clip/face/placement run passed 22 tests. Browser reproduced the source-load pause mistakenly abandoning effects; after the fix, playback used the rendered preview with the test image visible and explicit Pause returned to editable source. These fixes are already in installed 98d0971f.

Do not describe the whole app as green or Windows as verified. Full current validation/build results are recorded below when complete.
