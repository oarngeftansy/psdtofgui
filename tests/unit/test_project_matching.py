from __future__ import annotations

from pathlib import Path

import pytest

from figma_to_fgui.agent import AgentConfig, BoundProject, fingerprint_local_project
from figma_to_fgui.service_contracts import JobStatus, JobView
from figma_to_fgui.uploaded_project import index_uploaded_project


def write_project(root: Path, package_name: str = "Sample") -> Path:
    (root / package_name).mkdir(parents=True)
    (root / package_name / "package.xml").write_text(
        f"<package id='{package_name.lower()}'><resources/></package>", "utf-8"
    )
    (root / package_name / "Main.xml").write_text("<component/>", "utf-8")
    return root


def assignment(fingerprint: str, package_names: tuple[str, ...] = ("Sample",)) -> JobView:
    return JobView(
        job_id="job-1",
        project_id="uploaded-project",
        project_fingerprint=fingerprint,
        package_names=package_names,
        status=JobStatus.APPLYING,
    )


def bound_project(path: Path) -> BoundProject:
    return BoundProject(
        path=path,
        fingerprint=fingerprint_local_project(path),
        package_names=("Sample",),
    )


def test_local_fingerprint_reuses_uploaded_manifest_and_ignores_agent_artifacts(tmp_path: Path) -> None:
    project = write_project(tmp_path / "project")
    expected = index_uploaded_project(project, "project.zip").fingerprint
    (project / ".figma-to-fgui" / "state.json").parent.mkdir()
    (project / ".figma-to-fgui" / "state.json").write_text("local", "utf-8")

    assert fingerprint_local_project(project) == expected


def test_exact_fingerprint_selects_binding_without_path_leak(tmp_path: Path) -> None:
    project_a = write_project(tmp_path / "a", "Alpha")
    project_b = write_project(tmp_path / "b")
    config = AgentConfig(
        agent_id="agent-1",
        name="Desk",
        projects={"old-a": BoundProject(path=project_a, fingerprint="a" * 64, package_names=("Alpha",)),
                  "old-b": bound_project(project_b)},
    )
    request = assignment(fingerprint_local_project(project_b))

    assert config.select_project(request) == project_b
    assert str(project_b) not in request.model_dump_json()


def test_changed_bound_project_is_not_selected_by_its_old_fingerprint(tmp_path: Path) -> None:
    project = write_project(tmp_path / "project")
    config = AgentConfig(agent_id="agent-1", name="Desk", projects={"old": bound_project(project)})
    request = assignment(fingerprint_local_project(project))
    (project / "Sample" / "Main.xml").write_text("<component name='changed'/>", "utf-8")

    assert config.select_project(request) is None


def test_current_fingerprint_is_authoritative_over_saved_binding_metadata(tmp_path: Path) -> None:
    project = write_project(tmp_path / "project")
    config = AgentConfig(
        agent_id="agent-1",
        name="Desk",
        projects={"old": BoundProject(path=project, fingerprint="a" * 64, package_names=("Sample",))},
    )

    assert config.select_project(assignment(fingerprint_local_project(project))) == project


@pytest.mark.parametrize("candidate_count", (0, 1, 2))
def test_non_exact_package_matches_require_explicit_selection(
    tmp_path: Path, candidate_count: int
) -> None:
    projects = {
        f"local-{index}": bound_project(write_project(tmp_path / f"project-{index}"))
        for index in range(candidate_count)
    }
    config = AgentConfig(agent_id="agent-1", name="Desk", projects=projects)

    assert config.select_project(assignment("f" * 64)) is None


def test_config_persists_local_binding_metadata(tmp_path: Path) -> None:
    project = write_project(tmp_path / "project")
    path = tmp_path / "agent.json"
    config = AgentConfig(agent_id="agent-1", name="Desk", projects={"old": bound_project(project)})

    config.save(path)

    assert AgentConfig.load(path) == config
