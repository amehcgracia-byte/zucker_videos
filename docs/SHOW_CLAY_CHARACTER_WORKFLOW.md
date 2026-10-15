# Clay memories: stable cast, shot planning and honest preview stages

User direction, 15 October 2026: create a reusable clay version of the presenter in the actual outfit; later request reference photos for band members or other people. Re-enact memories/thoughts in context using the same cast and handmade visual language. No band-member generation is requested yet.

## Two separate records

A **character** owns a stable id, version, reference images, wardrobe, facial features, relative proportions, style recipe and user approval. A **scene** references those exact versions and owns the transcript quote, source interval, set, props, actions, shot list and original voice. Never overwrite a character to change a scene. A prompt alone is not an identity guarantee: every request also needs the approved reference sheet and each result needs review.

Suggested project storage uses the existing project library for images/video with SHA-256 ids, and small JSON character/scene records referencing it. Approved inputs and final outputs are protected. Disposable previews are listed separately for recoverable cleanup. Do not create another invisible global media cache.

## Interaction in the existing scene editor

`Clay memory` opens a contextual panel, not a mandatory general-purpose chat window:

1. **Story**: selected transcript, interval, intention (memory / imagined scene / visual joke). The app proposes a short shot script and shows its evidence.
2. **Cast**: choose saved characters. For an unknown person, ask for a photo and name; `Create character` prepares the fixed recipe plus photo. The user approves the reference sheet before it is reused.
3. **Set & action**: editable plain-English direction, reusable sets/props and missing-material requests. Preserve character references when changing direction.
4. **Preview**: distinguish `Character reference`, `Storyboard animatic`, `Generated motion` and `Rendered in the show`. Do not label the first two as fully animated video.
5. **Apply**: only a rendered, reviewed clip is inserted as B-roll over the existing original voice, with explicit timing. Regeneration affects this shot, not every use of that character.

Generating a new shot should use only the required view plus the canonical identity reference; independently generating every perspective can drift. Supporting all viewpoints/poses reliably requires a rigged 3D puppet, actual stop-motion footage or an image-conditioned animation service with result review. Holding frames at 8–12 poses per second can establish cadence but does not make a static image perform a new action. Keep the final show cadence; no need to lower the whole film's frame rate.

## Current private proof

- Source: C0271, 223.62–229.62 seconds. Cached transcript contains “I really dream about it. Dream about the day…” after the nature/city passage. This is a grounded imagined scene, not a fabricated autobiographical event or a new transcription.
- First image: a proposed three-view clay character in the presenter's video outfit. Generated with the built-in image tool from a locally inspected source frame. Not yet approved as canonical, not a 3D model.
- Second image: three miniature meadow storyboard shots using that character sheet as the image reference.
- Preview: six-second animatic made from three still panels with original source audio. Any stepped camera movement is an editing demonstration, not newly generated bodily motion or phonetic speech.
- Outputs stay under the private sample project, `work/show-motion-sample/pilot/clay-memory/`; no personal reference frames, voice, character likeness or footage are included in Git.
- No video-generation provider is connected by this work; no API key/paid service or automatic background spending is introduced. Installing a chat UI would not itself connect video generation.

## Fixed character prompt v1 (built-in image generator)

> Use the supplied photo as identity and wardrobe reference. Create one recognisable handmade plasticine puppet with the same facial structure, hair/beard, glasses and clothing. Matte clay, visible fingerprints, subtly uneven sculpting, miniature photographic lighting, slightly exaggerated expressive head. Show consistent front, three-quarter and side views on a neutral studio sheet. Keep wardrobe and relative proportions identical. No scenery, extra people, logos, text, glossy CGI or sticker outlines. Treat as a proposed design, not an approved identity or rigged 3D asset.

Private presenter-specific additions: light gray flat cap, off-white short-sleeve polo with dark buttons, dark gray trousers, round wire glasses, salt-and-pepper short beard/moustache. Clothes below what the photo reveals are a generated proposal, not verified footage detail.

## Scene prompt v1 (built-in image generator)

> Attach the proposed/approved character sheet. Use exactly the referenced character and wardrobe. Prepare three distinct still storyboard shots in one equal-panel row: wide handmade meadow and small house; thoughtful three-quarter close-up; medium shot gesturing toward the house. Same miniature set/daylight, tactile clay and imperfect physical surfaces across all shots. No other cast, writing or glossy CGI. This visualises the narrator imagining returning to nature. These are static storyboard images, not final animated acting.

