# Writer Capability Review Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one backend-authored conversion disposition drive selection fallback, pre-approval review, adjustment, final Writer validation, and the plugin's grouped three-level review UI.

**Architecture:** The plugin exports complete visual facts plus conservative fallback assets, without deciding final Writer support. A new pure backend analyzer maps normalized/compiled diagnostics and available fallback assets into a strict disposition set; the candidate lifecycle persists that review even when blocking items exist, applies safe defaults, and only permits final approval when the resulting Writer artifact passes all existing gates. The React panel renders the same server-authored dispositions as green, yellow, and red grouped reviews.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI/Starlette, SQLite, React 18, TypeScript, Vitest, pytest, Ruff, mypy, FairyGUI 6.1.4 deterministic Writer.

## Global Constraints

- Do not implement Project Binding or change the existing-project Update workflow.
- Do not add village/page-name/business-node special cases; the village selection is regression input only.
- Keep the existing FairyGUI 6.1.4 XML, asset, directory, archive, and atomic-publication gates unchanged.
- Existing gradient, shadow, mask, and instance PNG fidelity fallback must not regress.
- Only red `blocked` dispositions block approval; green and yellow dispositions may produce a candidate using the safe default.
- Unknown disposition states, reason codes, and strategies fail closed in Python and TypeScript.
- Public diagnostics must not expose local paths, tokens, resource bytes, storage paths, or private exception chains.
- The same input must produce deterministic disposition order, review order, and ZIP bytes.

---

## File Structure

- Create `src/figma_to_fgui/fgui_conversion_dispositions.py`: pure backend authority for reason classification, severity, default strategy, allowed strategies, and stable ordering.
- Modify `src/figma_to_fgui/service_contracts.py`: strict public disposition contracts and candidate/review projection.
- Modify `src/figma_to_fgui/figma_selection.py`: strict fallback-candidate metadata carried by committed selections.
- Modify `src/figma_to_fgui/fgui_new_project_workflow.py`: analyze before terminal validation, apply defaults, and return structured analysis/build results.
- Modify `src/figma_to_fgui/fgui_new_project_review.py`: project dispositions into review checks and approvability.
- Modify `src/figma_to_fgui/job_store.py`: persist review/dispositions for reviewable blocked candidates.
- Modify `src/figma_to_fgui/api.py`: lifecycle stages and public review behavior.
- Modify `apps/figma-plugin/src/selection.ts`: export complete facts and conservative fallback assets.
- Modify `apps/figma-plugin/src/project-client.ts`: strict disposition parsing.
- Modify `apps/web-console/src/figma/NewProjectReviewPanel.tsx`: grouped green/yellow/red UI.
- Modify `apps/web-console/src/figma/NewProjectWriterPanel.tsx`: reviewable blocked state and actionable Chinese failures.
- Rebuild `apps/figma-plugin/dist/code.js`, `apps/figma-plugin/dist/ui.html`, and `apps/figma-plugin/dist/manifest.json` only through the production build.

---

### Task 1: Strict conversion disposition contracts

**Files:**
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/figma_selection.py`
- Modify: `apps/figma-plugin/src/project-client.ts`
- Test: `tests/unit/test_service_contracts.py`
- Test: `apps/figma-plugin/src/project-client.test.ts`

**Interfaces:**
- Produces: `NewProjectDispositionLevel`, `NewProjectDispositionReason`, `NewProjectConversionDisposition`, and `NewProjectReview.dispositions`.
- Produces: selection property `fallback_resource_key` as an optional canonical resource key; its presence means only that a PNG fallback is available.
- Consumes: existing `NewProjectAdjustmentStrategy`, source node IDs, and strict versioned JSON conventions.

- [ ] **Step 1: Write the failing Python contract tests**

Add tests that construct one literal disposition of each level, verify alias serialization, and reject unknown level/reason, duplicated allowed strategies, a default strategy outside the allowed set, or `blocked=False` on a red item.

```python
def test_conversion_disposition_contract_is_closed() -> None:
    item = NewProjectConversionDisposition(
        schemaVersion=1,
        id="disposition:0011223344556677",
        sourceNodeId="node-17",
        sourceName="Rank label",
        sourceType="TEXT",
        level="editable_risk",
        reason="rich_text_runs",
        defaultStrategy="rasterize-subtree",
        allowedStrategies=("rasterize-subtree", "preserve-editable"),
        visualImpact="visual_preserved",
        editabilityImpact="text_not_editable",
        componentImpact="unchanged",
        blocksApproval=False,
    )
    assert item.model_dump(by_alias=True)["level"] == "editable_risk"
    with pytest.raises(ValidationError):
        item.model_copy(update={"level": "unknown"}).__class__.model_validate(
            item.model_dump(by_alias=True) | {"level": "unknown"}
        )
