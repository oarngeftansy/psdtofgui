from __future__ import annotations

import tarfile
from pathlib import Path

import py7zr
import pytest

from figma_to_fgui import project_upload
from figma_to_fgui.project_upload import (
    UploadError,
    UploadLimits,
    extract_project_archive,
    extract_project_zip,
)
from tests.helpers.zip_projects import (
    write_nul_directory_zip,
    write_nul_name_zip,
    write_project_zip,
)

PACKAGE_XML = b"<package name='Sample'><resources/></package>"


def test_default_upload_limits_match_the_boundary() -> None:
    assert UploadLimits() == UploadLimits(
        max_compressed_bytes=500 * 1024 * 1024,
        max_total_uncompressed_bytes=2 * 1024 * 1024 * 1024,
        max_entries=20_000,
        max_file_bytes=100 * 1024 * 1024,
        max_compression_ratio=20.0,
    )


def test_extracts_project_at_root_or_under_one_wrapper(tmp_path: Path) -> None:
    for prefix in ("", "GameUI/"):
        archive = write_project_zip(
            tmp_path / f"{prefix.rstrip('/') or 'root'}.zip",
            {
                f"{prefix}Sample/package.xml": PACKAGE_XML,
                f"{prefix}Sample/Panel/Main.xml": b"<component/>",
            },
        )

        destination = tmp_path / f"out-{prefix.rstrip('/') or 'root'}"
        extracted = extract_project_zip(archive, destination)

        assert extracted.root == (destination / "GameUI" if prefix else destination)
        assert extracted.files == ("Sample/package.xml", "Sample/Panel/Main.xml")


def test_accepts_real_fairygui_package_description_xml(tmp_path: Path) -> None:
    package_xml = b"""<?xml version='1.0' encoding='utf-8'?>
<packageDescription id='mrz8gz9s'><resources/></packageDescription>"""
    archive = write_project_zip(
        tmp_path / "fairygui-6.zip",
        {
            "figma2fgui/isekaiUI.fairy": b"{}",
            "figma2fgui/assets/MyVillage/package.xml": package_xml,
            "figma2fgui/assets/MyVillage/Panel/RankUp.xml": b"<component/>",
        },
    )

    extracted = extract_project_zip(archive, tmp_path / "project")

    assert extracted.root == tmp_path / "project" / "figma2fgui"
    assert "assets/MyVillage/package.xml" in extracted.files


@pytest.mark.parametrize("extension", [".tar", ".tar.gz", ".tgz"])
def test_extract_project_archive_accepts_tar_projects(tmp_path: Path, extension: str) -> None:
    source = tmp_path / "source"
    (source / "GameUI" / "assets" / "Sample" / "Panel").mkdir(parents=True)
    (source / "GameUI" / "GameUI.fairy").write_text("{}", encoding="utf-8")
    (source / "GameUI" / "assets" / "Sample" / "package.xml").write_bytes(PACKAGE_XML)
    (source / "GameUI" / "assets" / "Sample" / "Panel" / "Main.xml").write_bytes(b"<component/>")
    archive = tmp_path / f"GameUI{extension}"
    mode = "w:gz" if extension in {".tar.gz", ".tgz"} else "w"
    with tarfile.open(archive, mode) as output:
        output.add(source / "GameUI", arcname="GameUI")

    extracted = extract_project_archive(archive, tmp_path / "project", archive.name)

    assert extracted.root == tmp_path / "project" / "GameUI"
    assert "assets/Sample/package.xml" in extracted.files


def test_extract_project_archive_accepts_7z_projects(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "GameUI" / "assets" / "Sample" / "Panel").mkdir(parents=True)
    (source / "GameUI" / "GameUI.fairy").write_text("{}", encoding="utf-8")
    (source / "GameUI" / "assets" / "Sample" / "package.xml").write_bytes(PACKAGE_XML)
    (source / "GameUI" / "assets" / "Sample" / "Panel" / "Main.xml").write_bytes(b"<component/>")
    archive = tmp_path / "GameUI.7z"
    with py7zr.SevenZipFile(archive, "w") as output:
        output.writeall(source / "GameUI", arcname="GameUI")

    extracted = extract_project_archive(archive, tmp_path / "project", archive.name)

    assert extracted.root == tmp_path / "project" / "GameUI"
    assert "assets/Sample/package.xml" in extracted.files


def test_tar_gz_keeps_the_compression_ratio_limit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "Sample").mkdir(parents=True)
    (source / "Sample" / "package.xml").write_bytes(PACKAGE_XML)
    (source / "Sample" / "large.bin").write_bytes(b"0" * 100_000)
    archive = tmp_path / "GameUI.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source / "Sample", arcname="Sample")

    with pytest.raises(UploadError) as error:
        extract_project_archive(
            archive,
            tmp_path / "project",
            archive.name,
            UploadLimits(max_compression_ratio=2),
        )

    assert error.value.code == "archive_too_large"
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize("name", ["../escape.xml", "/absolute.xml", "C:/drive.xml"])
def test_rejects_unsafe_names_without_partial_output(tmp_path: Path, name: str) -> None:
    archive = write_project_zip(tmp_path / "bad.zip", {name: b"x"})
    destination = tmp_path / "project"

    with pytest.raises(UploadError) as error:
        extract_project_zip(archive, destination)

    assert error.value.code == "unsafe_archive"
    assert str(tmp_path) not in error.value.user_message
    assert not destination.exists()


