# Generate, view, then accept each Show scene

## Behaviour

A selected-word drawing idea or Add effect request now starts the current scene's asynchronous preview when its required settings are complete. The original cut remains visible while queued/rendering. The ready video replaces it; accepting the scene becomes available when that actual preview has loaded. This is a scene preview, not a new full-film export. Existing completed-export decisions are retained; loading a prior saved export does not validate newly edited effects.

Drawing editors also have a Generate & preview this scene button. Their preview buttons use the same asynchronous scene workflow, avoiding a synchronous request that looks stuck. Right-click an effect card, or the picture for a list of its effects: Change/replace opens the appropriate editor, Remove discards the effect reversibly, Restore proposes it again, Generate scene preview shows the current composition. Regeneration remains explicit after parameter changes; new ideas start it on submission. Missing region/voice settings are reported rather than silently omitting that character.

## Correctness

- Proposal additions, timing/style/prompt changes, removal/restoration and overlay changes invalidate the scene preview. They mark the scene as needing review.
- Accepting a current scene preserves its preview; an accepted status alone is not used as proof of previewing a new change.
- In-flight workers hold a revision ticket. Invalidation prevents a superseded result or progress message from being published as current. Repeated preview requests coalesce rather than start duplicate work. The worker checks cancellation and supersession at renderer/painter cancellation points.
- Debounced edits are kept per effect and flushed before generation. The owning scene is invalidated even if the user has navigated elsewhere.
- The scene-cut phase reports actual rendered-frame progress. Drawing steps and compositing have distinct messages. Painter step callbacks check cancellation; English display labels do not change existing cache folder names.
- Missing/unsuitable generated artwork causes an explicit incomplete-preview error. A partially finished final preview file is not treated as a completed cache entry; the final file is published only after compositing and audio.

## Validation

50 tests passed in 61.82 s across the Show saved-render, scene, review, Studio API/store and media suites. 19 painter tests also passed (69 targeted tests altogether). New coverage exercises edit/discard/restore invalidation, acceptance retaining the current result, missing scene IDs, duplicate requests, progress reporting, stale in-flight results, missing drawings, incomplete characters, failure in the last audio pass and painter-step cancellation. JS syntax, Python compilation and diff checks pass.

Browser verified Scene 2's Accept buttons disabled during generation; right-click Change/replace opened the sofa drawing's own prompt editor. A real scene preview using the existing requests completed, loaded with readyState 4 and a 25-second duration, and enabled acceptance. The new MP4 contains the added artwork in the actual composition. Reusing the resulting cache with the final implementation was verified without rendering again. Source footage, generated pictures and screenshots remain local and uncommitted.

## Limits seen in the real test

The local drawing model misinterpreted the requested lazy adult on a sofa as a child, and does not reproduce the host's face from a text-only request. Rendering successfully is not evidence that an image fulfils the idea. The preview enables rejecting or changing that result before accepting; semantic image quality/identity conditioning is not solved here. The first generation of this scene required four drawings and took minutes, not an instant response. Cached images/previews are retained. No claim of full-film export parity for every experimental Studio tool, native application installation or Windows validation is made.

The test runs on the isolated application-data directory and APFS project clone at port 8798. Existing painter models and library assets were cloned locally using APFS copy-on-write, without downloading another model, changing the original project, or closing the installed application.