```

- [ ] **Step 2: Run the Python contract test and observe RED**

Run: `pytest tests/unit/test_service_contracts.py -k conversion_disposition --basetemp=.pt-disposition-contract-red -q`

Expected: collection/import failure because disposition types do not exist.

- [ ] **Step 3: Implement the strict Python models**

Add closed enums and a frozen strict model. Use a model validator to enforce unique strategies, default membership, and exact `blocks_approval == (level is BLOCKED)`.

```python
class NewProjectDispositionLevel(StrEnum):
    NATIVE = "native"
    RASTER_PRESERVED = "raster_preserved"
    EDITABLE_RISK = "editable_risk"
    BLOCKED = "blocked"


class NewProjectDispositionReason(StrEnum):
    GRADIENT_PAINT = "gradient_paint"
    VISUAL_EFFECT = "visual_effect"
    MASK_COMPOSITE = "mask_composite"
    INSTANCE_COMPOSITE = "instance_composite"
    VISUAL_STYLE = "visual_style"
    UNREPRESENTABLE_TRANSFORM = "unrepresentable_transform"
    RICH_TEXT_RUNS = "rich_text_runs"
    COMPONENT_DEFINITION_MISSING = "component_definition_missing"
    INTERACTION_UNSUPPORTED = "interaction_unsupported"
    RESOURCE_MISSING = "resource_missing"


class NewProjectConversionDisposition(StrictVersionedModel):
    id: Annotated[str, Field(pattern=r"^disposition:[0-9a-f]{16}$")]
    source_node_id: SafeIdentifier = Field(alias="sourceNodeId")
    source_name: PublicDisplayName = Field(alias="sourceName")
    source_type: NonBlankString = Field(alias="sourceType")
    level: NewProjectDispositionLevel
    reason: NewProjectDispositionReason
    default_strategy: NewProjectAdjustmentStrategy | None = Field(alias="defaultStrategy")
    allowed_strategies: tuple[NewProjectAdjustmentStrategy, ...] = Field(alias="allowedStrategies")
    visual_impact: Literal["unchanged", "visual_preserved", "may_differ"] = Field(alias="visualImpact")
    editability_impact: Literal["unchanged", "subtree_not_editable", "text_not_editable"] = Field(alias="editabilityImpact")
    component_impact: Literal["unchanged", "instance_not_reusable"] = Field(alias="componentImpact")
    blocks_approval: bool = Field(alias="blocksApproval")
