from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from typer.testing import CliRunner

from figma_to_fgui.cli import app

FIXTURE = Path("tests/fixtures/fgui-new-project")


def _invoke(tmp_path: Path, *, plan: Path | None = None):
    return CliRunner().invoke(
        app,
        [
            "build-fgui-project",
            str(plan or FIXTURE / "generic-plan-v2.json"),
            str(FIXTURE / "config.json"),
            str(FIXTURE / "assets"),
            str(tmp_path),
        ],
    )


def test_build_fgui_project_cli_emits_verified_zip(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "first")
    repeated = _invoke(tmp_path / "second")

    assert result.exit_code == 0, result.output
    assert repeated.exit_code == 0, repeated.output
    artifacts = list((tmp_path / "first").glob("*.zip"))
    assert len(artifacts) == 1
    repeated_artifact = next((tmp_path / "second").glob("*.zip"))
    assert artifacts[0].read_bytes() == repeated_artifact.read_bytes()
    with ZipFile(artifacts[0]) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        png_names = [name for name in names if name.endswith(".png")]
        assert len(png_names) == 1
        assert archive.read(png_names[0]).startswith(b"\x89PNG\r\n\x1a\n")
        component = next(name for name in names if name.endswith(".xml") and not name.endswith("package.xml"))
        assert b"<image " in archive.read(component)
    public_result = json.loads(result.stdout)
    assert public_result["project_name"] == "GenericWriterFixture"
    assert public_result["sha256"]
    assert "path" not in public_result


def test_build_fgui_project_cli_publishes_nothing_for_unbindable_plan(
    tmp_path: Path,
) -> None:
    source = json.loads((FIXTURE / "generic-plan-v2.json").read_text("utf-8"))
    source["bindable"] = False
    blocked = tmp_path / "blocked.json"
    blocked.write_text(json.dumps(source), "utf-8")

    result = _invoke(tmp_path / "out", plan=blocked)

    assert result.exit_code == 2
    output = tmp_path / "out"
    assert not output.exists() or list(output.glob("*.zip")) == []
    assert "Traceback" not in result.output
