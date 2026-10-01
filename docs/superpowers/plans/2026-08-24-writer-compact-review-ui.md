# Writer Compact Review UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace oversized automatic-conversion cards with concise five-row groups and replace empty editable-risk previews with an honest structured summary.

**Architecture:** Keep presentation state local to `NewProjectReviewPanel`: a set of expanded reason keys controls independent groups, while evidence presence selects either the existing image comparison or a structured facts panel. The public disposition contract from the rich-text plan supplies run-specific facts; no synthetic images are generated.

**Tech Stack:** React 19, TypeScript, Testing Library, Vitest, CSS, esbuild plugin packaging.

## Global Constraints

- Do not start Project Binding or control Figma/FairyGUI.
- Do not add page-name, node-ID, or business-sample special cases.
- Default each automatic group to exactly 5 rows.
- Render names as text, preserve disposition order, and avoid horizontal scrolling at 360px.
- Show image comparison only for real `sourceNodeId`-matched evidence.
- The generated Figma plugin `dist` and `.local-acceptance/plugin` must match source before completion.

## File map

- Modify `apps/web-console/src/figma/NewProjectReviewPanel.tsx`: compact group behavior and evidence/summary branch.
- Modify `apps/web-console/src/styles.css`: single-column compact rows and structured risk summary.
- Modify `apps/web-console/src/figma/NewProjectWriterPanel.test.tsx`: public user-flow assertions.
- Regenerate `apps/figma-plugin/dist/ui.html`: checked-in embedded UI build.
- Rebuild `.local-acceptance/plugin`: the actual local plugin import directory.

---

### Task 1: Render concise independently expandable groups

**Files:**
- Modify: `apps/web-console/src/figma/NewProjectReviewPanel.tsx`
- Modify: `apps/web-console/src/styles.css`
- Test: `apps/web-console/src/figma/NewProjectWriterPanel.test.tsx`

**Interfaces:**
- Consumes: `review.dispositions` in server order.
- Produces: local `expandedAutomaticGroups: Set<ReviewDisposition["reason"]>` and accessible buttons with `aria-expanded`.

- [ ] **Step 1: Add a failing six-item group test**

```tsx
it("shows five concise rows and expands each automatic group independently", async () => {
  await reachReview(writerClient({ reviewNewProject: vi.fn().mockResolvedValue(reviewWithSixNativeTexts()) }));
  expect(screen.getAllByRole("listitem", { name: /TEXT/ })).toHaveLength(5);
  const expand = screen.getByRole("button", { name: "展开其余 1 项" });
  expect(expand).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(expand);
  expect(screen.getAllByRole("listitem", { name: /TEXT/ })).toHaveLength(6);
  expect(screen.getByRole("button", { name: "收起" })).toHaveAttribute("aria-expanded", "true");
});
```

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir apps/web-console test -- --run src/figma/NewProjectWriterPanel.test.tsx`

Expected: FAIL because automatic groups are cards without lists or expand buttons.

- [ ] **Step 3: Implement group state and five-row slicing**

```tsx
const AUTOMATIC_PREVIEW_LIMIT = 5;
const [expandedAutomaticGroups, setExpandedAutomaticGroups] = useState<Set<ReviewDisposition["reason"]>>(new Set());

const visibleItems = expandedAutomaticGroups.has(reason)
  ? items
  : items.slice(0, AUTOMATIC_PREVIEW_LIMIT);
