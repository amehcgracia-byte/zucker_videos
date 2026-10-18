# Show 2.6.10 — playback, local media, library and input boundaries

## User-visible behavior

- Play initializes the source video when no effects preview exists. A broken effects video falls back to the source instead of repeatedly attempting the same broken URL. Missing/unsupported files and buffering have explicit messages. Scene animation ticks cannot outlive their own playback session.
- A failed effects-preview request no longer presents a successfully saved edit as a failed save. Original-video playback remains available.
- Space plays/pauses the current scene or intro; arrows step frames when the preview/timeline has focus; Shift steps ten. Text editing, nested dialogs and menu navigation keep their own keyboard behavior. Enter confirms a link download or applies clip settings from its link/source-position fields. Existing timeline clipboard/split/undo commands remain scoped to the timeline.
- Help → Show Studio Keyboard Shortcuts… explains these bindings. Tools → Accounts & Connectors… opens the existing ChatGPT/Codex, Claude, Hugging Face and GitHub account panel. It does not create or change accounts.
- Cascading right-click menus and older effect panels consume their own wheel events, including at scroll boundaries. Scrolling a parent menu closes its now-misplaced submenus. The page behind does not scroll.
- Wheel seeking is frame-based rather than 0.2-second jumps. Pixel/line/page deltas are normalized, small trackpad movements accumulate, tiny inertial direction reversals are ignored, and each event has a bounded step count. Ctrl-wheel pinch gestures are left alone.
- Add → Video / audio from this computer… imports local media during the Show, at the selected words/playhead. The desktop route uses the native file picker and paths; a browser streams the selected file. Dropping video/audio onto the scene imports and places it. All file drops suppress browser navigation, including outside import zones.
- Library keeps the existing project/recurring/other-project/hidden picture shelves and adds Videos, Audio and Effects. Add media… and library drops accept video/audio as well as pictures. Videos have JPEG thumbnails and inline playback; audio has playback controls. Place at playhead / selected words creates an editable timeline clip.
- Downloaded clips, older project-owned media resources, originals and face-swapped derivatives remain reusable in the project catalog. Returning to the original resets its browser-preview path too. Removing an effect does not remove its media from the catalog. Identical video recipes are collapsed in the listing.

## Storage and quality

Imports copy into `assets/clips` once by SHA-256 of the complete input bytes. Original bytes remain unchanged and are used for final rendering. An incompatible browser codec/container gets a separate browser preview; conversion failure is reported, not hidden. Thumbnails are generated lazily and cached separately. No caches are emptied and no mtime/ctime hash shortcut is introduced.

Catalog writes are atomic and synchronized; changing a clip path archives its prior recipe. Placement/import validates the active project, scene and finite timing. The source-video endpoint accepts the project's registered/stored sources, never an arbitrary requested path. Media/thumbnail endpoints remain restricted to project-owned catalog files.

No style-learning memory or preference training is introduced. Existing project decisions continue to be saved through their existing project APIs.

## Validation

- Full Show and desktop-menu suite: **272 passed**, one existing Pillow deprecation warning (`/tmp/show-2610-suite-final.log`).
- After the additional preview-request failure regression: **20 focused tests passed** (`/tmp/show-2610-regressions.log`), including import, real JPEG thumbnail, range playback, audio placement, wrong-project rejection, retained original/face versions, parallel catalog writes and preview fallback. Counts overlap; do not add them together.
- Chromium and WebKit UI checks on an isolated synthetic project: effects deliberately unavailable, source playback/Space, local file import, thumbnail, editable timeline, small alternating trackpad deltas, nested menu scrolling without page movement, Enter confirmation in clip settings, ordinary text Space, and drop-navigation prevention. Both pass (`/tmp/show-2610-ui-final.log`).
- JavaScript syntax and Python compilation pass. The user's running app/project was not changed by these checks.

## Practical limits

These are browser-engine checks on macOS, not a physical trackpad recording or a Windows desktop-package certification. Actual native file-picker/drop integration still needs user exercise in the packaged app. Background downloads, face swapping and long format conversion retain their own processing time; this change does not promise instant generation. Missing/deleted original files cannot be played and now produce an explicit message.

The unified media tabs currently cover this project's imported/downloaded/catalogued media; existing cross-project recurring shelves remain picture shelves. They do not silently copy all videos from every other project or search the disk. Browser uploads retain the existing 512 MiB development-browser limit; the desktop app uses native paths and its existing unrestricted streamed-upload setting.

## Input convention references

- [MDN: WheelEvent.deltaMode](https://developer.mozilla.org/en-US/docs/Web/API/WheelEvent/deltaMode) specifies pixel, line and page units; deltas cannot be treated interchangeably.
- [MDN: overscroll-behavior](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/overscroll-behavior) describes containing scroll chaining. Explicit menu wheel handling additionally keeps each menu boundary independent.
- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/) is the baseline for retaining ordinary field editing and predictable keyboard/menu behavior. The custom Show menus remain web controls; this change does not relabel them as native OS menus.
