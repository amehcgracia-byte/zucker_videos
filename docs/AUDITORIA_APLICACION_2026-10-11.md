# Application audit — 11–12 October 2026

## Completed synthetic round

Revision a967731d, including PR #31 through f46d1714, passed all ten browser and
real-pipeline journeys: YouTube, Reel, Medley, 360 and Backstage on hosted macOS
and Windows. Run 38163170602 succeeded. Each OS also passed 26 targeted tests.
Windows package build and packaged startup checks passed in run 38163609156.
These are scoped results, not a claim that the historical full suite is green.

Corrections established in this round:
- Mode cards accept clicks on their child labels/icons.
- Continue accepts unchanged camera/filler settings while preparation runs.
- Decoder stderr is drained concurrently to prevent pipe deadlocks.
- Back navigation accepts preparation finishing before the cancellation request.
- Caption burning avoids Windows drive-letter parsing in subtitle filter paths.
- Arranging captions no longer starts an unsolicited composition render.

The macOS audit environment now installs ffmpeg-full for subtitle support. The
harness collects evidence per mode and requires every journey to pass; failed
steps cannot be hidden by continuing to the remaining modes.

## Follow-up on 12 October

The installed 2.6.3 application now reports revision 841d7a46. PR #31 includes
cherry-picked audit corrections plus new caption effects, direct caption editing,
Reel replacement safeguards, FFmpeg escaping, persistent probe caching, and safer
opening/listing of saved projects. The older local a967731d DMG was built but was
not installed over this newer application.

The audit branch merges the complete PR #31 head 841d7a46, retaining its new
caption renderer (including removal of the old animation expansion), its new
handoff regression test, and the newer mode-card handling. Remote validation of
this combined revision is being started; previous green results do not certify
these newly integrated features. Targeted checks now include filter escaping,
probe persistence and caption tests in addition to the existing regressions.

## Scope and remaining work

Journeys exercise new project, mode choice, import, mode-specific options,
back/forward navigation, shot or paper review where applicable, and export.
Outputs are decoded completely and checked for duration and audio/video streams.
Reel also exercises caption distribution, burning and final composition, and
asserts distribution does not start a render. Artifacts retain screenshots,
traces, reports and synthetic videos for seven days per mode and OS.

Only synthetic media has been uploaded so far. The authorized short real-recording
round remains pending and requires private transport, without public media
artifacts. Native microphone permission dialogs are not established by browser
journeys; packaged Windows startup is a separate, narrower check. No user render,
project or installed application was interrupted by this follow-up.

## Follow-up run 38215928111: test failures diagnosed

At fa24e231, macOS targeted checks and four mode journeys passed. Reel's burn
endpoint returned HTTP 200, but the harness waited for the obsolete word
“Created”; the UI now says “Showing the burned result”. The harness now verifies
the actual burn response and that the player loads the returned media URL.

Windows stopped in targeted checks (68 passed, 1 failed, 6 existing skips), before
any mode journey. The failing test tried to create a directory named `drive C:`,
which Windows forbids. It now exercises the actual drive colon in the absolute
temporary path, while retaining literal directory-colon coverage on POSIX.
Neither failure establishes a product regression. A fresh remote round is required.

## Follow-up — 14 October: evidence at 8d721075

Run [38429121991](https://github.com/amehcgracia-byte/zucker_videos/actions/runs/38429121991)
passed all ten synthetic journeys, with no reported browser exceptions or API HTTP
failures. Downloaded and inspected the ten reports, rendered-page screenshots and
one middle frame of each master. This is the audit branch, not the separate Show
PR #32 (fc106448); the Show prototype additions do not certify production Show.
PR #31 remains at 841d7a46; no new review comments were present on PR #30.

| Mode | macOS duration (s) | Windows duration (s) |
| --- | ---: | ---: |
| YouTube | 32.200 | 32.200 |
| Reel | 11.900 | 11.900 |
| Medley | 10.055 | 10.055 |
| 360 | 32.200 | 32.200 |
| Backstage | 10.233 | 20.900 |

These are **output durations**, not render wall times. Different speech synthesis
lengths affect Backstage, so the two outputs are not a content-equivalence test.
The black Medley middle frame is its intended inter-song gap: macOS blackdetect
reported 4.721–5.355 s (0.633 s), not an entirely black output. Other middle frames
show the synthetic image. Some rendered-page screenshots show black players;
a fixed two-second delay cannot distinguish a black intro from a player that has
not decoded. The next harness revision requires a visible player with decoded
video dimensions and readyState >= 2. No claim of verified player playback is
made for those older screenshots.

Full fast CI is **not green**: both runs 38429122493 and 38429131157 report
28 failed / 754 passed / 9 skipped / 1 xfailed, plus 16 deselected slow tests.
Existing skips/xfail were not introduced or altered here. The slow push run had
one bitrate-tolerance failure; the slow PR run passed. The Flask self-test failure
reports unavailable/non-discriminating CLIP scene tagging, not Flask failing to
initialize. The Reel scale-overlay regression exits with SIGSEGV on Ubuntu's
FFmpeg even with single-thread filter scheduling; macOS does not reproduce it.

Candidate correction: reevaluate pad placement per frame after dynamic overlay
scale, in both Reel graph paths. The source image dimensions change during the
animation but pad previously evaluated its placement only at initialization.
Local regression/escaping/path checks: 15 passed. **Ubuntu confirmation is still
required before calling the crash fixed**; the same regression is now included
in remote macOS/Windows targeted checks. No bitrate/resolution/animation change
is intended. Historical 360 assertions have not been changed in this follow-up.

The old harness fully decoded the first Reel master but only copied the caption
composition. The revised harness also fully decodes that final composition and
checks both streams and duration within 0.25 s of the first master. Earlier green
runs must not be presented as proving that final composed media decoded fully.

The private real-recording round, interactive native dialogs and a current
Windows package remain pending. Public synthetic artifacts must not be reused
for private clips: the existing workflow uploads complete screenshots, traces,
browser videos and masters. A dedicated private transport/evidence path is
required before introducing real footage. No app was closed or installed; no
user project or primary checkout was modified.
