# Writer Review Workbench Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained animated 640×800 portrait HTML demo of the automatic-conversion, illustrated review, and final-download workflow.

**Architecture:** One standalone HTML file owns demo-only markup, CSS illustrations, and a small vanilla-JavaScript state machine; it neither imports production code nor calls Figma or the server. A focused pytest gate uses real local Edge to verify viewport closure, one-scroll layout, large aligned evidence, step and item transitions, acknowledgement, engineering-detail expansion, and the copy-to-Figma review-frame animation.

**Tech Stack:** HTML5, CSS, vanilla JavaScript, pytest, Playwright Core, Microsoft Edge.

## Global Constraints

- The demo viewport is exactly 640×800 CSS pixels and optimized for portrait usage.
- The workflow is three separate screens: `自动转换` → `建议审核` → `确认下载`.
- There is no permanent review directory or four-tab evidence matrix.
- Each review item includes visible contextual Figma and FairyGUI illustrations.
- Each step has at most one vertical scroll surface; there is no nested scrollbar or footer overlap.
- Copying to Figma is an explicit animated demonstration of a separate frame placed to the right of the selected frame.
- Motion respects `prefers-reduced-motion`.
- The file is self-contained and uses no external assets, URLs, or service calls.
- No Project Binding, existing-project behavior, or business-specific conversion rule is added.

---

### Task 1: Portrait step workflow and interaction gate

**Files:**
- Modify: `docs/demos/writer-review-workbench.html`
- Modify: `tests/acceptance/test_writer_review_workbench_demo.py`
- Update: `docs/demos/writer-review-workbench-preview.png`

**Interfaces:**
- Produces: `.plugin-window[data-step]`, three `[data-go-step]` controls, `.step-scroll`, `.comparison-grid`, `.preview-canvas`, `[data-review-nav]`, `#acknowledge`, `#copy-to-figma`, `.figma-canvas-demo`, `#engineering-details`, and `.action-footer`.
- Consumes: only local browser capabilities; no production API, token, Figma mutation, or network access.

- [ ] **Step 1: Replace the acceptance contract and observe RED**

Update the static gate to require `width=640`, the three screen labels, contextual comparison copy, two review records, and the copy-to-Figma control. Update the Edge script to launch at 640×800 and collect root, scroll, footer, preview, step, item, acknowledgement, detail, and animation state.

```python
assert measurement["root"]["width"] == 640
assert measurement["root"]["height"] == 800
assert measurement["scrollCount"] == 1
assert all(item["width"] >= 220 for item in measurement["previews"])
assert states["reviewItemAfterNext"] == "2 / 2"
assert states["copyState"] == "copied"
```

Run: `python -m pytest tests/acceptance/test_writer_review_workbench_demo.py -q`

Expected: FAIL because the existing artifact is 520×720, scenario/tab based, and lacks the new step and copy state.

- [ ] **Step 2: Implement the three-screen portrait shell**

Replace the old scenario dashboard with a fixed shell and exactly one active screen:

```css
.plugin-window {
  width: 640px;
  height: 800px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  overflow: hidden;
}
.step-screen[hidden] { display: none; }
.step-scroll { min-height: 0; overflow-y: auto; overflow-x: hidden; }
```

Use a single `state` object with `step`, `reviewIndex`, `acknowledged`, `detailsOpen`, and `copyState`. `render()` must update step visibility, header progress, footer actions, and disabled states without creating additional scroll containers.

- [ ] **Step 3: Implement illustrated automatic and review content**

Render the automatic-conversion screen first with illustrated before/after rows and conversion/editability labels. Render review items one at a time with two equal portrait `.preview-canvas` elements, a persistent target outline, surrounding context, reason, impact, server-declared choice, item counter, and previous/next controls.

```javascript
const reviews = [
  { id: "visual-style", title: "视觉样式保真", reason: "复杂视觉已保真合成", impact: "外观保持一致，局部样式不可单独编辑" },
  { id: "instance-boundary", title: "实例边界确认", reason: "嵌套实例转换为可复用结构", impact: "请确认组件拆分边界符合后续维护方式" }
];
```

