# Music library and reviewed Backstage auto-mix

## Obtain credentials

Open **Connectors → API models**. The existing subscription/CLI account logins
remain separate. Enter a Jamendo client ID from https://devportal.jamendo.com/ or
a Freesound API key from https://freesound.org/apiv2/apply. These identifiers are
stored in the OS vault, with masked suffixes in the UI. No secret goes in a
project, the repo, or a query sent to Zucker's own HTTP logs. Freesound uses an
Authorization header upstream. Jamendo requires its client ID in the upstream
API query; result/preview URLs containing it are never returned to the frontend.

API access does not certify rights to a song. Jamendo's API can return NC/ND or
non-downloadable tracks; this implementation asks for non-NC/non-ND/non-SA and
also checks returned license URLs, allowing CC0 or CC BY only. Download-disabled
tracks remain listenable but cannot be downloaded. Freesound searches CC0 and
uses the MP3 preview, explicitly labeled **Preview audio**; original download via
OAuth2 is not implemented. This is not a promise of unlimited free service.
No provider account, key or terms were created/accepted on the user's behalf.

## Catalog

**Music library** in the nav has Local and Search sections, an audio preview,
imports, a name filter and a configurable folder. Default: `data_root()/library`.
Imported filenames are content hashes; JSON sidecars preserve source, source ID,
name, artist, license, attribution, duration and preview-only status. Local files
may remain in their existing formats. Service previews/downloads are MP3.

Used tracks are copied once into the project's `assets/music/`, including their
metadata. Cleaning or disconnecting the global library therefore does not remove
the music needed by an already approved project. Importing/downloading is explicit;
opening the library does not download songs or make a model inference request.

## Plan, approve, render

Open the Backstage paper edit, then **Auto-mix audio · review plan**. The existing
storyboard music notes remain available. **Suggest mix with AI** sends only kept
segment IDs, durations, text, existing audio policy and a bounded catalog of
available tracks to the selected narrative API. No audio/video is uploaded. It
runs as a background job and returns an editable draft. Without a narrative key,
the manual cards remain usable. Review mode, track, music gain and fades before
**Apply reviewed plan**. Neither a suggestion nor a draft changes the approved
export plan. A paper-edit change invalidates approval instead of guessing new
placement. Revising a draft keeps the previous approved plan available.

Canonical plan row:

```json
{"segment_id":"backstage-0001","mode":"musica_bajo_narrador","track_id":"library-id","gain_db":-18,"fade_in_s":0.5,"fade_out_s":0.5}
```

Modes: `narrador_solo`, `musica_bajo_narrador`, `solo_video`, `musica_solo`.
Spanish `segmento_id`, `modo`, `pista_id`, `ganancia_db` aliases are accepted;
`mix_con_ducking` maps to `musica_bajo_narrador`. Backstage's narrator is the
original camera soundtrack, so original-voice-only and original-video-sound-only
both preserve that same source; no fictional separate narrator stem is created.

The approved plan enters Backstage's existing **single FFmpeg pass** before
concat. It does not add preview renders or generate music/assets during export.
Music fades and sidechain compression apply per segment. Long files are trimmed;
short files use concatenated repeats with a 1-second crossfade (reduced to one
third for files shorter than 3 seconds). More than 128 repeats are rejected with
a request for a longer track. Input indices for logos, subtitles and parchment
remain correct. Original video encoding quality is unchanged.

`credits.txt` in the project includes the used tracks' attributions and identifies
Freesound previews. CC BY attribution requirements are visible in the catalog.
No claim is made that music prevents Content ID claims.

## External local control

With the bearer token explicitly copied in Connectors:

- `GET /api/remote/status`: open project/job.
- `POST /api/remote/render`: `{project_id, mode: "show" | "review"}`.
- `POST /api/remote/audio-plan`: `{project_id, segments, revision, signature,
  approve: false | true}`. Same validation and project snapshots as the UI.

