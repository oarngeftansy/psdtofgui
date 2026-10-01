# Universal UIR v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, engine-neutral UIR v1 contract and compiler for normalized Figma selections, including auditable component-mapping decisions and a minimal Village Ascend golden fixture.

**Architecture:** Add a focused `uir_models.py` contract, a pure `uir_compile.py` compiler, and a separate `uir_validate.py` integrity/canonicalization boundary. The compiler consumes existing `NormalizedNode`, optional selection assets, and the already migrated component catalog; it does not generate FairyGUI XML. FairyGUI-specific IDs remain outside UIR Core and only appear in mapping evidence passed to later Project Binding work.

**Tech Stack:** Python 3.11+, Pydantic 2, hashlib/json standard library, pytest 8, Ruff, mypy strict.

## Global Constraints

- UIR Core must contain no FairyGUI-specific fields such as `packageId`, `componentId`, `src`, `pkg`, `controller`, `gearDisplay`, or `gearFrame`.
- The same normalized input, source revision, mapping catalog version, and compiler version must produce byte-identical canonical UIR JSON.
- `source.name` and source node identity are immutable facts; semantic naming never overwrites them.
- Mapping states are `verified | missing | conflict`; missing maps to explicit `rasterFallback`, while conflict remains unresolved and blocks downstream generation.
- Legacy IDs from installed skills are hints only and must not enter UIR Core.
- Unknown fields, dangling node references, duplicate child ownership, invalid root references, and unresolved conflict decisions fail validation.
- No Figma access token, local absolute path, raw image bytes, or private service exception may be serialized into UIR.
- This batch does not alter FairyGUI XML generation, plugin confirmation UI, or Unity output.

## File Structure

- Create `src/figma_to_fgui/uir_models.py`: immutable UIR v1 Pydantic contract and enums only.
- Create `src/figma_to_fgui/uir_compile.py`: pure normalized-tree and mapping-candidate compiler.
- Create `src/figma_to_fgui/uir_validate.py`: cross-reference validation, canonical JSON, and digest generation.
- Modify `src/figma_to_fgui/cli.py`: add a `build-uir` command using the public compiler/validator interfaces.
- Create `tests/unit/test_uir_models.py`: schema strictness and engine-neutral contract tests.
- Create `tests/unit/test_uir_compile.py`: deterministic node/layout/text/component/asset compilation tests.
- Create `tests/unit/test_uir_validate.py`: reference integrity, conflict gate, canonical bytes, and digest tests.
- Create `tests/fixtures/uir/village-ascend-normalized.json`: minimal sanitized Village Ascend source fixture.
- Create `tests/fixtures/uir/common-project/assets/Common/package.xml`: minimal real project index used to verify the common-button candidate.
- Create `tests/golden/expected/village-ascend.uir.json`: canonical expected UIR.
- Create `tests/golden/test_village_ascend_uir.py`: byte-for-byte golden regression.

---

### Task 1: Define the strict engine-neutral UIR v1 contract

**Files:**
- Create: `src/figma_to_fgui/uir_models.py`
- Create: `tests/unit/test_uir_models.py`

**Interfaces:**
- Consumes: `FrozenModel`, `Bounds`, and `Diagnostic` from `figma_to_fgui.models`.
- Produces: `UIRDocument`, `UIRSource`, `UIRNode`, `UIRGeometry`, `UIRSemantic`, `UIRConversion`, `UIRComponentInstance`, `UIRComponentDefinition`, `UIRAsset`, `UIRMappingDecision`, `SemanticStatus`, `ConversionMode`, and `MappingStatus`.

- [ ] **Step 1: Write failing strict-schema and engine-neutrality tests**

