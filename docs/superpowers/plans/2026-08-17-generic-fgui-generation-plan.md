# Generic FairyGUI Generation Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, project-neutral `UIR → FGUI Generation Plan` compiler with native primitive, resource, nine-slice, component-reference, mask, raster-fallback, and blocking-unsupported decisions.

**Architecture:** Add a strict plan contract, a pure capability analyzer, a pure compiler, and a separate validation/canonicalization boundary. The plan contains logical resource and component keys only; Project Binding and XML writing remain later subsystems. Generic fixtures prove behavior across names and dimensions, while Village Ascend remains only a regression fixture.

**Tech Stack:** Python 3.11+, Pydantic 2, hashlib/json standard library, pytest 8, Ruff, mypy strict, Typer.

## Global Constraints

- No plan field may contain a real FairyGUI `packageId`, `componentId`, `src`, or `pkg`.
- No compiler or analyzer branch may inspect a page name, the string `村庄升阶`, a golden node ID, or a golden dimension.
- The analyzer and compiler are pure: they do not read Figma, call AI, scan a FairyGUI project, or write XML.
- The same canonical UIR bytes, profile version, and rule version produce byte-identical canonical plan JSON.
- `native` requires a native PlanNode; `rasterFallback` requires a ResourcePlan; `unsupported` requires a blocking diagnostic and sets `bindable=false`.
- Rectangle and rounded-rectangle clips are native; simple image masks are native; complex masks rasterize the smallest safe subtree; broken masks are unsupported.
- Rasterized descendants are consumed by the raster subtree and must not also appear as native PlanNodes.
- Invalid nine-slice bounds never enter a native ResourcePlan.
- This batch does not implement Project Binding, FairyGUI XML writing, Controller, Gear, List, or complex Auto Layout semantics.
- The future user-supplied UIR mapping file is not guessed in this batch; it will enter through a versioned validation/import boundary after its real schema is available.

## File Structure

- Create `src/figma_to_fgui/fgui_plan_models.py`: immutable project-neutral plan contract and enums.
- Create `src/figma_to_fgui/fgui_capabilities.py`: pure per-node capability and mask classification.
- Create `src/figma_to_fgui/fgui_plan_compile.py`: deterministic tree, resource, text, component, and mask plan compiler.
- Create `src/figma_to_fgui/fgui_plan_validate.py`: reference/capability validation, canonical bytes, and digest.
- Modify `src/figma_to_fgui/uir_models.py`: add optional engine-neutral asset dimensions and nine-slice facts.
- Modify `src/figma_to_fgui/cli.py`: add `build-fgui-plan`.
- Create `tests/unit/test_fgui_plan_models.py`.
- Create `tests/unit/test_fgui_capabilities.py`.
- Create `tests/unit/test_fgui_plan_compile.py`.
- Create `tests/unit/test_fgui_plan_validate.py`.
- Modify `tests/unit/test_cli.py`.
- Create `tests/fixtures/fgui-plan/generic-primitives.uir.json`.
- Create `tests/fixtures/fgui-plan/generic-masks.uir.json`.
- Create `tests/golden/expected/generic-primitives.fgui-plan.json`.
- Create `tests/golden/test_generic_fgui_plan.py`.
- Modify `README.md`.

---

### Task 1: Define the strict project-neutral plan contract

**Files:**
- Create: `src/figma_to_fgui/fgui_plan_models.py`
- Create: `tests/unit/test_fgui_plan_models.py`

**Interfaces:**
- Consumes: `Bounds`, `Diagnostic`, and `FrozenModel` from `figma_to_fgui.models`.
- Produces: `FGUIPlanDocument`, `FGUIPlanNode`, `TransformPlan`, `TextPlan`, `ResourcePlan`, `ComponentReferencePlan`, `MaskPlan`, `CapabilityDecision`, `PlanNodeType`, `CapabilityStatus`, and `MaskMode`.

- [ ] **Step 1: Write failing schema, enum, and leakage tests**

