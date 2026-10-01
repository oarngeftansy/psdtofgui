from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from figma_to_fgui import project_store
from figma_to_fgui.project_store import ProjectIntegrityError, ProjectStore
from figma_to_fgui.uploaded_project import UploadedProjectVersion, index_uploaded_project


def indexed_project(tmp_path: Path) -> tuple[Path, UploadedProjectVersion]:
    root = tmp_path / "source"
    (root / "Sample").mkdir(parents=True)
    (root / "Sample" / "package.xml").write_text(
        "<package id='sample'><resources><image id='image-1' name='hero.png'/></resources></package>",
        "utf-8",
    )
    Image.new("RGBA", (32, 16), (1, 2, 3, 4)).save(root / "Sample" / "hero.png")
    (root / "Sample" / "Main.xml").write_text("<component/>", "utf-8")
    return root, index_uploaded_project(root, "sample.zip")


def test_creates_distinct_versions_that_share_one_immutable_artifact(tmp_path: Path) -> None:
    root, first = indexed_project(tmp_path)
    second = index_uploaded_project(root, "repeat.zip")
    store = ProjectStore(tmp_path / "storage")

    assert store.create(first, root) == first
    assert store.create(second, root) == second
    artifact = store.artifact_path(first.project_id)
    assert artifact == store.artifact_path(second.project_id)
    assert artifact == tmp_path / "storage" / "projects" / "artifacts" / first.fingerprint
    assert (artifact / ".figma-to-fgui-preview").is_dir()
    assert (tmp_path / "storage" / "projects" / "versions" / f"{first.project_id}.json").is_file()
    assert (tmp_path / "storage" / "projects" / "versions" / f"{second.project_id}.json").is_file()


def test_detects_missing_or_tampered_metadata_and_artifacts_without_server_paths(tmp_path: Path) -> None:
    root, version = indexed_project(tmp_path)
    store = ProjectStore(tmp_path / "storage")
    store.create(version, root)
    metadata = tmp_path / "storage" / "projects" / "versions" / f"{version.project_id}.json"
    metadata.unlink()
    with pytest.raises(ProjectIntegrityError) as missing:
        store.get(version.project_id)
    assert str(tmp_path) not in str(missing.value)

    store.create(version, root)
    payload = json.loads(metadata.read_text("utf-8"))
    payload["payload"]["fingerprint"] = "0" * 64
    metadata.write_text(json.dumps(payload), "utf-8")
    with pytest.raises(ProjectIntegrityError) as mismatch:
        store.artifact_path(version.project_id)
    assert str(tmp_path) not in str(mismatch.value)

    metadata.unlink()
    store.create(version, root)
    artifact = tmp_path / "storage" / "projects" / "artifacts" / version.fingerprint
    (artifact / "Sample" / "Main.xml").write_text("tampered", "utf-8")
    with pytest.raises(ProjectIntegrityError):
        store.get(version.project_id)


def test_returns_only_known_thumbnail_paths_inside_the_artifact(tmp_path: Path) -> None:
    root, version = indexed_project(tmp_path)
    store = ProjectStore(tmp_path / "storage")
    store.create(version, root)

    thumbnail = store.thumbnail_path(version.project_id, "image-1")
    assert thumbnail.is_file()
    assert thumbnail.is_relative_to(store.artifact_path(version.project_id))
    with pytest.raises(ProjectIntegrityError):
        store.thumbnail_path(version.project_id, "../../escape")

    thumbnail.write_bytes(b"tampered preview")
    with pytest.raises(ProjectIntegrityError):
        store.get(version.project_id)


def test_concurrent_same_fingerprint_creates_two_versions_without_replacing_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, first = indexed_project(tmp_path)
    second = index_uploaded_project(root, "repeat.zip")
    storage = tmp_path / "storage"
    target = storage / "projects" / "artifacts" / first.fingerprint
    barrier = threading.Barrier(2)
    publish_lock = threading.Lock()
    replace = project_store.os.replace

    def publish_once(source: object, destination: object) -> None:
        if Path(destination) != target:
            replace(source, destination)
            return
        barrier.wait(timeout=5)
        with publish_lock:
            if target.exists():
                raise FileExistsError
            replace(source, destination)

    monkeypatch.setattr(project_store.os, "replace", publish_once)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(ProjectStore(storage).create, first, root)
        second_result = pool.submit(ProjectStore(storage).create, second, root)
        assert first_result.result(timeout=10) == first
        assert second_result.result(timeout=10) == second

    store = ProjectStore(storage)
    assert store.get(first.project_id) == first
    assert store.get(second.project_id) == second
    assert store.artifact_path(first.project_id) == store.artifact_path(second.project_id) == target
    assert not list(target.parent.glob("*.lock"))


def test_losing_artifact_publication_uses_an_identical_existing_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, version = indexed_project(tmp_path)
    storage = tmp_path / "storage"
    target = storage / "projects" / "artifacts" / version.fingerprint
    replace = project_store.os.replace

    def publish_then_report_conflict(source: object, destination: object) -> None:
        if Path(destination) != target:
            replace(source, destination)
            return
        replace(source, destination)
        raise OSError("another writer won")

    monkeypatch.setattr(project_store.os, "replace", publish_then_report_conflict)
    store = ProjectStore(storage)

    assert store.create(version, root) == version
    assert store.get(version.project_id) == version


def test_losing_artifact_publication_rejects_a_tampered_existing_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, version = indexed_project(tmp_path)
    storage = tmp_path / "storage"
    target = storage / "projects" / "artifacts" / version.fingerprint
    replace = project_store.os.replace

    def publish_tampered_artifact(source: object, destination: object) -> None:
        if Path(destination) != target:
            replace(source, destination)
            return
        target.mkdir()
        (target / "tampered").write_text("not the uploaded project", "utf-8")
        raise OSError("another writer won")

    monkeypatch.setattr(project_store.os, "replace", publish_tampered_artifact)
    with pytest.raises(ProjectIntegrityError):
        ProjectStore(storage).create(version, root)


def test_does_not_mask_unrelated_artifact_publication_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, version = indexed_project(tmp_path)
    target = tmp_path / "storage" / "projects" / "artifacts" / version.fingerprint
    replace = project_store.os.replace

    def deny_publication(source: object, destination: object) -> None:
        if Path(destination) == target:
            raise PermissionError("denied")
        replace(source, destination)

    monkeypatch.setattr(project_store.os, "replace", deny_publication)
    with pytest.raises(PermissionError, match="denied"):
        ProjectStore(tmp_path / "storage").create(version, root)
