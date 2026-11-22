# Engine native project flow #380 — guarded test candidate

Date: 2026-11-22. DRAFT, not deployed to JM, FER or MAIN.

## Implemented

- server/project_guard_380.py uses an OS file lock shared by Create,
  Save, Rename and Duplicate within a given native project root, with
  SHA256 project.json revision checked BEFORE native writes. Stale
  concurrent saves get 409, never overwrite the newer revision.
- create_app CANONICAL_380_STRICT flag is OFF by default.
  In isolated tests, guarded /wizard/draft, unique UUID project creation,
  project reopen and listing of revision tokens, and explicit
  /wizard/projects/rename and /wizard/projects/duplicate all work.
- While strict mode is ON, unreviewed POST/PATCH routes return HTTP 423,
  rather than bypassing the guard; GET /project does not write.
  This is NOT a production-ready active worker. The full native Editor,
  Camera, Mixer and export mutation routes have not been enrolled.
- Rename preserves the same native folder. Duplicate copies independent
  project files into a hidden temporary folder, verifies content SHA256,
  rejects symlinks and renames the new UUID folder atomically. Copies
  over 2 GiB are refused until async copying with progress exists.
- Browser Engine shelf only shows Rename / Duplicate As New controls when
  the worker returns a valid 64-digit project revision.

## Tests

- 10 new Flask/file-system tests PASS on synthetic 1.2-second MP4:
  create, save, reopen, Save versus Save, Save versus Rename, Save versus
  Duplicate, concurrent same-title create, clone independent assets,
  reopen and ffprobe of the copied MP4, stale writes, bad paths, and
  fail-closed unreviewed routes.
- 17 existing native Engine project/import/preview tests PASS.
- JS syntax and Python compile checks PASS.
- No actual new export through Companion, no 305-shot/360 real JM E2E.

## Blocks before enabling

- Wire every mutation route, including background jobs, Stage and export.
- Link canonical Owner broker registry in Montara-Projects/Montara PR #388
  with native Engine UUID paths, verifying private Owner scope each time.
- Implement async large-project copying with cancellation, progress and
  rollback. Existing synchronous guarded clone is limited to 2 GiB.
- Preserve historical same-title projects, no auto-merge/auto-deletion.
- Validate authenticated JM two-tab E2E, audio/video save/reopen/export,
  FER Windows and Companion, Linux CI after billing #372, rollback.
- Keep MAIN unchanged and #380 open. Feature flag stays OFF.

No live VM services were restarted and no user recordings modified.
