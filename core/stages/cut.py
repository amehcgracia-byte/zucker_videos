"""Stub cut stage."""

from __future__ import annotations

from typing import Any

from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json


class CutStage(Stage):
    """Placeholder for per-song coverage and segment export."""

    name = "cut"
    dependencies = ["sync"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint sync output and cut settings."""
        return stable_fingerprint(
            {
                "sync": project.data["stages"]["sync"].get("fingerprint"),
                "songs": project.data["inputs"].get("songs"),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the coverage artifact path."""
        return {"coverage": str(artifact_path(project, "coverage.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Write fake coverage data."""
        progress_callback(35, "Calculating placeholder coverage")
        path = artifact_path(project, "coverage.json")
        write_artifact_json(path, {"stage": self.name, "songs": [], "segments": []})
        progress_callback(100, "Cut stub complete")
        return self.outputs(project)
