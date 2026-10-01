import struct
from pathlib import Path

from figma_to_fgui.project_index import index_project


def _ttf_with_names(*names: tuple[int, str]) -> bytes:
    strings = b""
    records = []
    for name_id, value in names:
        encoded = value.encode("utf-16-be")
        records.append(struct.pack(">HHHHHH", 3, 1, 0x409, name_id, len(encoded), len(strings)))
        strings += encoded
    table = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records)) + b"".join(records) + strings
    offset = 12 + 16
    header = struct.pack(">IHHHH", 0x00010000, 1, 0, 0, 0)
    directory = struct.pack(">4sIII", b"name", 0, offset, len(table))
    return header + directory + table


def test_indexes_package_resources_without_mutating_snapshot() -> None:
    root = Path("tests/fixtures/fgui")
    before = (root / "Sample/package.xml").read_bytes()
    index = index_project(root)
    assert index.packages["Sample"] == "sample01"
    assert index.by_name["Bg_Main.png"].id == "img00001"
    assert index.by_name["Existing.xml"].kind == "component"
    assert (root / "Sample/package.xml").read_bytes() == before


def test_indexes_standard_fairygui_assets_package_layout(tmp_path: Path) -> None:
    package = tmp_path / "assets" / "Sample"
    package.mkdir(parents=True)
    (package / "package.xml").write_text(
        "<packageDescription id='sample'><resources/></packageDescription>", "utf-8"
    )

    index = index_project(tmp_path)

    assert index.packages == {"Sample": "sample"}
    assert index.package_roots == {"Sample": "assets/Sample"}


def test_indexes_valid_image_scale9grid_in_both_project_layouts(tmp_path: Path) -> None:
    legacy = tmp_path / "Legacy"
    standard = tmp_path / "assets" / "Standard"
    legacy.mkdir(parents=True)
    standard.mkdir(parents=True)
    (legacy / "package.xml").write_text(
        "<package id='legacy'><resources><image id='a' name='a.png' scale9grid='10,11,80,40'/></resources></package>",
        "utf-8",
    )
    (standard / "package.xml").write_text(
        "<packageDescription id='standard'><resources><image id='b' name='b.png' scale9grid='1,2,30,40'/></resources></packageDescription>",
        "utf-8",
    )

    index = index_project(tmp_path)

    assert index.by_name["a.png"].scale9grid is not None
    assert index.by_name["a.png"].scale9grid.model_dump() == {"x": 10, "y": 11, "width": 80, "height": 40}
    assert index.by_name["b.png"].scale9grid is not None


def test_ignores_invalid_image_scale9grid_without_losing_resource(tmp_path: Path) -> None:
    package = tmp_path / "Sample"
    package.mkdir()
    (package / "package.xml").write_text(
        "<package id='sample'><resources><image id='a' name='a.png' scale9grid='1,2,0,4'/></resources></package>",
        "utf-8",
    )

    resource = index_project(tmp_path).by_name["a.png"]

    assert resource.scale9grid is None


def test_indexes_font_aliases_from_ttf_metadata_for_cross_package_matching(tmp_path: Path) -> None:
    package = tmp_path / "assets" / "Base0"
    (package / "Font").mkdir(parents=True)
    (package / "Font" / "TextFont.ttf").write_bytes(
        _ttf_with_names(
            (1, "Core Sans E SW01"),
            (2, "55 Medium"),
            (4, "Core Sans E SW01 55 Medium"),
            (6, "CoreSansESW01-55Medium"),
        )
    )
    (package / "package.xml").write_text(
        "<packageDescription id='ub7gxzj7'><resources>"
        "<font id='kdwnp' name='TextFont.ttf' path='/Font/'/>"
        "</resources></packageDescription>",
        "utf-8",
    )

    index = index_project(tmp_path)

    matches = index.font_aliases["coresansesw0155medium"]
    assert len(matches) == 1
    assert matches[0].id == "kdwnp"
    assert matches[0].package_id == "ub7gxzj7"