```python
import json

import pytest
from pydantic import ValidationError

from figma_to_fgui.fgui_plan_models import (
    CapabilityStatus,
    FGUIPlanDocument,
    MaskMode,
    PlanNodeType,
)


def test_plan_enums_are_closed() -> None:
    assert set(CapabilityStatus) == {"native", "rasterFallback", "unsupported"}
    assert set(PlanNodeType) == {
        "container", "text", "richText", "image", "loader",
        "componentReference", "rasterSubtree",
    }
    assert set(MaskMode) == {"nativeClip", "nativeMask", "rasterSubtree"}


def test_plan_rejects_unknown_and_target_binding_fields(minimal_plan: dict[str, object]) -> None:
    minimal_plan["packageId"] = "real-id"
    with pytest.raises(ValidationError):
        FGUIPlanDocument.model_validate(minimal_plan)


def test_serialized_plan_contains_no_project_binding_fields(valid_plan: FGUIPlanDocument) -> None:
    encoded = json.dumps(valid_plan.model_dump(mode="json", by_alias=True))
    for forbidden in ("packageId", "componentId", '"src"', '"pkg"'):
        assert forbidden not in encoded
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_plan_models.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-plan-models-red`

Expected: collection fails with `ModuleNotFoundError: figma_to_fgui.fgui_plan_models`.

- [ ] **Step 3: Implement immutable plan models**

Use a `PlanModel(FrozenModel)` with `ConfigDict(populate_by_name=True)`. Define these exact fields:

```python
class CapabilityStatus(StrEnum):
    NATIVE = "native"
    RASTER_FALLBACK = "rasterFallback"
    UNSUPPORTED = "unsupported"


class PlanNodeType(StrEnum):
    CONTAINER = "container"
    TEXT = "text"
    RICH_TEXT = "richText"
    IMAGE = "image"
    LOADER = "loader"
    COMPONENT_REFERENCE = "componentReference"
    RASTER_SUBTREE = "rasterSubtree"


class MaskMode(StrEnum):
    NATIVE_CLIP = "nativeClip"
    NATIVE_MASK = "nativeMask"
    RASTER_SUBTREE = "rasterSubtree"


class TransformPlan(PlanModel):
    bounds: Bounds
    rotation: float = 0
    opacity: float = Field(default=1, ge=0, le=1)


class TextPlan(PlanModel):
    content: str
    font_candidates: tuple[str, ...] = Field(default=(), alias="fontCandidates")
    font_size: float | None = Field(default=None, alias="fontSize", gt=0)
    color: str | None = None
    horizontal_align: str | None = Field(default=None, alias="horizontalAlign")
    vertical_align: str | None = Field(default=None, alias="verticalAlign")
    style_facts: dict[str, object] = Field(default_factory=dict, alias="styleFacts")


class ResourcePlan(PlanModel):
    id: str
    source_asset_ref: str = Field(alias="sourceAssetRef")
    mime_type: str = Field(alias="mimeType")
    export_format: Literal["png", "jpg", "webp"] = Field(alias="exportFormat")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    nine_slice: tuple[int, int, int, int] | None = Field(default=None, alias="nineSlice")
    consumers: tuple[str, ...]
    reason: str | None = None


class ComponentReferencePlan(PlanModel):
    candidate_key: str = Field(alias="candidateKey")
    variant_properties: dict[str, str] = Field(default_factory=dict, alias="variantProperties")


class MaskPlan(PlanModel):
    id: str
    mode: MaskMode
    mask_node_ref: str = Field(alias="maskNodeRef")
    content_node_refs: tuple[str, ...] = Field(alias="contentNodeRefs")
    resource_ref: str | None = Field(default=None, alias="resourceRef")


class CapabilityDecision(PlanModel):
    id: str
    node_ref: str = Field(alias="nodeRef")
    status: CapabilityStatus
    rule_id: str = Field(alias="ruleId")
    rule_version: int = Field(alias="ruleVersion", ge=1)
    evidence: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    blocking: bool = False
```

`FGUIPlanNode` contains `id`, `uirNodeRef`, `parentId`, ordered `children`, `zIndex`, `type`, `transform`, optional `text`, `resourceRef`, `component`, `maskRef`, and `decisionRef`. `FGUIPlanDocument` contains literal schema version 1, IDs/versions/hashes, `bindable`, roots, node/resource/mask/decision dictionaries, and diagnostics.

