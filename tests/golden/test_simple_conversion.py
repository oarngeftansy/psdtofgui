from pathlib import Path

from figma_to_fgui.pipeline import ConversionRequest, convert


def test_simple_fixture_matches_golden_output(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    result = convert(
        ConversionRequest(
            figma_json=Path("tests/fixtures/figma/simple-frame.json"),
            project_root=Path("tests/fixtures/fgui"),
            package_name="Sample",
            staging_root=staging,
            classification_rules=Path("rules/default/classification.yaml"),
        )
    )
    actual = (staging / "Sample/Panel/Panel_Sample_Main.xml").read_bytes()
    expected = Path("tests/golden/expected/Panel_Sample_Main.xml").read_bytes()
    assert actual == expected.replace(b"\r\n", b"\n")
    assert result.applicable
