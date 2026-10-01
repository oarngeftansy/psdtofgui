import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectWorkflowPage, type ProjectWorkflowClientLike } from "./ProjectWorkflowPage";

const manifest = { version: 1 as const, display_name: "结算页", top_level_nodes: [], resources: [], warnings: [] };
const resources: [] = [];
const options = [
  { templateId: "fgui-2024-unity", fairyguiVersion: "2024.1", targetPlatform: "Unity", displayName: "FairyGUI 2024 / Unity" },
  { templateId: "fgui-2023-egret", fairyguiVersion: "2023.3", targetPlatform: "Egret", displayName: "FairyGUI 2023 / Egret" },
];

function workflowResult(diagnostics: Array<{ code: string; severity: "ERROR" | "WARNING" | "INFO"; message: string }> = []) {
  return {
    blob: new Blob(["zip"], { type: "application/zip" }),
    downloadName: "结算页.zip",
    project: { projectId: "a".repeat(32), displayName: "结算页", packages: [{ name: "Main", resourceCount: 1 }] },
    selection: { version: 1 as const, selection_id: "b".repeat(32), display_name: "结算页", top_level_summaries: [], preview_urls: [], warnings: [] },
    job: { jobId: "c".repeat(32), projectId: "a".repeat(32), status: "ready_for_review" },
    package: { jobId: "c".repeat(32), status: "ready" as const, stage: "ready" as const, progress: 100, diagnostics },
  };
}

function client(overrides: Partial<ProjectWorkflowClientLike> = {}): ProjectWorkflowClientLike {
  return {
    options: vi.fn().mockResolvedValue(options),
    runCreate: vi.fn().mockResolvedValue(workflowResult()),
    runUpdate: vi.fn().mockResolvedValue(workflowResult()),
    ...overrides,
  } as unknown as ProjectWorkflowClientLike;
}

function sendSelection(preflight: { sendable: boolean; warnings?: Array<{ code: string; message: string }> } = { sendable: true }) {
  window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-changed", preflight: { ...preflight, nodeCount: 1, assetCount: 0, estimatedBytes: 0, warnings: preflight.warnings ?? [], manifest } } } }));
}

function sendExport(attempt: string) {
  window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-export", attempt, manifest, resources } } }));
}

function sendScreenshot(attempt: string) {
  window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "semantic-screenshot-export", attempt, mimeType: "image/png", bytes: new Uint8Array([137, 80, 78, 71]) } } }));
}

afterEach(() => vi.restoreAllMocks());