- [ ] **Step 4: Run focused tests and static checks**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_plan_models.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-plan-models-green
..\..\.venv\Scripts\ruff.exe check src\figma_to_fgui\fgui_plan_models.py tests\unit\test_fgui_plan_models.py
..\..\.venv\Scripts\mypy.exe src\figma_to_fgui\fgui_plan_models.py
```

Expected: tests pass; Ruff and mypy report no issues.

- [ ] **Step 5: Commit Task 1**

```powershell
git add src/figma_to_fgui/fgui_plan_models.py tests/unit/test_fgui_plan_models.py
git commit -m "feat: define generic FairyGUI generation plan"
```

---

### Task 2: Classify generic native, fallback, and unsupported capabilities

**Files:**
- Create: `src/figma_to_fgui/fgui_capabilities.py`
- Create: `tests/unit/test_fgui_capabilities.py`

**Interfaces:**
- Consumes: `UIRDocument`, `UIRNode`, and `CapabilityDecision`.
- Produces: `analyze_capabilities(document: UIRDocument, *, rule_version: int = 1) -> dict[str, CapabilityDecision]` and `decision_for_node(node, document, rule_version) -> CapabilityDecision`.

- [ ] **Step 1: Write failing generic classification tests**

```python
@pytest.mark.parametrize(
    ("source_type", "expected_rule"),
    [("FRAME", "fgui.native.container"), ("GROUP", "fgui.native.container"),
     ("TEXT", "fgui.native.text"), ("RECTANGLE", "fgui.native.image")],
)
def test_generic_node_types_use_native_rules(source_type: str, expected_rule: str) -> None:
    node, document = document_with_node(source_type)
    decision = decision_for_node(node, document, 1)
    assert decision.status == "native"
    assert decision.rule_id == expected_rule


def test_verified_component_is_native_but_conflict_is_blocking() -> None:
    verified = decision_for_instance("verified")
    conflict = decision_for_instance("conflict")
    assert verified.rule_id == "fgui.native.component_reference"
    assert conflict.status == "unsupported"
    assert conflict.blocking is True


def test_explicit_raster_conversion_requires_an_asset() -> None:
    assert decision_for_raster(asset=True).status == "rasterFallback"
    missing = decision_for_raster(asset=False)
    assert missing.status == "unsupported"
    assert "raster_asset_missing" in missing.reasons


def test_rule_result_is_independent_of_name_and_dimensions() -> None:
    first = decision_for_generic_frame(name="Village", width=1080)
    second = decision_for_generic_frame(name="Inventory", width=750)
    assert (first.status, first.rule_id) == (second.status, second.rule_id)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_capabilities.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-cap-red`

Expected: collection fails because `fgui_capabilities` does not exist.

- [ ] **Step 3: Implement ordered capability rules**

Implement exact rule order so explicit conversion and component decisions win before source-type defaults:

```python
def decision_for_node(node: UIRNode, document: UIRDocument, rule_version: int = 1) -> CapabilityDecision:
    if node.conversion.mode == ConversionMode.UNSUPPORTED:
        return decision(node, UNSUPPORTED, "fgui.unsupported.source", rule_version, node.conversion.reasons, True)
    if node.conversion.mode == ConversionMode.RASTER_FALLBACK:
        if node.conversion.asset_ref not in document.assets:
            return decision(node, UNSUPPORTED, "fgui.unsupported.raster_asset", rule_version, ("raster_asset_missing",), True)
        return decision(node, RASTER_FALLBACK, "fgui.fallback.raster_subtree", rule_version, node.conversion.reasons)
    if node.conversion.mode == ConversionMode.COMPONENT_REFERENCE:
        return decision(node, NATIVE, "fgui.native.component_reference", rule_version)
    if node.source.type in {"FRAME", "GROUP", "COMPONENT", "SECTION"}:
        return decision(node, NATIVE, "fgui.native.container", rule_version)
    if node.source.type == "TEXT":
        return decision(node, NATIVE, "fgui.native.text", rule_version)
    if node.source.type in {"RECTANGLE", "ELLIPSE", "VECTOR", "IMAGE"} and node.conversion.asset_ref in document.assets:
        return decision(node, NATIVE, "fgui.native.image", rule_version)
    return decision(node, UNSUPPORTED, "fgui.unsupported.node_type", rule_version, ("node_type_unsupported",), True)
```

Stable decision IDs hash node ID, rule ID, and rule version. `analyze_capabilities` iterates `document.nodes` in sorted key order.

- [ ] **Step 4: Run focused tests and static checks**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_capabilities.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-cap-green
..\..\.venv\Scripts\ruff.exe check src\figma_to_fgui\fgui_capabilities.py tests\unit\test_fgui_capabilities.py
..\..\.venv\Scripts\mypy.exe src\figma_to_fgui\fgui_capabilities.py
```

