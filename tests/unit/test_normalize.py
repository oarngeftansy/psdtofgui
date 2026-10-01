import json
from pathlib import Path

from figma_to_fgui.normalize import normalize_document


def test_normalizes_children_without_rounding_geometry() -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, diagnostics = normalize_document(raw)
    assert roots[0].children[0].bounds.x == 100.2
    assert roots[0].children[0].text == "Hello"
    assert roots[0].children[0].source_order == 0
    assert diagnostics == ()
