"""Stub edit stage."""

from __future__ import annotations

from typing import Any

from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json


class EditStage(Stage):
    """Placeholder for edit decision generation."""

    name = "edit"
    dependencies = ["cut"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint cut output and edit settings."""
        return stable_fingerprint(
            {
                "cut": project.data["stages"]["cut"].get("fingerprint"),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the edit decision artifact path."""
        return {"edit_decisions": str(artifact_path(project, "edit_decisions.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Write placeholder edit decisions."""
        progress_callback(50, "Building placeholder edit decisions")
        path = artifact_path(project, "edit_decisions.json")
        write_artifact_json(path, {"stage": self.name, "decisions": []})
        progress_callback(100, "Edit stub complete")
        return self.outputs(project)
