from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from figma_to_fgui.api import create_app
from figma_to_fgui.fixed_fonts import FixedFontSpec, check_fixed_fonts


def _spec(content: bytes, *, family: str = "Project Font") -> FixedFontSpec:
    return FixedFontSpec(
        family=family,
        postscript_name=family.replace(" ", ""),
        source_filename="project.ttf",
        sha256=sha256(content).hexdigest(),
    )


def test_fixed_font_check_matches_content_hash_without_requiring_an_upload(tmp_path: Path) -> None:
    installed = tmp_path / "Windows Fonts"
    installed.mkdir()
    (installed / "renamed-font.ttf").write_bytes(b"fixed-font")

    result = check_fixed_fonts((_spec(b"fixed-font"),), (installed,))

    assert result[0].installed is True
    assert result[0].family == "Project Font"
    assert result[0].matched_filename == "renamed-font.ttf"


def test_fixed_font_check_reports_a_missing_registered_font(tmp_path: Path) -> None:
    result = check_fixed_fonts((_spec(b"missing"),), (tmp_path,))

    assert result[0].installed is False
    assert result[0].matched_filename is None


def test_local_app_bootstrap_and_font_status_are_available_without_figma(tmp_path: Path) -> None:
    font_root = tmp_path / "fonts"
    font_root.mkdir()
    (font_root / "project.ttf").write_bytes(b"fixed-font")
    token = b"local-token-with-at-least-32-bytes"
    app = create_app(
        data_dir=tmp_path / "data",
        fixtures_root=Path("tests/fixtures"),
        rules_path=Path("rules/default/classification.yaml"),
        plugin_access_token=token,
        local_app_access_token=token,
        fixed_font_specs=(_spec(b"fixed-font"),),
        fixed_font_search_roots=(font_root,),
    )
    client = TestClient(app)

    bootstrap = client.get("/v1/local/bootstrap")
    fonts = client.get(
        "/v1/hifi-sources/fonts",
        headers={"x-figma-plugin-token": token.decode("ascii")},
    )

    assert bootstrap.status_code == 200
    assert bootstrap.headers["cache-control"] == "no-store"
    assert bootstrap.json() == {"version": 1, "access_token": token.decode("ascii")}
    assert fonts.status_code == 200
    assert fonts.json()["fonts"][0]["installed"] is True


def test_local_bootstrap_is_absent_when_the_standalone_app_is_not_enabled(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"plugin-token-with-at-least-32-bytes",
        )
    )

    assert client.get("/v1/local/bootstrap").status_code == 404