```

Render one `<section>` per group, a heading `${reasonLabels[reason]} · ${items.length} 项`, and a `<ul>` whose rows contain only `${item.sourceName} · ${item.sourceType}`. Toggle by cloning the set; do not mutate React state in place.

- [ ] **Step 4: Replace card CSS with a narrow single-column list**

Remove the two-column grid and long-description rules. Add `.writer-automatic-group`, `.writer-automatic-items`, `.writer-automatic-item`, and `.writer-automatic-toggle`; use `min-width: 0`, wrapping text, and no fixed content width.

- [ ] **Step 5: Run focused UI tests and commit**

Run: `pnpm --dir apps/web-console test -- --run src/figma/NewProjectWriterPanel.test.tsx`

Expected: PASS; existing workflow steps remain intact.

```bash
git add apps/web-console/src/figma/NewProjectReviewPanel.tsx apps/web-console/src/styles.css apps/web-console/src/figma/NewProjectWriterPanel.test.tsx
git commit -m "feat: compact Writer automatic results"
```

### Task 2: Replace empty editable-risk previews with structured facts

**Files:**
- Modify: `apps/web-console/src/figma/NewProjectReviewPanel.tsx`
- Modify: `apps/web-console/src/styles.css`
- Test: `apps/web-console/src/figma/NewProjectWriterPanel.test.tsx`

**Interfaces:**
- Consumes: `current.details` from the completed rich-text plan and existing `evidence` matched by `sourceNodeId`.
- Produces: `EditableRiskSummary({ item }: { item: ReviewDisposition })`.

- [ ] **Step 1: Add failing structured-summary tests**

```tsx
it("shows rich-text facts instead of fake visual comparison when evidence is absent", async () => {
  await reachReview(writerClient({ reviewNewProject: vi.fn().mockResolvedValue(richTextRiskReview()) }));
  await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
  expect(screen.queryByText("Figma 原图")).not.toBeInTheDocument();
  expect(screen.queryByText("FairyGUI 结果")).not.toBeInTheDocument();
  expect(screen.getByText("2 个文本片段")) .toBeVisible();
  expect(screen.getByText(/已保留：文字内容/)).toBeVisible();
  expect(screen.getByText(/无法等价表达：逐段字号/)).toBeVisible();
});

it("keeps real raster evidence comparison", async () => {
  await reachRasterReview();
  expect(screen.getByText("Figma 原图")).toBeVisible();
  expect(screen.getByText("FairyGUI 结果")).toBeVisible();
});
```

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir apps/web-console test -- --run src/figma/NewProjectWriterPanel.test.tsx`

Expected: first test FAILS because two missing-preview frames are rendered.

- [ ] **Step 3: Implement the evidence branch**

Render `.writer-portrait-compare` only when `evidence` exists. When `current.level === "editable_risk" && !evidence`, render `EditableRiskSummary`. Blocked items without visual evidence keep a single explicit status message; failed fetch wording is reserved for an evidence URL that actually failed.

- [ ] **Step 4: Map registered fact keys to concise Chinese labels**

Use exhaustive records such as `content -> 文字内容`, `color -> 逐段颜色`, `fontSize -> 逐段字号`, `fontCandidates -> 逐段字体或字重`, `strokeColor/strokeSize -> 逐段描边`, `ubbEncoding -> 富文本编码`. Unknown keys render as escaped plain text instead of disappearing.

- [ ] **Step 5: Disable image-only actions without image evidence**

Do not render “复制到 Figma 审核区” for structured summaries. Keep “定位到图层” and allowed adjustment strategies unchanged.

- [ ] **Step 6: Run tests and commit**

Run: `pnpm --dir apps/web-console test -- --run src/figma/NewProjectWriterPanel.test.tsx src/figma/useNewProjectReviewPreviews.test.ts`

Expected: PASS for no-evidence, failed-evidence, and real-raster-evidence cases.

```bash
git add apps/web-console/src/figma/NewProjectReviewPanel.tsx apps/web-console/src/styles.css apps/web-console/src/figma/NewProjectWriterPanel.test.tsx
git commit -m "feat: summarize editable review risks"
```

### Task 3: Build both surfaces and refresh the local plugin

**Files:**
- Regenerate: `apps/figma-plugin/dist/ui.html`
- Regenerate: `apps/figma-plugin/dist/code.js` only if the plugin build changes it.
- Regenerate: `.local-acceptance/plugin/manifest.json`, `ui.html`, and `code.js` through the repository's existing local-acceptance build command.

