# Moving video layers · isolated sample (2026-10-18)

The user asked to see the result before installation. This sample changes neither
the installed application nor any project. It is deliberately separate from the
production storyboard: it does not claim to export or save video-layer decisions.

Run from the repository root using its Python environment (macOS Vision required):

```sh
python tools/qa/serve_video_layers_demo.py --source /path/to/talking.mp4 --background /path/to/another.mp4
```

Use a host clip at least eight seconds long. Visit `http://127.0.0.1:8810/`.
The first eight seconds are prepared locally at 25 fps, with the existing production
person segmenter. Masks are cached outside Git in `/tmp/zucker-video-layers-demo`.
The RGBA atlas contains real per-frame masks, not a still photograph. Preparation
uses the production source/framing cache key. No personal footage is bundled here.

The browser composites the moving host and selected background directly in canvas.
Changing position, size, silhouette mode or background does not submit a render.
The host video is the audio and playback clock; the second video's audio is muted.
The back video follows seeks, pauses with the host, and loops if shorter.
Custom image/video files are browser object URLs, not uploads to a remote service.

The four background images are the exact previously approved originals in
`tools/qa/show-backgrounds`, whose manifest records their AI-generated provenance.
They have not yet been added to the real app's library by this sample.

## Verified in the browser

- Actual recovered host sample, 200 masks over eight seconds, loads and plays.
- Both videos advance: host 6.822955 s / background 6.831613 s at one observation;
  host unmuted, background muted, both readyState 4 and neither paused.
- Switching person → black silhouette → person updates the live composition.
- Bullring, space and mountain backgrounds visibly replace one another.
- Setting Size to 70% updates the picture; dragging moves the cutout in the canvas.
- Reset returns the original placement and 100% size.
- No browser warning/error logs during those checks.

## Limits and next integration work

- This is an interactive demonstration, not a new installed app version.
- No project persistence, arbitrary multi-track editor, video export, background
  audio mixer or Windows segmentation implementation is added here.
- Low-resolution, interpolated Vision masks can leave rough hair/hand edges or
  include held objects. This is not studio-quality rotoscoping.
- Browser codecs determine which user-provided videos play; H.264 MP4 is safest.
- Production integration must use the same layer order, media-time sampling and
  cutout decisions in both preview and final export, and restore these approved
  background assets to the actual library. A demonstration is not that integration.
