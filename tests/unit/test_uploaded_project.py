from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from figma_to_fgui.uploaded_project import index_uploaded_project


def write_project(root: Path, image_bytes: bytes = b"image") -> Path:
    (root / "Beta").mkdir(parents=True)
    (root / "Alpha" / "Panel").mkdir(parents=True)
    (root / "Alpha" / "package.xml").write_text(
        "<package id='alpha'><resources><image id='img-alpha' name='hero.png' "
        "path='/Panel'/></resources></package>",
        "utf-8",
    )
    (root / "Beta" / "package.xml").write_text("<package id='beta'><resources/></package>", "utf-8")
    (root / "Alpha" / "Panel" / "hero.png").write_bytes(image_bytes)
    (root / "Alpha" / "Panel" / "Main.xml").write_text("<component/>", "utf-8")
    (root / "readme.txt").write_text("source", "utf-8")
    return root


def png_bytes(tmp_path: Path) -> bytes:
    source = tmp_path / "source.png"
    Image.new("RGBA", (1024, 256), (10, 20, 30, 80)).save(source)
    return source.read_bytes()


def test_indexes_source_files_and_packages_in_name_order(tmp_path: Path) -> None:
    version = index_uploaded_project(write_project(tmp_path / "project", png_bytes(tmp_path)), "upload.zip")

    assert [package.name for package in version.packages] == ["Alpha", "Beta"]
    assert [(item.relative_path, item.kind, item.package_name) for item in version.files] == [
        ("Alpha/Panel/Main.xml", "xml", "Alpha"),
        ("Alpha/Panel/hero.png", "image", "Alpha"),
        ("Alpha/package.xml", "xml", "Alpha"),
        ("Beta/package.xml", "xml", "Beta"),
        ("readme.txt", "other", None),
    ]
    image = next(item for item in version.files if item.asset_id == "img-alpha")
    assert image.size == len((tmp_path / "project" / image.relative_path).read_bytes())
    assert len(image.sha256) == 64


def test_indexes_standard_fairygui_assets_package_layout(tmp_path: Path) -> None:
    root = tmp_path / "project"
    package = root / "assets" / "MyVillage"
    (package / "Img").mkdir(parents=True)
    (package / "Panel").mkdir()
    (root / "isekaiUI.fairy").write_text("{}", "utf-8")
    (package / "package.xml").write_text(
        "<packageDescription id='mrz8gz9s'><resources>"
        "<image id='frrz1b' name='Bg.png' path='/Img/'/>"
        "</resources></packageDescription>",
        "utf-8",
    )
    (package / "Img" / "Bg.png").write_bytes(png_bytes(tmp_path))
    (package / "Panel" / "RankUp.xml").write_text("<component/>", "utf-8")

    version = index_uploaded_project(root, "figma2fgui.zip")

    assert [(item.name, item.id) for item in version.packages] == [("MyVillage", "mrz8gz9s")]
    panel = next(item for item in version.files if item.relative_path.endswith("RankUp.xml"))
    image = next(item for item in version.files if item.relative_path.endswith("Bg.png"))
    assert panel.package_name == "MyVillage"
    assert image.package_name == "MyVillage"
    assert image.package_id == "mrz8gz9s"
    assert image.asset_id == "frrz1b"


def test_creates_deterministic_thumbnail_outside_the_source_manifest(tmp_path: Path) -> None:
    root = write_project(tmp_path / "project", png_bytes(tmp_path))
    version = index_uploaded_project(root, "upload.zip")
    image = next(item for item in version.files if item.asset_id == "img-alpha")

    assert image.width == 1024
    assert image.height == 256
    assert image.thumbnail_relative_path is not None
    assert image.thumbnail_sha256 is not None
    assert len(image.thumbnail_sha256) == 64
    thumbnail = root / image.thumbnail_relative_path
    assert thumbnail.is_file()
    with Image.open(thumbnail) as result:
        assert result.format == "WEBP"
        assert result.size == (512, 128)
    assert image.thumbnail_relative_path not in {item.relative_path for item in version.files}


def test_thumbnail_indexing_safely_skips_pillow_bomb_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3)

    version = index_uploaded_project(write_project(tmp_path / "project", png_bytes(tmp_path)), "upload.zip")

    image = next(item for item in version.files if item.asset_id == "img-alpha")
    assert image.thumbnail_relative_path is None
    assert image.thumbnail_sha256 is None


def test_fingerprint_is_content_based_and_ignores_generated_and_temporary_files(tmp_path: Path) -> None:
    root = write_project(tmp_path / "project", png_bytes(tmp_path))
    first = index_uploaded_project(root, "first.zip")
    (root / ".figma-to-fgui" / "state.json").parent.mkdir()
    (root / ".figma-to-fgui" / "state.json").write_text("ignored", "utf-8")
    (root / "Alpha" / "scratch.tmp").write_text("ignored", "utf-8")
    second = index_uploaded_project(root, "second.zip")
    (root / "readme.txt").write_text("changed", "utf-8")
    third = index_uploaded_project(root, "third.zip")

    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != third.fingerprint
    assert first.project_id != second.project_id


def test_fingerprint_has_unambiguous_file_boundaries(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "a").write_bytes(b"one")
    (first_root / "b").write_bytes(b"two")
    (second_root / "a").write_bytes(b"oneb\0two")

    first = index_uploaded_project(first_root, "first.zip")
    second = index_uploaded_project(second_root, "second.zip")

    assert first.fingerprint != second.fingerprint