```

- [ ] **Step 4: Write the failing strict TypeScript parser tests**

Extend the literal review fixture with `dispositions`, then mutate `level`, `reason`, `allowedStrategies`, and `blocksApproval` one at a time. Assert `WorkflowError("invalid_response")` for every mutation.

- [ ] **Step 5: Run the TypeScript parser test and observe RED**

Run: `node node_modules/vitest/vitest.mjs run apps/figma-plugin/src/project-client.test.ts`

Expected: tests fail because the client neither parses nor returns dispositions.

- [ ] **Step 6: Implement the strict TypeScript contract**

Define matching string unions and parse every field with exact key closure. Return a frozen array sorted as supplied; do not sort client-side because server order is authoritative.

```ts
export type NewProjectDispositionLevel = "native" | "raster_preserved" | "editable_risk" | "blocked";
export type NewProjectDispositionReason = "gradient_paint" | "visual_effect" | "mask_composite" | "instance_composite" | "visual_style" | "unrepresentable_transform" | "rich_text_runs" | "component_definition_missing" | "interaction_unsupported" | "resource_missing";
```

- [ ] **Step 7: Run focused contracts GREEN and commit**

Run:

```powershell
pytest tests/unit/test_service_contracts.py -k conversion_disposition --basetemp=.pt-disposition-contract-green -q
node node_modules/vitest/vitest.mjs run apps/figma-plugin/src/project-client.test.ts
```

Expected: both commands pass.

Commit: `feat: define Writer conversion dispositions`

---

### Task 2: Complete selection facts and conservative fallback availability

**Files:**
- Modify: `apps/figma-plugin/src/visual-capability.ts`
- Modify: `apps/figma-plugin/src/selection.ts`
- Modify: `apps/figma-plugin/src/assets.ts`
- Test: `apps/figma-plugin/src/visual-capability.test.ts`
- Test: `apps/figma-plugin/src/selection.test.ts`
- Test: `tests/unit/test_live_selection_normalize.py`

**Interfaces:**
- Produces: complete public facts for fills/strokes/style references/blend/effects, local transform/rotation, and text runs.
- Produces: an optional PNG `fallback_resource_key` for every node that the backend may safely rasterize.
- Consumes: Task 1 selection metadata and existing bounded resource upload.

- [ ] **Step 1: Add one failing public selection test per missing category**

Create a neutral frame containing: solid fill plus style reference, a rotated boolean, and mixed-style text. Assert that serialization keeps the facts and provides a fallback PNG resource for each without labeling any item finally supported or unsupported.

```ts
expect(manifest.top_level_nodes[0].children.map((node) => ({
  name: node.name,
  fallback: node.properties.fallback_resource_key,
}))).toEqual([
  { name: "Styled card", fallback: "asset-1" },
  { name: "Rotated mark", fallback: "asset-2" },
  { name: "Mixed label", fallback: "asset-3" },
]);
```

- [ ] **Step 2: Run selection tests and observe RED**

Run: `node node_modules/vitest/vitest.mjs run apps/figma-plugin/src/selection.test.ts apps/figma-plugin/src/visual-capability.test.ts`

Expected: visual style, transform, and rich-text nodes have no fallback resource.

- [ ] **Step 3: Separate export availability from final capability**

Replace the single final-decision classifier with a conservative `fallbackCandidateReasons(node)` used only to decide whether a PNG should be exported. It must include direct visible visual facts, unrepresentable transforms/rotation, and mixed/unsupported text runs, while retaining the existing gradient/effect/mask/instance reasons.

```ts
export function fallbackCandidateReasons(node: VisualNode): readonly RasterReason[] {
  const reasons = [...existingRasterReasons(node)];
  if (hasUnrepresentedVisualFacts(node)) reasons.push("visual_style");
  if (hasUnrepresentableTransform(node)) reasons.push("unrepresentable_transform");
  if (hasComplexTextRuns(node)) reasons.push("rich_text_runs");
  return [...new Set(reasons)];
}
```

Do not emit the current repeated `visual_rasterized` warning from this function. Store facts and fallback availability; Task 3 is authoritative for disposition level and copy.

- [ ] **Step 4: Preserve editable source facts when a fallback exists**

Ensure serialization does not prune the source node's children/text facts merely because a fallback asset is available. Add `fallback_resource_key` separately from the eventual `export_strategy`; the server may select native or raster later.

- [ ] **Step 5: Add normalize round-trip tests**

Assert that `SelectionManifest → selection_conversion_document → normalize_document` retains source identity, fallback asset identity, transform facts, and text runs. Expected normalized values must be literals from the fixture rather than values recomputed by production helpers.

- [ ] **Step 6: Run plugin and Python selection tests GREEN and commit**

Run:

```powershell
node node_modules/vitest/vitest.mjs run apps/figma-plugin/src/selection.test.ts apps/figma-plugin/src/visual-capability.test.ts
pytest tests/unit/test_live_selection_normalize.py --basetemp=.pt-selection-dispositions-green -q
```

Expected: all pass.

Commit: `feat: export Writer fallback candidates`

---

### Task 3: Backend-authoritative disposition analyzer

**Files:**
- Create: `src/figma_to_fgui/fgui_conversion_dispositions.py`
- Modify: `src/figma_to_fgui/fgui_new_project_workflow.py`
- Test: `tests/unit/test_fgui_conversion_dispositions.py`
- Test: `tests/unit/test_fgui_new_project_workflow.py`

**Interfaces:**
- Produces: `analyze_conversion_dispositions(manifest, uir, plan, diagnostics) -> tuple[NewProjectConversionDisposition, ...]`.
- Produces: `apply_default_dispositions(manifest, dispositions) -> SelectionManifest` using only available fallback resources.
- Consumes: Task 1 contracts and Task 2 fallback metadata.

- [ ] **Step 1: Write a failing neutral analyzer matrix**

Use literal source nodes and literal compiler diagnostics to assert:

- gradient/effect/mask → `raster_preserved`;
- transform → `raster_preserved` when a fallback exists;
- rich text → `editable_risk` with raster default and preserve-editable option;
- missing component definition without fallback → `blocked`;
- interaction with no safe strategy → `blocked`.

Assert exact stable IDs, order, source IDs, levels, reasons, strategies, impacts, and `blocksApproval`.

- [ ] **Step 2: Run analyzer tests and observe RED**

Run: `pytest tests/unit/test_fgui_conversion_dispositions.py --basetemp=.pt-disposition-analyzer-red -q`

Expected: module import failure.

- [ ] **Step 3: Implement a closed diagnostic-to-reason policy**

Create a module-level exact mapping. Do not inspect English messages.

```python
_DIAGNOSTIC_POLICY = {
    "fgui.unsupported.visual_style": NewProjectDispositionReason.VISUAL_STYLE,
    "fgui.unsupported.transform": NewProjectDispositionReason.UNREPRESENTABLE_TRANSFORM,
    "fgui.text.runs_unsupported": NewProjectDispositionReason.RICH_TEXT_RUNS,
    "fgui.component.definition_missing": NewProjectDispositionReason.COMPONENT_DEFINITION_MISSING,
    "fgui.unsupported.interaction": NewProjectDispositionReason.INTERACTION_UNSUPPORTED,
}
```

Map normalized/plan node IDs back to canonical source IDs with the existing source identity bridge. Derive disposition IDs from a length-prefixed canonical tuple `(source_node_id, reason)` and stable SHA-256 prefix.

- [ ] **Step 4: Implement safe default application**

For raster defaults, update a copied committed manifest so the exact node uses its declared fallback resource and `export_strategy="composite_png"`. Reject duplicate targets, missing fallback resources, unknown strategies, or a resource owned by another node.

- [ ] **Step 5: Add the real failure-shape workflow regression**

Build a neutral selection with at least one ordinary visual style, rotated shape, and mixed text. Before implementation it must return `fgui.writer.workflow.validation_failed`; after default application it must produce a buildable plan plus green/yellow dispositions, with no remaining `fgui.unsupported.visual_style`, `fgui.unsupported.transform`, or `fgui.text.runs_unsupported` errors.

- [ ] **Step 6: Run focused analyzer/workflow GREEN and commit**

Run:

```powershell
pytest tests/unit/test_fgui_conversion_dispositions.py tests/unit/test_fgui_new_project_workflow.py --basetemp=.pt-disposition-workflow-green -q
```

Expected: all pass.

Commit: `feat: centralize Writer capability decisions`

---

### Task 4: Review-before-terminal-failure candidate lifecycle

**Files:**
- Modify: `src/figma_to_fgui/fgui_new_project_review.py`
- Modify: `src/figma_to_fgui/job_store.py`
- Modify: `src/figma_to_fgui/api.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Test: `tests/unit/test_fgui_new_project_review.py`
- Test: `tests/unit/test_job_store.py`
- Test: `tests/integration/test_figma_plugin_new_project_writer_api.py`