- [ ] **Step 5: Commit Task 2**

```powershell
git add src/figma_to_fgui/fgui_capabilities.py tests/unit/test_fgui_capabilities.py
git commit -m "feat: analyze generic FairyGUI capabilities"
```

---

### Task 3: Compile containers, text, resources, nine-slice, and component references

**Files:**
- Modify: `src/figma_to_fgui/uir_models.py`
- Create: `src/figma_to_fgui/fgui_plan_compile.py`
- Create: `tests/unit/test_fgui_plan_compile.py`

**Interfaces:**
- Consumes: `UIRDocument` and `dict[str, CapabilityDecision]`.
- Produces: `compile_fgui_plan(document: UIRDocument, *, profile_version="fgui-6.1.4-v1", rule_version=1) -> FGUIPlanDocument`.

- [ ] **Step 1: Write failing primitive, text, resource, and component tests**

```python
def test_compile_preserves_tree_transform_and_text_facts() -> None:
    plan = compile_fgui_plan(generic_primitives_document())
    root = plan.nodes[plan.roots[0]]
    text = next(plan.nodes[item] for item in root.children if plan.nodes[item].type == "text")
    assert text.transform.bounds == Bounds(x=12, y=20, width=240, height=44)
    assert text.text.content == "Generic title"
    assert text.text.font_size == 32
    assert text.text.horizontal_align == "CENTER"


def test_verified_component_uses_candidate_key_without_target_ids() -> None:
    plan = compile_fgui_plan(component_document("verified"))
    node = only_node(plan)
    assert node.type == "componentReference"
    assert node.component.candidate_key == "common_primary_button"
    encoded = plan.model_dump_json(by_alias=True).encode("utf-8")
    assert b"packageId" not in encoded and b"componentId" not in encoded


def test_valid_nine_slice_is_preserved_and_invalid_grid_blocks() -> None:
    valid = compile_fgui_plan(image_document(size=(100, 80), nine_slice=(10, 10, 70, 50)))
    assert only_resource(valid).nine_slice == (10, 10, 70, 50)
    invalid = compile_fgui_plan(image_document(size=(100, 80), nine_slice=(10, 10, 100, 50)))
    assert invalid.bindable is False
    assert any(item.code == "fgui.resource.nine_slice_invalid" for item in invalid.diagnostics)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_plan_compile.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-compile-red`

Expected: collection fails because `fgui_plan_compile` does not exist.

- [ ] **Step 3: Extend engine-neutral UIR asset facts**

Add optional fields without adding FairyGUI concepts:

```python
class UIRNineSlice(UIRModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class UIRAsset(UIRModel):
    # existing fields remain unchanged
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    nine_slice: UIRNineSlice | None = Field(default=None, alias="nineSlice")
    export_format: Literal["png", "jpg", "webp"] = Field(default="png", alias="exportFormat")
```

- [ ] **Step 4: Implement deterministic primitive compilation**

Compile nodes in UIR child order. Stable PlanNode IDs hash `sourceUirSha256`, UIR node ID, profile version, and rule version. Map:

```python
RULE_TO_NODE_TYPE = {
    "fgui.native.container": PlanNodeType.CONTAINER,
    "fgui.native.text": PlanNodeType.TEXT,
    "fgui.native.image": PlanNodeType.IMAGE,
    "fgui.native.component_reference": PlanNodeType.COMPONENT_REFERENCE,
    "fgui.fallback.raster_subtree": PlanNodeType.RASTER_SUBTREE,
}
```

Copy transforms from `geometry.resolved_bounds`, rotation, and opacity. Build `TextPlan` only from UIR text source facts. Build `ComponentReferencePlan` from the verified mapping decision candidate key and instance variant properties. Build each `ResourcePlan` from `UIRAsset`, then sort/deduplicate consumer IDs.

Validate nine-slice with:

```python
def valid_nine_slice(asset: UIRAsset) -> bool:
    grid = asset.nine_slice
    return grid is None or (
        asset.width is not None and asset.height is not None
        and grid.x + grid.width <= asset.width
        and grid.y + grid.height <= asset.height
    )
```

Invalid grids produce an ERROR diagnostic and make the document non-bindable; they are not copied into `ResourcePlan.nine_slice`.

