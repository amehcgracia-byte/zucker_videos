# Tracking, recorded voices, recovery and API connections — 19 October 2026

## Recovery (private data stays outside Git)

The user's current project had 39 proposals with only the first scene decided.
The earlier verified metadata snapshot contains 71 proposals and 14 decided scenes.
All referenced media files exist. The current metadata was backed up separately,
then the eight saved creative documents restored without copying or replacing media.
Recovery requests scene 6 on opening this project. No previous decisions were
reconstructed from visual guesses. Cursor metadata is separate from creative
scene JSON, so navigation cannot overwrite edits. Ordinary app launch remains a
new session; opening the recovered project resumes its review.

## Reproduced causes and fixes

* Marking an object saved the anchor but kept the picture's previous position and
  path animation. New marks attach the picture's centre to the marked rectangle
  and remove competing path movement. Moving cutouts attach their visible alpha
  bounding box instead of the centre of their full source frame.
* Textures learned pixels at effect start rather than at marking time. They now
  save the marked template too. A screen-space mark is no longer transformed a
  second time as if it were a source-space camera coordinate.
* Local failure now triggers a global search in the same frame. Missing objects
  hide immediately. Coarse full-frame matching is verified at source resolution.
  This remains appearance matching: substantial rotation/occlusion can require
  a new mark. There is no semantic recognition of a hand depositing an object.
* `Release at playhead · leave it here` stops following at the chosen time and
  retains the last tracked position. This explicitly supports the magnet example.
* Placed library characters' recorded/default voices now appear in the sound
  mixer. Volume, voice-only interruption, mix, narration-only and mute-both are
  saved with the overlay and survive re-editing. Mixed voices stay on the original
  clock, instead of inserting a pause and repeating narration. The recorded voice
  also drives the jaw. Normalization includes placed voices.

Browser evidence from a disposable synthetic project: a picture initially at the
left was marked at the right and appears there. The first harness attempt used
`/tmp` asset strings while the library canonicalized `/private/tmp`; that fixture
was corrected, not reported as a product tracking failure.

## Connections

The existing Connectors panel already supports Claude/Codex CLI account sign-in
and Hugging Face login. Those paths remain. Optional provider API keys are stored
separately in the OS credential vault; only masked suffixes reach the UI. Global
preferences contain provider/model only. DeepSeek credentials are never reused
as an OpenAI/Anthropic key. Selecting an API model does not generate or bill a
request, and API usage is distinct from a subscription/CLI sign-in.

Claude uses Anthropic's documented OpenAI SDK compatibility surface; unsupported
sampling and response-format fields are omitted. Existing caller JSON validation
and retry remain. HF `_hf_inference` uses the router for an explicitly supported
owner/model; it is a tested helper, not a fabricated hosted face-swap service.
The existing local face swap stays local.

Local external control: bearer token stored in the OS vault, loopback address
and Host validation, foreign-Origin rejection, exact open-project ID on render.
`GET /api/remote/status` returns current project/job; `POST /api/remote/render`
accepts `{project_id, mode: "show" | "review"}` and uses the existing reviewed
render operation. It is not an internet bridge or automatic MCP connection.
Copy the local token explicitly in Connectors; never put it in a URL or repo.

Primary references:
- https://developers.openai.com/api/reference/overview
- https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk
- https://huggingface.co/docs/inference-providers/providers/hf-inference

## Validation status

Initial focused tracking/audio tests passed (42); full-HD reacquisition and
recovery reopening regressions were added afterwards. The first broader Show
suite found one recipe-compatibility assertion: an unnecessary `follow_template:
null` field was emitted for old effects. The implementation now omits absent
fields, keeping the old recipe unchanged. No test was skipped or weakened.
Connection tests use fake vaults and mocked network requests; no real provider
credentials were saved or used for paid model requests.

Music-library/Backstage auto-mix requested afterwards is a separate pending block;
this document does not claim it has been delivered. Installed app remains 2.6.12
until the combined changes are packaged and verified.