**Interfaces:**
- Produces: review payload for both built candidates and reviewable blocked analyses.
- Produces: candidate stage `awaiting_review` with `artifactReady: bool`; approval additionally requires `artifactReady=True` and zero blocked dispositions.
- Consumes: Task 3 dispositions and existing owner/generation/lease rules.

- [ ] **Step 1: Write a failing REST lifecycle test**

POST a committed neutral selection containing one safe fallback and one genuinely blocked interaction. Poll until `awaiting_review`, GET `/review`, and assert the red item is present with source node and action. POST `/approve` must return the stable review-required conflict; no download is available. The response must not be terminal `failed` and must not contain a local path or exception text.

- [ ] **Step 2: Run the integration test and observe RED**

Run: `pytest tests/integration/test_figma_plugin_new_project_writer_api.py -k reviewable_blocked --basetemp=.pt-review-before-build-red -q`

Expected: candidate becomes `failed`, and review returns conflict/not found.

- [ ] **Step 3: Split analysis result from artifact result**

Change the workflow boundary to return a strict analysis result containing dispositions, diagnostics, and optionally a built artifact. A blocked disposition is a review result, not an exception. Operational corruption, invalid input contracts, and Writer gate failures remain failures.

- [ ] **Step 4: Persist review atomically with the candidate state**