**Interfaces:**
- Consumes: Tasks 1-2 and the rich-text public contract.
- Produces: source/build parity and the plugin directory the user imports.

- [ ] **Step 1: Run both TypeScript suites before building**

Run: `pnpm --dir apps/web-console test -- --run`

Run: `pnpm --dir apps/figma-plugin test`

Expected: both complete with zero failures.

- [ ] **Step 2: Build Web Console and plugin**

Run: `pnpm --dir apps/web-console build`

Run: `pnpm --dir apps/figma-plugin build`

Expected: TypeScript and Vite/esbuild builds exit 0; plugin build parity test passes afterward.

- [ ] **Step 3: Build directly into the local-acceptance plugin directory**

Run:

```powershell
$env:FGUI_SERVER_ORIGIN='http://localhost:8765'
$env:FIGMA_PLUGIN_ID='123456789'
$env:FGUI_PLUGIN_ACCESS_TOKEN=Get-Content -Raw '.local-acceptance/plugin-access-token.txt'
$env:FGUI_PLUGIN_DIST_DIR=(Resolve-Path '.local-acceptance/plugin').Path
pnpm --dir apps/figma-plugin build
```

Expected: `.local-acceptance/plugin/manifest.json` references `http://localhost:8765`, and the directory contains the newly built `ui.html` and `code.js`. The token value must not be printed or committed.

- [ ] **Step 4: Verify generated parity and commit**

Run: `pnpm --dir apps/figma-plugin run build:check`

Run: `git diff --check`

Expected: parity PASS and no whitespace errors.

```bash
git add apps/figma-plugin/dist
git commit -m "build: refresh Writer acceptance plugin"
```

`.local-acceptance/plugin` is intentionally local-only and remains ignored by Git.

### Task 4: Run final positive, negative, and full sandbox self-checks

**Files:**
- Modify only tests or generated artifacts required by an intentional contract change; do not weaken assertions.

**Interfaces:**
- Consumes: both implementation plans.
- Produces: completion evidence, not FairyGUI Editor GUI approval.

- [ ] **Step 1: Run focused positive and negative tests**

Run:

`python -m pytest tests/unit/test_fgui_capabilities.py tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_plan_validate.py tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_workflow.py tests/unit/test_service_contracts.py -q`

Run: `pnpm --dir apps/web-console test -- --run src/figma/NewProjectWriterPanel.test.tsx src/figma/useNewProjectReviewPreviews.test.ts`

Run: `pnpm --dir apps/figma-plugin vitest run src/project-client.test.ts`

Expected: color-only rich text is native; font/size/stroke risks are editable; closure mismatch blocks; no-evidence risk has structured UI; raster evidence retains comparison.

- [ ] **Step 2: Run full Python checks**

Run: `python -m pytest -q`

Run: `python -m ruff check src tests`

Run: `python -m mypy --strict src`

Expected: all exit 0 except already documented pytest skips/warnings.

- [ ] **Step 3: Run full frontend checks**

Run: `pnpm --dir apps/figma-plugin test`

Run: `pnpm --dir apps/figma-plugin typecheck`

Run: `pnpm --dir apps/web-console test -- --run`

Run: `pnpm --dir apps/web-console build`

Expected: all exit 0.

- [ ] **Step 4: Run project packaging and generic acceptance gates**

Run: `python -m pytest tests/integration/test_figma_plugin_new_project_writer_api.py tests/integration/test_figma_plugin_new_project_delivery_e2e.py tests/integration/test_fgui_new_project_cli.py tests/acceptance/test_new_project_writer_acceptance.py tests/acceptance/test_writer_review_workbench_demo.py -q`

Run: `pnpm --dir apps/figma-plugin run build:check`

Expected: artifact closure, ZIP integrity, and all generic regression assertions PASS in the sandbox.

- [ ] **Step 5: Final repository checks**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors; only intentional committed changes remain.
