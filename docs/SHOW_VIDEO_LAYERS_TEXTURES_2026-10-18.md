# Show 2.6.7 · moving video layers and shared nature textures

The user approved the live person-cutout demonstration and then requested installation
after validation. This change connects that compositor to project decisions and export;
the installed application must contain these assets, not merely link to a local demo.

## Video layers

- Select words → Video → Moving person over a photo or video / Moving black silhouette.
- The separate editor uses the real source cut, original voice and per-frame Vision masks.
  Drag the person, change Size, choose one of the four original approved backgrounds,
  or choose a local photo/video. `Use in scene` saves media and placement in the project.
- Both the scene preview and final export read the saved background, person style, size
  and offset. Background video is sampled on the effect's clock, muted, and loops to fit.
- The editor previews up to eight seconds, not the whole show. The timeline determines
  the actual effect duration. Initial segmentation is a preparation step; subsequent
  placement/background edits compose immediately in the browser without a new render.
- Approved bullring, space, study and meadow PNGs are reused byte-for-byte. Using a
  preset copies it into `assets/show-library/Backgrounds` in that project.
- Newly selected background videos stay in `assets/show-backgrounds`. No private footage
  or masks are committed, and no image/video generation service is involved.

## Effects → Textures

Rainbow smoke, Smoke, Mist, Water, Electricity, Fire, Snow, Rain, Sparks and Leaves
share position, object marking/following, size, density, intensity, height and colour.
These first presets are procedural animated overlays, not photorealistic stock footage.
Old steam proposals retain their IDs and settings, but render through the shared texture
path. A screen-fixed texture spans its requested interval, rather than ending at a cut.

The previous tracker latched `lost=True` permanently and relearned from each shot's old
position. It could also inspect frames already changed by background/mouth effects.
The replacement tracks a clean frame, retains its initial object appearance across shot
pieces, searches globally at several scales after loss, hides until confirmed recovery,
and does not relearn a wall as a cup. Shot pieces no longer restart fade-in/out; only the full effect endpoints fade. Whole-frame searches are bounded in resolution and
frequency. Two confirmations prevent isolated false-match flashes; one poor local frame
does not switch the effect off. An attached texture follows foreground-layer transforms.

This is **visual appearance matching**, not semantic detection of any coffee cup. Mark
a tight, recognisable object on the first frame of the effect. Very different views,
occlusion, similar objects or a weak selection may require a new mark. The UI says so.
Vision cutouts can have rough hair/hand edges and do not provide full object rotoscoping.
Automatic person segmentation remains macOS-only; Windows has not been visually tested.

## Verification and an additional audio fix

- Browser: production editor prepared the actual recovered clip, saved Space + a moving
  black silhouette at 70% into a temporary project. The standalone approved demo remains
  available separately; no real user project was altered during these checks.
- Tests cover absence/reappearance elsewhere, a new shot at 1.5× scale, non-identifiable
  selections, clean-frame tracking, all ten presets, API creation/edit/validation,
  duration across cuts, foreground transforms, video-loop sampling, media closure,
  preset byte identity and saved placement reaching render inputs.
- Actual `render_gags` output: `/tmp/show-layers-textures-proof/moving-person-video-background.mp4`:
  200 frames, 960×540, 25 fps, exactly 8.000 seconds, moving host over a second video.
- That check exposed an existing `-shortest` rawvideo/AAC race: the output lost the last
  two AAC packets (43 ms). The compositor now uses its known frame-count duration.
  The repeated export preserves all 376 AAC packets and their encoded SHA-256 payload
  exactly. A separate regression test covers the final audio packets.
- The initial Show-wide run passed 242 tests and failed one static menu assertion that
  still required standalone steam. Its expectation was updated to the user-requested
  Textures subgroup. No skip or xfail was added.

Full fast-suite run: **1088 passed / 26 failed**, with the same 25 failure IDs as
`/tmp/backstage-266-final-suite.log` plus one obsolete steam-duration assertion.
That assertion required truncation at a cut; it now checks the requested full bar
(and legacy steam settings), with no skip/xfail. The preceding focused run passed
52 tests. Final focused results, package and installation evidence follow below.

The first 2.6.7 frozen package (4a9e0f8a) passed its real executable selftest,
including bundled video-layer assets, all ten textures and macOS Vision loading.
It was **not installed**: a subsequent check found the per-shot fade restart and
required a new frozen build before installation.

## Claude handoff

Preserve `codex/show-textures-follow`, including the existing Backstage work beneath it.
Do not substitute old backgrounds, still-photo cutouts or a static mannequin for the
moving-video path. Keep `web/show_video_layers.js`, its HTML resource and all four original
backgrounds in both package recipes. Do not reconnect a generation provider to previews
or final render. Keep legacy steam IDs, preview recipe bumps and clean-frame tracking.
The menu test changed because steam is now inside Textures; it was not skipped.


## Final verification and installation

- Final focused suite (including legacy clip duration, fade continuity, background video,
  audio preservation, transforms, common texture controls and preview transport):
  **63 passed in 93.96 seconds** (`/tmp/show-267-final-validated.log`).
- A fresh full dependency analysis proved unnecessarily expensive and was stopped.
  The final bundle reuses the validated 4a9e0f8a dependency closure and unchanged
  bytecode. Only `show_framing`, `show_inserts` and `show_textures` changed afterwards;
  their imports were compared with AST and are identical. They were recompiled from
  the current source. The rebuilt PYZ was inspected to confirm all three contain the
  new fade flags, rather than their previous bytecode. Python version and source
  dependency paths were checked. Standard package cleanup, signing and the actual
  executable selftest then ran again.
- Final frozen executable: **2.6.7 / 7ef3f995f4a9f9a169cf8ff15923763be25c082e**.
  Its selftest returned `ok: true`, status 200, instruments loaded, intro rendered,
  Metal exactness, scene tagging and transcription all true. The added selftest
  assertions verify the video-layer resources, ten presets and macOS Vision loading.
  `windows_ui` and `backstage_export` are null; those are not new validation claims.
- Installed directly into `/Applications/Zucker Editor.app` after strict signature
  verification. The staged web scripts, HTML, SVG, four original background PNGs,
  fonts and Simón assets were compared byte-for-byte with source before replacement.
- Previous **2.6.6 / 9c989ad8** preserved and its executable hash verified in
  `~/Applications Backups/Zucker Editor before 2.6.7 20261018-102800.app`.
- The new app was launched with the existing project. Installation record:
  `/tmp/zucker-267-install-report.json`. This delivery installs the verified app;
  the earlier 4a9e0f8a DMG is an archived intermediate build, not this final delivery.

Do not describe the whole application suite as green: the full run above includes
25 inherited failure IDs plus the subsequently corrected obsolete duration test.
No test was skipped or xfailed to achieve the 63 passing focused checks.