- [ ] **Step 5: Run focused tests and static checks**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_plan_compile.py tests\unit\test_uir_models.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-compile-green
..\..\.venv\Scripts\ruff.exe check src\figma_to_fgui\uir_models.py src\figma_to_fgui\fgui_plan_compile.py tests\unit\test_fgui_plan_compile.py
..\..\.venv\Scripts\mypy.exe src\figma_to_fgui\uir_models.py src\figma_to_fgui\fgui_plan_compile.py
```

- [ ] **Step 6: Commit Task 3**

```powershell
git add src/figma_to_fgui/uir_models.py src/figma_to_fgui/fgui_plan_compile.py tests/unit/test_fgui_plan_compile.py
git commit -m "feat: compile generic FairyGUI primitive plans"
```

---

### Task 4: Classify and compile masks without duplicate descendants

**Files:**
- Modify: `src/figma_to_fgui/fgui_capabilities.py`
- Modify: `src/figma_to_fgui/fgui_plan_compile.py`
- Modify: `tests/unit/test_fgui_capabilities.py`
- Modify: `tests/unit/test_fgui_plan_compile.py`

**Interfaces:**
- Consumes mask facts from `UIRNode.visual["mask"]` with validated keys `kind`, `maskNodeRef`, `contentNodeRefs`, `safeRasterRootRef`, and `effects`.
- Produces native `MaskPlan`, fallback `MaskPlan + ResourcePlan`, or blocking diagnostics.

- [ ] **Step 1: Write failing mask matrix tests**

```python
@pytest.mark.parametrize("kind", ["rectangle", "roundedRectangle"])
def test_rectangle_masks_compile_as_native_clip(kind: str) -> None:
    plan = compile_fgui_plan(mask_document(kind=kind))
    assert only_mask(plan).mode == "nativeClip"
    assert plan.bindable is True


def test_simple_image_mask_compiles_as_native_mask() -> None:
    plan = compile_fgui_plan(mask_document(kind="image"))
    assert only_mask(plan).mode == "nativeMask"


@pytest.mark.parametrize("kind", ["boolean", "gradient", "blur", "blend"])
def test_complex_mask_rasterizes_only_safe_subtree(kind: str) -> None:
    plan = compile_fgui_plan(mask_document(kind=kind, safe_raster=True))
    raster = next(node for node in plan.nodes.values() if node.type == "rasterSubtree")
    assert raster.resource_ref in plan.resources
    assert set(descendants_of_raster_source()) - {raster.uir_node_ref} == {
        item for item in source_descendant_ids() if item not in {n.uir_node_ref for n in plan.nodes.values()}
    }


def test_missing_or_cross_parent_mask_is_blocking() -> None:
    for document in (mask_document(source_missing=True), mask_document(cross_parent=True)):
        plan = compile_fgui_plan(document)
        assert plan.bindable is False
        assert any(item.code in {"fgui.mask.source_missing", "fgui.mask.invalid_hierarchy"} for item in plan.diagnostics)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_capabilities.py tests\unit\test_fgui_plan_compile.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-mask-red`

Expected: mask assertions fail because mask facts are not analyzed.

- [ ] **Step 3: Implement strict mask fact parsing and hierarchy checks**

Create an internal strict `MaskFacts` Pydantic model in `fgui_capabilities.py`. Unknown keys are rejected into `fgui.mask.facts_invalid`. Verify all referenced nodes exist, mask/content nodes share the declared container, content order is contiguous, and safe raster root is an ancestor of every consumed node.

Rules:

```python
NATIVE_CLIP_KINDS = {"rectangle", "roundedRectangle"}
NATIVE_MASK_KINDS = {"image"}
RASTER_MASK_KINDS = {"boolean", "gradient", "blur", "blend"}
```

Missing sources use `fgui.mask.source_missing`; invalid ownership/order uses `fgui.mask.invalid_hierarchy`; complex masks without a valid raster asset use `fgui.visual.effect_unsupported`.

- [ ] **Step 4: Compile MaskPlan and consume raster descendants**

For native masks, preserve source/content references and original order. For fallback masks, emit one `rasterSubtree` node at the safe root position, attach its resource, and add every descendant source ID to a `consumed_uir_nodes` set before ordinary node compilation. Never emit a second PlanNode for consumed descendants.

- [ ] **Step 5: Run focused tests and static checks**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_capabilities.py tests\unit\test_fgui_plan_compile.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-mask-green
..\..\.venv\Scripts\ruff.exe check src\figma_to_fgui\fgui_capabilities.py src\figma_to_fgui\fgui_plan_compile.py tests\unit\test_fgui_capabilities.py tests\unit\test_fgui_plan_compile.py
..\..\.venv\Scripts\mypy.exe src\figma_to_fgui\fgui_capabilities.py src\figma_to_fgui\fgui_plan_compile.py
```

