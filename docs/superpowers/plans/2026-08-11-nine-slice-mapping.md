# Nine-Slice Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve deterministic nine-slice metadata from Figma annotations or existing FairyGUI image resources through generated projects used by Unity.

**Architecture:** The plugin parses `@9s(left,top,right,bottom)` into bounded node properties and removes the annotation from the exported display name. Python indexes existing `scale9grid`, resolves source priority per asset, validates insets against actual raster dimensions, and writes FairyGUI’s `x,y,width,height` form only on image resources.

**Tech Stack:** TypeScript, Vitest, Python 3.12, Pydantic, Pillow, lxml, pytest, Ruff, mypy.

## Global Constraints

- Existing FairyGUI `scale9grid` overrides Figma annotations.
- Never infer nine-slice boundaries from pixels, colors, coordinates, or screen-specific names.
- Invalid metadata degrades only the affected image to ordinary scaling.
- Version-1 manifests without nine-slice metadata remain compatible.
- Only raster PNG/WebP resources can receive newly inferred nine-slice metadata.

---

### Task 1: Parse and serialize Figma annotations

**Files:**
- Create: `apps/figma-plugin/src/nine-slice.ts`
- Create: `apps/figma-plugin/src/nine-slice.test.ts`
- Modify: `apps/figma-plugin/src/selection.ts`
- Modify: `apps/figma-plugin/src/selection.test.ts`

**Interfaces:**
- Produces: `parseNineSliceAnnotation(name, bounds): { displayName, insets, diagnostic }`.

- [ ] Write table-driven red tests for valid, absent, malformed, repeated, decimal, negative, and out-of-bounds annotations.
- [ ] Run `vitest run src/nine-slice.test.ts` and confirm module-not-found RED.
- [ ] Implement anchored annotation parsing with four non-negative integers and strict `left+right<width`, `top+bottom<height` validation.
- [ ] Add selection integration tests proving `properties.nine_slice_insets` is serialized, the display name is cleaned, and invalid annotations emit stable safe warnings.
- [ ] Run `vitest run src/nine-slice.test.ts src/selection.test.ts` and confirm GREEN.

### Task 2: Index existing FairyGUI scale9grid

**Files:**
- Modify: `src/figma_to_fgui/models.py`
- Modify: `src/figma_to_fgui/project_index.py`
- Modify: `tests/unit/test_project_index.py`

**Interfaces:**
- Produces: `NineSliceGrid(x, y, width, height)` and optional `ProjectResource.scale9grid`.

- [ ] Add red tests for valid image metadata in both supported project layouts and invalid values.
- [ ] Run the focused pytest target and confirm RED.
- [ ] Parse exactly four comma-separated non-negative integers with positive width and height; invalid metadata remains `None`.
- [ ] Run focused project-index tests and confirm GREEN.

### Task 3: Resolve priority and write package metadata

**Files:**
- Modify: `src/figma_to_fgui/generate.py`
- Modify: `tests/unit/test_generate.py`
- Modify: `tests/unit/test_live_selection_normalize.py`

**Interfaces:**
- Consumes: node `nine_slice_insets`, actual `SelectionAsset`, existing image elements and indexed resources.
- Produces: `scale9grid="x,y,width,height"`, `nine_slice_conflict`, and `nine_slice_out_of_bounds` diagnostics.

- [ ] Add red generation tests for new resource annotation, existing-resource priority, conflict diagnostics, out-of-bounds degradation, and unchanged ordinary images.
- [ ] Run focused generation tests and confirm RED.
- [ ] Collect one deterministic annotation per asset, read raster size through Pillow without mutating source files, convert insets to a grid, and pass the resolved grid into package registration.
- [ ] Preserve an existing image element’s valid grid; emit conflict diagnostics when it differs; add a grid only when creating a new raster resource.
- [ ] Run focused Python tests and confirm GREEN.

### Task 4: Full verification and delivery refresh

**Files:**
- Rebuild: `apps/figma-plugin/dist/*`
- Rebuild: `.local-acceptance/plugin-current-http/*`

- [ ] Run all plugin tests and `tsc --noEmit`.
- [ ] Run reproducible production build checks using the repository’s required HTTPS test configuration.
- [ ] Run `.venv\Scripts\python.exe -m pytest --basetemp=.t\nine-full -q`, Ruff, and mypy.
- [ ] Rebuild the localhost plugin, validate manifest JSON, verify `localhost:8765` returns HTTP 200, and record the bundle SHA256.
- [ ] Audit import → select → annotate/reuse → generate → FairyGUI 6.1.4 → Unity handoff, separating remaining component-mapping work from this phase.
