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
