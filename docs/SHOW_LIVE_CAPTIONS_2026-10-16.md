# Live Show captions and original lettering

Based on installed Claude commit `3e943193`, in isolated branch `codex/show-handdrawn-captions`. Claude's active checkout, its in-progress library work and the installed app are untouched.

## What changes

- The storyboard receives actual speech caption cues on its edited clock before export. A browser layer displays them over the source or scene-effects preview. No new MP4, Whisper pass or image-provider request is needed to display, move or resize plain captions.
- Drag the caption on the image; drag its corner to change size. `artifacts/show_caption_layout.json` stores per-cue position/size with revision checks. The final `cue_track.json` uses the same overrides. Stale project IDs/revisions are refused.
- The scene editor has a **Speech captions** row. Clicking a bar seeks to that caption in the live image; timing still follows the recognised source words. This is a locator row, not a new free-retiming tool.
- **Draw this phrase…** opens Einstein with an explicit **Words to draw** field and crayon/pencil/marker choices. Lettering requests use a separate prompt requiring original drawn letterforms, exact spelling/case and transparency. They do not use the illustration prompt that forbids lettering. The user reviews a real PNG before applying it; approval remains a separate existing proposal action.
- A saved lettering result is a `word` effect carrying `picture`, `lettering_text`, optional `replaces_caption`, position and width. Both live image placement and the production compositor use that exact PNG. Editing the text makes the old artwork stale: approval is blocked and rendering reports it instead of substituting a font.
- An approved, successfully composited lettering effect suppresses the normal caption only for the interval it actually covers. Outside that interval the normal caption remains. A failed/discarded/unapproved image does not remove the normal caption in the final cue track.
- Caption placement carries through to a newly generated result. Redrawing an existing effect preserves its placement. The browser clock accounts for time inserted by talking-character interventions; scene preview sidecars store those spans. Cache recipe version is advanced without deleting old caches.
- Automatic scene previews and the draft/approve workflow from Claude's installed version are preserved.

## Art sample

`assets/captions/lettering-this-guy-v1.png` is a newly generated transparent style sample, **not a user-approved default**. Its text is “This guy could talk”. It was generated with the built-in image tool, not rendered from a font. Its sidecar records the content hash and origin. Do not silently substitute it for arbitrary captions or declare the style approved.

The plain white subtitle layer is an immediate text editing preview. It is not described as original hand lettering. Generating all transcript cues automatically is **not implemented**: selected phrases are drawn on demand, reviewed and saved. This avoids hiding provider work/usage behind every seek or resize. A generated raster can still misspell a phrase; the user must review the real result.

## Verification

- A directed run of caption-layout, AI image, gag, Show scene and review tests passed: **68 passed**. A subsequent focused provider/gag/caption run passed **42 tests**, including the added insertion-clock test. Final caption-only regression run: **5 passed in 26.29 s**.
- Tests cover live/final layout persistence, stale revisions/project IDs, invalid geometry, original-picture byte preservation, applying only after image completion, refusal of stale lettering, partial caption replacement, and the browser's insertion clock. Provider responses in tests are fixtures, not live calls to a subscription.
- Existing Show clock/edit-queue Node checks and JS syntax pass. No new skips/xfails.
- Browser verification against an isolated four-second Caption Studio Lab: subtitles visible in source preview; dragging moved them; corner resize changed font size; reopening preserved both; caption row located its phrase inside the modal; Einstein opened for the selected caption. Positions/sizes were confirmed in the lab's saved JSON. Private screenshots remain outside the repository.
- The style sample itself was generated live with the built-in image tool. This is not a claim that the app's Codex subprocess was invoked in that browser test.

Limits: no complete fresh six-minute Show render, Windows package or native app installation was performed for this change. A saved final video with burned captions stays read-only; use the live source view to edit. The live preview's normal text is not pixel-identical to ASS font rendering. This work does not generate a custom font, automatically draw hundreds of cues, or rewrite Claude's resource library.

## Claude integration prompt

Continue from your installed `3e943193`; integrate the feature commit from `codex/show-handdrawn-captions` without replacing your in-progress `show_library.py` or library endpoints/UI. Shared files include `server/api.py`, `web/show_review.js`, `web/show_einstein.js`, `web/index.html` and `web/styles.css`: merge their individual changes, don't overwrite entire files.

Keep automatic previews and draft → review → approve. Load `show_live_captions.js` before `show_review.js`, include the new Python module in the build, preserve script/CSS version bumps and the cue-layout file in the project. Use the independent lettering request purpose; never send text to the illustration prompt that says “no lettering”. Keep generated word images as exact project assets, block stale text, and don't turn a plain font preview into a claim of hand-drawn artwork. Final rendering must never request a new drawing.

Before installation, check the new script is served, captions show in the storyboard and modal, bar selection locates the phrase, and position/size survive reopening. Preserve approved drawings and the new library work. Do not restore the old recommendation to disable automatic previews.