```python
import json

import pytest
from pydantic import ValidationError

from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    SemanticStatus,
    UIRDocument,
)


def minimal_document() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "documentId": "uir_test",
        "compilerVersion": "uir-v1",
        "source": {
            "kind": "figma",
            "revision": "a" * 64,
            "selectionId": "selection_test",
        },
        "roots": ["node:root"],
        "nodes": {
            "node:root": {
                "id": "node:root",
                "source": {
                    "nodeId": "root",
                    "type": "FRAME",
                    "name": "村庄升阶",
                    "fingerprint": "b" * 64,
                },
                "semantic": {
                    "name": "villageAscend",
                    "role": "panel",
                    "status": "confirmed",
                },
                "children": [],
                "zIndex": 0,
                "geometry": {
                    "resolvedBounds": {"x": 0, "y": 0, "width": 1080, "height": 1923},
                    "rotation": 0,
                    "opacity": 1,
                },
                "layout": {},
                "visual": {},
                "interactions": [],
                "conversion": {"mode": "native", "reasons": []},
            }
        },
        "componentDefinitions": {},
        "assets": {},
        "mappingDecisions": {},
        "diagnostics": [],
    }


def test_uir_v1_rejects_unknown_fields() -> None:
    payload = minimal_document()
    payload["packageId"] = "forbidden"
    with pytest.raises(ValidationError):
        UIRDocument.model_validate(payload)


def test_uir_schema_has_only_declared_status_values() -> None:
    assert set(SemanticStatus) == {"candidate", "confirmed", "rejected", "fallback"}
    assert set(ConversionMode) == {
        "native", "componentReference", "existingResource", "rasterFallback", "unsupported"
    }
    assert set(MappingStatus) == {"verified", "missing", "conflict"}


def test_serialized_core_contains_no_fairygui_target_fields() -> None:
    encoded = json.dumps(UIRDocument.model_validate(minimal_document()).model_dump(mode="json"))
    for forbidden in ("packageId", "componentId", "gearDisplay", "gearFrame", "controller"):
        assert forbidden not in encoded
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_models.py -q --basetemp=.pytest-uir-models-red`

Expected: collection fails with `ModuleNotFoundError: No module named 'figma_to_fgui.uir_models'`.

- [ ] **Step 3: Implement the minimal immutable models**

Implement strict `FrozenModel` subclasses with aliases matching the JSON contract:

```python
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from figma_to_fgui.models import Bounds, Diagnostic, FrozenModel


class SemanticStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FALLBACK = "fallback"


class ConversionMode(StrEnum):
    NATIVE = "native"
    COMPONENT_REFERENCE = "componentReference"
    EXISTING_RESOURCE = "existingResource"
    RASTER_FALLBACK = "rasterFallback"
    UNSUPPORTED = "unsupported"


class MappingStatus(StrEnum):
    VERIFIED = "verified"
    MISSING = "missing"
    CONFLICT = "conflict"


class UIRNodeSource(FrozenModel):
    node_id: str = Field(alias="nodeId", min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(max_length=256)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class UIRGeometry(FrozenModel):
    resolved_bounds: Bounds = Field(alias="resolvedBounds")
    local_transform: tuple[float, float, float, float, float, float] | None = Field(
        default=None, alias="localTransform"
    )
    rotation: float = 0
    opacity: float = Field(default=1, ge=0, le=1)


class UIRSemantic(FrozenModel):
    name: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=64)
    status: SemanticStatus = SemanticStatus.CANDIDATE
    decision_ref: str | None = Field(default=None, alias="decisionRef")


class UIRConversion(FrozenModel):
    mode: ConversionMode
    reasons: tuple[str, ...] = ()
    asset_ref: str | None = Field(default=None, alias="assetRef")


class UIRComponentInstance(FrozenModel):
    definition_ref: str | None = Field(default=None, alias="definitionRef")
    variant_properties: dict[str, str] = Field(default_factory=dict, alias="variantProperties")
    overrides: dict[str, Any] = Field(default_factory=dict)


class UIRNode(FrozenModel):
    id: str
    source: UIRNodeSource
    semantic: UIRSemantic
    parent_id: str | None = Field(default=None, alias="parentId")
    children: tuple[str, ...] = ()
    z_index: int = Field(alias="zIndex", ge=0)
    geometry: UIRGeometry
    layout: dict[str, Any] = Field(default_factory=dict)
    visual: dict[str, Any] = Field(default_factory=dict)
    text: dict[str, Any] | None = None
    component: UIRComponentInstance | None = None
    interactions: tuple[dict[str, Any], ...] = ()
    conversion: UIRConversion
```