def test_rejects_nul_name_without_partial_output(tmp_path: Path) -> None:
    destination = tmp_path / "project"

    with pytest.raises(UploadError) as error:
        extract_project_zip(write_nul_name_zip(tmp_path / "null-name.zip"), destination)

    assert error.value.code == "unsafe_archive"
    assert not destination.exists()


def test_rejects_nul_header_before_copying_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy_calls = 0

    def count_copy(*args: object, **kwargs: object) -> int:
        nonlocal copy_calls
        del args, kwargs
        copy_calls += 1
        return 0

    monkeypatch.setattr(project_upload, "_copy_entry", count_copy)

    with pytest.raises(UploadError) as error:
        extract_project_zip(write_nul_name_zip(tmp_path / "null-name.zip"), tmp_path / "project")

    assert error.value.code == "unsafe_archive"
    assert copy_calls == 0
    assert not (tmp_path / "project").exists()


def test_rejects_nul_directory_header_before_copying_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy_calls = 0

    def count_copy(*args: object, **kwargs: object) -> int:
        nonlocal copy_calls
        del args, kwargs
        copy_calls += 1
        return 0

    monkeypatch.setattr(project_upload, "_copy_entry", count_copy)
    archive = write_nul_directory_zip(
        tmp_path / "null-directory.zip",
        {"Sample/package.xml": PACKAGE_XML},
    )

    with pytest.raises(UploadError) as error:
        extract_project_zip(archive, tmp_path / "project")

    assert error.value.code == "unsafe_archive"
    assert copy_calls == 0
    assert not (tmp_path / "project").exists()


def test_rejects_duplicate_normalized_names_without_partial_output(tmp_path: Path) -> None:
    archive = write_project_zip(
        tmp_path / "duplicate.zip",
        [
            ("Sample/package.xml", PACKAGE_XML),
            ("Sample\\package.xml", PACKAGE_XML),
        ],
    )
    destination = tmp_path / "project"

    with pytest.raises(UploadError) as error:
        extract_project_zip(archive, destination)

    assert error.value.code == "unsafe_archive"
    assert not destination.exists()


def test_rejects_symbolic_link_entries_without_partial_output(tmp_path: Path) -> None:
    archive = write_project_zip(
        tmp_path / "symlink.zip",
        {"Sample/package.xml": PACKAGE_XML},
        symlinks=("Sample/link",),
    )
    destination = tmp_path / "project"

    with pytest.raises(UploadError) as error:
        extract_project_zip(archive, destination)

    assert error.value.code == "unsafe_archive"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("limits", "entries"),
    [
        (UploadLimits(max_entries=1), {"Sample/package.xml": PACKAGE_XML, "Sample/Main.xml": b"x"}),
        (
            UploadLimits(max_file_bytes=1_000),
            {"Sample/package.xml": PACKAGE_XML, "Sample/Main.xml": b"x" * 1_001},
        ),
        (
            UploadLimits(max_total_uncompressed_bytes=len(PACKAGE_XML)),
            {"Sample/package.xml": PACKAGE_XML, "Sample/Main.xml": b"x"},
        ),
        (
            UploadLimits(max_compression_ratio=20),
            {"Sample/package.xml": PACKAGE_XML, "Sample/Main.xml": b"x" * 10_000},
        ),
    ],
)
def test_rejects_archive_limits_without_partial_output(
    tmp_path: Path, limits: UploadLimits, entries: dict[str, bytes]
) -> None:
    archive = write_project_zip(tmp_path / "limited.zip", entries)
    destination = tmp_path / "project"

    with pytest.raises(UploadError) as error:
        extract_project_zip(archive, destination, limits)

    assert error.value.code == "archive_too_large"
    assert str(tmp_path) not in error.value.user_message
    assert not destination.exists()


def test_rejects_compressed_upload_limit_without_partial_output(tmp_path: Path) -> None:
    archive = write_project_zip(tmp_path / "compressed.zip", {"Sample/package.xml": PACKAGE_XML})
    destination = tmp_path / "project"

    with pytest.raises(UploadError) as error:
        extract_project_zip(archive, destination, UploadLimits(max_compressed_bytes=1))

    assert error.value.code == "archive_too_large"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("payload", "entries", "expected_code"),
    [
        (b"not a zip", None, "unsafe_archive"),
        (None, {"Sample/package.xml": b"<package>"}, "invalid_fgui_project"),
        (None, {"Sample/package.xml": b"<component/>"}, "invalid_fgui_project"),
        (None, {"Sample/Panel/Main.xml": b"<component/>"}, "invalid_fgui_project"),
        (None, {"outer/inner/Sample/package.xml": PACKAGE_XML}, "invalid_fgui_project"),
    ],
)
def test_rejects_invalid_projects_without_partial_output(
    tmp_path: Path, payload: bytes | None, entries: dict[str, bytes] | None, expected_code: str
) -> None:
    archive = tmp_path / "invalid.zip"
    if payload is not None:
        archive.write_bytes(payload)
    else:
        assert entries is not None
        write_project_zip(archive, entries)
    destination = tmp_path / "project"

    with pytest.raises(UploadError) as error:
        extract_project_zip(archive, destination)

    assert error.value.code == expected_code
    assert str(tmp_path) not in error.value.user_message
    assert not destination.exists()