- [ ] **Step 4: Implement copy-to-Figma and final confirmation interactions**

On `#copy-to-figma`, animate a miniature selected frame and a separate `FairyGUI 待审核` frame appearing to its right, then set `data-copy-state="copied"` and visible confirmation text. Toggle `#engineering-details` from the final screen, and enable download only after the review acknowledgement is complete.

```javascript
copyButton.addEventListener("click", () => {
  state.copyState = "copying";
  render();
  window.setTimeout(() => { state.copyState = "copied"; render(); }, 240);
});
```

- [ ] **Step 5: Run real Edge geometry and interaction verification**

Run: `python -m pytest tests/acceptance/test_writer_review_workbench_demo.py -q`

Expected: PASS. The Edge gate must prove exact 640×800 geometry, one active scroll surface, no horizontal overflow, footer separation, equal preview dimensions/alignment, step transitions, review navigation, acknowledgement, detail expansion, and copied animation state.

- [ ] **Step 6: Regenerate and inspect the preview**

Use the same Playwright/Edge runtime to save `docs/demos/writer-review-workbench-preview.png` after navigating to the first recommended-review item. Inspect it for portrait-image legibility, clear target outlines, hierarchy, clipping, duplicate navigation, nested scrollbars, and footer overlap.

Expected: the evidence pair is the dominant visual area; no review directory or package tab competes with it.

- [ ] **Step 7: Run hygiene and commit**

Run: `python -m pytest tests/acceptance/test_writer_review_workbench_demo.py -q`

Run: `python -m ruff check tests/acceptance/test_writer_review_workbench_demo.py`

Run: `git diff --check`

Expected: all checks pass.

Commit: `feat: demonstrate portrait Writer review flow`

---

### Task 2: Production portrait Writer workflow

**Files:**
- Modify: `apps/web-console/src/figma/NewProjectWriterPanel.tsx`
- Modify: `apps/web-console/src/figma/NewProjectReviewPanel.tsx`
- Modify: `apps/web-console/src/figma/NewProjectWriterPanel.test.tsx`
- Modify: `apps/web-console/src/styles.css`
- Modify: `apps/figma-plugin/src/code.ts`
- Modify: `apps/figma-plugin/src/bridge.test.ts`

**Interfaces:**
- Produces: a 640×800 plugin window and a three-step production flow using `automatic`, `review`, and `confirm` as presentation steps while preserving the existing candidate lifecycle state machine.
- Consumes: the existing strict `NewProjectReview`, preview-object URLs, warning acknowledgement, adjustment, approval, rejection, and selection invalidation contracts.

- [ ] **Step 1: Add failing production UI and viewport tests**

Require the plugin runtime to call `showUI` with `{ width: 640, height: 800 }`. Replace tab-centric UI assertions with user-facing behavior: automatic results render first, `查看建议审核` opens one illustrated review item, next/previous changes the counter, acknowledgement enables `确认审核结果`, and final confirmation exposes engineering details and download.

Run: `node node_modules/vitest/vitest.mjs run src/figma/NewProjectWriterPanel.test.tsx` from `apps/web-console` and `node node_modules/vitest/vitest.mjs run src/bridge.test.ts` from `apps/figma-plugin`.

Expected: FAIL because production remains 360×680 and tab-based.

- [ ] **Step 2: Implement presentation-step state without changing lifecycle semantics**

Keep `WriterUiState` authoritative for asynchronous work and add a separate `WriterPresentationStep = "automatic" | "review" | "confirm"`. Enter `automatic` when review data becomes ready; invalidate/reset it with the existing candidate reset paths. Render selection/setup only before candidate creation, then render exactly one step screen with a stable footer.

- [ ] **Step 3: Replace review tabs with illustrated one-item review**