Store review/disposition JSON in the same SQLite transaction that transitions the lease-owned candidate to `awaiting_review`. Store artifact metadata only when an artifact exists. Recovery must preserve the invariant that a row never advertises `artifactReady=True` without exact path/size/SHA metadata.

- [ ] **Step 5: Enforce approval and download gates**

Approval requires: correct owner/build/generation, `awaiting_review`, zero blocked dispositions, warnings acknowledged exactly, preview evidence valid, artifact ready, and artifact identity revalidated. A blocked analysis remains rejectable and adjustable but never downloadable.

- [ ] **Step 6: Replace generic validation copy with structured Chinese actions**

The public candidate/review should carry disposition copy. Reserve terminal `fgui.writer.workflow.validation_failed` for an uncovered final capability error and include the safe rule code/source node rather than a single English summary.

- [ ] **Step 7: Run focused API/store/review GREEN and commit**

Run:

```powershell
pytest tests/unit/test_fgui_new_project_review.py tests/unit/test_job_store.py tests/integration/test_figma_plugin_new_project_writer_api.py --basetemp=.pt-review-lifecycle-green -q
```

Expected: all pass.

Commit: `feat: review Writer issues before approval`

---

### Task 5: Grouped three-level plugin review UI

**Files:**
- Modify: `apps/web-console/src/figma/NewProjectReviewPanel.tsx`
- Modify: `apps/web-console/src/figma/NewProjectWriterPanel.tsx`
- Modify: `apps/web-console/src/styles.css`
- Test: `apps/web-console/src/figma/NewProjectReviewPanel.test.tsx`
- Test: `apps/web-console/src/figma/NewProjectWriterPanel.test.tsx`

**Interfaces:**
- Consumes: strict dispositions and `artifactReady` from Tasks 1 and 4.
- Produces: grouped summary, expandable rows, locate/adjust actions, warning acknowledgment, and correct approval lock.

- [ ] **Step 1: Write the failing UI behavior tests**

Render a review with two green, one yellow, and one red disposition. Assert visible group counts, collapsed details by default, expand behavior, Chinese impact copy, locate action, server-declared strategy buttons only, and disabled approval while red exists or artifact is unavailable.

```tsx
expect(screen.getByRole("button", { name: "已自动保真 2" })).toBeVisible();
expect(screen.getByRole("button", { name: "建议确认 1" })).toBeVisible();
expect(screen.getByRole("button", { name: "必须处理 1" })).toBeVisible();
expect(screen.getByRole("button", { name: "确认并下载 ZIP" })).toBeDisabled();
```

- [ ] **Step 2: Run UI tests and observe RED**

Run: `node node_modules/vitest/vitest.mjs run apps/web-console/src/figma/NewProjectReviewPanel.test.tsx apps/web-console/src/figma/NewProjectWriterPanel.test.tsx`

Expected: there are no disposition groups and blocked analyses show the retry screen.

- [ ] **Step 3: Implement semantic grouping**

Group by the exact server level without reclassifying reasons client-side. Use green/yellow/red tokens with text labels, not color alone. Keep details collapsed and render source name/type, impact copy, locate, and allowed strategies when expanded.

- [ ] **Step 4: Remove repeated selection warning spam**

Replace one paragraph per `visual_rasterized` warning with one summary that points to the review. Preflight may say “发现 N 个需要转换处置的图层”; final semantics come only from the server review.

- [ ] **Step 5: Make reviewable blocked analyses visible**

Do not route `awaiting_review + artifactReady=false` to the terminal retry screen. Show the review, allow locate/adjust/reject, and label the primary action according to available server strategies. Only operational `failed` candidates show retry.

- [ ] **Step 6: Run UI tests GREEN and commit**

Run:

```powershell
node node_modules/vitest/vitest.mjs run apps/web-console/src/figma/NewProjectReviewPanel.test.tsx apps/web-console/src/figma/NewProjectWriterPanel.test.tsx
node node_modules/typescript/bin/tsc -p apps/web-console/tsconfig.json --noEmit
```

Expected: tests and typecheck pass.

Commit: `feat: group Writer conversion review`

---

### Task 6: Public end-to-end regression, production build, and durable handoff