Complete the file with strict models for document source, component definition, asset, mapping decision, and `UIRDocument`. Use `Literal[1]` for `schemaVersion`; constrain all SHA-256 strings; use tuples for ordered collections; use `dict` only where v1 intentionally preserves source facts.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_models.py -q --basetemp=.pytest-uir-models-green
.\.venv\Scripts\ruff.exe check src\figma_to_fgui\uir_models.py tests\unit\test_uir_models.py
.\.venv\Scripts\mypy.exe src\figma_to_fgui\uir_models.py
```

Expected: all tests pass; Ruff and mypy report no issues.

- [ ] **Step 5: Commit Task 1**

```powershell
git add src/figma_to_fgui/uir_models.py tests/unit/test_uir_models.py
git commit -m "feat: define universal UIR v1 contract"
```

---

### Task 2: Compile normalized source facts deterministically

**Files:**
- Create: `src/figma_to_fgui/uir_compile.py`
- Create: `tests/unit/test_uir_compile.py`

**Interfaces:**
- Consumes: `tuple[NormalizedNode, ...]`, `source_revision: str`, `selection_id: str`, and models from Task 1.
- Produces: `compile_uir(roots, *, source_revision, selection_id, compiler_version="uir-v1", assets=(), mapping_catalog=None) -> UIRDocument`.

- [ ] **Step 1: Write failing node-order, geometry, text, and determinism tests**

```python
from figma_to_fgui.models import Bounds, NormalizedNode
from figma_to_fgui.uir_compile import compile_uir


def sample_roots() -> tuple[NormalizedNode, ...]:
    return (
        NormalizedNode(
            id="frame",
            name="村庄升阶",
            type="FRAME",
            bounds=Bounds(x=100, y=200, width=1080, height=1923),
            children=(
                NormalizedNode(
                    id="title",
                    name="标题",
                    type="TEXT",
                    bounds=Bounds(x=130, y=233, width=239, height=40),
                    text="Village Ascend",
                    source_order=0,
                    raw_style={"fontSize": 36, "textAlignHorizontal": "LEFT"},
                ),
            ),
        ),
    )


def test_compile_preserves_order_source_facts_and_resolved_geometry() -> None:
    document = compile_uir(
        sample_roots(), source_revision="a" * 64, selection_id="selection_village"
    )
    root = document.nodes[document.roots[0]]
    child = document.nodes[root.children[0]]
    assert root.source.name == "村庄升阶"
    assert child.source.name == "标题"
    assert child.geometry.resolved_bounds == Bounds(x=30, y=33, width=239, height=40)
    assert child.text == {
        "content": "Village Ascend",
        "style": {"fontSize": 36, "textAlignHorizontal": "LEFT"},
    }


def test_compile_is_deterministic_for_the_same_inputs() -> None:
    first = compile_uir(sample_roots(), source_revision="a" * 64, selection_id="same")
    second = compile_uir(sample_roots(), source_revision="a" * 64, selection_id="same")
    assert first == second
    assert first.document_id == second.document_id
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_compile.py -q --basetemp=.pytest-uir-compile-red`

Expected: collection fails because `figma_to_fgui.uir_compile` does not exist.

- [ ] **Step 3: Implement stable IDs, fingerprints, local bounds, and source copying**

Use canonical JSON for stable hashes and never include runtime timestamps:

```python
def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}:{hashlib.sha256(_canonical(value)).hexdigest()[:24]}"


def _local_bounds(node: NormalizedNode, parent: NormalizedNode | None) -> Bounds:
    if parent is None:
        return Bounds(x=0, y=0, width=node.bounds.width, height=node.bounds.height)
    return Bounds(
        x=node.bounds.x - parent.bounds.x,
        y=node.bounds.y - parent.bounds.y,
        width=node.bounds.width,
        height=node.bounds.height,
    )
