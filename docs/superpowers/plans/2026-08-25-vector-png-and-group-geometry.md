# Vector PNG And Group Geometry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export arbitrary Figma vector artwork as transparent PNG while retaining simple native shapes as editable FairyGUI graphs and preserving nested geometry exactly once.

**Architecture:** Keep the existing `vector_asset` semantic strategy so vectors remain distinguishable from other raster fallbacks, but change its declared and exported payload to PNG. Mark its rotation as baked by serializing axis-aligned Figma bounds with zero downstream rotation; keep container hierarchy and sibling order unchanged. Validate the PNG payload dimensions against resource metadata while continuing to use node display bounds for FairyGUI layout.

**Tech Stack:** TypeScript/Vitest Figma plugin, Python/Pydantic conversion pipeline, pytest, FairyGUI Editor 6.1.4 XML.

## Global Constraints

- Do not start Project Binding.
- Do not add page-name, node-ID, or village-specific behavior.
- Simple solid rectangles, ellipses, supported strokes, and supported corner radii remain native editable graphs.
- `VECTOR`, `BOOLEAN_OPERATION`, `STAR`, `LINE`, and `POLYGON` export as transparent PNG at 1x.
- Canonical Figma sibling order and parent relationships remain unchanged.
- A baked vector PNG has zero FairyGUI rotation; its axis-aligned Figma bounds control display geometry.
- SVG payloads declared by the new plugin path are rejected by contract tests.
- Completion requires focused positive tests, focused negative tests, and the full sandbox self-check.

---

### Task 1: Declare vector assets as PNG

**Files:**
- Modify: `apps/figma-plugin/src/visual-capability.ts`
- Test: `apps/figma-plugin/src/visual-capability.test.ts`

**Interfaces:**
- Consumes: `classifyVisualNode(node, context): VisualCapability`
- Produces: `vector_asset` capabilities with `mimeType: "image/png"`

- [ ] **Step 1: Write the failing classification tests**

Change every vector-family table row to expect `vector_asset`, `image/png`, and no raster-risk reason. Add assertions that a simple rectangle and ellipse remain `native` with no resource MIME type.

- [ ] **Step 2: Run the focused test and verify red**

Run: `pnpm --dir apps/figma-plugin test -- visual-capability.test.ts`
Expected: FAIL because vector-family nodes still declare `image/svg+xml`.

- [ ] **Step 3: Implement the policy**

Return `{ strategy: "vector_asset", mimeType: "image/png", reasons: [] }` for `VECTOR_TYPES`; do not broaden `VECTOR_TYPES` and do not change `isEditableGraph`.

- [ ] **Step 4: Run the focused test and verify green**

Run: `pnpm --dir apps/figma-plugin test -- visual-capability.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add apps/figma-plugin/src/visual-capability.ts apps/figma-plugin/src/visual-capability.test.ts && git commit -m "fix: rasterize vector assets as png"`

### Task 2: Export only the manifest-declared PNG format

**Files:**
- Modify: `apps/figma-plugin/src/assets.ts`
- Test: `apps/figma-plugin/src/assets.test.ts`
- Test: `apps/figma-plugin/src/selection.test.ts`

**Interfaces:**
- Consumes: `SelectionManifest.resources[*].mime_type`
- Produces: `ExportedResource` whose MIME exactly matches the manifest and whose bytes come from `exportAsync({format: "PNG"})`

- [ ] **Step 1: Write failing export and manifest tests**

Assert vector resources are declared as `image/png`, exporter calls PNG once, output remains PNG, and a failed PNG export raises `AssetExportError` instead of silently changing formats. Assert serialized vector nodes have no children and retain `export_strategy: "vector_asset"`.

- [ ] **Step 2: Run focused tests and verify red**

Run: `pnpm --dir apps/figma-plugin test -- assets.test.ts selection.test.ts`
Expected: FAIL on old SVG declarations and fallback expectations.

- [ ] **Step 3: Remove SVG retry behavior from plugin export**

Use the declared format once; with the new policy vectors select PNG. Preserve existing compatibility typing for historical server responses, but never emit an SVG for a newly serialized vector selection.

- [ ] **Step 4: Run focused tests and verify green**

Run: `pnpm --dir apps/figma-plugin test -- assets.test.ts selection.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add apps/figma-plugin/src/assets.ts apps/figma-plugin/src/assets.test.ts apps/figma-plugin/src/selection.test.ts && git commit -m "fix: export declared vector png payloads"`

### Task 3: Bake vector rotation exactly once

**Files:**
- Modify: `apps/figma-plugin/src/selection.ts`
- Test: `apps/figma-plugin/src/selection.test.ts`
- Test: `tests/unit/test_live_selection_normalize.py`
- Test: `tests/unit/test_fgui_plan_compile.py`

**Interfaces:**
- Consumes: vector node `absoluteBoundingBox`, source `rotation`, and `export_strategy`
- Produces: vector selection node with axis-aligned display bounds, `rotation: 0`, PNG intrinsic dimensions on its resource ref, and unchanged parent/child ordering

- [ ] **Step 1: Write failing rotation and nested-group tests**

