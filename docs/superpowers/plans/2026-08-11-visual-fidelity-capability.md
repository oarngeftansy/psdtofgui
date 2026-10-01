# Visual Fidelity Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic capability matrix that preserves editable FairyGUI output when safe and rasterizes only the smallest Figma subtree required for unsupported visual semantics.

**Architecture:** A new pure TypeScript module classifies each Figma node as `native`, `vector_asset`, `image_asset`, `composite_png`, or `skip`. The existing selection DFS consumes that classification as its single resource-planning authority, serializes safe diagnostic metadata, and treats every declared resource as an atomic subtree boundary. Python remains backward-compatible and verifies the serialized strategy without trying to recreate missing raster data.

**Tech Stack:** TypeScript, Vitest, Figma Plugin API-shaped fixtures, Python 3.12, pytest, Ruff, mypy.

## Global Constraints

- No screen-name, node-name, coordinate, or “村庄升阶” special cases.
- Prefer editable native output; rasterize the smallest node whose own semantics require compositing.
- Never leak raw Figma IDs, image hashes, URLs, paths, or bytes into the manifest or warnings.
- Invisible paints and effects do not affect classification.
- Classification reason order and resource ordering must be deterministic.
- Existing version-1 selection manifests remain accepted.

---

### Task 1: Pure visual capability classifier

**Files:**
- Create: `apps/figma-plugin/src/visual-capability.ts`
- Create: `apps/figma-plugin/src/visual-capability.test.ts`

**Interfaces:**
- Consumes: a structural `VisualNode` with `type`, `fills`, `strokes`, `effects`, `blendMode`, `clipsContent`, and `children`.
- Produces: `classifyVisualNode(node, { isRoot }): VisualCapability` where `VisualCapability` contains `strategy`, `mimeType`, and stable `reasons`.

- [ ] **Step 1: Write failing table-driven tests**

Cover native text, native container, vector asset, image asset, instance, mask group, internal clip, root clip, visible gradient, invisible gradient, visible shadow/blur, invisible effect, non-normal blend mode, pass-through blend mode, multiple visible paints, combined reasons, and video.

```ts
expect(classifyVisualNode(node({ fills: [{ type: "GRADIENT_LINEAR", visible: true }] }), { isRoot: false }))
  .toEqual({ strategy: "composite_png", mimeType: "image/png", reasons: ["gradient_paint"] });
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `vitest run src/visual-capability.test.ts`

Expected: FAIL because `./visual-capability` does not exist.

- [ ] **Step 3: Implement the minimal pure classifier**

```ts
export type ExportStrategy = "native" | "vector_asset" | "image_asset" | "composite_png" | "skip";
export type RasterReason = "instance_composite" | "mask_composite" | "clip_composite" | "gradient_paint" | "visual_effect" | "blend_mode" | "multiple_paints";
export type VisualCapability = { strategy: ExportStrategy; mimeType: "image/png" | "image/svg+xml" | null; reasons: RasterReason[] };
export function classifyVisualNode(node: VisualNode, context: { isRoot: boolean }): VisualCapability;
```

Use a fixed reason list for ordering. Count only paints with `visible !== false`; treat paint types prefixed by `GRADIENT_` as unsupported; treat visible `DROP_SHADOW`, `INNER_SHADOW`, `LAYER_BLUR`, and `BACKGROUND_BLUR` as unsupported; accept only `NORMAL` and `PASS_THROUGH` blend modes.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `vitest run src/visual-capability.test.ts`

Expected: all classifier tests pass.

### Task 2: Make classification the selection planner authority

**Files:**
- Modify: `apps/figma-plugin/src/selection.ts`
- Modify: `apps/figma-plugin/src/selection.test.ts`
- Modify: `apps/figma-plugin/src/assets.test.ts`

**Interfaces:**
- Consumes: `classifyVisualNode` from Task 1.
- Produces: `properties.export_strategy`, optional `properties.raster_reasons`, deterministic resource declarations, lookup, and safe `visual_rasterized` warnings.

- [ ] **Step 1: Add failing integration tests**

Add tests proving a gradient child becomes one PNG while its parent and sibling remain editable, a composited parent prunes descendants, an ordinary root clip remains editable, reasons are deterministic, and resource lookup points at the classified node.

```ts
expect(manifest.top_level_nodes[0]?.children.map((child) => child.resource_keys)).toEqual([["asset-1"], []]);
expect(manifest.top_level_nodes[0]?.children[0]?.properties).toMatchObject({
  export_strategy: "composite_png",
  raster_reasons: ["gradient_paint"],
});
```

- [ ] **Step 2: Run selection tests and verify RED**

Run: `vitest run src/selection.test.ts src/assets.test.ts`

Expected: new strategy metadata and gradient composite assertions fail.

- [ ] **Step 3: Replace `isOpaqueComposite` with classifier output**

Store `capability: VisualCapability` in `NodePlan`. Declare resources from `mimeType`; use the existing image reference for `image_asset`, deterministic local references for vector/composite assets, and no resource for native/skip. Serialize the strategy and reasons through `nodeProperties` without adding unsanitized source data.

- [ ] **Step 4: Add one aggregate warning per rasterized node**

Use stable text and code only:

```ts
warnings.push({ code: "visual_rasterized", message: `已将不支持的视觉效果合成为图片：${capability.reasons.join(",")}` });
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `vitest run src/visual-capability.test.ts src/selection.test.ts src/assets.test.ts`