```

Compile children in `NormalizedNode.children` order, use `source_order` only as preserved evidence, copy text and source style without target translation, and set conservative conversion defaults: text/frame/instance as `native`, direct resource-backed unsupported visual subtrees as `rasterFallback` only when `properties["export_strategy"] == "composite_png"`.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_compile.py -q --basetemp=.pytest-uir-compile-green
.\.venv\Scripts\ruff.exe check src\figma_to_fgui\uir_compile.py tests\unit\test_uir_compile.py
.\.venv\Scripts\mypy.exe src\figma_to_fgui\uir_compile.py
```

Expected: all commands succeed.

- [ ] **Step 5: Commit Task 2**

```powershell
git add src/figma_to_fgui/uir_compile.py tests/unit/test_uir_compile.py
git commit -m "feat: compile normalized Figma facts to UIR"
```

---

### Task 3: Add auditable component-mapping decisions without target leakage

**Files:**
- Modify: `src/figma_to_fgui/uir_compile.py`
- Modify: `tests/unit/test_uir_compile.py`
- Read only: `src/figma_to_fgui/component_mapping.py`
- Read only: `rules/default/component-mapping-candidates.json`

**Interfaces:**
- Consumes: `ComponentMappingCatalog | None` through `compile_uir(..., mapping_catalog=...)`.
- Produces: `UIRDocument.mapping_decisions`, `UIRNode.semantic.decision_ref`, and conversion modes `componentReference`, `rasterFallback`, or `unsupported`.

- [ ] **Step 1: Write failing verified, missing, conflict, and no-ID-leak tests**

```python
def test_verified_candidate_creates_engine_neutral_component_decision() -> None:
    catalog = catalog_with("通用一级按钮", status="verified")
    document = compile_uir(
        instance_roots("通用一级按钮"),
        source_revision="a" * 64,
        selection_id="selection",
        mapping_catalog=catalog,
    )
    node = next(item for item in document.nodes.values() if item.source.type == "INSTANCE")
    decision = document.mapping_decisions[node.semantic.decision_ref]
    assert decision.status == "verified"
    assert decision.candidate_key == "common_primary_button"
    assert node.conversion.mode == "componentReference"
    encoded = document.model_dump_json(by_alias=True)
    assert "qil5i1mk" not in encoded
    assert "v27f1nupomj" not in encoded


def test_missing_candidate_explicitly_falls_back_but_conflict_stays_blocking() -> None:
    missing = compile_with_status("missing")
    conflict = compile_with_status("conflict")
    assert instance_node(missing).conversion.mode == "rasterFallback"
    assert instance_node(conflict).conversion.mode == "unsupported"
    assert decision_for(conflict).status == "conflict"
```

The test helper constructs real `ComponentMappingCatalog` objects; do not mock catalog behavior.

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_compile.py -q --basetemp=.pytest-uir-mapping-red`

Expected: assertions fail because mapping decisions are absent.

- [ ] **Step 3: Implement exact alias matching and explicit decisions**

Add a normalized lookup that only matches declared `figma.names` and `figma.node_ids`; do not add fuzzy matching:

```python
def _mapping_for(node: NormalizedNode, catalog: ComponentMappingCatalog) -> ComponentMapping | None:
    matches = [
        item
        for item in catalog.components
        if node.id in item.figma.node_ids or node.name in item.figma.names
    ]
    return matches[0] if len(matches) == 1 else None
```

Serialize only candidate key, status, evidence, confidence, rule source, and human decision. Never serialize `legacy_hint` or `resolved`. A verified mapping sets `componentReference`; missing sets `rasterFallback` with reason `component_mapping_missing`; conflict sets `unsupported` with reason `component_mapping_conflict`.

Reject an unvalidated catalog entry whose status is still `candidate`; callers must run `validate_mapping_catalog` against the current project first. This prevents a Skill seed from becoming a generation decision without project evidence.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_compile.py tests\unit\test_component_mapping.py -q --basetemp=.pytest-uir-mapping-green
.\.venv\Scripts\ruff.exe check src\figma_to_fgui\uir_compile.py tests\unit\test_uir_compile.py
.\.venv\Scripts\mypy.exe src\figma_to_fgui\uir_compile.py
```

Expected: all commands succeed.

