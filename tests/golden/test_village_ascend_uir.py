import json
from pathlib import Path

from figma_to_fgui.component_mapping import (
    load_mapping_catalog,
    validate_mapping_catalog,
)
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.project_index import index_project
from figma_to_fgui.uir_compile import compile_uir
from figma_to_fgui.uir_validate import canonical_uir_bytes, validate_uir


def test_village_ascend_compiles_to_the_reviewed_golden_uir() -> None:
    raw = json.loads(
        Path("tests/fixtures/uir/village-ascend-normalized.json").read_text("utf-8")
    )
    roots, diagnostics = normalize_document(raw)
    assert diagnostics == ()
    candidates = load_mapping_catalog(
        Path("rules/default/component-mapping-candidates.json")
    )
    catalog = validate_mapping_catalog(
        candidates, index_project(Path("tests/fixtures/uir/common-project"))
    )
    document = compile_uir(
        roots,
        source_revision="7" * 64,
        selection_id="selection_village_ascend",
        mapping_catalog=catalog,
    )
    assert validate_uir(document) == ()
    button = next(node for node in document.nodes.values() if node.source.type == "INSTANCE")
    background = next(node for node in document.nodes.values() if node.source.node_id == "background")
    assert button.conversion.mode == "componentReference"
    assert background.conversion.mode == "rasterFallback"
    assert background.conversion.asset_ref in document.assets
    assert canonical_uir_bytes(document) == Path(
        "tests/golden/expected/village-ascend.uir.json"
    ).read_bytes()
