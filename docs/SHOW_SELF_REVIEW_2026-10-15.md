# Show editor self-review — 15 October 2026

## Assessment

The previous placement delivery verified saved timing and a rendered MP4, but did not verify the immediate layout layer during playback. That layer was static: it stayed on screen regardless of its saved interval. This was a product defect, not incorrect user operation. The inline timeline also did not match the requested separate editing window.

A passing backend/render test subset was not adequate evidence of an editor that works consistently. The correct acceptance loop is: choose a picture → select words → inspect before/during/after → trim/move → inspect again → save/reopen → render and compare.

## Repairs in this revision

| Finding | Repair |
|---|---|
| The selected sofa drawing stayed visible outside its bar. | Chosen pictures are now timed layers, evaluated against the edited playhead on seeking, playback and after edits. Before the start and at/after the end they are hidden. |
| The timeline was embedded below the main player. | **Open scene editor · video & timeline** opens a separate native HTML modal with the player, transport, selected-effect tools and tracks. X, Escape and outside click close it. The original player DOM is moved and restored; no second player or second project state is created. |
| Native controls displayed the original 9-minute source clock while bars used edited scene time. | Source/saved playback uses a scene transport and edited-time ruler. `ShowClock` owns source-segment mapping, pending seeks, interval visibility and timing bounds. Rendered effect previews retain their own video controls. |
| A picture disappeared entirely when scrubbing the timeline. | Seeking rebuilds/retains timed chosen-picture layers; it no longer deletes the image-edit layer as a substitute for updating visibility. |
| Dragging the bar restarted the player at its new beginning. | Moving/trimming saves the new interval without jumping the current playhead. A simple clip selection or **Go to effect** locates its start explicitly. |
| A geometry drag could jump back on the next drag or on failure. | Picture dragging uses a draft and persists on release; cancellation restores the saved geometry. It does not seek/restart the camera after every drag. |
| A saved render could be mistaken for a live source after invalidation, duplicating a baked picture. | Playback sessions record whether they actually use the saved render. Live layers are never composited again over that saved render. Opening the scene editor switches to the live source clock. |
| Late metadata from an old source seek could affect a newer player session. | Metadata callbacks check the playback session identity; pending seeks use their intended edited time instead of an old frame's source time. |
| Source playback jumped back to the beginning at its end. | It stops at the final source segment. Requests beyond the edited range clamp to its last frame. |
| Large explicitly positioned artwork was silently shrunk again by the renderer. | Automatic positions still fit the frame; explicit image placement preserves the requested size, including artwork partly outside the frame. |
| Incomplete characters were labelled “Outside this scene”. | Zero-duration character placeholders instead offer **Voice & hold timing · Adjust**. Their duration semantics are distinct and still need a dedicated editing contract. |
| A failed debounced save could drop the remaining effects in the drained batch. | `ShowEditQueue` preserves failed drafts, continues saving the other effects, gives newer values precedence, and blocks acceptance/render until the queued saves succeed. A visible **Retry saving** action is provided. |
| Canvas editors could retain an old proposal object after a save. | Preserved canvas instances are rebound to the current proposal rather than drawing stale settings. |
| A saved edit message claimed no changes were visible even in the live view. | Messages distinguish updated live picture timing from an obsolete rendered preview. |

## Validation

- Browser, real isolated project on port 8798: selected sofa picture visible inside its interval, hidden before and after; checked by moving the scene playhead and by playing through the end of the picture interval.
- Modal opening/closing returns the original player: one `showStageVideo` remains in the document, and the launcher becomes available again. Switching to source timing leaves the user's saved decisions and assets intact.
- `node tools/qa/show_edit_queue_test.cjs`: injected failures verify that a failed sofa save retains its draft, a later sun edit is still saved, newer changes win, and a second awaited flush really retries before reporting success.
- `node tools/qa/show_clock_test.cjs`: production clock tests cover discontinuous source segments, pending source seeks, exact interval edges, 80 ms intervals, moves, bounds and end-of-source behaviour. No DOM mock or duplicate clock implementation.
- 47 targeted Show/API/scene tests passed; 31 gag/placement tests passed at intermediate checkpoints. The final combined run passed **117 tests in 130.17 s**, alongside the broad application run. The longer wall time includes concurrent test load, not an application render benchmark. JS syntax and diff checks pass.

### Whole-application test pass

Executed `pytest -q -m "not slow" -p no:cacheprovider` across the application. Result: **951 passed, 26 failed, 2 skipped, 1 xfailed, 16 deselected**, 456.21 s. The skips/xfail already existed; none were added here.

All 26 failing node IDs were then rerun against the unchanged parent commit **8173ae51**, in a temporary detached worktree, and **the same 26 failed** in 6.12 s. The temporary checkout was removed after comparison. This demonstrates these failures predate this revision; it does not make them acceptable.

