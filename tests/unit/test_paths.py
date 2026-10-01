import pytest

from figma_to_fgui.paths import safe_relative_path, validate_project_name


@pytest.mark.parametrize("value", ["../package.xml", "/tmp/x", "C:/x", "Panel/../../x"])
def test_rejects_paths_outside_project(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe relative path"):
        safe_relative_path(value)


def test_normalizes_windows_separators() -> None:
    assert safe_relative_path(r"Sample\Panel\Main.xml") == "Sample/Panel/Main.xml"


def test_project_name_validation_is_shared() -> None:
    assert validate_project_name("问卷-Quiz_1") == "问卷-Quiz_1"
    with pytest.raises(ValueError, match="project name"):
        validate_project_name("../Quiz")
