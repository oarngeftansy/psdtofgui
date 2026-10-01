import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HifiReplacementPanel, type HifiClientLike } from "./HifiReplacementPanel";


const project = { projectId: "a".repeat(32), displayName: "OldVillage.zip", packages: [{ name: "MyVillage", resourceCount: 3 }] };
const tree = {
  projectId: project.projectId,
  projectFingerprint: "b".repeat(64),
  packages: [{
    packageId: "myvillage01",
    name: "MyVillage",
    directories: [{
      path: "Panel",
      selectable: true,
      components: [{ resourceId: "sketch01", name: "Panel_MyVillage_Sketchboard", relativePath: "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml", selectable: true }],
    }],
  }],
};
const target = {
  version: 1 as const,
  projectId: project.projectId,
  projectFingerprint: tree.projectFingerprint,
  packageId: "myvillage01",
  packageName: "MyVillage",
  directory: "Panel",
  componentId: "sketch01",
  componentName: "Panel_MyVillage_Sketchboard",
  componentRelativePath: "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml",
};
const mapping = {
  mappingRevision: 1,
  unresolvedCount: 1,
  items: [{
    itemId: "old:title_bar",
    oldObjectId: "title_bar",
    oldName: "TitleBar",
    figmaNodeId: "hifi-title",
    figmaName: "TitleBar",
    status: "suggested" as const,
    score: .88,
    candidates: ["hifi-title"],
    oldBounds: [.05, .1, .4, .12] as [number, number, number, number],
    figmaBounds: [.06, .11, .4, .12] as [number, number, number, number],
  }],
};

function client(): HifiClientLike {
  return {
    inspectPsd: vi.fn(async () => ({
      sourceName: "SampleHomePage.psd",
      byteSize: 266052490,
      sha256: "e".repeat(64),
      width: 1080,
      height: 2340,
      depth: 16 as const,
      colorMode: "RGB" as const,
      layerCount: 438,
      kindCounts: { curves: 3, group: 66, huesaturation: 2, pixel: 57, shape: 230, smartobject: 44, type: 36 },
      textLayerCount: 36,
      smartObjectCount: 44,
      adjustmentLayerCount: 5,
      effectLayerCount: 140,
      blockingIssues: ["smart_objects_require_equivalence_check", "adjustment_layers_require_equivalence_check", "layer_effects_require_equivalence_check"],
      warnings: ["16_bit_pixels_must_not_be_downconverted"],
    })),
    uploadProject: vi.fn(async () => project),
    hifiTargets: vi.fn(async () => tree),
    createHifiReplacement: vi.fn(async () => ({
      project,
      selection: { version: 1 as const, selection_id: "c".repeat(32), display_name: "HIFI", top_level_summaries: [], preview_urls: [], warnings: [] },
      replacement: { sessionId: "d".repeat(32), status: "mapping" as const, selectionId: "c".repeat(32), target, mappingRevision: 1, unresolvedCount: 1, artifactReady: false },
      mapping,
    })),
    hifiMapping: vi.fn(async () => mapping),
    decideHifiMapping: vi.fn(),
    buildHifiReplacement: vi.fn(),
    reviewHifiReplacement: vi.fn(),
    approveHifiReplacement: vi.fn(),
    rejectHifiReplacement: vi.fn(),
    downloadHifiReplacement: vi.fn(),
  };
}

function selectionMessage(type: "selection-preflight" | "selection-changed" = "selection-preflight") {
  window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: {
    type,
    preflight: {
      sendable: true,
      nodeCount: 6,
      assetCount: 0,
      warnings: [],
      manifest: { version: 1, display_name: "速记板 / HIFI v4", top_level_nodes: [{ id: "root", name: "速记板", type: "FRAME", bounds: { x: 0, y: 0, width: 750, height: 420 }, children: [], resource_keys: [], rotation: 0, visible: true, opacity: 1, source_order: 0, properties: {}, style: {} }], resources: [], warnings: [] },
    },
  } } }));
}

describe("HifiReplacementPanel", () => {
  it("uses PSD as the primary HIFI source and reports real document blockers", async () => {
    const api = client();
    render(<HifiReplacementPanel client={api} postToFigma={vi.fn()} onOpenNew={vi.fn()} />);

    expect(screen.getByRole("radio", { name: "PSD 文件" })).toBeChecked();
    await userEvent.upload(screen.getByLabelText("选择 PSD 文件"), new File(["8BPS"], "SampleHomePage.psd", { type: "image/vnd.adobe.photoshop" }));

    await screen.findByText("1080 × 2340 · 16-bit RGB");
    expect(screen.getByText("438 个图层 · 36 个文字层 · 44 个智能对象")).toBeVisible();
    expect(screen.getByText(/3 项无损阻断/)).toBeVisible();
    expect(api.inspectPsd).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "开始盘点与映射" })).toBeDisabled();
  });

  it("selects the target tree and highlights the current mapping on both sides", async () => {
    const api = client();
    const postToFigma = vi.fn();
    render(<HifiReplacementPanel client={api} postToFigma={postToFigma} onOpenNew={vi.fn()} />);
    selectionMessage();
    await userEvent.click(screen.getByRole("radio", { name: "当前 Figma 框选" }));

    const file = new File(["zip"], "OldVillage.zip", { type: "application/zip" });
    const projectInput = screen.getByLabelText("旧 FairyGUI 工程压缩包");
    expect(projectInput).toHaveAttribute("accept", expect.stringContaining(".rar"));
    await userEvent.upload(projectInput, file);
    await userEvent.click(await screen.findByRole("button", { name: /MyVillage.*1 个目录/ }));
    await userEvent.click(screen.getByRole("button", { name: /Panel.*1 个组件/ }));
    await userEvent.click(screen.getByRole("button", { name: /Panel_MyVillage_Sketchboard/ }));
    expect(screen.getByText("MyVillage / Panel")).toBeVisible();
    expect(screen.queryByText(/新增图片/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "开始盘点与映射" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt;
    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: {
      type: "selection-export",
      attempt,
      manifest: { version: 1, display_name: "速记板 / HIFI v4", top_level_nodes: [], resources: [], warnings: [] },
      resources: [],
    } } }));

    await screen.findByText("组件对齐工作台");
    await waitFor(() => expect(document.querySelectorAll(".hifi-canvas-object.is-active")).toHaveLength(2));
    expect(screen.getByRole("region", { name: "结构视图" })).toBeVisible();
    expect(screen.getByRole("button", { name: "对 · 就是这个图层" })).toBeVisible();
    expect(screen.getByRole("button", { name: "跳 · 保留旧对象不替换" })).toBeVisible();
    expect(screen.queryByLabelText("换成哪个图层")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "换 · 选另一个" }));
    expect(screen.getByLabelText("换成哪个图层")).toBeVisible();
    expect(screen.getByText(/^建议对应 · 匹配分/)).toBeVisible();
  });
});