Loopback address/Host and foreign-Origin checks apply. No token in URLs. This
prepares a local HTTP integration; a cloud chat cannot reach this computer's
localhost without a separate local bridge/MCP integration.

## Evidence and limits

- Service clients, vaults, plan generation and graph construction are tested with
  mocks. No real credentials or paid model request were used for QA.
- A disposable 4-second, 160×90 synthetic FFmpeg proof verifies both 300 Hz source
  and 1500 Hz music are present, duration remains exactly 4 seconds, ducking and
  loop crossfade are active (`/tmp/show-2613-graph-proof.log`). No private recording
  was uploaded or rendered for that check.
- Live provider availability, permission/allowance and original Freesound OAuth
  downloads are not certified by those mocked tests.

Primary references (checked 19 October 2026):
https://developer.jamendo.com/v3.0/tracks
https://freesound.org/docs/api/overview.html
https://freesound.org/docs/api/authentication.html

Additional source QA: the Music library modal lists a locally imported synthetic
track; the Backstage plan modal changes the first segment to music-under-voice,
selects that track and applies it successfully, with the UI confirming the
project copy and credits. All actions used a disposable project and fake/no
credential vault; they did not alter the recovered user project. The combined
focused regression selection passes 118 tests (`/tmp/show-2613-final-checks.log`).
Source screenshots: `/tmp/show-2613-mix-plan.png` and
`/tmp/show-2613-voice-mixer.png`. Final package validation is recorded below.

Frozen-package QA exposed another preservation edge case: if the global library
folder changes or its copy is removed, the approved project's music must still
be offered when revising its plan. Both the plan catalog and save validation now
reuse the checked project-owned copy. An additional regression covers cleanup,
reopening and re-approval. The focused music/connection selection passes 14 tests.
The first frozen selftest had no audio fixture supplied and stopped before the
mandatory transcription check; supplying six local seconds of the existing
recording completed it: `ok`, transcription/Silero, scene tagging, instrument
model, intro render and exact Metal remap all true. No footage was uploaded.
This native dependency check does not certify Windows dialogs or live provider
access. Final rebuilt package metadata and installation are recorded below.


## Final package and installation — 19 October 2026

Installed and launched **2.6.13**, application commit `09983ff8`, after strict
signature verification and byte-for-byte checks of 5,664 bundle files/symlinks.
The previous app was copied and all 5,663 files/symlinks verified before replacement:
`~/Applications Backups/Zucker Editor before 2.6.13 20261019-121314.app`.
The final frozen selftest passed transcription, scene tagging, instrument model,
intro render, exact Metal remap and HTTP initialization. Windows UI and full
Backstage export were not run by that selftest.

The installed native process opened the recovered project, returned
`waiting_show_review`, 14 scenes, 71 proposals and `resume_scene: scene-06`.
Its served `show_review.js`, `show_audio.js`, `connectors.js` and
`music_library.js` exactly match this source. Native window appearance was not
independently captured; browser interaction QA used a disposable project.
Recovery preserved the earlier proposal document and the first five creative
scene states; the pre-recovery state remains separately backed up.

GitHub fast-test run **39234362152** (earlier PR head `a5fe8c3b`) reported
**28 failed, 1,141 passed**, with existing skips/xfail unchanged. Comparison with
parent-branch run **39187579381** yields exactly the same 28 failing test IDs,
no added or removed failures. These include the existing 360 failures and a
version-format test that accepts only a one-digit patch number. They are not
claimed fixed by the focused green tests. No skips/xfails were added.
The newer packaging/provider/project-owned-music commits were checked locally;
subsequent CI is separate evidence and must not be implied green.

Evidence retained locally: `/tmp/show-2613-install-result.json`,
`/tmp/show-2613-installed-verification.json`,
`/tmp/show-2613-selftest-final.json`, browser screenshots and test logs listed
above. No recordings, project state, credentials or private assets are in this PR.