describe("ProjectWorkflowPage", () => {
  it("keeps legacy as the component default and opts Writer into the plugin-only mode", async () => {
    const writer = client({
      createNewProjectCandidate: vi.fn(), reviewNewProject: vi.fn(), adjustNewProject: vi.fn(), regenerateNewProject: vi.fn(),
      approveNewProject: vi.fn(), rejectNewProject: vi.fn(), downloadNewProject: vi.fn(), newProjectPreview: vi.fn(),
    });
    const postToFigma = vi.fn();
    const { rerender } = render(<ProjectWorkflowPage client={writer} postToFigma={postToFigma} />);
    expect(screen.getByRole("heading", { name: "1. Figma 选择" })).toBeVisible();
    rerender(<ProjectWorkflowPage client={writer} postToFigma={postToFigma} defaultMode="writer" />);
    expect(screen.getByRole("heading", { name: "新建工程" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "1. Figma 选择" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("FairyGUI 版本")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "更多操作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "更新现有工程" }));
    expect(screen.getByLabelText("现有 FairyGUI 工程压缩包")).toBeVisible();
    expect(screen.queryByRole("radio", { name: "新建工程" })).not.toBeInTheDocument();
  });

  it("guides a create workflow without pairing or ZIP input", async () => {
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client()} postToFigma={postToFigma} />);

    expect(screen.getByRole("heading", { name: "1. Figma 选择" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "2. 新建或更新" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "3. 生成与检查" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "4. 下载工程" })).toBeVisible();
    expect(screen.getByRole("radio", { name: "新建工程" })).toBeChecked();
    expect(screen.getByLabelText("工程名称")).toBeRequired();
    expect(screen.getByLabelText("FairyGUI 版本")).toBeRequired();
    expect(screen.getByLabelText("目标平台")).toBeRequired();
    expect(screen.queryByLabelText(/ZIP/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/配对|Web Console/i)).not.toBeInTheDocument();

    expect(postToFigma).toHaveBeenCalledWith({ type: "selection-preflight" });
  });

  it("shows empty selections and refreshes the current Figma selection", async () => {
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client()} postToFigma={postToFigma} />);
    sendSelection({ sendable: false, warnings: [{ code: "selection_empty", message: "请选择要导出的图层" }] });

    expect(await screen.findByRole("alert")).toHaveTextContent("请选择要导出的图层");
    expect(screen.getByRole("button", { name: "生成工程" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "刷新选择" }));
    expect(postToFigma).toHaveBeenLastCalledWith({ type: "selection-preflight" });
  });

  it("runs update with a ZIP archive and rejects an invalid file before upload", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const runUpdate = vi.fn().mockResolvedValue(workflowResult());
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client({ runUpdate })} postToFigma={postToFigma} />);
    sendSelection();
    await user.click(screen.getByRole("radio", { name: "更新现有工程" }));
    const input = screen.getByLabelText("现有 FairyGUI 工程压缩包");
    await user.upload(input, new File(["no"], "project.txt", { type: "text/plain" }));
    const invalidArchive = await screen.findByRole("alert");
    expect(invalidArchive).toHaveTextContent("压缩包");
    expect(within(invalidArchive).queryByRole("button", { name: "重试" })).not.toBeInTheDocument();

    await user.upload(input, new File(["zip"], "project.zip", { type: "application/zip" }));
    await user.click(screen.getByRole("button", { name: "生成工程" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt as string;
    sendExport(attempt);
    await waitFor(() => expect(runUpdate).toHaveBeenCalledWith(
      manifest,
      resources,
      expect.objectContaining({ name: "project.zip" }),
      expect.any(Function),
      expect.objectContaining({ onScreenshotConsent: expect.any(Function), requestScreenshot: expect.any(Function), signal: expect.any(AbortSignal) }),
    ));
  });

  it("locks and snapshots create inputs while waiting for the selection export", async () => {
    const runCreate = vi.fn().mockResolvedValue(workflowResult());
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client({ runCreate })} postToFigma={postToFigma} />);
    sendSelection();
    await userEvent.type(screen.getByLabelText("工程名称"), "快照工程");
    await userEvent.click(screen.getByRole("button", { name: "生成工程" }));

    expect(screen.getByRole("radio", { name: "新建工程" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "更新现有工程" })).toBeDisabled();
    expect(screen.getByLabelText("工程名称")).toBeDisabled();
    expect(screen.getByLabelText("FairyGUI 版本")).toBeDisabled();
    expect(screen.getByLabelText("目标平台")).toBeDisabled();

    sendExport(postToFigma.mock.calls.at(-1)?.[0].attempt as string);
    await waitFor(() => expect(runCreate).toHaveBeenCalledWith(
      manifest,
      resources,
      { templateId: "fgui-2024-unity", projectName: "快照工程" },
      expect.any(Function),
      expect.objectContaining({ onScreenshotConsent: expect.any(Function), requestScreenshot: expect.any(Function), signal: expect.any(AbortSignal) }),
    ));
  });

  it("reloads project options when their error action is retried", async () => {
    const loadOptions = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(options);
    render(<ProjectWorkflowPage client={client({ options: loadOptions })} postToFigma={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法加载工程选项");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(loadOptions).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("FairyGUI 版本")).toHaveValue("2024.1");
    expect(screen.getByLabelText("目标平台")).toHaveValue("Unity");
  });

  it("reports progress, warnings, errors, retries with preserved inputs, and prevents duplicate clicks", async () => {
    let resolveRun: ((value: ReturnType<typeof workflowResult>) => void) | undefined;
    let reportStage: NonNullable<Parameters<ProjectWorkflowClientLike["runCreate"]>[3]> | undefined;
    const runCreate = vi.fn((_manifest, _resources, _params, onStage) => {
      reportStage = onStage;
      return new Promise<ReturnType<typeof workflowResult>>((resolve) => { resolveRun = resolve; });
    });
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client({ runCreate })} postToFigma={postToFigma} />);
    sendSelection();
    const projectName = screen.getByLabelText("工程名称");
    await userEvent.type(projectName, "商城");
    const generate = screen.getByRole("button", { name: "生成工程" });
    await userEvent.dblClick(generate);
    expect(postToFigma).toHaveBeenCalledTimes(2); // initial preflight plus one export request
    sendExport(postToFigma.mock.calls.at(-1)?.[0].attempt as string);

    await waitFor(() => expect(reportStage).toBeTypeOf("function"));
    reportStage?.({ stage: "uploading", progress: 10 });
    expect(await screen.findByRole("status")).toHaveTextContent("上传当前选择");
    reportStage?.({ stage: "parsing", progress: 35 });
    expect(await screen.findByRole("status")).toHaveTextContent("解析工程");
    reportStage?.({ stage: "converting", progress: 50 });
    expect(await screen.findByRole("progressbar")).toHaveAttribute("value", "50");
    expect(screen.getByRole("status")).toHaveTextContent("转换");
    resolveRun?.(workflowResult([
      { code: "unsupported_effect", severity: "WARNING", message: "阴影已简化" },
      { code: "invalid_resource", severity: "ERROR", message: "资源命名需要处理" },
      { code: "preserved_layout", severity: "INFO", message: "布局信息已保留" },
    ]));
    expect(await screen.findByText("阴影已简化")).toHaveClass("message-warning");
    expect(screen.getByRole("alert")).toHaveTextContent("资源命名需要处理");
    expect(screen.getByText("布局信息已保留")).toHaveClass("message-info");
    expect(screen.getByText("布局信息已保留")).not.toHaveClass("message-error");

    cleanup();
    const createFailure = vi.fn().mockRejectedValueOnce(new Error("network down")).mockResolvedValueOnce(workflowResult());
    const retryPost = vi.fn();
    render(<ProjectWorkflowPage client={client({ runCreate: createFailure })} postToFigma={retryPost} />);
    sendSelection();
    await userEvent.type(screen.getByLabelText("工程名称"), "保留名称");
    await userEvent.click(screen.getByRole("button", { name: "生成工程" }));
    sendExport(retryPost.mock.calls.at(-1)?.[0].attempt as string);
    expect(await screen.findByRole("alert")).toHaveTextContent("生成失败");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    sendExport(retryPost.mock.calls.at(-1)?.[0].attempt as string);
    await waitFor(() => expect(createFailure).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("工程名称")).toHaveValue("保留名称");
  });

  it("keeps keyboard focus usable and downloads with a temporary object URL", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:download");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click");
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client()} postToFigma={postToFigma} />);
    sendSelection();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "刷新选择" })).toHaveFocus();
    await userEvent.type(screen.getByLabelText("工程名称"), "下载项目");
    await userEvent.click(screen.getByRole("button", { name: "生成工程" }));
    sendExport(postToFigma.mock.calls.at(-1)?.[0].attempt as string);
    await screen.findByRole("button", { name: "下载工程" });
    await userEvent.click(screen.getByRole("button", { name: "下载工程" }));
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:download");
  });

  it("asks for explicit screenshot consent, locks inputs, and approves only once", async () => {
    let capturedName = "";
    const runCreate = vi.fn(async (_manifest, _resources, params, _onStage, workflow) => {
      capturedName = params.projectName;
      const approved = await workflow!.onScreenshotConsent({ jobId: "c".repeat(32), reason: "Internal reason", signal: new AbortController().signal });
      if (approved) await workflow!.requestScreenshot("c".repeat(32), new AbortController().signal);
      return workflowResult();
    });
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client({ runCreate })} postToFigma={postToFigma} />);
    sendSelection();
    await userEvent.type(screen.getByLabelText("工程名称"), "截图流程");
    await userEvent.click(screen.getByRole("button", { name: "生成工程" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt as string;
    sendExport(attempt);

    expect(await screen.findByText("仅靠图层结构无法可靠判断部分组件。是否允许上传当前选择的截图辅助识别？")).toBeVisible();
    expect(screen.getByLabelText("工程名称")).toBeDisabled();
    expect(screen.getByRole("button", { name: "生成工程" })).toBeDisabled();
    expect(postToFigma.mock.calls.filter(([message]) => message.type === "semantic-screenshot-export")).toHaveLength(0);
    await userEvent.dblClick(screen.getByRole("button", { name: "允许并继续" }));
    await waitFor(() => expect(postToFigma).toHaveBeenCalledWith({ type: "semantic-screenshot-export", attempt }));
    expect(postToFigma.mock.calls.filter(([message]) => message.type === "semantic-screenshot-export")).toHaveLength(1);
    sendScreenshot(attempt);

    await screen.findByRole("button", { name: "下载工程" });
    expect(capturedName).toBe("截图流程");
  });

  it("declines screenshot upload and continues without requesting bridge export", async () => {
    const runCreate = vi.fn(async (_manifest, _resources, _params, _onStage, workflow) => {
      await workflow!.onScreenshotConsent({ jobId: "c".repeat(32), reason: "Internal reason", signal: new AbortController().signal });
      return workflowResult();
    });
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client({ runCreate })} postToFigma={postToFigma} />);
    sendSelection();
    await userEvent.type(screen.getByLabelText("工程名称"), "规则流程");
    await userEvent.click(screen.getByRole("button", { name: "生成工程" }));
    sendExport(postToFigma.mock.calls.at(-1)?.[0].attempt as string);

    await userEvent.dblClick(await screen.findByRole("button", { name: "不上传，按规则继续" }));
    await screen.findByRole("button", { name: "下载工程" });
    expect(postToFigma).not.toHaveBeenCalledWith(expect.objectContaining({ type: "semantic-screenshot-export" }));
  });

  it.each([
    ["wrong attempt", (attempt: string) => ({ type: "semantic-screenshot-export", attempt: `${attempt}-other`, mimeType: "image/png", bytes: new Uint8Array([1]) })],
    ["wrong MIME", (attempt: string) => ({ type: "semantic-screenshot-export", attempt, mimeType: "image/jpeg", bytes: new Uint8Array([1]) })],
    ["non-byte payload", (attempt: string) => ({ type: "semantic-screenshot-export", attempt, mimeType: "image/png", bytes: [1] })],
    ["empty payload", (attempt: string) => ({ type: "semantic-screenshot-export", attempt, mimeType: "image/png", bytes: new Uint8Array() })],
    ["oversized payload", (attempt: string) => ({ type: "semantic-screenshot-export", attempt, mimeType: "image/png", bytes: new Uint8Array(1_024_001) })],
  ])("rejects a %s screenshot response immediately and unlocks the workflow", async (_label, response) => {
    let observed: unknown;
    const runCreate = vi.fn(async (_manifest, _resources, _params, _onStage, workflow) => {
      try {
        await workflow!.requestScreenshot("c".repeat(32), new AbortController().signal);
      } catch (error) {
        observed = error;
        throw error;
      }
      return workflowResult();
    });
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client({ runCreate })} postToFigma={postToFigma} />);
    sendSelection();
    await userEvent.type(screen.getByLabelText("工程名称"), "invalid response");
    await userEvent.click(screen.getByRole("button", { name: "生成工程" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt as string;
    sendExport(attempt);
    await waitFor(() => expect(postToFigma).toHaveBeenCalledWith({ type: "semantic-screenshot-export", attempt }));

    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: response(attempt) } }));

    await waitFor(() => expect(observed).toMatchObject({ code: "invalid_response" }), { timeout: 500 });
    expect(screen.getByRole("button", { name: "生成工程" })).toBeEnabled();
  });

  it("rejects and unlocks when isolated screenshot cleanup reports an export error", async () => {
    let observed: unknown;
    const runCreate = vi.fn(async (_manifest, _resources, _params, _onStage, workflow) => {
      try {
        await workflow!.requestScreenshot("c".repeat(32), new AbortController().signal);
      } catch (error) {
        observed = error;
        throw error;
      }
      return workflowResult();
    });
    const postToFigma = vi.fn();
    render(<ProjectWorkflowPage client={client({ runCreate })} postToFigma={postToFigma} />);
    sendSelection();
    await userEvent.type(screen.getByLabelText("工程名称"), "cleanup failure");
    await userEvent.click(screen.getByRole("button", { name: "生成工程" }));
    const attempt = postToFigma.mock.calls.at(-1)?.[0].attempt as string;
    sendExport(attempt);
    await waitFor(() => expect(postToFigma).toHaveBeenCalledWith({ type: "semantic-screenshot-export", attempt }));

    window.dispatchEvent(new MessageEvent("message", { data: { pluginMessage: { type: "selection-error", attempt, code: "selection_export_failed" } } }));

    await waitFor(() => expect(observed).toMatchObject({ code: "conversion_failed" }), { timeout: 500 });
    expect(screen.getByRole("button", { name: "生成工程" })).toBeEnabled();
  });
});
