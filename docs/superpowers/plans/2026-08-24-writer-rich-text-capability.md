# Writer Rich Text Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve color-only Figma text runs as editable FairyGUI 6.1.4 rich text, while reporting concrete editable risks for run styles that are not proven expressible.

**Architecture:** Put one pure run-analysis function in the capability layer and reuse its result when selecting the Plan node type and authoring review facts. Content closure is a blocker; unsupported style differences retain editable plain text and become an explicit review disposition; the XML writer remains the final dialect guard.

**Tech Stack:** Python 3.12, Pydantic v2, lxml, pytest, Ruff, mypy, FairyGUI 6.1.4 XML dialect.

## Global Constraints

- Do not start Project Binding.
- Do not add page-name, node-ID, or business-sample special cases.
- Preserve native editability whenever FairyGUI 6.1.4 has verified syntax.
- Do not invent UBB tags or split text nodes without character-level geometry.
- Rasterization is opt-in and limited to the smallest unsupported text node.
- Do not claim FairyGUI Editor GUI acceptance until the user opens the ZIP and confirms it.

## File map

- Modify `src/figma_to_fgui/fgui_capabilities.py`: own the pure run capability classification and stable evidence facts.
- Modify `src/figma_to_fgui/fgui_plan_compile.py`: emit `richText` only for proven native runs and editable plain text for reviewable runs.
- Modify `src/figma_to_fgui/fgui_conversion_dispositions.py`: project concrete run facts into review dispositions.
- Modify `src/figma_to_fgui/service_contracts.py`: carry stable, non-visual review facts (`runCount`, preserved and unsupported properties).
- Modify `apps/figma-plugin/src/project-client.ts`: parse the exact extended public contract.
- Modify `src/figma_to_fgui/fgui_xml_dialect_614.py`: retain the final content/UBB safety checks.
- Test `tests/unit/test_fgui_capabilities.py`, `tests/unit/test_fgui_plan_compile.py`, `tests/unit/test_fgui_xml_dialect_614.py`, `tests/unit/test_fgui_new_project_workflow.py`, `tests/unit/test_service_contracts.py`, and `apps/figma-plugin/src/project-client.test.ts`.

---

### Task 1: Classify text runs from verified facts

**Files:**
- Modify: `src/figma_to_fgui/fgui_capabilities.py`
- Test: `tests/unit/test_fgui_capabilities.py`

**Interfaces:**
- Produces: `TextRunCapability(kind, run_count, preserved_properties, unsupported_properties)` and `analyze_text_runs(node: UIRNode) -> TextRunCapability`.
- Consumes: `UIRNode.text`, base `TextStyle`, and each `UIRTextRun.style`.

- [ ] **Step 1: Write failing positive and negative tests**

```python
def test_color_only_runs_are_native_but_size_and_font_differences_are_reviewable() -> None:
    native = _text_node(base_size=20, runs=[("Buy ", 20, None), ("now", 20, "#ff0000")])
    sized = _text_node(base_size=20, runs=[("Buy ", 20, None), ("now", 24, None)])
    assert analyze_text_runs(native).kind == "native-rich-text"
    assert analyze_text_runs(native).preserved_properties == ("content", "color")
    assert analyze_text_runs(sized).kind == "editable-risk"
    assert analyze_text_runs(sized).unsupported_properties == ("fontSize",)

def test_run_content_mismatch_is_blocking() -> None:
    node = _text_node(content="Buy now", runs=[("Buy", 20, None)])
    assert analyze_text_runs(node).kind == "blocked"
    assert "contentClosure" in analyze_text_runs(node).unsupported_properties
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/unit/test_fgui_capabilities.py -k "color_only_runs or run_content_mismatch" -q`

Expected: FAIL because `analyze_text_runs` is not defined.

- [ ] **Step 3: Implement the pure classifier**

