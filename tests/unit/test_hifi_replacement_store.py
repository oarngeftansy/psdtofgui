from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from figma_to_fgui.figma_selection import SelectionManifest
from figma_to_fgui.hifi_mapping import build_mapping
from figma_to_fgui.hifi_project_inspector import (
    inspect_component,
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.hifi_replacement_models import (
    HIFI_MAPPING_POLICY_REVISION,
    HifiEditorVerification,
    HifiReplacementReview,
)
from figma_to_fgui.hifi_replacement_store import HifiReplacementStore, HifiReplacementStoreError
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement"


def _values():
    root = FIXTURE / "old_project"
    project = index_uploaded_project(root, "old.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == "sketch01")
    target = target_from_option(project, package, directory, component)
    inventory = inspect_component(root, target)
    manifest = SelectionManifest.model_validate_json((FIXTURE / "hifi-selection.json").read_text("utf-8"))
    return target, build_mapping(inventory, manifest)


def test_store_is_idempotent_and_isolates_owner(tmp_path: Path) -> None:
    store = HifiReplacementStore(tmp_path)
    target, mapping = _values()
    selection_id = uuid.uuid4().hex
    first = store.begin("owner-a", selection_id, target, mapping, "same-request")
    second = store.begin("owner-a", selection_id, target, mapping, "same-request")
    assert first.view.session_id == second.view.session_id
    with pytest.raises(HifiReplacementStoreError, match="not_found"):
        store.get(first.view.session_id, "owner-b")


def test_same_request_key_gets_a_new_session_after_policy_upgrade(tmp_path: Path) -> None:
    store = HifiReplacementStore(tmp_path)
    target, mapping = _values()
    selection_id = uuid.uuid4().hex
    old_policy = mapping.model_copy(update={
        "policy_revision": max(0, HIFI_MAPPING_POLICY_REVISION - 1),
    })
    current_policy = mapping.model_copy(update={
        "policy_revision": HIFI_MAPPING_POLICY_REVISION,
    })

    old = store.begin("owner", selection_id, target, old_policy, "same-request")
    current = store.begin("owner", selection_id, target, current_policy, "same-request")

    assert old.view.session_id != current.view.session_id
    assert old.mapping.policy_revision != current.mapping.policy_revision
    assert current.mapping.policy_revision == HIFI_MAPPING_POLICY_REVISION


def test_new_mapping_supersedes_in_flight_candidate_without_failed_overwrite(
    tmp_path: Path,
) -> None:
    store = HifiReplacementStore(tmp_path)
    target, draft = _values()
    confirmed = draft.model_copy(update={"unresolved_count": 0})
    session = store.begin(
        "owner-a", uuid.uuid4().hex, target, confirmed, "supersede-request"
    )
    store.mark_building(
        session.view.session_id, "owner-a", confirmed.mapping_revision
    )
    revised = confirmed.model_copy(
        update={"mapping_revision": confirmed.mapping_revision + 1}
    )
    changed = store.save_mapping(
        session.view.session_id,
        "owner-a",
        confirmed.mapping_revision,
        revised,
    )
    assert changed.view.status == "mapping"
    after_old_failure = store.mark_failed(
        session.view.session_id,
        "owner-a",
        confirmed.mapping_revision,
    )
    assert after_old_failure.view.status == "mapping"
    assert after_old_failure.mapping.mapping_revision == revised.mapping_revision


def test_approval_requires_all_evidence_for_the_same_current_candidate(tmp_path: Path) -> None:
    store = HifiReplacementStore(tmp_path)
    target, mapping = _values()
    mapping = mapping.model_copy(update={"unresolved_count": 0})
    stored = store.begin("owner", uuid.uuid4().hex, target, mapping, "approval-matrix")
    digest = "a" * 64
    review = HifiReplacementReview(
        policy_revision=HIFI_MAPPING_POLICY_REVISION, session_id=stored.view.session_id, mapping_revision=1,
        target=target, changed_files=(), object_diffs=(), protected_checks_passed=True,
        parse_coverage_complete=True, approvable=True, candidate_sha256=digest,
    )
    verification = HifiEditorVerification(
        session_id=stored.view.session_id, candidate_sha256=digest,
        editor_found=True, editor_version="6.1.4", project_opened=True,
        component_opened=True, render_captured=True, expected_width=750,
        expected_height=420, full_frame=True, approvable=True,
    )
    ready = replace(stored, review=review, editor_verification=verification, artifact_sha256=digest)
    assert ready.approval_ready
    assert not replace(ready, editor_verification=None).approval_ready
    assert not replace(ready, artifact_sha256="b" * 64).approval_ready
    for field, value in (("policy_revision", 0), ("unresolved_count", 1)):
        assert not replace(ready, mapping=mapping.model_copy(update={field: value})).approval_ready
    for field, value in (("policy_revision", 0), ("parse_coverage_complete", False),
                         ("protected_checks_passed", False), ("approvable", False),
                         ("candidate_sha256", "b" * 64)):
        assert not replace(ready, review=review.model_copy(update={field: value})).approval_ready
    for field, value in (("approvable", False), ("full_frame", False),
                         ("render_captured", False), ("component_opened", False),
                         ("project_opened", False), ("candidate_sha256", "b" * 64)):
        assert not replace(ready, editor_verification=verification.model_copy(update={field: value})).approval_ready
