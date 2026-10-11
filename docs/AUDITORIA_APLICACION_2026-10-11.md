# Application audit — 2026-10-11

## Revision under review

The audit branch includes PR #31 through f46d1714 (faster Reel scoring,
damaged-source interval avoidance, and measured background scoring progress).
The installed 2.6.3 application was built from 25314b3c; it predates the final
progress update. No installation or user render was interrupted for this audit.

## Findings and corrections

- Clicking a mode card's child label did not select the mode. The handler now
  resolves the enclosing card with `closest`; the UI journey clicks the label.
- The first audit harness tried to click New Project behind the automatically
  opened mode dialog, then expected the tutorial prompt after its answer was
  already saved. The revised journey observes tutorial state and closes the
  initial chooser before exercising New Project. These were harness failures.
- PR #31's JPEG extraction read stderr only after stdout completed. A damaged
  decoder can fill the stderr pipe and block both processes. Commit 56025ca9
  drains stderr concurrently, retaining a bounded diagnostic tail. A child
  process emitting more than pipe capacity exercises this regression.
- A UI report must only set `passed` after its JavaScript error assertions pass.

## Validation and remaining work

GitHub-hosted macOS and Windows execute real browser navigation and the real
pipeline for YouTube, Reel, Medley, 360 and Backstage. The journey preserves
screenshots, browser recordings, traces, server logs and output videos for seven
days. It checks complete video decoding, duration and audio/video streams; Reel
also exercises caption arrangement, burning and final composition.

Run 38142633156 contains the decoder-pipe fix. The focused regression step passed
on macOS; full journeys and Windows validation were still running when this
record was written. This is not a completed all-mode validation report.

The older packaged Windows build smoke test succeeded in run 38121532007, before
the PR #31 merge; it is not evidence for the current revision's packaged build.
Browser journeys do not exercise native microphone permission dialogs.

Only synthetic media has been used remotely so far. The user authorized short
real-recording fragments for a second round, but none has been uploaded. That
round, visual review of the rendered evidence, and any failures found by the
ongoing journeys remain outstanding. Existing full-suite failures are not
silenced or treated as successes by this audit.
