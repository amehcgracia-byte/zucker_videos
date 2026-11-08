# Show integration handoff — 2.6.4

Continue from `codex/show-complete-tool-menu`. This branch includes Claude's **028201bc** library and Codex's **613a8173** live captions. Do not rebuild from the earlier library-only checkout: it omits captions and these tools.

## Preserve

- Automatic scene previews and draft → review → approve. Final renders never generate replacement artwork.
- This project / Recurring / Other projects / Hidden libraries, name suggestions, original chosen guitar/sofa/plant assets, copying into the destination project.
- English UI. Explicit image providers, no silent local-painter fallback.

## Added

The selected-words menu and Add offer realistic photo editing, cutout, silhouette, clay sample and normal speech captions, alongside existing drawing/steam/character/close-up/formulas/word tools.

Realistic photo editing is a distinct ChatGPT request with an actual frame reference and photographic instructions. It does not inherit the crayon prompt. Generated still is reviewed and explicitly applied as an editable picture; it does not turn into a generated video. The provider was unit tested, not exercised against the user's allowance in this round.

Cutout/silhouette starts from the source frame corresponding to selected words, through kept scene segments. Original subject pixels and alpha are preserved; silhouette fills RGB black while retaining alpha. Keep-background and correction brushes remain available. Save puts the image in the project gallery AND places it in the scene as a visible timeline clip. This is a still-image tool; continuous person matting/tracking is not added here.

Clay: previous build scripts omitted `assets/clay`. Both platform recipes now include it. Explicitly place the existing Simón countryside sample on selected words (full / bubble / small window / blend). Each placement gets a unique story ID and new source clock, limited to the current source shot, so the original C0271 timestamps cannot silently attach to recovered footage. Poses are scaled sample cues, not newly generated semantic animation. Controls and a timeline locator appear immediately. New memories still need story/pose creation; do not claim an arbitrary memory generator exists.

Live speech captions and exact saved layout are from PR #34. The scene editor shows speech-caption bars; click a bar to seek, drag the caption over the image, use its corner to resize. No render or provider request per drag. Original drawn lettering remains an explicit separate draft, and final exports use the approved image. Plain white text is an editing preview, not handmade lettering.

The effect list no longer disappears solely because Modify was collapsed. Saved pictures are visible as editable clips. Clay has its own controls/locator, not a generic proposal card.

## Build and verification

Version 2.6.4 identifies the unified build. Verify installed Resources/build_info.json AND live captions JS, library controls, and bundled Simón resources; checking only the version label is insufficient. Installation uses a verified backup before replacement.

See SHOW_LIVE_CAPTIONS_2026-10-16.md for the caption contract. Do not replace these files with older counterparts when merging.

### Verification evidence

- Show suite: 190 passed (95.74 s), followed by 12 targeted caption/pixel-layer tests (7.83 s) after the silhouette addition.
- Node clock and edit queue checks passed; JS syntax and diff whitespace checks passed.
- Browser, isolated four-second lab: opened Scene Editor, saw caption bars and live text, opened Realistic photo edit, placed the bundled clay sample and obtained actual project controls/preview. This is not a complete-show performance benchmark.
- `test_storage`: 21 passed, one pre-existing failure. The failing first-launch fixture replaces `sys.modules['webview']` with a SimpleNamespace and omits `webview.menu`; real installed pywebview imports Menu/MenuAction successfully. No production fallback was added to hide it.
- An extra duplicate cutout request introduced during integration was caught by browser review and removed before packaging.

## Internet references and external video requests

User added this during integration. Both Add and Animation for selection now include Search internet references, Video reference (link + times), and Create video (external prompt). Storytelling mode is explicitly deferred.

Photos: real Commons search thumbnails, explicit choice, full photo / person without background / head mask / background only / silhouette. Chosen images are copied into the project, with source, author and licence metadata, and are available to the existing cutout/brush/rig tools. Heads get a suggested face rectangle with keep/erase/box correction. A saved cutout is placed on the originally selected words, not always at the start of the scene. Imports only download from exact allowlisted Wikimedia HTTPS hosts, including each redirect, and bound bytes/pixels. No private footage is sent in a search.

