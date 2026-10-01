import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App, type LocalHifiClientLike } from "./App";

const project = {
  projectId: "a".repeat(32),
  displayName: "HIFI_Replace.zip",
  packages: [{ name: "Tower", resourceCount: 98 }],
};
const tree = {
  projectId: project.projectId,
  projectFingerprint: "b".repeat(64),
  packages: [
    {
      packageId: "tgn8y213",
      name: "Tower",
      directories: [
        {
          path: "Panel",
          selectable: true,
          components: [
            {
              resourceId: "main",
              name: "Panel_Tower_Main",
              relativePath: "assets/Tower/Panel/Panel_Tower_Main.xml",
              selectable: true,
            },
          ],
        },
      ],
    },
  ],
};
const mapping = {
  mappingRevision: 1,
  unresolvedCount: 1,
  items: [
    {
      itemId: "old:title",
      oldObjectId: "title",
      oldName: "TitleBar",
      figmaNodeId: `psd-layer:${"e".repeat(64)}:11`,
      figmaName: "TitleBar",
      status: "suggested" as const,
      score: 0.86,
      candidates: [`psd-layer:${"e".repeat(64)}:11`],
      oldBounds: [0.05, 0.07, 0.47, 0.11] as [number, number, number, number],
      figmaBounds: [0.06, 0.07, 0.47, 0.11] as [number, number, number, number],
    },
  ],
};
const psdSource = {
  sourceId: "e".repeat(64),
  inspection: {
    sourceName: "SampleHomePage.psd",
    byteSize: 266052490,
    sha256: "e".repeat(64),
    width: 1080,
    height: 2340,
    depth: 16 as const,
    colorMode: "RGB" as const,
    layerCount: 438,
    kindCounts: {
      curves: 3,
      group: 66,
      huesaturation: 2,
      pixel: 57,
      shape: 230,
      smartobject: 44,
      type: 36,
    },
    textLayerCount: 36,
    smartObjectCount: 44,
    adjustmentLayerCount: 5,
    effectLayerCount: 140,
    blockingIssues: [
      "smart_objects_require_equivalence_check",
      "adjustment_layers_require_equivalence_check",
      "layer_effects_require_equivalence_check",
    ],
    warnings: ["16_bit_pixels_must_not_be_downconverted"],
  },
  layers: [],
};

function client(): LocalHifiClientLike {
  return {
    resumePsdHifiReplacement: vi.fn(),
    fixedFonts: vi.fn(async () => [
      {
        family: "HYZhengYuan-75S",
        postscriptName: "HYZhengYuan-GES",
        sourceFilename: "HYZhengYuan-75S.ttf",
        sha256: "0".repeat(64),
        installed: true,
        matchedFilename: "HYZhengYuan-75S.ttf",
      },
      {
        family: "CoreSansESW01-55Medium",
        postscriptName: "CoreSansESW01-55Medium",
        sourceFilename: "core sans es w01_55 medium.ttf",
        sha256: "a".repeat(64),
        installed: true,
        matchedFilename: "core sans es w01_55 medium.ttf",
      },
    ]),
    uploadProject: vi.fn(async () => project),
    hifiTargets: vi.fn(async () => tree),
    uploadPsd: vi.fn(async () => psdSource),
    psdComposite: vi.fn(async () => new Blob(["png"], { type: "image/png" })),
    projectAssetThumbnail: vi.fn(
      async () => new Blob(["webp"], { type: "image/webp" }),
    ),
    createPsdHifiReplacementBatch: vi.fn(async () => []),
    createPsdHifiReplacement: vi.fn(async () => ({
      project,
      replacement: {
        sessionId: "d".repeat(32),
        status: "mapping" as const,
        selectionId: "e".repeat(64),
        target: {
          version: 1 as const,
          projectId: project.projectId,
          projectFingerprint: tree.projectFingerprint,
          packageId: "tgn8y213",
          packageName: "Tower",
          directory: "Panel",
          componentId: "main",
          componentName: "Panel_Tower_Main",
          componentRelativePath: "assets/Tower/Panel/Panel_Tower_Main.xml",
        },
        mappingRevision: 1,
        unresolvedCount: 1,
        artifactReady: false,
      },
      mapping,
    })),
    hifiMapping: vi.fn(async () => mapping),
    decideHifiMapping: vi.fn(),
    buildHifiReplacement: vi.fn(),
    reviewHifiReplacement: vi.fn(),
    verifyHifiReplacementInEditor: vi.fn(),
    hifiEditorScreenshot: vi.fn(),
    approveHifiReplacement: vi.fn(),
    rejectHifiReplacement: vi.fn(),
    downloadHifiReplacement: vi.fn(),
  };
}

