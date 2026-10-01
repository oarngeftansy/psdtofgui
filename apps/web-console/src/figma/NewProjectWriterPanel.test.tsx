import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { WorkflowError, type NewProjectCandidate, type NewProjectReview } from "../../../figma-plugin/src/project-client";
import type { SelectionManifest } from "../../../figma-plugin/src/selection";
import { NewProjectReviewPanel } from "./NewProjectReviewPanel";
import { NewProjectWriterPanel } from "./NewProjectWriterPanel";

const manifest: SelectionManifest = { version: 1, display_name: "Writer", top_level_nodes: [{ id: "node-1", name: "Screen", type: "FRAME", bounds: { x: 0, y: 0, width: 100, height: 80 }, children: [], resource_keys: [] }], resources: [], warnings: [] };
const candidate = (generation = 1, status: NewProjectCandidate["status"] = "awaiting_review", buildId = String(generation).repeat(32)): NewProjectCandidate => ({ buildId, generation, status, stage: status, progress: 100, downloadName: "Writer-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, artifactReady: true, diagnostics: [] });
const review = (generation = 1, buildId = String(generation).repeat(32)): NewProjectReview => ({
  version: 1, buildId, generation,
  dispositions: [
    { version: 1, id: "native", sourceNodeId: "node-native", sourceName: "标题", sourceType: "TEXT", level: "native", reason: "native_text", allowedStrategies: [], visualImpact: "unchanged", editabilityImpact: "unchanged", componentImpact: "unchanged", blocksApproval: false, details: null },
    { version: 1, id: "raster", sourceNodeId: "node-raster", sourceName: "复杂阴影", sourceType: "FRAME", level: "raster_preserved", reason: "visual_effect", defaultStrategy: "rasterize-subtree", allowedStrategies: ["rasterize-subtree"], visualImpact: "visual_preserved", editabilityImpact: "subtree_not_editable", componentImpact: "unchanged", blocksApproval: false, details: null },
    { version: 1, id: "risk", sourceNodeId: "node-risk", sourceName: "富文本", sourceType: "TEXT", level: "editable_risk", reason: "rich_text_runs", defaultStrategy: "rasterize-subtree", allowedStrategies: ["rasterize-subtree", "preserve-editable"], visualImpact: "visual_preserved", editabilityImpact: "text_not_editable", componentImpact: "unchanged", blocksApproval: false, details: { runCount: 2, preservedProperties: ["content"], unsupportedProperties: ["fontCandidates", "fontSize"] } },
  ],
  imageReviews: [{ resourceId: "asset", sourceNodeId: "node-raster", label: "复杂阴影", evidenceKind: "source-image", sourcePreviewUrl: `/v1/figma/selections/${"a".repeat(32)}/resources/asset`, generatedAssetUrl: `/v1/new-fgui-projects/${buildId}/previews/resources/asset`, width: 100, height: 80, nineSlice: false, cropBoundsMatch: true, transparencyPreserved: true }],
  componentReviews: [{ componentId: "screen", label: "Screen", evidenceKind: "structured-summary", objectCount: 4, textCount: 1, resourceRefs: 1, componentRefs: 0, hierarchyValid: true, geometryValid: true, textValid: true }],
  packageReview: { packageName: "Generated", fairyguiVersion: "6.1.4", publishTarget: "unity", componentsAdded: 1, resourcesAdded: 1, componentNames: ["Screen"], resourceNames: ["Hero"], resourceClosureValid: true, namingConflicts: [], integrityValid: true },
  checks: [{ id: "review:0123456789abcdef", severity: "WARNING", message: "请确认布局", issueId: "review:0123456789abcdef", issueKind: "raster-fallback", uirNodeId: "uir-node-risk", sourceNodeId: "node-risk", actionable: true, allowedStrategies: ["preserve-editable"] }],
  warningIds: ["review:0123456789abcdef"], approvable: true,
});

function nativeDisposition(
  id: string,
  sourceName: string,
  sourceType: "TEXT" | "RECTANGLE",
  reason: "native_text" | "native_shape",
): NewProjectReview["dispositions"][number] {
  return {
    version: 1,
    id,
    sourceNodeId: `node-${id}`,
    sourceName,
    sourceType,
    level: "native",
    reason,
    allowedStrategies: [],
    visualImpact: "unchanged",
    editabilityImpact: "unchanged",
    componentImpact: "unchanged",
    blocksApproval: false,
    details: null,
  };
}

function reviewWithLongAutomaticGroups(): NewProjectReview {
  const expanded = review();
  expanded.dispositions = [
    ...Array.from({ length: 7 }, (_, index) => nativeDisposition(`text-${index + 1}`, `文字 ${index + 1}`, "TEXT", "native_text")),
    ...Array.from({ length: 6 }, (_, index) => nativeDisposition(`shape-${index + 1}`, `图形 ${index + 1}`, "RECTANGLE", "native_shape")),
    ...expanded.dispositions.filter((item) => item.level !== "native"),
  ];
  return expanded;
}

function automaticReviewPanel(review: NewProjectReview) {
  return <NewProjectReviewPanel
    review={review}
    step="automatic"
    reviewIndex={0}
    onReviewIndexChange={vi.fn()}
    warningAcknowledged={false}
    onWarningAcknowledged={vi.fn()}
    onLocate={vi.fn()}
    onAdjust={vi.fn()}
    onCopyReviewArea={vi.fn()}
  />;
}

function reviewPanel(review: NewProjectReview, previewObjects: Readonly<Record<string, string>> = {}) {
  return <NewProjectReviewPanel
    review={review}
    step="review"
    reviewIndex={0}
    onReviewIndexChange={vi.fn()}
    warningAcknowledged={false}
    onWarningAcknowledged={vi.fn()}
    onLocate={vi.fn()}
    onAdjust={vi.fn()}
    onCopyReviewArea={vi.fn()}
    previewObjects={previewObjects}
  />;
}

function sendPreflight(sendable = true) {
  window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-preflight", preflight: { manifest: sendable ? manifest : null, nodeCount: sendable ? 1 : 0, assetCount: 0, estimatedBytes: 0, warnings: sendable ? [] : [{ code: "selection_empty", message: "请选择图层" }], sendable } } } }));
}

function writerClient(overrides: Record<string, unknown> = {}) {
  return {
    createNewProjectCandidate: vi.fn().mockResolvedValue({ selection: { version: 1, selection_id: "a".repeat(32), display_name: "Writer", top_level_summaries: [], preview_urls: [], warnings: [] }, candidate: candidate() }),
    reviewNewProject: vi.fn().mockResolvedValue(review()),
    adjustNewProject: vi.fn().mockResolvedValue(candidate(1, "adjusting")),
    regenerateNewProject: vi.fn().mockResolvedValue(candidate(2, "awaiting_review")),
    approveNewProject: vi.fn().mockResolvedValue(candidate(1, "approved")),
    rejectNewProject: vi.fn().mockResolvedValue(candidate(1, "rejected")),
    downloadNewProject: vi.fn().mockResolvedValue({ blob: new Blob(["zip"]), downloadName: "Writer-FairyGUI.zip" }),
    newProjectPreview: vi.fn().mockResolvedValue(new Blob(["image"], { type: "image/png" })),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}

function previewPng(width = 100, height = 80): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82]);
  new DataView(bytes.buffer).setUint32(16, width);
  new DataView(bytes.buffer).setUint32(20, height);
  return bytes;
}

