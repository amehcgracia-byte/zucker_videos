# Optional insert continuity

Stage export preserves every authored slot. Missing required originals still block
with a source diagnostic. An unavailable optional insert uses a proven synchronized
camera when possible; otherwise it uses a black interval of exactly the same
length. Master audio and required camera timings are unchanged. No preview is
promoted to an original.

Optional means an explicit library filler role, or saved `filler: true` provenance
on **all** appearances of that source in an existing edit. Names, silence and
periodic repetition alone never make a camera optional. This repairs legacy edits
without importing again or regenerating their edit plan.

The effective export snapshot records each omission and its replacement. Warnings
are grouped by missing source. Neutral intervals are generated losslessly in the
export cache and excluded from camera colour calibration. The authored plan and
source media are not modified. Restoring the insert restores it on the next export.

Regression: `tests/test_optional_server_export.py` renders 80 actual slots with 16
absent inserts (every fifth slot), no usable synchronization and only saved filler
provenance. It checks final duration, all required slot controls, the untouched edit
plan and grouped warning. Montara additionally drives the native browser policy
through Companion and FFmpeg and checks every slot's pixels and audio timing.
These are synthetic regression fixtures reproducing the reported structure, not
the owner's private Tune Up media or a production deployment test.