| Existing failing area | Count | Observed disagreement |
|---|---:|---|
| 360 export safety/projection consistency | 2 | `static` versus `native_remap`; saved-pose projection assumptions. |
| API | 5 | Three landmark/FOV sanitization contracts; old `sphericalSetup` still present; existing project adoption returns a different wizard state. |
| Composition forensics | 8 | Reserve-view count, hold cadence, motion endpoints and canonical 360 pose/FOV assumptions. |
| Segment export | 10 | Proxy fallback/sendcmd, pan steps, continuity `previous_shot`, wide FOV/stereographic/planet expectations. |
| Show music | 1 | Render recipe now includes proposal provenance, while the test compares an older exact dictionary without that field. This assertion alone is not evidence that the audio failed to play. |

The exact failing node IDs are retained below for follow-up. No 360 behaviour, cadence policy, project-adoption contract or existing test expectation was changed to conceal these failures.

## Remaining product work, in priority order

1. **Preview parity across effect kinds.** Chosen PNGs and placed library pictures have immediate visibility/timing. Locally painted artwork without a chosen image, formulas, steam/tracking, character inserts, clay and camera effects still require rendering. The UI explicitly states that a local drawing is not present in the live view. It must not advertise complete live render parity.
2. **Character duration and inserted speech.** Voice length/hold can insert time into the composition. Generic proposal `duration` is not yet the performed length of such an insert. This needs an explicit source/edit/output-time mapping before offering the same trimming contract as an image clip.
3. **Durable drafts across app termination.** The repaired queue retains failed edits in memory and exposes retry; it does not yet persist unsaved drafts across a forced app/browser termination. Add a journal and explicit restore flow if this is required.
4. **Resolve the 26 inherited test disagreements by root cause.** Keep the user's authorized `native_remap` decision; do not revert it merely to satisfy a stale assertion. Diagnose actual regressions versus obsolete tests before changing either.
5. **One production workspace.** The experimental Studio and the production storyboard still have different capabilities and source windows. Port missing capabilities through the shared project model, not by shipping an older demo HTML or creating a second editor state.
6. **Distribution verification.** The installed native app and Windows package were not rebuilt or tested in this revision. A successful browser test on this Mac does not establish Windows/native-dialog/permission behaviour.

No promise of an error-free application, full-film validation or complete visual testing of every edit mode is made. The broad run is automated coverage; the interactive verification in this revision concerns Show's production scene editor.

## Failing node IDs at the baseline

```text
tests/test_360_export_safety.py::test_packaged_360_export_uses_static_projection_safety_mode
tests/test_360_projection_consistency.py::test_thumbnail_filter_uses_saved_spherical_pose
tests/test_api.py::test_api_spherical_landmark_save_merges_partial_and_empty_payloads
tests/test_api.py::test_wizard_prepare_adopts_matching_project_from_disk_without_suffix_copy
tests/test_api.py::test_spherical_landmarks_accept_comma_decimal_and_normalize_yaw
tests/test_api.py::test_spherical_landmark_sanitizer_preserves_saved_values_and_defaults_blanks
tests/test_api.py::test_360_shot_setup_is_not_a_per_export_ui_step
tests/test_composition_forensics.py::test_saved_360_profile_roundtrip_uses_original_source_over_proxy
tests/test_composition_forensics.py::test_360_review_pool_has_twenty_reserve_views_across_proxy_aliases
tests/test_composition_forensics.py::test_edit_cadence_uses_long_holds_until_music_is_very_intense
tests/test_composition_forensics.py::test_gentle_fixed_camera_motion_reaches_endpoint
tests/test_composition_forensics.py::test_preview_and_export_share_canonical_360_pose
tests/test_composition_forensics.py::test_safe_360_framing_limits_zoom_and_pitch
tests/test_composition_forensics.py::test_saved_360_landmarks_are_canonicalized_before_persistence
tests/test_composition_forensics.py::test_360_motion_commands_reach_the_end_of_the_shot
tests/test_export.py::test_spherical_proxy_fallback_keeps_v360_sendcmd
tests/test_export.py::test_spherical_pan_steps_keep_angle_increments_small
tests/test_export.py::test_continuous_spherical_segments_keep_cut_count_flat
tests/test_export.py::test_sendcmd_rate_fields_are_seconds_not_frames[shot1-90.0]
tests/test_export.py::test_shot_wider_than_the_old_rectilinear_cap_is_no_longer_clamped_to_190
tests/test_export.py::test_normal_landmark_fov_stays_natural_and_reaches_v360_unchanged_when_in_range
tests/test_export.py::test_automatic_hold_yaw_travel_is_capped_to_a_few_degrees
tests/test_export.py::test_projection_choice_is_fixed_per_segment_so_fov_drift_cannot_pop_the_framing
tests/test_export.py::test_a_recorded_take_that_zooms_wide_is_stereographic_for_its_whole_duration
tests/test_export.py::test_planet_framing_is_unchanged_by_the_wide_shot_support
tests/test_show_music.py::test_a_jingle_proposal_reaches_the_render_as_a_sound_not_a_picture
```
