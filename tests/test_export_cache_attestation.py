from __future__ import annotations

from core.stages.export import _segment_cache_stamp_payload


def test_segment_cache_attestation_is_not_invalidated_by_git_commit():
    payload = _segment_cache_stamp_payload(
        {
            "spherical_shot": {
                "label": "singer",
                "yaw": 120.0,
                "pitch": -10.0,
                "fov": 82.0,
            }
        }
    )
    assert "git_commit" not in payload
    assert payload["export_segment_recipe"] > 0
    assert payload["spherical_motion_recipe_version"] > 0
