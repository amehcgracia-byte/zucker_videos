# Show 2.6.8 · clips, original eyes and responsive cutout preparation

This branch follows `codex/show-textures-follow` (PR #41), preserving the approved
backgrounds, Simón, captions, library, clip download and face-swap work. No changes
to the main checkout or Claude's checkout are required.

## Fixed behavior

- **Clip settings** reopens the link/import controls from the effect card, scene
  timeline and context menu. Its source player supports range requests through
  a project-checked media endpoint; the generic review-media route did not permit
  assets/clips and would return 404 for those files.
- The timeline bar determines the played duration, including a clip's own audio.
  Previously inserts always read `clip_length` (the downloaded buffer), ignoring
  edits to `item.duration`. Source position is chosen on the player's picture;
  source seconds are optional. Import retains up to 30 seconds for subsequent
  trimming. Extending beyond available footage produces an explicit error.
- Downloading another link from the same clip's settings replaces that proposal
  instead of adding a second effect. The new video is also registered as a source.
- **Animate original eyes / mouth** on a close-up saves `params.pixel_parts` on
  that same proposal. There is no additional sticker/overlay. Eye pixels are moved
  in a small region of the current close-up, after applying its zoom/focus mapping;
  nothing outside that region is redrawn. Mouths use the existing voice-driven
  mouth renderer. The rig button says **Apply to this close-up**.

## Video cutouts

- A conservative border-based detector enables green-screen removal at import.
  Settings can explicitly choose auto, keep complete video, remove green, or
  cut out the moving person (macOS). A green shirt alone does not trigger auto.
- Cutout clips are composited over the continuing original video. Their audio can
  mix with the host. Ordinary clips retain the existing pause-host / own-audio or
  picture-only modes. Source files are never destructively keyed.
- **Video cutouts library** is available in selection actions and the Video menu,
  and as a tab in the reference dialog. Assets and cutout recipes live in
  `assets/video-library/catalog.json` inside the project. Reuse references the same
  video bytes; it creates an independently editable timeline effect.
- The moving-person background editor also accepts downloaded clips. It applies
  the same person/silhouette, size, position and photo/video-background controls.
  Person masks use the same cover framing as the displayed video.
- Added allowed source portals: Dailymotion, Twitch, TikTok, Instagram, Reddit,
  Archive.org, Pexels and Pixabay, alongside YouTube/Vimeo. These use yt-dlp's own
  extractors and format normalization. Site availability/login requirements vary;
  **live downloads from every added portal have not been verified**. Arbitrary
  URLs/local IP addresses remain disallowed.

## Why the installed cutout window looked frozen

The old document immediately requested its video, mask and configuration. Each
request called the same synchronous preparation function under a global lock.
It could wait for local Vision inference with no progress, then reopen the same
preparation for the other media requests. The browser demo had precomputed masks.

There is now one asynchronous job per exact source/span. `/sample` returns 202
with progress while it runs; media requests reuse its completed paths rather than
calling preparation again. The source video is requested only after configuration
is ready. Failures can be retried; a stuck FFmpeg reader is bounded. Vision worker
calls use an Objective-C autorelease pool per copied mask.

The interactive sample is now **up to two seconds**. This changes only the tool's
sample length, not the duration, mask quality, resolution or interpolation of the
final effect. The saved timeline interval still renders in full. Initial person
segmentation is not instantaneous: with the user's real recovered recording, a
private two-second sample took **32.695 s initially / 0.034 s cached**, 60 frames.
That measurement was during other app/test activity and is not a full-render A/B
benchmark or a promise about other machines.

## Verification

- Show-wide run: **258 passed**, one Pillow deprecation warning; not a claim that
  every historical application test is green.
- Focused final checks include timeline duration, same-ID eye updates, cropped
  original pixels, range-readable clip media/path restrictions, cutout-library
  reuse, progress/single preparation, portal validation and actual keyed rendering.
- Actual FFmpeg output with green-screen footage preserves 36 frames and the host's
  encoded audio hash. The green area reveals the original picture; blue subject
  pixels remain visible. No private originals enter Git or public artifacts.
- Chromium and WebKit: source video decoded in reopened settings; saving worked;
  cutout library visible; no JavaScript errors.
- WebKit original-parts workflow: mark eyes, Apply to this close-up, same proposal
  count and no extra overlay, no JavaScript errors.
- WebKit real-person editor: actual masks loaded, live size changed, Use in scene
  persisted the result, no JavaScript errors. Only a temporary project was edited.
- JS syntax and Python compilation checks pass. No test skip/xfail was added.

Private local evidence: `/tmp/show-person-preview-proof.json`,
`/tmp/show-person-webkit-proof.log`, `/tmp/show-eyes-ui-proof.log`,
`/tmp/show-clip-ui-proof.log`, `/tmp/show-clip-full-show-suite.log`,
`/tmp/show-clips-final-tests.log`. Screenshots stay local.

## Claude handoff

Keep the single-effect close-up path, duration authority and shared async job.
Do not restore whole-buffer clip duration or create an overlay while editing an
existing close-up. Preserve all existing approved artwork. Video-library entries
are recipes referencing project clips, not newly generated images or duplicate
copies. Preview preparation must not happen independently in each media GET.
Windows person segmentation and native-dialog behavior have not been certified
by these macOS WebKit checks. The library stores compositing recipes; it does not
export alpha-channel video files.

## Follow-up: nested tools and large photo libraries

The original-pixel studio was a normal DIV underneath the native scene-editor
modal. It is now a native modal too: opening eyes/mouth joins the top layer,
without closing the timeline. Close/Escape returns to the timeline; contextual
menus mount in the modal under the pointer instead of behind it. Opening an
existing character rig also refreshes the same-close-up Apply action.

Photo sources and library folders/types now have collapsible groups with counts,
Expand all / Collapse all, and remembered expansion during the session. Search
opens matching groups. The library header, navigation and close button remain
outside the independently scrolling image list. The studio header stays outside
its scrolling tool/photo body. A board-resize callback now tolerates the player
being temporarily owned by the scene editor rather than dereferencing a missing
pane.

Browser evidence: WebKit and Chromium on macOS, 90 synthetic photos across three
sources and 90 library pictures across three folders. Checked nested timeline →
eyes studio, topmost context menu, Escape back to still-open timeline, group
expansion, searching, independent body scrolling and close-button hit testing at
1280×800 and 600×550. No JavaScript errors after the missing-pane fix. This is
browser verification, not a claim of testing every native Windows dialog.
