from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from figma_to_fgui import artifacts
from figma_to_fgui.artifacts import ArtifactIntegrityError, ArtifactStore
from figma_to_fgui.service_contracts import ChangeBundle, ChangeFile, FileOperation


def make_bundle() -> ChangeBundle:
    payload = b"<component/>"
    return ChangeBundle(
        job_id="job-1",
        project_id="project-1",
        files=(
            ChangeFile(
                operation=FileOperation.CREATE,
                relative_path="Sample/Main.xml",
                after_sha256=hashlib.sha256(payload).hexdigest(),
                content_b64=base64.b64encode(payload).decode("ascii"),
            ),
        ),
    )


def test_artifact_store_round_trips_by_content_hash(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    bundle = make_bundle()
    digest = store.put(bundle)
    assert len(digest) == 64
    assert store.get(digest) == bundle
    assert list(tmp_path.iterdir()) == [tmp_path / f"{digest}.json"]


def test_identical_concurrent_artifact_writes_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path)
    bundle = make_bundle()
    original_mkstemp = artifacts.tempfile.mkstemp
    create_barrier = Barrier(2)
    temporary_paths: list[Path] = []

    def synchronized_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        descriptor, path = original_mkstemp(*args, **kwargs)  # type: ignore[arg-type]
        temporary_paths.append(Path(path))
        create_barrier.wait(timeout=10)
        return descriptor, path

    monkeypatch.setattr(artifacts.tempfile, "mkstemp", synchronized_mkstemp)
    with ThreadPoolExecutor(max_workers=2) as executor:
        digests = list(executor.map(lambda _: store.put(bundle), range(2)))

    assert digests == [digests[0], digests[0]]
    assert len(set(temporary_paths)) == 2
    assert store.get(digests[0]) == bundle
    assert list(tmp_path.iterdir()) == [tmp_path / f"{digests[0]}.json"]


def test_artifact_store_detects_tampering(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    digest = store.put(make_bundle())
    (tmp_path / f"{digest}.json").write_text("{}", "utf-8")
    with pytest.raises(ArtifactIntegrityError):
        store.get(digest)


@pytest.mark.parametrize("digest", ["../escape", "not-a-hash", "a" * 63])
def test_artifact_store_rejects_invalid_digest(tmp_path: Path, digest: str) -> None:
    with pytest.raises(ArtifactIntegrityError):
        ArtifactStore(tmp_path).get(digest)