Expected: all focused plugin tests pass.

### Task 3: Preserve manifest compatibility through normalization and generation

**Files:**
- Modify: `tests/unit/test_live_selection_normalize.py`
- Modify only if the red test requires it: `src/figma_to_fgui/models.py`
- Modify only if the red test requires it: `src/figma_to_fgui/normalize.py`
- Modify: `tests/unit/test_generate.py`
- Modify only if the red test requires it: `src/figma_to_fgui/generate.py`

**Interfaces:**
- Consumes: version-1 node properties containing export metadata plus an optional PNG resource.
- Produces: normalized nodes that retain safe strategy metadata and generated XML that renders one resource object without rendering pruned descendants.

- [ ] **Step 1: Add failing normalization and generation regression tests**

Build a manifest fixture with a `composite_png` child and assert the normalized property values survive. Assert the XML contains exactly one image object for that node and no duplicate descendant object.

- [ ] **Step 2: Run focused Python tests and verify RED or compatibility**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_live_selection_normalize.py tests/unit/test_generate.py --basetemp=.test-tmp/capability-python-red -q`

Expected: either the new assertions fail (then implement the minimum compatibility change) or pass, proving the current generic property pipeline already supports the schema.

- [ ] **Step 3: Implement only required compatibility changes**

Do not duplicate the TypeScript capability matrix in Python. Preserve `export_strategy` and `raster_reasons` as bounded metadata and keep the existing resource-backed atomic rendering rule.

- [ ] **Step 4: Run focused Python tests and verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_live_selection_normalize.py tests/unit/test_generate.py --basetemp=.test-tmp/capability-python-green -q`

Expected: all focused Python tests pass.

### Task 4: Full verification and plugin delivery refresh

**Files:**
- Modify generated plugin bundle only through the repository’s existing build/deployment command.
- Verify: `.local-acceptance/plugin-current-http/manifest.json`

**Interfaces:**
- Consumes: completed Tasks 1–3.
- Produces: a locally importable plugin build and fresh verification evidence.

- [ ] **Step 1: Run the complete plugin suite**

Run: `vitest run`

Expected: zero failed tests.

- [ ] **Step 2: Run TypeScript type checking and plugin build**

Run from `apps/figma-plugin`: `pnpm typecheck`, then `pnpm build`, then `pnpm build:check`.

Expected: both commands exit 0.

- [ ] **Step 3: Run the complete Python suite and static checks**

Run:

```powershell
.venv\Scripts\python.exe -m pytest --basetemp=.test-tmp\capability-full -q
.venv\Scripts\python.exe -m ruff check src tests
.venv\Scripts\python.exe -m mypy src
```

Expected: zero failures/errors.

- [ ] **Step 4: Validate the delivery manifest and bundle**

Parse the local manifest as JSON, confirm `main` and `ui` targets exist, and compare the delivered bundle hash with the freshly built bundle.

- [ ] **Step 5: Perform the product-flow pain audit**

Walk the real job: import plugin → select a mixed-capability frame → preflight → upload → generate → download/open in FairyGUI. Record immediate residual risks separately from later roadmap items.
