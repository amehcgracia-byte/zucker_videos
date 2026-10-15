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