```python
@dataclass(frozen=True)
class TextRunCapability:
    kind: Literal["plain-text", "native-rich-text", "editable-risk", "blocked"]
    run_count: int
    preserved_properties: tuple[str, ...]
    unsupported_properties: tuple[str, ...]

def analyze_text_runs(node: UIRNode) -> TextRunCapability:
    text = node.text
    if text is None or not text.runs:
        return TextRunCapability("plain-text", 0, ("content",), ())
    if "".join(run.content for run in text.runs) != text.content:
        return TextRunCapability("blocked", len(text.runs), (), ("contentClosure",))
    unsupported: set[str] = set()
    for run in text.runs:
        if run.unsupported_features:
            unsupported.update(run.unsupported_features)
        if run.style.font_candidates not in {(), text.style.font_candidates}:
            unsupported.add("fontCandidates")
        if run.style.font_size not in {None, text.style.font_size}:
            unsupported.add("fontSize")
        if run.style.stroke_color not in {None, text.style.stroke_color}:
            unsupported.add("strokeColor")
        if run.style.stroke_size not in {None, text.style.stroke_size}:
            unsupported.add("strokeSize")
        if run.style.horizontal_align is not None or run.style.vertical_align is not None:
            unsupported.add("paragraphAlignment")
    ambiguous = any("[" in run.content or "]" in run.content for run in text.runs)
    if ambiguous and any(run.style.color not in {None, text.style.color} for run in text.runs):
        unsupported.add("ubbEncoding")
    if unsupported:
        return TextRunCapability("editable-risk", len(text.runs), ("content",), tuple(sorted(unsupported)))
    return TextRunCapability("native-rich-text", len(text.runs), ("content", "color"), ())
```

- [ ] **Step 4: Route capability decisions through the classifier**

Use `fgui.text.runs_content_mismatch` with `blocking=True` for `blocked`, retain native rule `fgui.native.rich_text` for `native-rich-text`, and use a non-blocking reviewed fallback reason `rich_text_runs` with evidence entries `text.runs.count=N`, `text.runs.preserved=...`, and `text.runs.unsupported=...` for `editable-risk`.

- [ ] **Step 5: Run focused capability tests and commit**

Run: `python -m pytest tests/unit/test_fgui_capabilities.py -q`

Expected: all capability tests PASS.

```bash
git add src/figma_to_fgui/fgui_capabilities.py tests/unit/test_fgui_capabilities.py
git commit -m "feat: classify verified rich text runs"
```

### Task 2: Compile native rich text and reviewable plain text safely

**Files:**
- Modify: `src/figma_to_fgui/fgui_plan_compile.py`
- Modify: `src/figma_to_fgui/fgui_xml_dialect_614.py`
- Test: `tests/unit/test_fgui_plan_compile.py`
- Test: `tests/unit/test_fgui_xml_dialect_614.py`

**Interfaces:**
- Consumes: `analyze_text_runs(node)` from Task 1.
- Produces: `TextPlan.runs` only for `native-rich-text`; reviewable fallback is `PlanNodeType.TEXT` with base editable content and no runs.

- [ ] **Step 1: Add failing compile and XML tests**

```python
def test_color_only_runs_compile_to_ubb_without_raster() -> None:
    plan = compile_fgui_plan(_color_run_document())
    node = only_node(plan)
    assert node.type == "richText"
    assert node.text is not None and len(node.text.runs) == 2
    xml = serialize_component(_manifest_from_plan(plan))
    assert b'ubb="true"' in xml
    assert b'[color=#ff0000]now[/color]' in xml

def test_size_difference_keeps_editable_plain_text_for_review() -> None:
    plan = compile_fgui_plan(_size_run_document())
    node = only_node(plan)
    assert node.type == "text"
    assert node.text is not None and node.text.content == "Buy now"
    assert node.text.runs == ()
    assert any(decision.reasons == ("rich_text_runs",) for decision in plan.decisions.values())
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_xml_dialect_614.py -k "color_only_runs or size_difference" -q`

Expected: at least the size-difference test FAILS because runs are currently forwarded to the Writer.

- [ ] **Step 3: Make `_text_plan` accept the analysis result**

Change the helper to `_text_plan(node: UIRNode, run_capability: TextRunCapability) -> TextPlan`. Populate `runs` only when `kind == "native-rich-text"`; otherwise keep the base content/style and set `runs=()`.

- [ ] **Step 4: Select node type from the same analysis result**

In the compile loop, compute the classifier once per text node. Emit `PlanNodeType.RICH_TEXT` only for `native-rich-text`, `PlanNodeType.TEXT` for `plain-text` and `editable-risk`, and quarantine/diagnose `blocked` before artifact writing.

- [ ] **Step 5: Keep Writer guards strict and run tests**

Do not loosen `_rich_text_content`: it must continue rejecting content mismatch, unverified per-run font/size/stroke, and ambiguous UBB. Run:

`python -m pytest tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_plan_validate.py tests/unit/test_fgui_xml_dialect_614.py -q`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/fgui_plan_compile.py src/figma_to_fgui/fgui_xml_dialect_614.py tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_xml_dialect_614.py
git commit -m "feat: preserve supported rich text natively"
```

### Task 3: Expose concrete review facts through the public contract

**Files:**
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/fgui_conversion_dispositions.py`
- Modify: `apps/figma-plugin/src/project-client.ts`
- Test: `tests/unit/test_service_contracts.py`
- Test: `tests/unit/test_fgui_new_project_workflow.py`
- Test: `apps/figma-plugin/src/project-client.test.ts`

**Interfaces:**
- Produces: required disposition field `details: { runCount: number; preservedProperties: string[]; unsupportedProperties: string[] } | null` in both Python and TypeScript.
- Consumes: stable `CapabilityDecision.evidence` entries authored in Task 1.

- [ ] **Step 1: Add failing serialization and parser tests**

```python
assert disposition.model_dump(mode="json", by_alias=True)["details"] == {
    "runCount": 2,
    "preservedProperties": ["content"],
    "unsupportedProperties": ["fontSize"],
}
```

```ts
expect(review.dispositions[0].details).toEqual({
  runCount: 2,
  preservedProperties: ["content"],
  unsupportedProperties: ["fontSize"],
});
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/unit/test_service_contracts.py tests/unit/test_fgui_new_project_workflow.py -k "rich_text or disposition" -q`

Run: `pnpm --dir apps/figma-plugin vitest run src/project-client.test.ts`

Expected: FAIL because `details` is absent.

- [ ] **Step 3: Add exact typed detail models**

Add `NewProjectDispositionDetails` with non-negative `runCount` and duplicate-free string tuples. Add required nullable `details` to `NewProjectConversionDisposition`; native and non-text dispositions set it to `None`.

- [ ] **Step 4: Parse decision evidence without display-name inference**

Add a private parser that accepts only the three registered prefixes and rejects malformed or duplicate evidence. `build_conversion_dispositions` supplies details only for `rich_text_runs`; missing required run facts raises `ValueError` and prevents a misleading review response.

- [ ] **Step 5: Update the exact TypeScript parser and fixtures**

Extend `NewProjectConversionDisposition`, its exact key list, and test fixtures. Require `details` to be either `null` or an exact record with the three fields; never accept arbitrary diagnostic maps.

- [ ] **Step 6: Run contract and workflow tests and commit**

Run: `python -m pytest tests/unit/test_service_contracts.py tests/unit/test_fgui_new_project_workflow.py -q`

Run: `pnpm --dir apps/figma-plugin test`

Expected: all selected suites PASS.

```bash
git add src/figma_to_fgui/service_contracts.py src/figma_to_fgui/fgui_conversion_dispositions.py apps/figma-plugin/src/project-client.ts tests/unit/test_service_contracts.py tests/unit/test_fgui_new_project_workflow.py apps/figma-plugin/src/project-client.test.ts
git commit -m "feat: report concrete rich text review facts"
```

### Task 4: Run backend acceptance gates

**Files:**
- Modify only regressions or fixtures that fail because the public contract intentionally gained `details`.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: a backend and plugin-client baseline ready for the Writer UI plan.

- [ ] **Step 1: Run positive and negative focused suites**

Run: `python -m pytest tests/unit/test_fgui_capabilities.py tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_plan_validate.py tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_workflow.py tests/unit/test_service_contracts.py -q`

Expected: PASS, including color-only native, size/font/stroke risk, UBB-risk, and content-closure blocker cases.

- [ ] **Step 2: Run Python full checks**

Run: `python -m pytest -q`

Run: `python -m ruff check src tests`

Run: `python -m mypy --strict src`

Expected: pytest PASS with only documented skips/warnings; Ruff and mypy exit 0.

- [ ] **Step 3: Run plugin contract checks**

Run: `pnpm --dir apps/figma-plugin test`

Run: `pnpm --dir apps/figma-plugin typecheck`

Expected: all plugin tests and build parity PASS; TypeScript exits 0.

- [ ] **Step 4: Check diff and commit any intentional fixture updates**

Run: `git diff --check`

Expected: no whitespace errors.

```bash
git add tests apps/figma-plugin/src
git commit -m "test: close rich text capability regressions"
```