- [ ] **Step 6: Commit Task 4**

```powershell
git add src/figma_to_fgui/fgui_capabilities.py src/figma_to_fgui/fgui_plan_compile.py tests/unit/test_fgui_capabilities.py tests/unit/test_fgui_plan_compile.py
git commit -m "feat: plan native and fallback masks"
```

---

### Task 5: Validate and canonicalize generation plans

**Files:**
- Create: `src/figma_to_fgui/fgui_plan_validate.py`
- Create: `tests/unit/test_fgui_plan_validate.py`

**Interfaces:**
- Consumes: `FGUIPlanDocument`.
- Produces: `validate_fgui_plan(plan) -> tuple[Diagnostic, ...]`, `canonical_plan_bytes(plan) -> bytes`, and `plan_sha256(plan) -> str`.

- [ ] **Step 1: Write failing integrity, leakage, capability, and determinism tests**

```python
def test_validation_reports_all_dangling_references() -> None:
    codes = {item.code for item in validate_fgui_plan(plan_with_dangling_refs())}
    assert codes == {
        "fgui.plan.root_missing", "fgui.plan.child_missing",
        "fgui.plan.resource_missing", "fgui.plan.mask_missing",
        "fgui.plan.decision_missing",
    }


def test_fallback_requires_resource_and_unsupported_requires_blocking_diagnostic() -> None:
    codes = {item.code for item in validate_fgui_plan(inconsistent_capability_plan())}
    assert "fgui.plan.fallback_resource_required" in codes
    assert "fgui.plan.unsupported_not_blocked" in codes


def test_binding_field_leak_is_rejected_recursively() -> None:
    assert any(item.code == "fgui.plan.binding_field_leak" for item in validate_fgui_plan(plan_with_style_fact("pkg", "bad")))


def test_canonical_bytes_and_hash_are_stable() -> None:
    first = canonical_plan_bytes(valid_plan())
    second = canonical_plan_bytes(valid_plan())
    assert first == second and first.endswith(b"\n")
    assert plan_sha256(valid_plan()) == hashlib.sha256(first).hexdigest()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_plan_validate.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-validate-red`

Expected: collection fails because `fgui_plan_validate` does not exist.

- [ ] **Step 3: Implement complete validation and canonical serialization**

Validate roots, node dictionary keys, child ownership, parent symmetry, resources, masks, decisions, mask source/content references, raster descendant duplication, node-type required payloads, capability consistency, bindable/error consistency, and recursively forbidden keys `{packageId, componentId, src, pkg}`.

```python
def canonical_plan_bytes(plan: FGUIPlanDocument) -> bytes:
    payload = plan.model_dump(mode="json", by_alias=True)
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def plan_sha256(plan: FGUIPlanDocument) -> str:
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()
```

- [ ] **Step 4: Run focused tests and static checks**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_plan_validate.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-validate-green
..\..\.venv\Scripts\ruff.exe check src\figma_to_fgui\fgui_plan_validate.py tests\unit\test_fgui_plan_validate.py
..\..\.venv\Scripts\mypy.exe src\figma_to_fgui\fgui_plan_validate.py
```

- [ ] **Step 5: Commit Task 5**

```powershell
git add src/figma_to_fgui/fgui_plan_validate.py tests/unit/test_fgui_plan_validate.py
git commit -m "feat: validate and canonicalize FairyGUI plans"
```

---

### Task 6: Expose plan compilation through the production CLI

**Files:**
- Modify: `src/figma_to_fgui/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: canonical UIR JSON.
- Produces: `fgui-tool build-fgui-plan SOURCE OUTPUT [--profile-version VERSION] [--rule-version N]`.

- [ ] **Step 1: Write failing CLI success and blocking tests**