Create a rotated vector fixture inside a translated group. Assert the manifest vector has the original axis-aligned bounds and zero rotation. Normalize it and assert the resource intrinsic dimensions match decoded PNG dimensions while the node geometry still uses the manifest bounds. Compile it and assert the child position is localized once and the generated image object rotation is zero.

- [ ] **Step 2: Run focused TypeScript and Python tests and verify red**

Run: `pnpm --dir apps/figma-plugin test -- selection.test.ts`

Run: `pytest -q tests/unit/test_live_selection_normalize.py tests/unit/test_fgui_plan_compile.py`

Expected: at least the vector rotation assertion fails before implementation.

- [ ] **Step 3: Serialize baked vector geometry**

When `item.capability.strategy === "vector_asset"` and MIME is PNG, serialize rotation as `0`; for every other strategy preserve the source rotation behavior. Do not mutate the Figma node, reverse children, recalculate parent bounds, or apply a second local-coordinate conversion.

- [ ] **Step 4: Run focused tests and verify green**

Run the two commands from Step 2.
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add apps/figma-plugin/src/selection.ts apps/figma-plugin/src/selection.test.ts tests/unit/test_live_selection_normalize.py tests/unit/test_fgui_plan_compile.py && git commit -m "fix: bake vector png geometry once"`

### Task 4: Fail closed on payload and geometry mismatches

**Files:**
- Modify if required: `src/figma_to_fgui/normalize.py`
- Modify if required: `src/figma_to_fgui/fgui_asset_payloads.py`
- Test: `tests/unit/test_live_selection_normalize.py`
- Test: `tests/unit/test_fgui_asset_payloads.py`
- Test: `tests/unit/test_fgui_new_project_workflow.py`

**Interfaces:**
- Consumes: declared PNG MIME, decoded PNG dimensions, Plan resource dimensions, and source `export_strategy`
- Produces: validated PNG-only vector assets with no SVG disposition and no dimension drift

- [ ] **Step 1: Add negative tests**

Assert a vector strategy paired with SVG in a new-selection fixture is rejected by the writer workflow contract, truncated/invalid PNG is rejected, decoded dimensions differing from committed resource metadata are rejected, and a vector PNG carrying nonzero downstream rotation is rejected or normalized only at the plugin boundary.

- [ ] **Step 2: Run negative tests and verify red where coverage is missing**

Run: `pytest -q tests/unit/test_live_selection_normalize.py tests/unit/test_fgui_asset_payloads.py tests/unit/test_fgui_new_project_workflow.py`
Expected: new policy assertions expose any missing validation.

- [ ] **Step 3: Add the minimum validation needed**

Keep historical generic SVG ingestion intact where required, but reject the incompatible combination `export_strategy == "vector_asset"` plus SVG in the new writer workflow. Compare decoded PNG size with Plan resource metadata and reject mismatch before archive creation.

- [ ] **Step 4: Run negative tests and verify green**

Run the command from Step 2.
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/figma_to_fgui/normalize.py src/figma_to_fgui/fgui_asset_payloads.py tests/unit/test_live_selection_normalize.py tests/unit/test_fgui_asset_payloads.py tests/unit/test_fgui_new_project_workflow.py && git commit -m "fix: validate baked vector png contracts"`

### Task 5: Regression, documentation, and runtime handoff

**Files:**
- Modify: `wiki.md`
- Modify: `.claude/memory/*` only as directed by the repository memory rules

**Interfaces:**
- Consumes: completed policy, focused tests, full test suite, and real-selection replay fixture
- Produces: a reproducible verified build and accurate handoff status

- [ ] **Step 1: Run positive and negative focused checks**

Run the focused TypeScript and Python commands from Tasks 1–4.
Expected: all PASS.

- [ ] **Step 2: Run plugin full checks**

Run the repository-declared plugin typecheck/build/test commands discovered from `package.json` and `CLAUDE.md`.
Expected: all PASS.

- [ ] **Step 3: Run Python full self-check in the sandbox**

Run the repository-declared full pytest command with a writable temporary/cache directory inside the worktree.
Expected: all PASS; no skipped newly added vector/group cases.

- [ ] **Step 4: Replay the generic real selection fixture**

Generate and validate a ZIP from the existing real-selection fixture. Inspect archive XML to confirm one component hierarchy, canonical sibling order, simple graphs still editable, vector assets PNG, localized nested positions, and zero vector-image rotation.
Expected: archive validator PASS and no SVG payload produced by the new plugin path.

- [ ] **Step 5: Update project memory and commit**

Record the generic policy, evidence, exact commands, and the remaining boundary that only a user-opened FairyGUI Editor can visually confirm. Do not claim GUI acceptance before that evidence exists.

Run: `git add wiki.md .claude/memory docs/superpowers/plans/2026-08-25-vector-png-and-group-geometry.md && git commit -m "docs: record vector png validation evidence"`

## Self-Review

- Spec coverage: vector-family PNG policy, native editable shapes, rotation baking, nested geometry, canonical ordering, payload validation, positive/negative/full checks, and GUI evidence boundary are each assigned above.
- Placeholder scan: no deferred implementation placeholders remain; conditional backend modification is explicitly limited to validation proven missing by red tests.
- Type consistency: `vector_asset`, `image/png`, zero serialized rotation, decoded intrinsic dimensions, and display bounds use the same names throughout.