Video references: open a query or selected YouTube/Vimeo link in the OS default browser; save the source start/end and selected phrase in artifacts/show_references.json. Saved references appear below the scene. These are explicitly links, not downloaded video, not new media clips. A local source file remains necessary to include actual footage.

External video generation: prepare/copy a shot prompt locally; NO connected video provider and no claims of free quota, automatic face replacement or generated footage. Wan2.2-Animate is a candidate to evaluate on a suitable GPU, not installed here. OpenAI's current deprecation page records Videos API/Sora shutdown on 2026-09-24. Sources: https://github.com/Wan-Video/Wan2.2 and https://developers.openai.com/api/docs/deprecations .

Browser verification in an isolated project: real Bill Gates search produced 12 loaded photo thumbnails and the five import choices; selected a 2023 portrait (CC BY 4.0) and opened its head-mask editor. The actual image and mask stay local; no personal clips were added to Git. Reference import/deduplication/provenance/host validation and video link interval tests pass (19 caption/library/pixel tests, 33.11 s). No real actor face-swap or new video generation was tested.

## Follow-up: library access and original pixel parts

Reviewed and fast-forwarded Claude's `fa63ba9c` (the selected-words Library picker) before changing anything. Preserve that picker, cross-project search and chosen-word placement. The inspected checkout has no newer committed Hugging Face/Wan connector; this does not rule out external tests or work elsewhere.

Selected words and Add now expose Move original eyes, Animate original mouth, and Move original part (sideways). Close-up/character/picture cards have a direct original-parts button. The layer context menu opens the same rig editor for ordinary pictures, not only previously rigged Characters. Its API preserves the exact RGBA pixels and alpha; it never generates replacement eyes. Saving selects Look or Talk from the tool used and replaces an existing layer instead of adding an accidental duplicate. New full-frame layers retain the original frame position; selected-word timing is retained.

This is a still-photo pixel-patch workflow: mark the actual eye(s) or jaw, then move those pixels. Sideways parts use the same patch mechanism. It does not automatically track eyes on moving video, provide arbitrary deformation, or connect Wan. Existing voice recording/import remains available.

Browser evidence (isolated four-second Original Parts Lab): Add → Move original eyes extracted a source frame, opened the Eyes tool, accepted a marked region, saved a character and placed a timeline clip. Right-click clip → Edit this effect showed Edit original eyes / mouth, Talk, and Look left and right. This uncovered an actual ReferenceError on opening Scene Editor (`seekTo` was local to another function); the first-caption seek now uses the existing playback functions directly. Screenshot: local `/tmp/show-original-parts-ui.png` (not committed, contains user footage).

Targeted pixel/library/character suite: 19 passed in 126.95 s. The prior PR35 CI on 926951ee is NOT green: 27 failed / 1001 passed, including 360, app selftest, captions isolation and wizard adoption. These are recorded separately from this new workflow; do not describe the entire application as passing or silently skip them.

Final follow-up validation: `pytest -q tests/test_show*.py` completed with **195 passed in 352.03 s**. JS syntax and diff checks passed. Reopening Scene Editor after the scope fix showed the editable first caption at 0:00.2 and completed effects preparation, without a new seekTo exception.

Version handoff: installed application was re-read after Claude's pasted report: version **2.6.4 / fa63ba9c**, not 926951ee. New original-parts work is **a83797c5** on PR35 and is not installed yet. Do not rebuild from 926951ee or from the PR34 branch alone. Use the current PR35 branch, retain fa63ba9c, and verify the packaged scripts. Installing yt-dlp is separate from Wan authentication/generation and must not be described as a video generator or face-swap connector. No token/cookie was extracted and no private footage was sent to a community Space during this follow-up.