function changeSelection() {
  window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-changed", preflight: { manifest, nodeCount: 1, assetCount: 0, estimatedBytes: 0, warnings: [], sendable: true } } } }));
}

async function reachReview(client = writerClient(), postToFigma = vi.fn()) {
  render(<NewProjectWriterPanel client={client as never} postToFigma={postToFigma} />);
  sendPreflight();
  await userEvent.type(screen.getByLabelText("工程名称"), "Writer");
  await userEvent.click(screen.getByRole("button", { name: "生成候选工程" }));
  const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt;
  window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-export", attempt, manifest, resources: [] } } }));
  await screen.findByRole("heading", { name: "先看已经处理好的内容" });
  return { client, postToFigma };
}

describe("NewProjectWriterPanel", () => {
  it("renders registered rich-text details without fabricating a preview", () => {
    const detailedReview = review();
    detailedReview.dispositions = [{
      ...detailedReview.dispositions[2]!,
      details: {
        runCount: 3,
        preservedProperties: ["content", "color", "fontSize"],
        unsupportedProperties: ["fontCandidates", "strokeColor", "paragraphAlignment", "ubbEncoding", "unknown-safe-key"],
      },
    }];
    detailedReview.imageReviews = [];
    detailedReview.checks = [];
    detailedReview.warningIds = [];

    render(reviewPanel(detailedReview));

    expect(screen.getByText("3 个文本片段")).toBeVisible();
    expect(screen.getByText("已保留：文字内容、逐段颜色、逐段字号")).toBeVisible();
    expect(screen.getByText("无法等价表达：逐段字体或字重、逐段描边、逐段对齐、富文本编码、unknown-safe-key")).toBeVisible();
    expect(screen.getByText(/文字仍保持可编辑/)).toBeVisible();
    expect(screen.getByText(/栅格化为可选操作/)).toBeVisible();
    expect(screen.queryByText("Figma 原图")).not.toBeInTheDocument();
    expect(screen.queryByText("FairyGUI 结果")).not.toBeInTheDocument();
    expect(screen.queryByText("此项没有真实预览")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制到 Figma 审核区" })).not.toBeInTheDocument();
  });

  it("groups identical editable text risks into one confirmation card", () => {
    const groupedReview = review();
    const risk = groupedReview.dispositions[2]!;
    groupedReview.dispositions = [
      risk,
      { ...risk, id: "risk-2", sourceNodeId: "node-risk-2", sourceName: "副标题" },
      { ...risk, id: "risk-3", sourceNodeId: "node-risk-3", sourceName: "说明文字" },
      groupedReview.dispositions[1]!,
    ];
    groupedReview.checks = [];
    groupedReview.warningIds = [];

    render(reviewPanel(groupedReview));

    expect(screen.getByText("1 / 1")).toBeVisible();
    expect(screen.getByRole("heading", { name: "3 个同类文本图层" })).toBeVisible();
    expect(screen.getByText("统一处理 · 3 项")).toBeVisible();
    expect(screen.getByText("富文本、副标题、说明文字")).toBeVisible();
    expect(screen.getByText(/只需确认一次/)).toBeVisible();
    expect(screen.getByRole("button", { name: "定位首个图层" })).toBeVisible();
  });

  it("renders an honest legacy rich-text strategy when details are absent", () => {
    const legacyReview = review();
    legacyReview.dispositions = [{
      ...legacyReview.dispositions[2]!,
      level: "raster_preserved",
      details: null,
    }];
    legacyReview.imageReviews = [];
    legacyReview.checks = [];
    legacyReview.warningIds = [];

    render(reviewPanel(legacyReview));

    expect(screen.getByText(/历史候选未提供文本片段明细/)).toBeVisible();
    expect(screen.getByText(/已按明确的栅格化策略处理/)).toBeVisible();
    expect(screen.queryByText(/个文本片段/)).not.toBeInTheDocument();
    expect(screen.queryByText("Figma 原图")).not.toBeInTheDocument();
  });

  it("shows one explicit status panel for a blocked item without image evidence", () => {
    const blockedReview = review();
    blockedReview.dispositions = [{
      version: 1, id: "blocked", sourceNodeId: "node-instance", sourceName: "按钮实例", sourceType: "INSTANCE", level: "blocked", reason: "component_definition_missing", allowedStrategies: [], visualImpact: "may_differ", editabilityImpact: "unchanged", componentImpact: "instance_not_reusable", blocksApproval: true, details: null,
    }];
    blockedReview.imageReviews = [];
    blockedReview.checks = [];
    blockedReview.warningIds = [];

    render(reviewPanel(blockedReview));

    expect(screen.getByRole("status")).toHaveTextContent("没有 sourceNodeId 匹配的图像证据");
    expect(screen.queryByText("Figma 原图")).not.toBeInTheDocument();
    expect(screen.queryByText("FairyGUI 结果")).not.toBeInTheDocument();
    expect(screen.queryByText("此项没有真实预览")).not.toBeInTheDocument();
  });

  it("keeps the matched two-image comparison and copy action for unverified raster evidence", () => {
    const rasterReview = review();
    rasterReview.dispositions = [rasterReview.dispositions[1]!];
    rasterReview.checks = [];
    rasterReview.warningIds = [];
    rasterReview.imageReviews = [{ ...rasterReview.imageReviews[0]!, cropBoundsMatch: false }];
    const rasterEvidence = rasterReview.imageReviews[0]!;

    render(reviewPanel(rasterReview, {
      [rasterEvidence.sourcePreviewUrl!]: "blob:figma",
      [rasterEvidence.generatedAssetUrl]: "blob:fairygui",
    }));

    expect(screen.getByRole("img", { name: "复杂阴影 Figma 原图" })).toHaveAttribute("src", "blob:figma");
    expect(screen.getByRole("img", { name: "复杂阴影 FairyGUI 结果" })).toHaveAttribute("src", "blob:fairygui");
    expect(screen.getByRole("button", { name: "复制到 Figma 审核区" })).toBeEnabled();
  });

  it("does not render the image-only copy action without a generated image", () => {
    const noEvidenceReview = review();
    noEvidenceReview.dispositions = [noEvidenceReview.dispositions[2]!];
    noEvidenceReview.imageReviews = [{ ...noEvidenceReview.imageReviews[0]!, sourceNodeId: "node-other" }];
    noEvidenceReview.checks = [];
    noEvidenceReview.warningIds = [];

    render(reviewPanel(noEvidenceReview, { [noEvidenceReview.imageReviews[0]!.generatedAssetUrl]: "blob:unmatched" }));

    expect(screen.getByRole("button", { name: "定位到图层" })).toBeVisible();
    expect(screen.queryByText("Figma 原图")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制到 Figma 审核区" })).not.toBeInTheDocument();
  });

  it("uses one bounded step scroll surface with a separate action row", async () => {
    const { readFileSync } = await vi.importActual<{ readFileSync(path: string, encoding: string): string }>("node:fs");
    const writerCss = readFileSync("src/styles.css", "utf8");
    expect(writerCss).toMatch(/\.writer-shell\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\) auto/s);
    expect(writerCss).toMatch(/\.writer-shell\s*\{[^}]*overflow:\s*hidden/s);
    expect(writerCss).toMatch(/\.writer-step-scroll\s*\{[^}]*min-height:\s*0[^}]*overflow-y:\s*auto/s);
    expect(writerCss).toMatch(/\.writer-actions\s*\{[^}]*border-top:\s*1px solid/s);
    expect(writerCss).not.toContain(".writer-tab-panel");
  });

  it("uses a responsive shell width contract for the 360px plugin", async () => {
    const { readFileSync } = await vi.importActual<{ readFileSync(path: string, encoding: string): string }>("node:fs");
    const writerCss = readFileSync("src/styles.css", "utf8");
    const shellRule = writerCss.match(/\.writer-shell\s*\{([^}]*)\}/s)?.[1] ?? "";
    expect(shellRule).toMatch(/width:\s*100%[\s\S]*max-width:\s*640px[\s\S]*min-width:\s*0/);
    expect(shellRule).not.toMatch(/(?:^|;)\s*width:\s*640px/);
  });

  it("styles only step-description paragraphs and leaves the automatic eyebrow intact", async () => {
    const { readFileSync } = await vi.importActual<{ readFileSync(path: string, encoding: string): string }>("node:fs");
    const writerCss = readFileSync("src/styles.css", "utf8");
    expect(writerCss).toContain(".writer-step-heading > h2 + p");
    expect(writerCss).not.toContain(".writer-step-heading > p:last-child");
  });

  it("moves through automatic conversion, illustrated review, and final confirmation as separate portrait steps", async () => {
    let objectUrl = 0;
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => `blob:preview-${++objectUrl}`), revokeObjectURL: vi.fn() });
    await reachReview();

    expect(screen.getByRole("heading", { name: "先看已经处理好的内容" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "文字保持可编辑 · 1 项" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "复杂阴影或效果已按画面保真 · 1 项" })).toBeVisible();
    expect(screen.getByText("标题 · TEXT")).toBeVisible();
    expect(screen.queryByRole("img", { name: "复杂阴影 Figma 原图" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));

    expect(screen.getByRole("heading", { name: "逐项确认转换结果" })).toBeVisible();
    expect(screen.queryByText("Figma 原图")).not.toBeInTheDocument();
    expect(screen.queryByText("FairyGUI 结果")).not.toBeInTheDocument();
    expect(screen.getByText(/1 \/ 1/)).toBeVisible();
    expect(screen.getByText("同一文本内存在两种或更多字符样式。", { exact: false })).toBeVisible();
    expect(screen.getByRole("button", { name: "下一项" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /已查看图示和影响/ }));
    await userEvent.click(screen.getByRole("button", { name: "确认审核结果" }));

    expect(screen.getByRole("heading", { name: "工程已经可以交付" })).toBeVisible();
    expect(screen.getByRole("button", { name: "工程详情" })).toBeVisible();
    expect(screen.getByRole("button", { name: "确认并下载 ZIP" })).toBeEnabled();
  });

  it("shows five concise rows per automatic group and expands each group independently", async () => {
    await reachReview(writerClient({ reviewNewProject: vi.fn().mockResolvedValue(reviewWithLongAutomaticGroups()) }));

    expect(screen.getByText("文字保持可编辑 · 7 项")).toBeVisible();
    expect(screen.getByText("图形样式保持可编辑 · 6 项")).toBeVisible();
    expect(screen.getByText("文字 1 · TEXT")).toBeVisible();
    expect(screen.getByText("文字 5 · TEXT")).toBeVisible();
    expect(screen.queryByText("文字 6 · TEXT")).not.toBeInTheDocument();
    expect(screen.getByText("图形 1 · RECTANGLE")).toBeVisible();
    expect(screen.getByText("图形 5 · RECTANGLE")).toBeVisible();
    expect(screen.queryByText("图形 6 · RECTANGLE")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开其余 2 项" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "展开其余 1 项" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/已转换为可编辑文本/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "展开其余 2 项" }));

    expect(screen.getByText("文字 6 · TEXT")).toBeVisible();
    expect(screen.getByText("文字 7 · TEXT")).toBeVisible();
    expect(screen.queryByText("图形 6 · RECTANGLE")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "展开其余 1 项" })).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(screen.getByRole("button", { name: "收起" }));

    expect(screen.queryByText("文字 6 · TEXT")).not.toBeInTheDocument();
    expect(screen.queryByText("图形 6 · RECTANGLE")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开其余 2 项" })).toHaveAttribute("aria-expanded", "false");
  });

  it("returns regenerated automatic groups to their default five visible rows", async () => {
    const regeneratedReview = reviewWithLongAutomaticGroups();
    regeneratedReview.generation = 2;
    regeneratedReview.buildId = "2".repeat(32);
    const client = writerClient({
      reviewNewProject: vi.fn().mockResolvedValueOnce(reviewWithLongAutomaticGroups()).mockResolvedValueOnce(regeneratedReview),
    });
    await reachReview(client);
    await userEvent.click(screen.getByRole("button", { name: "展开其余 2 项" }));
    expect(screen.getByText("文字 6 · TEXT")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    await userEvent.click(screen.getByRole("button", { name: "保留可编辑结构" }));
    await userEvent.click(screen.getByRole("button", { name: "重新生成候选" }));

    expect(await screen.findByRole("heading", { name: "先看已经处理好的内容" })).toBeVisible();
    expect(screen.queryByText("文字 6 · TEXT")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开其余 2 项" })).toHaveAttribute("aria-expanded", "false");
  });

  it("renders a regenerated build collapsed on the same rerender", async () => {
    const oldReview = reviewWithLongAutomaticGroups();
    const newReview = reviewWithLongAutomaticGroups();
    newReview.generation = 2;
    newReview.buildId = "2".repeat(32);
    const { rerender } = render(automaticReviewPanel(oldReview));

    await userEvent.click(screen.getByRole("button", { name: "展开其余 2 项" }));
    expect(screen.getByText("文字 6 · TEXT")).toBeVisible();

    rerender(automaticReviewPanel(newReview));

    expect(screen.queryByText("文字 6 · TEXT")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开其余 2 项" })).toHaveAttribute("aria-expanded", "false");
  });

  it("allows warning acknowledgement on the final step when there are no suggested review items", async () => {
    const automaticOnly = review();
    automaticOnly.dispositions = automaticOnly.dispositions.filter((item) => item.level === "native");
    const client = writerClient({ reviewNewProject: vi.fn().mockResolvedValue(automaticOnly) });
    await reachReview(client);

    await userEvent.click(screen.getByRole("button", { name: "查看最终检查" }));
    expect(screen.getByRole("button", { name: "确认并下载 ZIP" })).toBeDisabled();
    expect(screen.getByText("待完成：请确认转换警告")).toBeVisible();
    await userEvent.click(screen.getByRole("checkbox", { name: /已查看图示和影响/ }));
    expect(screen.getByRole("button", { name: "确认并下载 ZIP" })).toBeEnabled();
  });

  it("summarizes verified raster-preserved items without forcing individual review", async () => {
    const rasterOnlyReview = review();
    rasterOnlyReview.dispositions = rasterOnlyReview.dispositions.filter((item) => item.level === "native" || item.level === "raster_preserved");
    const client = writerClient({ reviewNewProject: vi.fn().mockResolvedValue(rasterOnlyReview) });
    await reachReview(client);

    expect(screen.getByText("复杂阴影或效果已按画面保真 · 1 项")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "查看最终检查" }));

    expect(screen.getByRole("heading", { name: "工程已经可以交付" })).toBeVisible();
    expect(screen.getByText("✓ 2 项自动转换完成")).toBeVisible();
    expect(screen.getByText("✓ 0 组建议审核已确认（覆盖 0 项）")).toBeVisible();
  });

  it("copies authenticated generated evidence to a separate Figma review area", async () => {
    const postToFigma = vi.fn();
    const reviewWithUnverifiedRaster = review();
    reviewWithUnverifiedRaster.imageReviews = [{ ...reviewWithUnverifiedRaster.imageReviews[0]!, cropBoundsMatch: false }];
    await reachReview(writerClient({
      reviewNewProject: vi.fn().mockResolvedValue(reviewWithUnverifiedRaster),
      newProjectPreview: vi.fn().mockResolvedValue(new Blob([previewPng().buffer as ArrayBuffer], { type: "image/png" })),
    }), postToFigma);
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    await userEvent.click(screen.getByRole("button", { name: "下一项" }));
    await userEvent.click(screen.getByRole("button", { name: "复制到 Figma 审核区" }));
    await waitFor(() => expect(postToFigma).toHaveBeenCalledWith(expect.objectContaining({
      type: "create-review-area",
      nodeId: "node-raster",
      previewBytes: expect.any(Uint8Array),
      previewWidth: 100,
      previewHeight: 80,
    })));
    const request = postToFigma.mock.calls.at(-1)?.[0];
    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "review-area-created", attempt: request.attempt } } }));
    expect(await screen.findByText("已放到当前画板右侧")).toBeVisible();
  });

  it("shows the approved single-screen inputs without legacy template/version controls", async () => {
    const postToFigma = vi.fn();
    render(<NewProjectWriterPanel client={writerClient() as never} postToFigma={postToFigma} />);
    sendPreflight();
    expect(await screen.findByText(/1 个图层/)).toBeVisible();
    expect(screen.getByText("Screen · FRAME")).toBeVisible();
    expect(screen.getByLabelText("工程名称")).toBeRequired();
    expect(screen.getByText("FairyGUI 6.1.4")).toBeVisible();
    expect(screen.getByText("新建独立工程")).toBeVisible();
    expect(screen.getByText("可选设置")).toBeVisible();
    expect(screen.queryByLabelText("FairyGUI 版本")).not.toBeInTheDocument();
    expect(screen.queryByText("1. Figma 选择")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "生成候选工程" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "更多操作" }));
    expect(screen.getByRole("menuitem", { name: "更新现有工程" })).toBeVisible();
  });

  it("rejects an invalid project name before exporting the selection", async () => {
    const postToFigma = vi.fn();
    render(<NewProjectWriterPanel client={writerClient() as never} postToFigma={postToFigma} />);
    sendPreflight();
    await userEvent.type(screen.getByLabelText("工程名称"), "Village UI");
    await userEvent.click(screen.getByRole("button", { name: "生成候选工程" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "工程名称仅支持中文、英文、数字、下划线和连字符，长度 1–64。",
    );
    expect(postToFigma).not.toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export" }));
  });

  it("shows a field-specific message and copyable diagnostic for a validation response", async () => {
    const client = writerClient({
      createNewProjectCandidate: vi.fn().mockRejectedValue(new WorkflowError("validation")),
    });
    const postToFigma = vi.fn();
    render(<NewProjectWriterPanel client={client as never} postToFigma={postToFigma} />);
    sendPreflight();
    await userEvent.type(screen.getByLabelText("工程名称"), "Writer");
    await userEvent.click(screen.getByRole("button", { name: "生成候选工程" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt;
    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-export", attempt, manifest, resources: [] } } }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "工程名称格式不正确。仅支持中文、英文、数字、下划线和连字符，长度 1–64。",
    );
    expect(screen.getByText("阶段：创建候选 · 错误码：validation")).toBeVisible();
    expect(screen.getByRole("button", { name: "复制诊断信息" })).toBeVisible();
  });

  it("reports selection-manifest rejection as a selection error instead of blaming the project name", async () => {
    const client = writerClient({
      createNewProjectCandidate: vi.fn().mockRejectedValue(new WorkflowError("selection_invalid")),
    });
    const postToFigma = vi.fn();
    render(<NewProjectWriterPanel client={client as never} postToFigma={postToFigma} />);
    sendPreflight();
    await userEvent.type(screen.getByLabelText("工程名称"), "合法工程名");
    await userEvent.click(screen.getByRole("button", { name: "生成候选工程" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt;
    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-export", attempt, manifest, resources: [] } } }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前选择的数据未通过校验，请刷新选择后重试。");
    expect(screen.queryByText(/工程名称格式不正确/)).not.toBeInTheDocument();
    expect(screen.getByText("阶段：读取选择 · 错误码：selection_invalid")).toBeVisible();
  });

  it("renders every review type, honest evidence labels, issue actions and declared strategies", async () => {
    const { postToFigma } = await reachReview();
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    expect(screen.queryByText("Figma 原图")).not.toBeInTheDocument();
    expect(screen.queryByText("FairyGUI 结果")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "定位到图层" }));
    expect(postToFigma).toHaveBeenCalledWith(expect.objectContaining({ type: "locate-node", nodeId: "node-risk", attempt: expect.any(String) }));
    expect(screen.getByRole("button", { name: "保留可编辑结构" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "栅格化子树" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /已查看图示和影响/ }));
    await userEvent.click(screen.getByRole("button", { name: "确认审核结果" }));
    await userEvent.click(screen.getByRole("button", { name: "工程详情" }));
    expect(screen.getByText(/FairyGUI 6.1.4/)).toBeVisible();
    expect(screen.getByText(/1 个组件 · 1 个资源/)).toBeVisible();
  });

  it("groups conversion results into automatic, recommended-review and blocked decisions", async () => {
    await reachReview();
    expect(screen.getByRole("heading", { name: "先看已经处理好的内容" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "文字保持可编辑 · 1 项" })).toBeVisible();
    expect(screen.getByText("标题 · TEXT")).toBeVisible();
    expect(screen.getByRole("heading", { name: "复杂阴影或效果已按画面保真 · 1 项" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "复杂阴影" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    expect(screen.getByText("富文本")).toBeVisible();
    expect(screen.getByText("1 / 1")).toBeVisible();
    expect(screen.getByText(/同一文本内存在两种或更多字符样式/)).toBeVisible();
    expect(screen.getByText(/默认保留为可编辑文本/)).toBeVisible();
    expect(screen.getByText(/确认转图后才会失去逐字编辑能力/)).toBeVisible();
    expect(screen.getByRole("button", { name: "下一项" })).toBeDisabled();
  });

  it("shows blocked analysis without an artifact and never enables approval", async () => {
    const blockedCandidate = { ...candidate(), artifactReady: false, downloadName: undefined, sha256: undefined, byteSize: undefined };
    const blockedReview = {
      ...review(),
      dispositions: [{ version: 1, id: "blocked", sourceNodeId: "node-instance", sourceName: "按钮实例", sourceType: "INSTANCE", level: "blocked", reason: "component_definition_missing", allowedStrategies: [], visualImpact: "may_differ", editabilityImpact: "unchanged", componentImpact: "instance_not_reusable", blocksApproval: true, details: null }],
      imageReviews: [], componentReviews: [], checks: [], warningIds: [], approvable: false,
    } satisfies NewProjectReview;
    await reachReview(writerClient({
      createNewProjectCandidate: vi.fn().mockResolvedValue({ selection: { version: 1, selection_id: "a".repeat(32), display_name: "Writer", top_level_summaries: [], preview_urls: [], warnings: [] }, candidate: blockedCandidate }),
      reviewNewProject: vi.fn().mockResolvedValue(blockedReview),
    }));
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    expect(screen.getByText("必须处理")).toBeVisible();
    expect(screen.getByText(/按钮实例/)).toBeVisible();
    expect(screen.getByRole("button", { name: "确认审核结果" })).toBeDisabled();
  });

  it("regenerates, visibly invalidates v1 and resets warning acknowledgement for v2", async () => {
    const client = writerClient({ reviewNewProject: vi.fn().mockResolvedValueOnce(review()).mockResolvedValueOnce(review(2, "2".repeat(32))) });
    await reachReview(client);
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    await userEvent.click(screen.getByRole("button", { name: "保留可编辑结构" }));
    expect(screen.getByRole("button", { name: "重新生成候选" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "重新生成候选" }));
    expect(await screen.findByText(/候选 v1 已失效/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "先看已经处理好的内容" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    expect(screen.getByRole("checkbox", { name: /已查看图示和影响/ })).not.toBeChecked();
  });

  it("blocks approval until warnings are acknowledged, then downloads and offers re-download", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:writer");
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { client } = await reachReview();
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    expect(screen.getByRole("button", { name: "确认审核结果" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /已查看图示和影响/ }));
    await userEvent.click(screen.getByRole("button", { name: "确认审核结果" }));
    await userEvent.click(screen.getByRole("button", { name: "确认并下载 ZIP" }));
    expect(await screen.findByRole("button", { name: "再次下载" })).toBeVisible();
    expect(client.approveNewProject).toHaveBeenCalled();
    expect(client.downloadNewProject).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "再次下载" }));
    expect(client.downloadNewProject).toHaveBeenCalledTimes(2);
  });

  it("fails closed when required generated preview evidence cannot load", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(), revokeObjectURL: vi.fn() });
    await reachReview(writerClient({ newProjectPreview: vi.fn().mockRejectedValue(new Error("preview unavailable")) }));
    await screen.findByText(/预览证据加载失败/);
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /已查看图示和影响/ }));
    expect(screen.getByRole("button", { name: "确认审核结果" })).toBeDisabled();
  });

  it("rejects only the whole candidate and never exposes per-file approval", async () => {
    const { client } = await reachReview();
    expect(screen.queryByText(/逐文件批准|批准此文件/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "拒绝候选" }));
    expect(await screen.findByText("当前候选已拒绝，不会提供下载。")).toBeVisible();
    expect(client.rejectNewProject).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "确认并下载 ZIP" })).not.toBeInTheDocument();
  });

  it("rejects the whole candidate, retries failures, cancels active work and ignores stale bridge attempts", async () => {
    const deferred: { reject?: (reason: unknown) => void } = {};
    const client = writerClient({ createNewProjectCandidate: vi.fn((_m, _r, _n, options) => new Promise((_resolve, reject) => { deferred.reject = reject; options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))); })) });
    const postToFigma = vi.fn();
    render(<NewProjectWriterPanel client={client as never} postToFigma={postToFigma} />);
    sendPreflight();
    await userEvent.type(screen.getByLabelText("工程名称"), "Writer");
    await userEvent.click(screen.getByRole("button", { name: "生成候选工程" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt;
    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-export", attempt: `${attempt}-stale`, manifest, resources: [] } } }));
    expect(client.createNewProjectCandidate).not.toHaveBeenCalled();
    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-export", attempt, manifest, resources: [] } } }));
    await screen.findByRole("button", { name: "取消" });
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成候选工程" })).toBeEnabled());
  });

  it("invalidates and rejects active review on an ordinary Figma selection change", async () => {
    const { client } = await reachReview();
    changeSelection();
    expect(await screen.findByText(/旧候选已失效并清除/)).toBeVisible();
    expect(screen.queryByRole("tab", { name: "图片" })).not.toBeInTheDocument();
    await waitFor(() => expect(client.rejectNewProject).toHaveBeenCalledOnce());
    expect(document.querySelectorAll(".primary-button")).toHaveLength(1);
  });

  it("keeps idle and rejects the server candidate when selection changes during adjustment", async () => {
    const pending = deferred<NewProjectCandidate>();
    const client = writerClient({ adjustNewProject: vi.fn().mockReturnValue(pending.promise) });
    await reachReview(client);
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    await userEvent.click(screen.getByRole("button", { name: "保留可编辑结构" }));
    changeSelection();
    await act(async () => pending.resolve(candidate(1, "adjusting")));

    expect(await screen.findByText(/本次生成已取消并清除/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成候选工程" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(client.rejectNewProject).toHaveBeenCalledWith(expect.objectContaining({ generation: 1 })));
  });

  it("keeps idle and rejects the server candidate when selection changes during regeneration", async () => {
    const pending = deferred<NewProjectCandidate>();
    const live = candidate(2, "regenerating", "2".repeat(32));
    const client = writerClient({ regenerateNewProject: vi.fn((_candidate, options) => {
      options.onStage(live);
      return pending.promise;
    }) });
    await reachReview(client);
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    await userEvent.click(screen.getByRole("button", { name: "保留可编辑结构" }));
    await userEvent.click(screen.getByRole("button", { name: "重新生成候选" }));
    changeSelection();
    await act(async () => pending.resolve(candidate(2)));

    expect(await screen.findByText(/本次生成已取消并清除/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成候选工程" })).toBeEnabled();
    expect(screen.queryByText("候选 v2")).not.toBeInTheDocument();
    await waitFor(() => expect(client.rejectNewProject).toHaveBeenCalledWith(expect.objectContaining({ buildId: "2".repeat(32), generation: 2 })));
    expect(client.rejectNewProject).not.toHaveBeenCalledWith(expect.objectContaining({ buildId: "1".repeat(32) }));
  });

  it("keeps idle and rejects the server candidate when selection changes during approval", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn().mockReturnValue("blob:writer"), revokeObjectURL: vi.fn() });
    const pending = deferred<NewProjectCandidate>();
    const client = writerClient({ approveNewProject: vi.fn().mockReturnValue(pending.promise) });
    await reachReview(client);
    await userEvent.click(screen.getByRole("button", { name: "查看建议审核" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /已查看图示和影响/ }));
    await userEvent.click(screen.getByRole("button", { name: "确认审核结果" }));
    await userEvent.click(screen.getByRole("button", { name: "确认并下载 ZIP" }));
    changeSelection();
    await act(async () => pending.resolve(candidate(1, "approved")));

    expect(await screen.findByText(/本次生成已取消并清除/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成候选工程" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "再次下载" })).not.toBeInTheDocument();
    expect(client.downloadNewProject).not.toHaveBeenCalled();
    await waitFor(() => expect(client.rejectNewProject).toHaveBeenCalledWith(expect.objectContaining({ generation: 1 })));
  });
});
