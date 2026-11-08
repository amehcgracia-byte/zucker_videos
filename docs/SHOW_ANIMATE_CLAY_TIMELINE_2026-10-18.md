# Show: Animate, moving person preview and editable Clay stories

## User-facing changes (2.6.9)

Image right-click menus have **Animate** with mouth, original eyes/parts,
movements and **Movement strength → Gentle / Normal / Strong / Very strong /
Wild**. For drawings/photographs, choosing a movement reuses the exact selected
picture as an animated layer and retires its old drawing proposal. Failed
retirement rolls back the extra layer. Existing close-ups keep their same ID and
use the original-pixel rig; strength affects their eye displacement and mouth
opening in the render. Right-click no longer starts a left-button trim operation.

The interactive moving-person editor now draws the video first and applies the
PNG alpha using destination-in. Person and silhouette share the same mask path.
This is a browser compositing change, not a new segmentation model or a change
in export quality.

Clay placements participate in the normal timeline, with drag/trim, splitting,
copy/paste on the same row, duplicate, delete and undo. They have their own
**Clay settings** modal, above the scene editor. New placements use edited-time
anchors and fit the story's held poses to their bar exactly once, without looping.
They can cross source cuts and are not limited to the source shot selected when
created. Legacy story/source-clock placements keep their mapping until moved or
trimmed. The full bundled story is preserved instead of destructively shortening
it to a one-second selected phrase. Changing a bar does not change the host audio.

New-story requests use **ChatGPT via the existing signed-in Codex image provider**
with Simón's character image as an explicit reference. The fixed material brief
specifies the child's lumpy matte plasticine style, yellow coat and tilted green
flat cap with a projecting front brim. It asks for six sequential poses on a 3×2
sheet, including reaction holds. Opaque miniature scenery is accepted for this
purpose, independently of the transparent-image rule for ordinary stickers.
The app locally renders those poses into an H.264 review movie. This is **held-pose
animation from generated images**, not an integrated generative-video model,
continuous articulated 3D acting or phonetic lip sync. Image consistency and the
joke require review. New requests consume the user's Codex allowance only when
Create is pressed; no automatic local-painter/provider fallback was added.

Drafts do not alter the montage. The actual movie is playable before **Approve &
add to scene**. A new draft requested from an existing placement replaces that
story on approval while retaining its bar ID, timing and layout. Repeated approval
is idempotent. Previous story assets remain available; no libraries are purged.

## Evidence and limits

- WebKit + Chromium: actual Clay bar edge dragging lengthens the placement,
  settings open over the timeline, mode changes persist, Animate → strength is
  visible/persisted and Move mouth opens the mouth tool. No JS errors.
- WebKit + Chromium on a private recovered recording: sampled thousands of
  transparent-background pixels and foreground pixels from the live canvas.
  Background pixels differ from the original camera picture; person and silhouette
  have the same outside pixels. Private footage and screenshots stay local.
- A real ChatGPT request produced six new kitchen poses with Simón and a neighbour.
  Local render: 640×360 H.264, 25 fps, 100 frames / 4 seconds. Visually checked the
  generated sheet. WebKit decoded/played the real draft; scene placement count
  stayed unchanged until approval, then increased by exactly one editable bar.
- Regression tests cover full/window render pixel equality for anchored Clay,
  intervals across source cuts, invalid timing, actual draft frame count/duration,
  separate media/approval routes, idempotence, content replacement retaining bar
  timing and unchanged legacy source-clock rendering. No skip/xfail added.
- Updated the older sample-duration assertion: the reusable story now retains its
  21.28-second source content; its independent placement bar is one second in that
  test. The former test asserted destructive compression, superseded by this request.
- Native Windows rendering/dialog behavior and large-scale multi-minute generated
  stories have not been certified by these macOS checks.

Local proof movie (not published in Git): `build/show-269-proof/clay-kitchen.mp4`.
Detailed local logs: `/tmp/show-animate-clay-ui.log`,
`/tmp/show-person-alpha-ui.log`, `/tmp/show-clay-real-approve-ui.log`.

## Claude handoff

Keep the single Animate submenu for both pictures and layers. Do not restore the
long top-level original-parts action or hide movement strength for pictures.
`web/show_clay_editor.js` must be included in the package along with the modified
`show_clay` / `show_ai_images` runtime modules. Always attach Simón's actual
reference, use the clay-specific prompt (not the crayon/sticker prompt), keep a
real video draft before approval, and never claim an attached video-model API.
Do not compress stored stories to the selected word length or put Clay back on
its old read-only timeline row. Window previews must preserve `_edited_offset`
so their timing agrees with the full master. Preserve older source-clock stories.

Final Show-wide regression run: **265 passed**, one existing Pillow deprecation
warning, 148.67 seconds. The previous isolated voice-studio test now includes the
real `closeStudio()` helper and a native-dialog close mock, checking both closure
and the existing voice/replacement behavior; its extracted-save-function harness
had missed that helper after the earlier DIV-to-dialog change. No product voice
behavior was weakened to satisfy the test.

The 2.6.9 frozen bundle at code commit `833bbe2b` passed strict signature checking
and its packaged self-test (render, real frozen Whisper/VAD dependency path,
scene tagging, instrument model, Metal reprojection equality and green-key
compositor availability). A four-second scene preview containing the newly
ChatGPT-generated story also fully decoded: 120 H.264 frames at 30 fps and four
seconds of original audio. Its composed frame was visually checked.