**Files:**
- Modify: `tests/integration/test_figma_plugin_new_project_delivery_e2e.py`
- Modify: `tests/integration/test_village_new_project_regression.py`
- Modify: `apps/figma-plugin/scripts/check-package.mjs`
- Modify: `apps/figma-plugin/dist/code.js`
- Modify: `apps/figma-plugin/dist/ui.html`
- Modify: `apps/figma-plugin/dist/manifest.json`
- Modify: `.claude/memory/wiki.md`
- Append: `.claude/memory/learnings.md` only if a new reusable pitfall is discovered

**Interfaces:**
- Consumes: Tasks 1–5 through public HTTP and built plugin surfaces.
- Produces: distributable plugin source/dist parity and durable verification evidence.

- [ ] **Step 1: Add a neutral complex-selection E2E test**

Through the public upload/create/poll/review/adjust/regenerate/approve/download API, exercise gradient, shadow, ordinary style, transform, mixed text, and one blocked item. Assert grouped disposition counts, authenticated evidence, old-generation invalidation, exact ZIP metadata, and archive validation. Do not query SQLite or invoke private workflow helpers.

- [ ] **Step 2: Strengthen the no-special-case scan**

Keep scanning all production Python/TypeScript/rules/default inputs for fixture-unique village markers. The generic demand-side mapping catalog remains the only exact allow-listed location for approved mapping terms.

- [ ] **Step 3: Run focused public E2E RED then GREEN**

Run:

```powershell
pytest tests/integration/test_figma_plugin_new_project_delivery_e2e.py tests/integration/test_village_new_project_regression.py --basetemp=.pt-capability-review-e2e -q
```

Expected after Tasks 1–5: all pass; before the complete lifecycle implementation, at least the complex-selection E2E must fail at a public assertion.

- [ ] **Step 4: Rebuild the production plugin and enforce dist parity**

Use the existing production build with explicit build-time origin, plugin ID, and token from the approved local test configuration. Do not print the token. Extend package checks so `dist` must contain disposition schema tokens, grouped-review labels, reviewable blocked handling, and existing regeneration/source-preview/locate tokens.

Run:

```powershell
pnpm --dir apps/figma-plugin build
node node_modules/vitest/vitest.mjs run apps/figma-plugin/scripts/check-package.test.ts
```

Expected: production build succeeds and package parity is 5/5.

- [ ] **Step 5: Run all verification gates**

Run with a short Windows basetemp:

```powershell
pytest --basetemp=.pt-capability-review-full -q
ruff check .
mypy src
node node_modules/vitest/vitest.mjs run apps/figma-plugin/src apps/web-console/src
node node_modules/typescript/bin/tsc -p apps/figma-plugin/tsconfig.json --noEmit
node node_modules/typescript/bin/tsc -p apps/web-console/tsconfig.json --noEmit
pnpm --dir apps/web-console build
git diff --check
```

Expected: no failures; only documented pre-existing skips/warnings are permitted.

- [ ] **Step 6: Rebuild the local importable plugin and perform manual acceptance**

Rebuild `.local-acceptance/plugin` against `http://localhost:8765`, reimport its `manifest.json`, select the authorized real complex frame, and verify:

- no repeated warning wall;
- green/yellow/red counts render;
- categories expand and locate works;
- safe defaults produce a candidate when no red item remains;
- approve/download remains locked while red exists;
- generated ZIP opens through the existing FairyGUI acceptance path.

Record only actually observable GUI evidence. If screenshot or coordinate APIs remain unavailable, state that limitation and do not fabricate screenshots.

- [ ] **Step 7: Update durable memory and commit**

Update `.claude/memory/wiki.md` with final contract, verification numbers, local plugin path, and acceptance status. Append to learnings only for a genuinely reusable new pitfall.

Commit: `fix: align Writer capability review flow`

---

## Plan Self-Review

- Spec coverage: all design sections map to Tasks 1–6; Project Binding and business-special-case exclusions are explicit.
- Public seams: selection manifest, Writer REST lifecycle, plugin review UI, and final archive are all exercised without database-side assertions in E2E.
- Type consistency: Python and TypeScript share the same four levels, ten reasons, adjustment strategy vocabulary, `artifactReady`, and disposition collection.
- Safety: unknown contracts fail closed; blocked review is separated from operational failure; approval and download retain artifact/evidence/owner/generation gates.
- Determinism: disposition identity/order and ZIP byte parity are tested.
- Placeholder scan: no TBD/TODO/future implementation placeholders remain.