async function restore(
  api: LocalHifiClientLike,
  draft = mapping,
  status: "mapping" | "review_ready" | "approved" = "mapping",
) {
  const started = await api.createPsdHifiReplacement(
    psdSource.sourceId,
    project,
    {} as never,
  );
  api.resumePsdHifiReplacement = vi.fn(async () => ({
    ...started,
    replacement: { ...started.replacement, status },
    mapping: draft,
    source: psdSource,
    tree,
    stale: false,
  }));
  localStorage.setItem("hifi-last-session", started.replacement.sessionId);
  render(<App client={api} />);
  await waitFor(() =>
    expect(screen.queryByText("正在恢复上次会话…")).not.toBeInTheDocument(),
  );
  return started.replacement;
}

const candidateReview = {
  sessionId: "d".repeat(32),
  mappingRevision: 1,
  changedFiles: [],
  objectDiffs: [],
  protectedChecksPassed: true,
  parseCoverageComplete: true,
  approvable: false,
  candidateSha256: "b".repeat(64),
  warnings: [],
  editorCheckRequired: true,
};

describe("standalone PSD HIFI app", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, "", "/");
  });
  it("restores the saved session without re-uploading either material", async () => {
    const api = client();
    const target = {
      version: 1 as const,
      projectId: project.projectId,
      projectFingerprint: tree.projectFingerprint,
      packageId: "tgn8y213",
      packageName: "Tower",
      directory: "Panel",
      componentId: "main",
      componentName: "Panel_Tower_Main",
      componentRelativePath: "assets/Tower/Panel/Panel_Tower_Main.xml",
    };
    const started = await api.createPsdHifiReplacement(
      psdSource.sourceId,
      project,
      target,
    );
    api.resumePsdHifiReplacement = vi.fn(async () => ({
      ...started,
      source: psdSource,
      tree,
      stale: false,
    }));
    localStorage.setItem("hifi-last-session", started.replacement.sessionId);
    const legacyId = "0".repeat(32);
    window.history.replaceState(null, "", `#session=${legacyId}`);
    render(<App client={api} />);
    expect(await screen.findByText("组件对齐工作台")).toBeVisible();
    expect(api.resumePsdHifiReplacement).toHaveBeenCalledWith(legacyId);
    await waitFor(() =>
      expect(window.location.hash).toBe(
        `#session=${started.replacement.sessionId}`,
      ),
    );
    expect(api.uploadProject).not.toHaveBeenCalled();
    expect(api.uploadPsd).not.toHaveBeenCalled();
  });
  it("keeps an old-project upload error visible after the PSD finishes", async () => {
    const api = client();
    api.uploadProject = vi.fn(async () => {
      throw { code: "invalid_zip" };
    });
    render(<App client={api} />);

    await userEvent.upload(
      screen.getByLabelText("旧 FairyGUI 工程压缩包"),
      new File(["rar"], "HIFI_Replace.rar"),
    );
    expect(
      await screen.findByText(
        "旧 FairyGUI 工程压缩包无效。请检查文件并重新选择。",
      ),
    ).toBeVisible();
    await userEvent.upload(
      screen.getByLabelText("HIFI PSD"),
      new File(["8BPS"], "SampleHomePage.psd"),
    );

    expect(
      screen.getByText("旧 FairyGUI 工程压缩包无效。请检查文件并重新选择。"),
    ).toBeVisible();
    expect(screen.getByText("请重新选择旧 FairyGUI 工程压缩包")).toBeVisible();
  });

  it("prepares the old project, target, fixed fonts and PSD without Figma", async () => {
    const api = client();
    render(<App client={api} />);

    expect(await screen.findByText("固定字体 2 / 2")).toBeVisible();
    expect(screen.getByRole("heading", { name: "设计稿预览" })).toBeVisible();
    const projectInput = screen.getByLabelText("旧 FairyGUI 工程压缩包");
    expect(projectInput).toHaveAttribute(
      "accept",
      expect.stringContaining(".rar"),
    );
    expect(projectInput).toHaveAttribute(
      "accept",
      expect.stringContaining(".7z"),
    );
    expect(projectInput).toHaveAttribute(
      "accept",
      expect.stringContaining(".tar.gz"),
    );
    await userEvent.upload(
      projectInput,
      new File(["zip"], "HIFI_Replace.zip", { type: "application/zip" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Tower.*1 个目录/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Panel.*1 个组件/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Panel_Tower_Main/ }),
    );
    await userEvent.upload(
      screen.getByLabelText("HIFI PSD"),
      new File(["8BPS"], "SampleHomePage.psd", {
        type: "image/vnd.adobe.photoshop",
      }),
    );

    expect(screen.getByText("Tower / Panel")).toBeVisible();
    expect(screen.getByText("1080 × 2340 · 16-bit RGB")).toBeVisible();
    expect(
      screen.getByText("PSD 已保存在本机，后续映射不会重复上传。"),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "进入盘点与映射" }),
    ).toBeEnabled();
    await userEvent.click(
      screen.getByRole("button", { name: "进入盘点与映射" }),
    );
    expect(await screen.findByText("组件对齐工作台")).toBeVisible();
    expect(screen.getByTestId("fgui-focus")).toHaveAccessibleName(
      "旧 FGUI · TitleBar",
    );
    expect(screen.getByTestId("hifi-focus")).toHaveAccessibleName(
      "HIFI · TitleBar",
    );
    expect(screen.getByRole("button", { name: "生成审核候选" })).toBeDisabled();
    expect(api.uploadProject).toHaveBeenCalledOnce();
    expect(api.uploadPsd).toHaveBeenCalledOnce();
    expect(api.psdComposite).toHaveBeenCalledOnce();
    expect(api.createPsdHifiReplacement).toHaveBeenCalledOnce();
  });

  it("does not offer uploads that have no processing implementation", async () => {
    render(<App client={client()} />);

    expect(screen.queryByLabelText("可选 PNG 切图")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("可选效果图")).not.toBeInTheDocument();
  });

  it("batch-accepts suggested mappings while keeping the focused review flow", async () => {
    const resolvedMapping = {
      ...mapping,
      mappingRevision: 2,
      unresolvedCount: 0,
      items: mapping.items.map((item) => ({
        ...item,
        action: "accept" as const,
      })),
    };
    const replacement = {
      sessionId: "d".repeat(32),
      status: "mapping" as const,
      selectionId: "e".repeat(64),
      target: {
        version: 1 as const,
        projectId: project.projectId,
        projectFingerprint: tree.projectFingerprint,
        packageId: "tgn8y213",
        packageName: "Tower",
        directory: "Panel",
        componentId: "main",
        componentName: "Panel_Tower_Main",
        componentRelativePath: "assets/Tower/Panel/Panel_Tower_Main.xml",
      },
      mappingRevision: 1,
      unresolvedCount: 1,
      artifactReady: false,
    };
    const api: LocalHifiClientLike = {
      ...client(),
      createPsdHifiReplacement: vi.fn(async () => ({
        project,
        replacement,
        mapping,
      })),
      decideHifiMapping: vi.fn(async () => ({
        ...replacement,
        mappingRevision: 2,
        unresolvedCount: 0,
      })),
      hifiMapping: vi.fn(async () => resolvedMapping),
    };
    render(<App client={api} />);
    await screen.findByText("固定字体 2 / 2");
    await userEvent.upload(
      screen.getByLabelText("旧 FairyGUI 工程压缩包"),
      new File(["zip"], "HIFI_Replace.zip", { type: "application/zip" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Tower.*1 个目录/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Panel.*1 个组件/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Panel_Tower_Main/ }),
    );
    await userEvent.upload(
      screen.getByLabelText("HIFI PSD"),
      new File(["8BPS"], "SampleHomePage.psd", {
        type: "image/vnd.adobe.photoshop",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "进入盘点与映射" }),
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "接受建议对应 1" }),
    );

    expect(api.decideHifiMapping).toHaveBeenCalledWith(
      replacement.sessionId,
      1,
      "old:title",
      "accept",
    );
    expect(
      await screen.findByRole("button", { name: "生成审核候选" }),
    ).toBeEnabled();
  });

  it("continues from a resolved mapping through candidate review", async () => {
    const resolvedMapping = {
      ...mapping,
      unresolvedCount: 0,
      items: mapping.items.map((item) => ({
        ...item,
        action: "accept" as const,
      })),
    };
    const replacement = {
      sessionId: "d".repeat(32),
      status: "mapping" as const,
      selectionId: "e".repeat(64),
      target: {
        version: 1 as const,
        projectId: project.projectId,
        projectFingerprint: tree.projectFingerprint,
        packageId: "tgn8y213",
        packageName: "Tower",
        directory: "Panel",
        componentId: "main",
        componentName: "Panel_Tower_Main",
        componentRelativePath: "assets/Tower/Panel/Panel_Tower_Main.xml",
      },
      mappingRevision: 1,
      unresolvedCount: 0,
      artifactReady: false,
    };
    const review = {
      sessionId: replacement.sessionId,
      mappingRevision: 1,
      changedFiles: [
        {
          relativePath: "assets/Tower/Panel/Panel_Tower_Main.xml",
          operation: "replace" as const,
          summary: "更新已确认的视觉属性",
        },
      ],
      objectDiffs: [
        {
          itemId: "old:title",
          kind: "changed" as const,
          oldObjectId: "title",
          oldName: "TitleBar",
          figmaNodeId: `psd-layer:${"e".repeat(64)}:11`,
          figmaName: "TitleBar",
          changedFields: ["xy"],
          summary: "修改视觉字段：xy",
        },
      ],
      protectedChecksPassed: true,
      parseCoverageComplete: true,
      approvable: false,
      candidateSha256: "b".repeat(64),
      warnings: ["PSD 无损证据待验证：layer_effects_require_equivalence_check"],
      editorCheckRequired: true,
    };
    const api: LocalHifiClientLike = {
      ...client(),
      createPsdHifiReplacement: vi.fn(async () => ({
        project,
        replacement,
        mapping: resolvedMapping,
      })),
      hifiMapping: vi.fn(async () => resolvedMapping),
      buildHifiReplacement: vi.fn(async () => ({
        ...replacement,
        status: "review_ready" as const,
        artifactReady: true,
      })),
      reviewHifiReplacement: vi.fn(async () => review),
      verifyHifiReplacementInEditor: vi.fn(async () => ({
        sessionId: replacement.sessionId,
        candidateSha256: "b".repeat(64),
        editorFound: true,
        editorVersion: "6.1.4" as const,
        projectOpened: true,
        componentOpened: true,
        renderCaptured: true,
        screenshotUrl: `/v1/hifi-replacements/${replacement.sessionId}/editor-screenshot`,
        screenshotSha256: "c".repeat(64),
        screenshotWidth: 1078,
        screenshotHeight: 1855,
        expectedWidth: 1080,
        expectedHeight: 1920,
        fullFrame: false,
        approvable: false,
        warnings: ["Editor 截图不是完整画面。"],
      })),
      hifiEditorScreenshot: vi.fn(
        async () => new Blob(["png"], { type: "image/png" }),
      ),
    };
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:editor-evidence"),
      revokeObjectURL: vi.fn(),
    });
    render(<App client={api} />);
    await screen.findByText("固定字体 2 / 2");
    await userEvent.upload(
      screen.getByLabelText("旧 FairyGUI 工程压缩包"),
      new File(["zip"], "HIFI_Replace.zip", { type: "application/zip" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Tower.*1 个目录/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Panel.*1 个组件/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Panel_Tower_Main/ }),
    );
    await userEvent.upload(
      screen.getByLabelText("HIFI PSD"),
      new File(["8BPS"], "SampleHomePage.psd", {
        type: "image/vnd.adobe.photoshop",
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "进入盘点与映射" }),
    );
    const build = await screen.findByRole("button", { name: "生成审核候选" });
    expect(build).toBeEnabled();
    await userEvent.click(build);
    expect(await screen.findByText("候选差异审核")).toBeVisible();
    expect(screen.getByText("修改视觉字段：xy")).toBeVisible();
    expect(
      screen.queryByText(/require_equivalence_check/),
    ).not.toBeInTheDocument();
    expect(api.buildHifiReplacement).toHaveBeenCalledOnce();
    expect(api.reviewHifiReplacement).toHaveBeenCalledOnce();
    await userEvent.click(
      screen.getByRole("button", { name: "自动打开并截图" }),
    );
    expect(await screen.findByText("已取得 Editor 渲染证据")).toBeVisible();
    expect(
      screen.getByText("截图 1078 × 1855；目标 1080 × 1920"),
    ).toBeVisible();
    expect(screen.getByAltText("FairyGUI Editor 候选渲染截图")).toHaveAttribute(
      "src",
      "blob:editor-evidence",
    );
    expect(api.verifyHifiReplacementInEditor).toHaveBeenCalledWith(
      replacement.sessionId,
    );
  });
  it("shows every pending mapping and keeps selection after returning to materials", async () => {
    const api = client();
    const draft = {
      ...mapping,
      unresolvedCount: 7,
      items: Array.from({ length: 7 }, (_, i) => ({
        ...mapping.items[0],
        itemId: `old:${i}`,
        oldName: `对象 ${i}`,
      })),
    };
    await restore(api, draft);
    expect(
      await screen.findByRole("button", { name: "待确认 7" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /对象 6.*TitleBar/ }),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "返回材料页" }));
    await userEvent.click(screen.getByRole("button", { name: "继续当前映射" }));
    expect(api.createPsdHifiReplacement).toHaveBeenCalledOnce();
  });

  it("restores a built candidate at review instead of requiring another build", async () => {
    const api = client();
    api.reviewHifiReplacement = vi.fn(async () => candidateReview);
    await restore(api, mapping, "review_ready");
    expect(
      await screen.findByRole("heading", { name: "候选差异审核" }),
    ).toBeVisible();
    expect(api.buildHifiReplacement).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "确认并交付 ZIP" }),
    ).toBeDisabled();
  });

  it("clears the persisted session when the old project is replaced", async () => {
    const api = client();
    await restore(api);
    await userEvent.click(
      await screen.findByRole("button", { name: "返回材料页" }),
    );
    await userEvent.upload(
      screen.getByLabelText("旧 FairyGUI 工程压缩包"),
      new File(["zip"], "another.zip"),
    );
    await waitFor(() => expect(api.uploadProject).toHaveBeenCalledOnce());
    expect(localStorage.getItem("hifi-last-session")).toBeNull();
    expect(window.location.hash).toBe("");
    expect(
      screen.queryByRole("button", { name: "继续当前映射" }),
    ).not.toBeInTheDocument();
  });

  it("preserves an uploaded PSD and retries only its preview after a preview failure", async () => {
    const api = client();
    api.psdComposite = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(new Blob(["png"]));
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:preview"),
      revokeObjectURL: vi.fn(),
    });
    render(<App client={api} />);
    await userEvent.upload(
      screen.getByLabelText("HIFI PSD"),
      new File(["psd"], "source.psd"),
    );
    expect(
      await screen.findByText("PSD 预览加载失败，材料已保留。请重试加载预览。"),
    ).toBeVisible();
    expect(
      screen.getByText("PSD 已保存在本机，后续映射不会重复上传。"),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "重新加载预览" }));
    expect(await screen.findByAltText("PSD 合成基准图")).toHaveAttribute(
      "src",
      "blob:preview",
    );
    expect(api.uploadPsd).toHaveBeenCalledOnce();
  });

  it("refreshes review after Editor verification and can retry a failed delivery download", async () => {
    const api = client();
    api.reviewHifiReplacement = vi
      .fn()
      .mockResolvedValueOnce(candidateReview)
      .mockResolvedValue({ ...candidateReview, approvable: true });
    api.verifyHifiReplacementInEditor = vi.fn(async () => ({
      sessionId: candidateReview.sessionId,
      candidateSha256: candidateReview.candidateSha256,
      editorFound: true,
      editorVersion: "6.1.4" as const,
      expectedWidth: 1080,
      expectedHeight: 1920,
      projectOpened: true,
      componentOpened: true,
      renderCaptured: true,
      fullFrame: true,
      approvable: true,
      warnings: [],
    }));
    api.approveHifiReplacement = vi.fn(async () => ({
      sessionId: candidateReview.sessionId,
      status: "approved" as const,
      selectionId: psdSource.sourceId,
      target: {} as never,
      mappingRevision: 1,
      unresolvedCount: 0,
      artifactReady: true,
    }));
    api.downloadHifiReplacement = vi
      .fn()
      .mockRejectedValue(new Error("network"));
    await restore(api, mapping, "review_ready");
    await userEvent.click(
      await screen.findByRole("button", { name: "自动打开并截图" }),
    );
    await waitFor(() =>
      expect(api.reviewHifiReplacement).toHaveBeenCalledTimes(2),
    );
    for (const name of [
      "布局与图层顺序正确",
      "图片与共享组件引用正常",
      "Controller、Gear、Transition 正常",
    ])
      await userEvent.click(screen.getByRole("checkbox", { name }));
    const deliver = screen.getByRole("button", { name: "确认并交付 ZIP" });
    expect(deliver).toBeEnabled();
    await userEvent.click(deliver);
    expect(await screen.findByText("已交付 HIFI 替换工程")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "再次下载正式 ZIP" }),
    );
    expect(api.downloadHifiReplacement).toHaveBeenCalledTimes(2);
    expect(api.approveHifiReplacement).toHaveBeenCalledOnce();
  });
  it("restores an approved session directly to its download screen", async () => {
    const api = client();
    await restore(api, mapping, "approved");
    expect(await screen.findByText("已交付 HIFI 替换工程")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "再次下载正式 ZIP" }),
    ).toBeVisible();
    expect(api.reviewHifiReplacement).not.toHaveBeenCalled();
    expect(api.approveHifiReplacement).not.toHaveBeenCalled();
  });
});