- [ ] **Step 5: Commit Task 3**

```powershell
git add src/figma_to_fgui/uir_compile.py tests/unit/test_uir_compile.py
git commit -m "feat: record UIR component mapping decisions"
```

---

### Task 4: Validate references and produce canonical UIR bytes

**Files:**
- Create: `src/figma_to_fgui/uir_validate.py`
- Create: `tests/unit/test_uir_validate.py`

**Interfaces:**
- Consumes: `UIRDocument`.
- Produces: `validate_uir(document: UIRDocument) -> tuple[Diagnostic, ...]`, `canonical_uir_bytes(document: UIRDocument) -> bytes`, and `uir_sha256(document: UIRDocument) -> str`.

- [ ] **Step 1: Write failing cross-reference, conflict-gate, and canonicalization tests**

```python
from figma_to_fgui.uir_validate import canonical_uir_bytes, uir_sha256, validate_uir


def test_validation_rejects_dangling_root_child_and_decision_refs() -> None:
    document = document_with_dangling_refs()
    assert {item.code for item in validate_uir(document)} == {
        "uir.root_missing",
        "uir.child_missing",
        "uir.decision_missing",
    }


def test_validation_blocks_unresolved_mapping_conflicts() -> None:
    diagnostics = validate_uir(document_with_mapping_status("conflict"))
    assert any(item.code == "uir.mapping_conflict" and item.severity == "ERROR" for item in diagnostics)


def test_canonical_bytes_and_digest_are_stable() -> None:
    document = valid_document()
    first = canonical_uir_bytes(document)
    second = canonical_uir_bytes(document)
    assert first == second
    assert first.endswith(b"\n")
    assert uir_sha256(document) == hashlib.sha256(first).hexdigest()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_validate.py -q --basetemp=.pytest-uir-validate-red`

Expected: collection fails because `uir_validate` does not exist.

- [ ] **Step 3: Implement deterministic validation and serialization**

Validation must report all safe errors in one pass. Check roots, child ownership, parent symmetry, decision refs, asset refs, component definition refs, conversion requirements, and unresolved conflicts. Canonical output uses aliases and excludes no declared field:

```python
def canonical_uir_bytes(document: UIRDocument) -> bytes:
    payload = document.model_dump(mode="json", by_alias=True)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def uir_sha256(document: UIRDocument) -> str:
    return hashlib.sha256(canonical_uir_bytes(document)).hexdigest()
```

- [ ] **Step 4: Run focused tests and static checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_validate.py -q --basetemp=.pytest-uir-validate-green
.\.venv\Scripts\ruff.exe check src\figma_to_fgui\uir_validate.py tests\unit\test_uir_validate.py
.\.venv\Scripts\mypy.exe src\figma_to_fgui\uir_validate.py
```

Expected: all commands succeed.

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/figma_to_fgui/uir_validate.py tests/unit/test_uir_validate.py
git commit -m "feat: validate and canonicalize UIR documents"
```

---

### Task 5: Expose deterministic UIR construction through the CLI

**Files:**
- Modify: `src/figma_to_fgui/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: normalized Figma JSON, source revision, selection ID, optional component catalog.
- Produces: `fgui-tool build-uir SOURCE OUTPUT --source-revision SHA --selection-id ID [--mapping-catalog PATH]`.

- [ ] **Step 1: Write a failing CLI contract test**

```python
def test_build_uir_writes_canonical_valid_document(tmp_path: Path) -> None:
    source = Path("tests/fixtures/figma/simple-frame.json")
    output = tmp_path / "simple.uir.json"
    result = runner.invoke(
        app,
        [
            "build-uir",
            str(source),
            str(output),
            "--source-revision",
            "a" * 64,
            "--selection-id",
            "selection_simple",
        ],
    )
    assert result.exit_code == 0
    assert output.read_bytes().endswith(b"\n")
    document = UIRDocument.model_validate_json(output.read_text("utf-8"))
    assert validate_uir(document) == ()
```

- [ ] **Step 2: Run the CLI test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\test_cli.py::test_build_uir_writes_canonical_valid_document -q --basetemp=.pytest-uir-cli-red`

