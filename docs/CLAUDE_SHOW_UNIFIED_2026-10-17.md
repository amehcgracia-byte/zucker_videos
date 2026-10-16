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
