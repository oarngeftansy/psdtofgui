from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from figma_to_fgui import api, project_upload
from figma_to_fgui.api import create_app
from figma_to_fgui.project_upload import UploadLimits
from tests.helpers.zip_projects import write_project_zip

PACKAGE_XML = b"""<package id='pkg-sample'><resources>
<image id='img-background' name='Background.png' path='assets'/>
</resources></package>"""


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
        )
    )


@pytest.fixture
def valid_zip(tmp_path: Path) -> Path:
    return write_project_zip(
        tmp_path / "GameUI.zip",
        {
            "Sample/package.xml": PACKAGE_XML,
            "Sample/Panel/Main.xml": b"<component/>",
            "Sample/assets/Background.png": (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f"
                b"\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
        },
    )


def upload(
    client: TestClient, archive: Path, content_type: str = "application/zip"
) -> dict[str, object]:
    with archive.open("rb") as content:
        response = client.post(
            "/v1/projects/uploads",
            files={"project": (archive.name, content, content_type)},
        )
    assert response.status_code == 201, response.text
    return response.json()


def project_client(tmp_path: Path) -> tuple[TestClient, Path]:
    data_dir = tmp_path / "data"
    return (
        TestClient(
            create_app(
                data_dir=data_dir,
                fixtures_root=Path("tests/fixtures"),
                rules_path=Path("rules/default/classification.yaml"),
            )
        ),
        data_dir,
    )


def test_upload_returns_only_designer_project_summary(client: TestClient, valid_zip: Path) -> None:
    response = upload(client, valid_zip)

    assert response["version"] == 1
    assert len(response["project_id"]) == 32
    assert response["display_name"] == "GameUI.zip"
    assert response["packages"] == [{"name": "Sample", "resource_count": 3}]
    serialized = str(response).lower()
    for forbidden in ("fingerprint", "sha", "relative_path", "xml", "asset_id", "changeset"):
        assert forbidden not in serialized


def test_upload_accepts_x_zip_content_type(client: TestClient, valid_zip: Path) -> None:
    response = upload(client, valid_zip, "application/x-zip-compressed")

    assert response["display_name"] == "GameUI.zip"


def test_upload_accepts_generic_browser_content_type_for_valid_archive(client: TestClient, valid_zip: Path) -> None:
    response = upload(client, valid_zip, "application/octet-stream")

    assert response["display_name"] == "GameUI.zip"


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("GameUI.rar", "application/vnd.rar"),
        ("GameUI.7z", "application/x-7z-compressed"),
        ("GameUI.tar", "application/x-tar"),
        ("GameUI.tar.gz", "application/gzip"),
        ("GameUI.tgz", "application/gzip"),
    ],
)
def test_upload_routes_supported_archive_formats_to_the_archive_reader(
    client: TestClient, filename: str, content_type: str
) -> None:
    response = client.post(
        "/v1/projects/uploads",
        files={"project": (filename, b"not an archive", content_type)},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsafe_archive"


def test_upload_requires_the_named_project_field_without_validation_details(
    client: TestClient,
) -> None:
    response = client.post("/v1/projects/uploads", files={"archive": ("GameUI.zip", b"zip")})

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "code": "invalid_fgui_project",
            "message": project_upload._upload_error("invalid_fgui_project").user_message,
        }
    }


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("GameUI.txt", "application/zip")],
)
def test_upload_rejects_an_unrecognized_zip_field(
    client: TestClient, valid_zip: Path, filename: str, content_type: str
) -> None:
    with valid_zip.open("rb") as content:
        response = client.post(
            "/v1/projects/uploads",
            files={"project": (filename, content, content_type)},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_fgui_project"


def test_upload_stops_an_oversized_stream_without_temporary_files(
    tmp_path: Path, valid_zip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, data_dir = project_client(tmp_path)
    monkeypatch.setattr(api, "DEFAULT_UPLOAD_LIMITS", UploadLimits(max_compressed_bytes=8))

    with valid_zip.open("rb") as content:
        response = client.post(
            "/v1/projects/uploads",
            files={"project": (valid_zip.name, content, "application/zip")},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "archive_too_large"
    assert list((data_dir / "uploads").iterdir()) == []
    assert not (data_dir / "projects" / "versions").exists()


def test_upload_maps_invalid_project_metadata_to_the_designer_error(tmp_path: Path) -> None:
    client, data_dir = project_client(tmp_path)
    malformed = write_project_zip(
        tmp_path / "Broken.zip",
        {"Sample/package.xml": b"<package><resources/></package>"},
    )

    with malformed.open("rb") as content:
        response = client.post(
            "/v1/projects/uploads",
            files={"project": (malformed.name, content, "application/zip")},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_fgui_project",
        "message": project_upload._upload_error("invalid_fgui_project").user_message,
    }
    assert list((data_dir / "uploads").iterdir()) == []
    assert not (data_dir / "projects" / "versions").exists()


def test_upload_preserves_the_task_one_unsafe_archive_error(tmp_path: Path) -> None:
    client, data_dir = project_client(tmp_path)
    unsafe = tmp_path / "unsafe.zip"
    unsafe.write_bytes(b"not a zip")

    with unsafe.open("rb") as content:
        response = client.post(
            "/v1/projects/uploads",
            files={"project": (unsafe.name, content, "application/zip")},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "unsafe_archive",
        "message": project_upload._upload_error("unsafe_archive").user_message,
    }
    assert list((data_dir / "uploads").iterdir()) == []
    assert not (data_dir / "projects" / "versions").exists()


def test_successful_upload_cleans_temporary_upload_and_extraction_paths(
    tmp_path: Path, valid_zip: Path
) -> None:
    client, data_dir = project_client(tmp_path)

    upload(client, valid_zip)

    assert list((data_dir / "uploads").iterdir()) == []


def test_close_failure_does_not_stop_later_upload_cleanup(
    tmp_path: Path, valid_zip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = project_client(tmp_path)
    closed: list[object] = []
    unlinked: list[Path] = []
    removed: list[Path] = []
    original_close = StarletteUploadFile.close
    original_unlink = api.Path.unlink
    original_rmtree = api.shutil.rmtree

    async def fail_first_close(upload_file: StarletteUploadFile) -> None:
        closed.append(upload_file)
        if len(closed) == 1:
            raise OSError("close failed")
        await original_close(upload_file)

    def record_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith("upload-"):
            unlinked.append(path)
        original_unlink(path, *args, **kwargs)

    def record_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        removed.append(path)
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(StarletteUploadFile, "close", fail_first_close)
    monkeypatch.setattr(api.Path, "unlink", record_unlink)
    monkeypatch.setattr(api.shutil, "rmtree", record_rmtree)

    assert upload(client, valid_zip)["display_name"] == "GameUI.zip"
    assert closed and unlinked and removed


def test_unlink_failure_does_not_stop_extraction_cleanup(
    tmp_path: Path, valid_zip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = project_client(tmp_path)
    removed: list[Path] = []
    original_unlink = api.Path.unlink
    original_rmtree = api.shutil.rmtree

    def fail_upload_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith("upload-"):
            raise OSError("unlink failed")
        original_unlink(path, *args, **kwargs)

    def record_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        removed.append(path)
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(api.Path, "unlink", fail_upload_unlink)
    monkeypatch.setattr(api.shutil, "rmtree", record_rmtree)

    assert upload(client, valid_zip)["display_name"] == "GameUI.zip"
    assert removed


def test_project_and_package_routes_return_the_same_safe_summary(
    client: TestClient, valid_zip: Path
) -> None:
    created = upload(client, valid_zip)
    project_id = str(created["project_id"])

    project = client.get(f"/v1/projects/{project_id}")
    packages = client.get(f"/v1/projects/{project_id}/packages")

    assert project.status_code == packages.status_code == 200
    assert project.json() == packages.json() == created


@pytest.mark.parametrize("project_id", ["missing", "not-a-project-id", "f" * 32])
def test_unknown_or_malformed_project_ids_have_a_safe_not_found_response(
    client: TestClient, project_id: str
) -> None:
    response = client.get(f"/v1/projects/{project_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "project_not_found",
        "message": "鎵句笉鍒拌繖涓?FairyGUI 宸ョ▼銆?",
    }
    assert "projects" not in response.text.lower()
    assert ":\\" not in response.text


def test_thumbnail_route_serves_only_a_declared_image_asset(
    client: TestClient, valid_zip: Path
) -> None:
    created = upload(client, valid_zip)
    project_id = str(created["project_id"])

    thumbnail = client.get(f"/v1/projects/{project_id}/assets/img-background/thumbnail")
    missing_asset = client.get(f"/v1/projects/{project_id}/assets/not-declared/thumbnail")
    missing_project = client.get(f"/v1/projects/{'f' * 32}/assets/img-background/thumbnail")

    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/webp"
    assert thumbnail.content[:4] == b"RIFF"
    assert missing_asset.status_code == 404
    assert missing_asset.json()["detail"]["code"] == "asset_not_found"
    assert missing_project.status_code == 404
    assert missing_project.json()["detail"]["code"] == "project_not_found"
