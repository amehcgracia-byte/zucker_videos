# Simón clay story

The user-selected recurring character is the plant figurine with a green grooved cap and a yellow coat, originally handmade by Simón. This pack contains newly generated renditions, not camera footage or an extracted photo. Do not revert to the older bearded presenter.

- `character.json` and `simon-clay-proposal-v1.png`: character identity and generated reference.
- `meadow-poses-v1.png`: six held poses in the countryside.
- `neighbours-poses-v1.png`: three city poses and three village poses with anonymous neighbours.
- `story-simon-v1.json`: four contiguous shots, 21.28 seconds, on the original source clock (223.62–244.90 s).
- `manifest.json`: asset hashes, generation route and reusable prompt constraints.

The story cues each gesture once and holds it through pauses. It is limited pose animation, not a generated continuous video, articulated 3D character or lip sync. `fit: contain` preserves proportions of the square generated cells in a wide video. Anonymous neighbours are not portraits of the user's band.

Import into an existing Show project:

```sh
python tools/import_clay_story.py /path/to/Project.zuckervid \
  assets/clay/simon/story-simon-v1.json assets/clay/simon/character.json
```

Import does not remove other stories automatically. When replacing an existing old story, first preserve its state in project history and retarget/remove only its placements; never delete unrelated edits or shared assets. The local Application Review project has this new story imported with five editable placements (full, bubble, pip, blend, full), leaving space to return to the speaker.

The user's voice, original reference photo and contextual video remain private, outside this repository. With a matching 21.28-second voice clip, the standalone renderer is:

```sh
python tools/qa/build_clay_pose_preview.py \
  assets/clay/simon/story-simon-v1.json /private/story-voice.m4a /private/simon-story.mp4
```

Private previews already rendered locally:
`work/show-motion-sample/pilot/clay-memory/nature-story-simon-v1.mp4` (21.28 s), and
`nature-story-context-simon-v1.mp4` (29.29 s, including four seconds of the speaker before and after).
The active clay preview and editing page use the new assets; previous presenter studies were moved outside that preview to `work/show-motion-sample/retired-clay-presenter-20261016/`.

Validation: eight clay compositor tests, the JS word-cue test, complete FFmpeg decode of both previews; contextual output 1280×720, 25 fps, 732 video frames with audio. This is not a complete rerender or audit of every Show feature.

User clarification after reviewing this pack: the green headwear is a slightly tilted flat cap with a small projecting front brim, standing off the head, not a helmet or tight skullcap. Current animations are accepted as-is; `character.json` records this override for future generations. The PNGs have not been redrawn.
