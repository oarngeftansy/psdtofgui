from __future__ import annotations

from pathlib import Path

from figma_to_fgui.hifi_preview import build_structure_preview
from figma_to_fgui.hifi_project_inspector import (
    inspect_component,
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement/old_project"


def _inventory():
    project = index_uploaded_project(FIXTURE, "old.zip")
    tree = inspect_hifi_targets(FIXTURE, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == "sketch01")
    return inspect_component(FIXTURE, target_from_option(project, package, directory, component))


def test_preview_is_explicitly_structural_and_has_normalized_bounds() -> None:
    preview = build_structure_preview(_inventory())
    assert preview.evidence_kind == "structure_preview"
    assert preview.objects
    for item in preview.objects:
        assert all(0 <= value <= 1 for value in item.bounds)
