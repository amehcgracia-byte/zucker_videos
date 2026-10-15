# Shared Show workspace — 2026-10-15

## Reconciliation

Reviewed open PRs #30 and #32. PR #32 still points to fc106448 (the older Show phase 1). The more complete Claude work was local on `claude/show-app-build`, through 051d0121, already containing Codex changes via d8fe9e9f. Fast-forwarded `codex/crossplatform-ui-audit` to that history before adding this integration. No primary checkout changes or application installation.

## What is now shared

- `Show storyboard` opens the real application's Identity → Intro → scenes → Review & render workflow, including explanations, Accept/Modify/Without effect decisions and the selected-word actions.
- A completed Show export is available as **Saved render**, the default scene view when present. It includes the exact existing automatic edit, captions and effects, rather than presenting the plain source as if it represented that export.
- **Quick: the cut only** plays the original source with removed intervals skipped. **Preview with effects** remains an explicit request; browsing does not start an effects render.
- Selected words expose drawing, large text, formulas, steam, close-up and character actions. A drawing direction field saves a proposed effect at the selected phrase, retaining both source text and direction for review before generation.
- Show Studio uses the same app/project storage and links back to the storyboard. It remains the detailed pilot editor, with its renderer limitation stated visibly.
- Direct storyboard startup avoids constructing the separate result/captions editor, its hundreds of cue controls, and duplicate media loading. Narrow layouts no longer expand to the storyboard navigation's minimum width. Status text is below the video, and the overlay layer no longer intercepts the native video controls.

## Validation

45 tests passed in 60.71 s across test_show_saved_render, test_show_review, test_show_scenes, test_show_studio_api, test_show_studio_store and test_media. New tests cover byte-range streaming, stale filenames, project-path containment, non-Show manifests and selected-word directions persisted as unapproved proposals. JS syntax checks and git diff --check passed.

Browser: opened the real 15-scene storyboard; selected Scene 2; loaded Saved render; switched to the original source (readyState 4, 579.84 s); switched back; selected words with mouse drag and opened the right-click actions, including the new idea field; opened the detailed Studio and verified project save status/backlink. API proposal persistence was tested in isolated fixtures, not by adding test drawings to the user's project.

Preview uses an APFS copy-on-write clone of Show Studio Pilot in `work/show-motion-sample/Application Review.zuckervid`, separate app data, port 8798. Only project-local path references were rebased. Original project and native app remain untouched. The saved 381.766667 s, 1080p30/AAC MP4 is byte-identical in both projects: SHA-256 `730e961729a7e4c8bf95009479d3cad3119d88be8647f16e8ebe0f7492f83b33`. Private footage and screenshots are not committed.

## Remaining limits

Saved render is a completed snapshot, not a live rendering of newly changed proposals. It is labelled accordingly. New storyboard decisions require an explicit effects preview/render. Detailed Studio pilot effects do not yet all compile into the production MP4 renderer; its UI still states this. Older standalone-browser decisions are preserved at their old origin and are not silently migrated. This integration provides the common review and actual export view; it does not claim full rendering parity for every experimental tool, a fresh complete-film render, native-package validation or Windows testing.