```python
def test_build_fgui_plan_writes_canonical_valid_plan(tmp_path: Path) -> None:
    output = tmp_path / "generic.fgui-plan.json"
    result = CliRunner().invoke(app, ["build-fgui-plan", "tests/fixtures/fgui-plan/generic-primitives.uir.json", str(output)])
    assert result.exit_code == 0, result.output
    plan = FGUIPlanDocument.model_validate_json(output.read_text("utf-8"))
    assert validate_fgui_plan(plan) == ()
    assert output.read_bytes().endswith(b"\n")


def test_build_fgui_plan_writes_diagnostics_but_exits_two_when_not_bindable(tmp_path: Path) -> None:
    output = tmp_path / "broken.fgui-plan.json"
    result = CliRunner().invoke(app, ["build-fgui-plan", "tests/fixtures/fgui-plan/generic-masks.uir.json", str(output)])
    assert result.exit_code == 2
    assert output.is_file()
    assert FGUIPlanDocument.model_validate_json(output.read_text("utf-8")).bindable is False
```

- [ ] **Step 2: Run tests and verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_cli.py::test_build_fgui_plan_writes_canonical_valid_plan tests\unit\test_cli.py::test_build_fgui_plan_writes_diagnostics_but_exits_two_when_not_bindable -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-cli-red`

Expected: Typer exits 2 because `build-fgui-plan` is unknown.

- [ ] **Step 3: Implement the CLI command**

```python
@app.command("build-fgui-plan")
def build_fgui_plan_command(
    source: Path,
    output: Path,
    profile_version: Annotated[str, typer.Option("--profile-version")] = "fgui-6.1.4-v1",
    rule_version: Annotated[int, typer.Option("--rule-version", min=1)] = 1,
) -> None:
    document = UIRDocument.model_validate_json(source.read_text("utf-8"))
    uir_diagnostics = validate_uir(document)
    if any(item.severity == Severity.ERROR for item in uir_diagnostics):
        raise typer.BadParameter("source UIR is invalid", param_hint="SOURCE")
    plan = compile_fgui_plan(document, profile_version=profile_version, rule_version=rule_version)
    plan_diagnostics = validate_fgui_plan(plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_plan_bytes(plan))
    if not plan.bindable or any(item.severity == Severity.ERROR for item in plan_diagnostics):
        raise typer.Exit(code=2)
```

Malformed JSON or schema input must become a safe Typer parameter error without printing a Pydantic traceback.

- [ ] **Step 4: Run CLI and related tests**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_cli.py tests\unit\test_fgui_plan_models.py tests\unit\test_fgui_capabilities.py tests\unit\test_fgui_plan_compile.py tests\unit\test_fgui_plan_validate.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-cli-green
..\..\.venv\Scripts\ruff.exe check src\figma_to_fgui\cli.py tests\unit\test_cli.py
..\..\.venv\Scripts\mypy.exe src\figma_to_fgui\cli.py
```

- [ ] **Step 5: Commit Task 6**

```powershell
git add src/figma_to_fgui/cli.py tests/unit/test_cli.py tests/fixtures/fgui-plan/generic-primitives.uir.json tests/fixtures/fgui-plan/generic-masks.uir.json
git commit -m "feat: add generic FairyGUI plan command"
```

---

### Task 7: Add a generic golden and prove there are no sample-specific rules

**Files:**
- Create: `tests/golden/expected/generic-primitives.fgui-plan.json`
- Create: `tests/golden/test_generic_fgui_plan.py`
- Modify: `README.md`

**Interfaces:**
- Consumes public UIR/plan models, compiler, validator, and canonical serializers.
- Produces a byte-stable generic plan golden and documented developer workflow.

- [ ] **Step 1: Write the failing generic golden and anti-hardcode tests**

```python
def test_generic_primitives_match_reviewed_plan_golden() -> None:
    document = UIRDocument.model_validate_json(Path("tests/fixtures/fgui-plan/generic-primitives.uir.json").read_text("utf-8"))
    plan = compile_fgui_plan(document)
    assert validate_fgui_plan(plan) == ()
    assert canonical_plan_bytes(plan) == Path("tests/golden/expected/generic-primitives.fgui-plan.json").read_bytes()


def test_plan_implementation_contains_no_village_specific_branch() -> None:
    implementation = "\n".join(
        Path(path).read_text("utf-8")
        for path in (
            "src/figma_to_fgui/fgui_capabilities.py",
            "src/figma_to_fgui/fgui_plan_compile.py",
        )
    )
    for forbidden in ("村庄升阶", "village-root", "selection_village_ascend"):
        assert forbidden not in implementation


def test_name_and_size_mutation_preserves_rule_selection() -> None:
    original = compile_fgui_plan(generic_document(name="Inventory", width=750))
    mutated = compile_fgui_plan(generic_document(name="Event Shop", width=1440))
    assert decision_rules(original) == decision_rules(mutated)
```

