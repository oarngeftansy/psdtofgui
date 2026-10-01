from pathlib import Path

from figma_to_fgui.project_index import ProjectIndex
from figma_to_fgui.validate import has_errors, validate_staging


def test_invalid_xml_and_missing_text_autosize_are_errors(tmp_path: Path) -> None:
    panel = tmp_path / "Sample/Panel/Bad.xml"
    panel.parent.mkdir(parents=True)
    panel.write_text('<component><displayList><text text="x"/></displayList></component>', "utf-8")
    diagnostics = validate_staging(tmp_path, ProjectIndex())
    assert has_errors(diagnostics)
    assert {item.code for item in diagnostics} == {"text.fixed-size"}


def test_well_formed_empty_component_has_no_errors(tmp_path: Path) -> None:
    panel = tmp_path / "Sample/Panel/Good.xml"
    panel.parent.mkdir(parents=True)
    panel.write_text("<component><displayList/></component>", "utf-8")
    assert not has_errors(validate_staging(tmp_path, ProjectIndex()))