Expected: Typer exits with code 2 because `build-uir` is unknown.

- [ ] **Step 3: Implement the CLI command**

```python
@app.command("build-uir")
def build_uir_command(
    source: Path,
    output: Path,
    source_revision: str,
    selection_id: str,
    mapping_catalog: Path | None = None,
) -> None:
    roots, normalize_diagnostics = normalize_document(json.loads(source.read_text("utf-8")))
    catalog = None if mapping_catalog is None else load_mapping_catalog(mapping_catalog)
    document = compile_uir(
        roots,
        source_revision=source_revision,
        selection_id=selection_id,
        mapping_catalog=catalog,
    )
    diagnostics = (*normalize_diagnostics, *validate_uir(document))
    if any(item.severity == Severity.ERROR for item in diagnostics):
        raise typer.Exit(code=2)
    output.write_bytes(canonical_uir_bytes(document))
```

Validate `source_revision` against `^[0-9a-f]{64}$` before compilation and return a safe Typer parameter error for malformed input.

- [ ] **Step 4: Run CLI and related tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_cli.py tests\unit\test_uir_models.py tests\unit\test_uir_compile.py tests\unit\test_uir_validate.py -q --basetemp=.pytest-uir-cli-green
.\.venv\Scripts\ruff.exe check src\figma_to_fgui\cli.py tests\unit\test_cli.py
.\.venv\Scripts\mypy.exe src\figma_to_fgui\cli.py
```

Expected: all commands succeed.

- [ ] **Step 5: Commit Task 5**

```powershell
git add src/figma_to_fgui/cli.py tests/unit/test_cli.py
git commit -m "feat: add deterministic UIR build command"
```

---

### Task 6: Add the minimal Village Ascend golden UIR fixture

**Files:**
- Create: `tests/fixtures/uir/village-ascend-normalized.json`
- Create: `tests/fixtures/uir/common-project/assets/Common/package.xml`
- Create: `tests/golden/expected/village-ascend.uir.json`
- Create: `tests/golden/test_village_ascend_uir.py`

**Interfaces:**
- Consumes: public `normalize_document`, `compile_uir`, `load_mapping_catalog`, `canonical_uir_bytes`, and `validate_uir` APIs.
- Produces: a sanitized repeatable fixture covering panel geometry, editable text, a verified common button candidate, and an explicit raster fallback asset.

- [ ] **Step 1: Create the sanitized source fixture**

Create a JSON document with one root `FRAME` named `村庄升阶`, bounds `1080×1923`, and these ordered direct children:

1. `TEXT` named `Village Ascend`, bounds `(109,33,239,40)`, characters `Village Ascend`, font size `36`, left aligned.
2. `TEXT` named `txt_rank_before`, bounds `(35,134,408,40)`, characters `Decrepit Village·1`, font size `52`, centered.
3. `INSTANCE` named `通用一级按钮`, bounds `(278,1766,776,132)`, component properties `type=正常`, `Size=L`.
4. `GROUP` named `village_background`, bounds `(0,300,1080,1383)`, properties `export_strategy=composite_png` and `raster_reasons=[visual_effect]`, with a synthetic `resourceRefs` entry containing only a logical asset ID and MIME type.

Use sanitized node IDs `village-root`, `title`, `rank-before`, `primary-button`, and `background`; include no local paths or raw asset bytes.

Create the minimal FairyGUI manifest with the real candidate name but fixture-only IDs:

```xml
<packageDescription id="common01">
  <resources>
    <component id="primary1" name="Common_Btn_Primary.xml" path="/Core/Button/"/>
  </resources>
</packageDescription>
```

- [ ] **Step 2: Write the failing golden test**

```python
def test_village_ascend_compiles_to_the_reviewed_golden_uir() -> None:
    raw = json.loads(Path("tests/fixtures/uir/village-ascend-normalized.json").read_text("utf-8"))
    roots, diagnostics = normalize_document(raw)
    assert diagnostics == ()
    candidates = load_mapping_catalog(Path("rules/default/component-mapping-candidates.json"))
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
    assert canonical_uir_bytes(document) == Path(
        "tests/golden/expected/village-ascend.uir.json"
    ).read_bytes()