A future motion request must add a bounded action, duration, camera, continuity constraints and explicit end pose. Example: “Six seconds: the approved clay character turns toward the small house, pauses, then gestures once; locked miniature camera, same costume/set, handmade stop-motion cadence, no identity change.” This is a prepared request, not a delivered video-generation function.

### Preview verification

The private `clay-memory/index.html` uses `show_clay_memory_demo.html` and exposes the draft reference sheet, storyboard, original-voice preview and an editable direction that downloads JSON. It explicitly says that saving starts no generation. The six-second MP4 is 720×810 at 25 fps (150 frames), with six seconds of audio, 529,700 bytes; full FFmpeg decode completed without errors. Its portrait storyboard framing is a design study, not the final landscape Show composition. Browser loaded the video with readyState 4 and duration 6, and playback was exercised. No existing scene was silently replaced or added to the user's timeline.

## Approved v2 puppet and expanded pose sequence

The user approved the second, deliberately child-built design: lumpy potato head, uneven white eye blobs, round wonky glasses, beard pellets, mitten hands, flattened cap and crude off-white polo. Approval is stored in the private character record; the earlier polished design is retained for comparison. Built-in image generation used the approved character image as an explicit reference for both new pose sheets. Actual prompts are saved privately as `pose-v2-prompt.json` and `story-v2-prompt.json`; generated personal likenesses and voice remain outside Git.

The first six-second revision introduced a head tilt and arm gesture, then a closer camera view. One generated pose raised the opposite hand and was excluded. After the user's follow-up, the story extends through source **223.62–244.90 seconds** (21.28 seconds):

- 0.00–4.24: imagine the meadow/house;
- 4.24–8.96: anonymous neighbours gather in a crude clay city;
- 8.96–16.08: a closer group view during the question about staying in the city;
- 16.08–21.28: the same cast in a little clay village.

The neighbours are generic supporting characters, not invented portraits of actual band members. This remains discrete held-pose animation (three pose changes per second), with repeated gestures and no continuous body simulation or phonetic mouth synthesis. The original voice continues unchanged in meaning/timing; it is encoded for the preview, not replaced by synthetic speech. The surrounding context preview contains four seconds of the real video before and after the clay sequence.

`build_clay_pose_preview.py` consumes the private JSON plan, original voice and two 3×2 pose grids. It validates contiguous finite intervals, renders four video shots, stream-copies them with one final audio encode, fully decodes the pending output, checks duration/frame/audio counts and publishes by same-directory rename. Temporary shot files are scoped to the build and removed; existing inputs and prior revisions are retained. Rendered clay sequence: **1280×720, 25 fps, 532 frames, 21.28 s**. Context version: **732 frames, 29.28 s**, equal audio/video duration; full decode passed. The visual sampling sheet was reviewed across all four shots.

`show_clay_scene_tool.html` is a private-study workbench: approved cast reference, four selectable shots, bounded selected-shot playback, adjustable shared cut boundaries, per-shot editable directions and JSON download. Moving a cut changes the canvas preview immediately while the source voice stays continuous. Directions only prepare a generation brief; no generator/provider is launched. The rendered MP4 remains a snapshot until an edited JSON is rendered separately. This workbench is **not yet connected to the production project store or a one-button in-app MP4 renderer**.

Browser validated a first cut changing from 4.24 to 5.24 seconds (the next shot starts at 5.24), then restored it; selected village seek to 16.10 s; selected city playback starting at 4.38 s and stopping at 8.97 s for an 8.96 s boundary. A real bug was caught: the temporary static server supplies no usable seek range for direct media URLs, so selection reset to zero. These short preview/voice files now load once as bounded local blobs (5 MiB voice / 32 MiB preview caps), never the multi-gigabyte original; browser then reports seekable 0–21.28 and 0–29.28 and seeks correctly. Main context preview was verified at 5.20 s. Python compile, HTML script syntax and diff checks pass. No claim of full application/Windows regression validation for this sample.

Private URLs while the sample server is running:

- `/pilot/clay-memory/`: updated context preview, approved v2 sheet, prior version comparison;
- `/pilot/clay-memory/editor.html`: timing/direction workbench;
- `/pilot/clay-memory/story-v2.json`: current source cues, cut intervals and image references.

Render an edited plan with:

```sh
python tools/qa/build_clay_pose_preview.py PRIVATE_PLAN.json PRIVATE_VOICE.m4a PRIVATE_OUTPUT.mp4 --ffmpeg PATH --ffprobe PATH
```

Downloaded plans should be kept beside their referenced private sheets, or their relative sheet paths adjusted deliberately. No media migration/deletion, installed-app replacement, video-generation provider or paid API was introduced.
