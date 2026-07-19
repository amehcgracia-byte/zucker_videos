"""Stub export stage."""

from __future__ import annotations

from typing import Any

from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json


class ExportStage(Stage):
    """Placeholder for final video rendering."""

    name = "export"
    dependencies = ["edit"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint edit output and export settings."""
        return stable_fingerprint(
            {
                "edit": project.data["stages"]["edit"].get("fingerprint"),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the export manifest artifact path."""
        return {"export_manifest": str(artifact_path(project, "export_manifest.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Write a placeholder export manifest."""
        progress_callback(50, "Preparing placeholder exports")
        path = artifact_path(project, "export_manifest.json")
        write_artifact_json(path, {"stage": self.name, "exports": []})
        progress_callback(100, "Export stub complete")
        return self.outputs(project)