```

- [ ] **Step 3: Run the golden test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\golden\test_village_ascend_uir.py -q --basetemp=.pytest-uir-golden-red`

Expected: failure because the expected golden file does not exist.

- [ ] **Step 4: Generate and review the expected UIR once**

Use the public compiler and canonical serializer to write `tests/golden/expected/village-ascend.uir.json`. Manually verify before accepting it:

- roots and children match source order;
- local coordinates are relative to the root;
- source text and style remain unchanged;
- the primary button has a mapping decision and no FairyGUI IDs;
- background conversion is explicit `rasterFallback`;
- no absolute local path, access token, or raw image data appears.

- [ ] **Step 5: Run the golden test twice to prove repeatability**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\golden\test_village_ascend_uir.py -q --basetemp=.pytest-uir-golden-green1
.\.venv\Scripts\python.exe -m pytest tests\golden\test_village_ascend_uir.py -q --basetemp=.pytest-uir-golden-green2
```

Expected: both runs pass without modifying the golden file.

- [ ] **Step 6: Commit Task 6**

```powershell
git add tests/fixtures/uir/village-ascend-normalized.json tests/fixtures/uir/common-project/assets/Common/package.xml tests/golden/expected/village-ascend.uir.json tests/golden/test_village_ascend_uir.py
git commit -m "test: add Village Ascend UIR golden fixture"
```

---

### Task 7: Run the first-batch quality gate and document the next boundary

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: all public UIR APIs and the `build-uir` CLI command.
- Produces: a short documented developer workflow and verified first-batch evidence.

- [ ] **Step 1: Add the exact UIR developer command to README**

Document this command and explain that the output is an intermediate contract, not a FairyGUI project:

```powershell
fgui-tool build-uir tests/fixtures/figma/simple-frame.json out/simple.uir.json `
  --source-revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa `
  --selection-id selection_simple `
  --mapping-catalog rules/default/component-mapping-candidates.json
```

State that FairyGUI Profile, Project Binding confirmation UI, and UIR→FGUI generation are the next implementation batch.

- [ ] **Step 2: Run all UIR-focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_uir_models.py tests\unit\test_uir_compile.py tests\unit\test_uir_validate.py tests\unit\test_cli.py tests\golden\test_village_ascend_uir.py -q --basetemp=.pytest-uir-focused-final
```

Expected: zero failures.

- [ ] **Step 3: Run the full quality gate**

Run each command separately so a later command cannot hide an earlier failure:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-uir-full-final
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
```

Expected: all tests pass except explicitly documented platform-permission skips; Ruff and mypy report no issues. If the four pre-existing `tests/integration/test_ai_semantic_package.py` scenarios still fail, record them separately and do not claim the complete suite passes.

- [ ] **Step 4: Verify the production CLI artifact**

Install the local project in the existing virtual environment without changing dependency versions, then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\fgui-tool.exe build-uir tests/fixtures/figma/simple-frame.json .test-tmp/simple.uir.json `
  --source-revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa `
  --selection-id selection_simple
```

Expected: exit code 0 and `.test-tmp/simple.uir.json` parses as `UIRDocument` with no validation errors.

- [ ] **Step 5: Commit Task 7**

```powershell
git add README.md
git commit -m "docs: describe the UIR v1 build workflow"
```

## Self-Review Results

- **Spec coverage:** Tasks 1–4 cover the v1 schema, normalized source facts, mapping decisions, integrity, and determinism. Task 6 covers the minimal Village Ascend golden fixture. FGUI Profile, Project Binding UI, and XML generation are intentionally excluded exactly as specified by the first-batch boundary.
- **Placeholder scan:** No implementation placeholder remains; every task names exact files, interfaces, commands, expected failure, implementation behavior, and commit boundary.
- **Type consistency:** `compile_uir`, `validate_uir`, `canonical_uir_bytes`, and `uir_sha256` keep the same signatures across all tasks. JSON aliases consistently use camelCase while Python fields use snake_case.
