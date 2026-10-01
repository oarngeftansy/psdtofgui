import json
from pathlib import Path

from typer.testing import CliRunner

from figma_to_fgui.cli import app
from figma_to_fgui.component_mapping import load_mapping_catalog, validate_mapping_catalog
from figma_to_fgui.project_index import index_project


def _write_package(root: Path, resources: str) -> None:
    package = root / "assets" / "Common"
    package.mkdir(parents=True)
    (package / "package.xml").write_text(
        f"<packageDescription id='actualpkg'><resources>{resources}</resources></packageDescription>",
        "utf-8",
    )


def _write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "sources": ["figma-to-fgui", "auto-panel"],
                "components": [
                    {
                        "key": "common_primary_button",
                        "figma": {
                            "names": ["通用一级按钮"],
                            "nodeIds": ["23:55"],
                        },
                        "fgui": {
                            "package": "Common",
                            "component": "Common_Btn_Primary",
                            "path": "Core/Button/Common_Btn_Primary.xml",
                        },
                        "legacyHint": {
                            "packageId": "qil5i1mk",
                            "componentId": "v27f1nupomj",
                        },
                        "properties": {},
                        "source": ["figma-to-fgui", "auto-panel"],
                        "status": "candidate",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )


def test_validates_candidate_against_uploaded_project_without_trusting_legacy_ids(
    tmp_path: Path,
) -> None:
    _write_package(
        tmp_path,
        "<component id='actualid' name='Common_Btn_Primary.xml' path='/Core/Button/'/>",
    )
    catalog_path = tmp_path / "mapping.json"
    _write_catalog(catalog_path)

    result = validate_mapping_catalog(load_mapping_catalog(catalog_path), index_project(tmp_path))

    assert result.components[0].status == "verified"
    assert result.components[0].resolved is not None
    assert result.components[0].resolved.package_id == "actualpkg"
    assert result.components[0].resolved.component_id == "actualid"
    assert result.components[0].resolved.relative_path == (
        "assets/Common/Core/Button/Common_Btn_Primary.xml"
    )


def test_keeps_missing_skill_mapping_unverified(tmp_path: Path) -> None:
    _write_package(tmp_path, "")
    catalog_path = tmp_path / "mapping.json"
    _write_catalog(catalog_path)

    result = validate_mapping_catalog(load_mapping_catalog(catalog_path), index_project(tmp_path))

    assert result.components[0].status == "missing"
    assert result.components[0].resolved is None
    assert result.components[0].reason == "component_not_found"


def test_rejects_same_named_non_component_as_a_mapping_target(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        "<image id='actualid' name='Common_Btn_Primary.xml' path='/Core/Button/'/>",
    )
    catalog_path = tmp_path / "mapping.json"
    _write_catalog(catalog_path)

    result = validate_mapping_catalog(load_mapping_catalog(catalog_path), index_project(tmp_path))

    assert result.components[0].status == "conflict"
    assert result.components[0].reason == "target_is_not_component"


def test_builtin_skill_catalog_contains_migrated_components_and_property_maps() -> None:
    catalog = load_mapping_catalog(
        Path("rules/default/component-mapping-candidates.json")
    )

    assert len(catalog.components) >= 18
    primary = next(item for item in catalog.components if item.key == "common_primary_button")
    assert primary.figma.names == ("通用一级按钮",)
    assert primary.figma.node_ids == ("23:55",)
    assert primary.status == "candidate"
    assert primary.properties["type"]["valueMap"]["置灰"] == {
        "type": "0",
        "isGray": "1",
    }
    assert set(primary.source) == {"figma-to-fgui", "auto-panel"}


def test_cli_writes_project_verified_mapping_report(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        "<component id='actualid' name='Common_Btn_Primary.xml' path='/Core/Button/'/>",
    )
    catalog_path = tmp_path / "mapping.json"
    _write_catalog(catalog_path)
    output = tmp_path / "verified.json"

    result = CliRunner().invoke(
        app,
        [
            "verify-component-mappings",
            str(tmp_path),
            str(output),
            "--catalog",
            str(catalog_path),
        ],
    )

    assert result.exit_code == 0
    report = json.loads(output.read_text("utf-8"))
    assert report["components"][0]["status"] == "verified"
    assert report["components"][0]["resolved"]["component_id"] == "actualid"
