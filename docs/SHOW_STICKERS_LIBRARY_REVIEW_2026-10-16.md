# Stickers and library review — 16 October 2026

## Reproduced defects

A library picture was stored in `scene.overlays`, but timeline rows only enumerated `scene.effects` (proposal IDs). Adding a picture could leave the saved-render player active and therefore show neither its live image nor its own row. The overlay context menu lived outside the modal's top layer and could be invisible. There was also no individual sticker-preview route; reviewing an image could require every other scene proposal to be configured first.

## Changes

- Overlay stickers get persistent IDs. The timeline adapter includes both proposals and placed stickers; the renderer continues using the same saved project model, without duplicating an overlay into a second proposal.
- Adding a sticker switches to its source-cut moment, opens the scene editor and shows a **Sticker** clip. It can be moved, trimmed on both edges and placed on transcript words. Selected words and subsecond durations persist.
- The popup includes **Add sticker**, so another chosen picture can be added without leaving the editor.
- Right-click on a placed sticker opens its tools inside the active modal. **Remove sticker** removes the picture and its row. Proposal pictures keep their own effect-specific remove menu. Timeline clips also expose the appropriate tools.
- **Render effect preview** works for a sticker independently of unfinished characters or locally generated drawing proposals elsewhere in the scene. It uses the same compositor as the scene/final render and publishes a completed contextual MP4, with its edited-clock origin returned to the player.
- Overlay edits mark accepted/modified scenes as needing review, including removing the last overlay. Invalid timing does not mutate the stored scene; positive subsecond durations no longer get rounded up to half a second.

## Library cleanup

The default picker now excludes old local-painter entries under `Gag library/Painted`. Retired pictures remain readable by saved projects, and can be explicitly restored from **Retired images**. **Remove from library** persists a per-project retirement record; it is a recoverable removal from the selector, not destruction of shared source files.

The isolated application project has 30 retired images and five visible resources: the chosen sofa, original crayon sun, original flying bull, and exact fridge/plant cutouts. The originals were copied locally to `assets/show-library`, with names and origins in its `catalog.json`. The earlier, more detailed sofa version is retired. No new picture was generated and no personal media were committed.

The earlier approved guitar has still not been positively identified. The local-painter guitar is retired with the other rejected cache pictures; it is not relabelled as the user's approved original.

## Validation

- 120 targeted Show, rendering, bridge, media, API and library tests passed in 46.46 s. Production JS clock/edit-queue checks pass; JS syntax, Python compilation and diff checks pass.
- New tests verify persistent sticker identity, selected text, 80 ms duration, rejection of invalid timing without mutation, removal reaching the compositor, retirement/restore without breaking references, and a real synthetic-video render. Pixel checks confirm the sticker is absent before/after its interval and present inside it, even with another incomplete character in the scene.
- Browser on the real isolated project: added the original sun, saw its new Sticker row, rendered a 5.5-second 960×540 contextual video (loaded readyState 4), opened the right-click menu on the image and removed it. Both the image and its row disappeared.

## What works and what remains

| Area | Current state |
|---|---|
| Exact chosen PNG, position, size, timing, save and rendered appearance | Connected through the production project model. |
| Add/remove sticker and its timeline row | Connected, including right-click within the modal. |
| Old rejected local-painter gallery | Retired from the default picker, recoverable, existing references preserved. |
| Instant preview of animation trajectories/mouth/steam/math/camera effects | Not complete. Position/timing is live; use rendered preview to assess the motion. |
| All previously existing locally painted proposal requests | Preserved in existing projects; retiring a library entry does not silently rewrite or delete a film's decisions. They can be removed or replaced individually. |
| Public/native package contents | Code is in the PR; these private chosen resources are in the local project. Packaging must preserve/copy the actual selected resources. |
| General application test health | The previous whole-suite audit found 26 inherited failures, all reproduced on the previous baseline. This targeted change does not claim those have been fixed. |

The installed app and original project have not been replaced. No new Windows/package/full-film validation is claimed.