`NewProjectReviewPanel` receives `step`, `reviewIndex`, and navigation callbacks. It pairs available source/generated image evidence; when a disposition lacks a direct image pair, it renders an honest structured context illustration and labels it as such rather than claiming a screenshot. Recommended and blocked items always have visible evidence, reason, impact, locate, and server-declared strategies. Package/components/checks move into final `工程详情`.

- [ ] **Step 4: Apply the 640×800 one-scroll layout**

Update `.writer-shell` to fixed grid rows, render one `.writer-step-scroll`, reserve the footer row, and remove the permanent disposition directory and four-tab matrix. The evidence pair must be equal-width portrait cards with a minimum 220px width at 640px.

- [ ] **Step 5: Verify focused production behavior**

Run the web-console Writer suite, plugin bridge suite, both TypeScript checks, and Web build. Expected: all pass with the old candidate lifecycle/security tests retained.

---

### Task 3: Explicit Figma review-area creation

**Files:**
- Modify: `apps/figma-plugin/src/contracts.ts`
- Modify: `apps/figma-plugin/src/contracts.test.ts`
- Modify: `apps/figma-plugin/src/code.ts`
- Modify: `apps/figma-plugin/src/bridge.test.ts`
- Modify: `apps/web-console/src/figma/NewProjectWriterPanel.tsx`
- Modify: `apps/web-console/src/figma/NewProjectWriterPanel.test.tsx`

**Interfaces:**
- Produces: strict UI message `{ type: "create-review-area"; attempt: string; nodeId: string; previewBytes: Uint8Array; previewWidth: number; previewHeight: number }` and main-thread result `{ type: "review-area-created"; attempt: string }` or existing safe `selection-error`.
- Consumes: the source node identity already present on review dispositions, the authenticated generated preview Blob already loaded by the UI, and the current selection snapshot. The operation copies the source node and the exact generated preview into a separate top-level `FairyGUI 待审核` frame; it never fabricates generated evidence.

- [ ] **Step 1: Add failing strict-contract and bridge tests**

Reject missing/oversized IDs and attempts, non-`Uint8Array` payloads, invalid PNG, invalid dimensions, and preview bytes over the explicit cap. Prove the bridge creates one top-level frame to the right of the selected bounds, clones only the requested source node, places the exact generated PNG beside it, never reparents or mutates the original, reuses the named review frame on repeat, and reports a safe failure for missing nodes or unsupported clones.

- [ ] **Step 2: Implement the bounded bridge operation**

Add the closed message variants. Resolve the node by ID, validate finite positive bounds and clone support, validate the bounded PNG payload, create/reuse `FairyGUI 待审核`, position it at `selectedBounds.right + 160`, append the source clone and a generated-preview rectangle using `createImage`, and scroll to the new frame only after successful construction. On partial failure, remove only newly created review objects and return a stable error.

- [ ] **Step 3: Wire the production review action**

Read the current authenticated generated preview Blob with a bounded allocation, send its bytes with the illustrated review item's source node ID, show `正在创建…`, and only show `已放到当前画板右侧` after the matching result attempt. Ignore stale results after item/candidate/selection changes.

- [ ] **Step 4: Verify and commit the production baseline**

Run focused Web/plugin tests, all Web/plugin tests, TypeScript, production build, plugin packaging parity, Ruff/diff checks as applicable. Rebuild tracked plugin `dist` with the existing local configuration. Commit the production implementation and push it to a verified GitHub remote before any cleanup begins.

---

## Plan Self-Review

- Spec coverage: Task 1 covers the visual prototype; Task 2 applies the portrait workflow and honest illustrated evidence to production; Task 3 adds the explicit, non-mutating Figma review-area operation and the pre-cleanup remote baseline.
- Placeholder scan: no deferred implementation or ambiguous test step remains.
- Interface consistency: every selector asserted by the browser gate is produced by Task 1, while the strict review-area message/result pair is defined once in Task 3 and consumed by both production surfaces.