- [ ] **Step 2: Run golden test and verify RED**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\golden\test_generic_fgui_plan.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-golden-red`

Expected: failure because the reviewed expected file does not exist.

- [ ] **Step 3: Generate and manually review the canonical golden once**

Generate through `compile_fgui_plan` and `canonical_plan_bytes`. Review that hierarchy/order, text, resource consumers, nine-slice, component candidate key, decisions, and hashes are correct; verify no target IDs, local paths, access tokens, raw bytes, or sample-specific conditions appear.

- [ ] **Step 4: Document the plan command and boundary**

Add this exact workflow to README and state that the output is not XML and contains no project IDs:

```powershell
fgui-tool build-fgui-plan out/simple.uir.json out/simple.fgui-plan.json `
  --profile-version fgui-6.1.4-v1 `
  --rule-version 1
```

Document that a future mapping file must first pass a dedicated versioned importer after its schema is supplied.

- [ ] **Step 5: Run the generic golden twice**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\golden\test_generic_fgui_plan.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-golden-green1
..\..\.venv\Scripts\python.exe -m pytest tests\golden\test_generic_fgui_plan.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-golden-green2
```

- [ ] **Step 6: Commit Task 7**

```powershell
git add tests/golden/expected/generic-primitives.fgui-plan.json tests/golden/test_generic_fgui_plan.py README.md
git commit -m "test: add generic FairyGUI plan golden"
```

---

### Task 8: Run the complete quality gate and verify the installed CLI

**Files:**
- No source changes expected.

**Interfaces:**
- Consumes all public UIR and generation-plan APIs.
- Produces final verification evidence for the complete implementation batch.

- [ ] **Step 1: Run all plan-focused tests**

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests\unit\test_fgui_plan_models.py tests\unit\test_fgui_capabilities.py tests\unit\test_fgui_plan_compile.py tests\unit\test_fgui_plan_validate.py tests\unit\test_cli.py tests\golden\test_generic_fgui_plan.py -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-plan-focused-final
```

Expected: zero failures.

- [ ] **Step 2: Run the full Python quality gate separately**

```powershell
..\..\.venv\Scripts\python.exe -m pytest -q --basetemp=C:\Users\<user>\AppData\Local\Temp\fgui-plan-full-final
..\..\.venv\Scripts\ruff.exe check src tests
..\..\.venv\Scripts\mypy.exe src
git diff --check
```

Expected: all commands succeed; existing explicit platform skips may remain skipped.

- [ ] **Step 3: Install and verify the production entry point**

```powershell
..\..\.venv\Scripts\python.exe -m pip install -e . --no-deps
..\..\.venv\Scripts\fgui-tool.exe build-uir tests/fixtures/figma/simple-frame.json .test-tmp/simple.uir.json `
  --source-revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa `
  --selection-id generic_simple
..\..\.venv\Scripts\fgui-tool.exe build-fgui-plan .test-tmp/simple.uir.json .test-tmp/simple.fgui-plan.json
```

Parse both outputs through `UIRDocument` and `FGUIPlanDocument`, assert both validators return no diagnostics, and assert the plan is bindable.

- [ ] **Step 4: Record final branch state**

Run these separately:

```powershell
git status --short
git log -10 --oneline
```

Expected: only intentionally generated ignored artifacts remain; all implementation commits are visible.

## Self-Review Results

- **Spec coverage:** Tasks 1–5 implement the contract, generic capability decisions, primitive/text/resource/component compilation, mask behavior, validation, determinism, and binding isolation. Tasks 6–8 add the CLI, generic golden, anti-hardcode tests, documentation, and full production verification.
- **Placeholder scan:** No incomplete marker, vague error-handling instruction, or undefined follow-up step remains. The future UIR mapping file is explicitly outside this implementation because its real schema has not yet been supplied.
- **Type consistency:** `compile_fgui_plan`, `analyze_capabilities`, `validate_fgui_plan`, `canonical_plan_bytes`, and `plan_sha256` retain identical names and signatures across tasks. JSON uses camelCase aliases; Python access uses snake_case.
- **Scope check:** Project Binding, XML Writer, Controller, Gear, List, and complex Auto Layout remain separate implementation cycles, matching the approved design.
