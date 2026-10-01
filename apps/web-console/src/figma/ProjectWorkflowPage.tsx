import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ExportedResource } from "../../../figma-plugin/src/assets";
import { MAX_SEMANTIC_SCREENSHOT_BYTES } from "../../../figma-plugin/src/contracts";
import type { MainToUiMessage, UiToMainMessage } from "../../../figma-plugin/src/contracts";
import type { ProjectOption, SemanticScreenshot, WorkflowResult, WorkflowRunOptions, WorkflowStage } from "../../../figma-plugin/src/project-client";
import { isSupportedProjectArchive, PROJECT_ARCHIVE_ACCEPT, WorkflowError } from "../../../figma-plugin/src/project-client";
import type { SelectionManifest, SelectionPreflight } from "../../../figma-plugin/src/selection";
import { NewProjectWriterPanel, type WriterClientLike } from "./NewProjectWriterPanel";
import { ExistingProjectUpdatePanel } from "./ExistingProjectUpdatePanel";
import { HifiReplacementPanel, type HifiClientLike } from "./HifiReplacementPanel";

// Keep the completed HIFI workflow available in code while it is hidden from the plugin UI.
const HIFI_REPLACEMENT_VISIBLE = false;

export type ProjectWorkflowClientLike = {
  options(signal?: AbortSignal): Promise<ProjectOption[]>;
  runCreate(manifest: SelectionManifest, resources: readonly ExportedResource[], params: { templateId: string; projectName: string }, onStage?: (stage: WorkflowStage) => void, options?: WorkflowRunOptions): Promise<WorkflowResult>;
  runUpdate(manifest: SelectionManifest, resources: readonly ExportedResource[], archive: File, onStage?: (stage: WorkflowStage) => void, options?: WorkflowRunOptions): Promise<WorkflowResult>;
} & Partial<WriterClientLike> & Partial<HifiClientLike>;

type MainMessage = MainToUiMessage;
type UiMessage = UiToMainMessage;
type Mode = "create" | "update";
type WorkflowRequest =
  | { mode: "create"; templateId: string; projectName: string }
  | { mode: "update"; archive: File };

const stageLabels: Record<WorkflowStage["stage"], string> = {
  uploading: "上传当前选择",
  parsing: "解析工程",
  converting: "转换",
  checking: "检查",
  awaiting_screenshot_consent: "等待截图授权",
  packaging: "打包",
  ready: "已完成",
  failed: "生成失败",
};

function postToParent(message: UiMessage) {
  window.parent.postMessage({ pluginMessage: message }, "*");
}

function safeError(error: unknown): string {
  if (error instanceof WorkflowError) return error.message;
  return "生成失败，请重试。";
}

function validArchive(file: File | undefined): boolean {
  return Boolean(file && isSupportedProjectArchive(file));
}

function errorForExport(code: string): string {
  if (code === "selection_empty") return "请选择要导出的图层";
  if (code === "selection_changed") return "当前选择已变化，请重新生成。";
  if (code === "selection_too_large") return "当前选择内容过大，请缩小选择范围后重试";
  return "导出当前选择失败，请重试。";
}

export function LegacyProjectWorkflowPage({ client, postToFigma = postToParent, initialMode = "create", updateOnly = false }: { client: ProjectWorkflowClientLike; postToFigma?: (message: UiMessage) => void; initialMode?: Mode; updateOnly?: boolean }) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [selection, setSelection] = useState<SelectionPreflight | null>(null);
  const [projectName, setProjectName] = useState("");
  const [archive, setArchive] = useState<File>();
  const [options, setOptions] = useState<ProjectOption[]>([]);
  const [fairyguiVersion, setFairyguiVersion] = useState("");
  const [targetPlatform, setTargetPlatform] = useState("");
  const [stage, setStage] = useState<WorkflowStage | null>(null);
  const [result, setResult] = useState<WorkflowResult | null>(null);
  const [error, setError] = useState("");
  const [errorRetryable, setErrorRetryable] = useState(false);
  const [optionsError, setOptionsError] = useState("");
  const [waitingForExport, setWaitingForExport] = useState(false);
  const [workflowRunning, setWorkflowRunning] = useState(false);
  const [screenshotConsent, setScreenshotConsent] = useState<{ jobId: string } | null>(null);
  const attempt = useRef("");
  const running = useRef(false);
  const exportRequested = useRef(false);
  const pendingWorkflow = useRef<WorkflowRequest | null>(null);
  const optionsAbort = useRef<AbortController | null>(null);
  const workflowAbort = useRef<AbortController | null>(null);
  const consentDecision = useRef<null | { jobId: string; resolve(value: boolean): void; reject(error: unknown): void }>(null);
  const screenshotRequest = useRef<null | { jobId: string; resolve(value: SemanticScreenshot): void; reject(error: unknown): void }>(null);

  const loadOptions = useCallback(() => {
    optionsAbort.current?.abort();
    const controller = new AbortController();
    optionsAbort.current = controller;
    setOptionsError("");
    void client.options(controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setOptions(next);
      if (next[0]) {
        setFairyguiVersion((value) => value || next[0]!.fairyguiVersion);
        setTargetPlatform((value) => value || next[0]!.targetPlatform);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setOptionsError("无法加载工程选项，请重试。");
    });
  }, [client]);

  useEffect(() => {
    loadOptions();
    postToFigma({ type: "selection-preflight" });
    return () => {
      optionsAbort.current?.abort();
      workflowAbort.current?.abort();
    };
  }, [loadOptions, postToFigma]);

  const matchingOptions = useMemo(
    () => options.filter((option) => option.fairyguiVersion === fairyguiVersion && option.targetPlatform === targetPlatform),
    [fairyguiVersion, options, targetPlatform],
  );
  const selectedOption = matchingOptions[0];
  const versions = [...new Set(options.map((option) => option.fairyguiVersion))];
  const platforms = [...new Set(options.filter((option) => option.fairyguiVersion === fairyguiVersion).map((option) => option.targetPlatform))];
  const controlsLocked = waitingForExport || workflowRunning;
  const readyToGenerate = Boolean(selection?.sendable && !controlsLocked && !exportRequested.current && !running.current && (mode === "create" ? projectName.trim() && selectedOption : validArchive(archive)));

  useEffect(() => {
    const receive = (event: MessageEvent<{ pluginMessage?: MainMessage }>) => {
      const message = event.data?.pluginMessage;
      if (!message) return;
      if (message.type === "selection-preflight" || message.type === "selection-changed") {
        setSelection(message.preflight);
        setError("");
        return;
      }
      if (message.type !== "selection-error" && message.type !== "selection-export" && message.type !== "semantic-screenshot-export") return;
      if (message.type === "semantic-screenshot-export" && screenshotRequest.current) {
        const pending = screenshotRequest.current;
        screenshotRequest.current = null;
        if (message.attempt !== attempt.current || message.mimeType !== "image/png" || !(message.bytes instanceof Uint8Array) || !message.bytes.length || message.bytes.length > MAX_SEMANTIC_SCREENSHOT_BYTES) {
          pending.reject(new WorkflowError("invalid_response"));
        } else {
          pending.resolve({ mimeType: "image/png", bytes: message.bytes });
        }
        return;
      }
      if (message.attempt !== attempt.current) return;
      if (message.type === "selection-error") {
        if (screenshotRequest.current) {
          const pending = screenshotRequest.current;
          screenshotRequest.current = null;
          pending.reject(new WorkflowError(message.code === "selection_too_large" ? "validation" : "conversion_failed"));
          return;
        }
        running.current = false;
        exportRequested.current = false;
        pendingWorkflow.current = null;
        setWaitingForExport(false);
        setWorkflowRunning(false);
        setStage(null);
        setErrorRetryable(true);
        setError(errorForExport(message.code));
        return;
      }
      if (message.type === "semantic-screenshot-export") return;
      if (message.type === "selection-export" && !running.current) {
        const request = pendingWorkflow.current;
        if (!request) return;
        pendingWorkflow.current = null;
        exportRequested.current = false;
        running.current = true;
        setWaitingForExport(false);
        setWorkflowRunning(true);
        const controller = new AbortController();
        workflowAbort.current = controller;
        const onStage = (next: WorkflowStage) => setStage(next);
        const workflowOptions: WorkflowRunOptions = {
          signal: controller.signal,
          onScreenshotConsent: ({ jobId, signal }) => new Promise<boolean>((resolve, reject) => {
            if (signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
            if (consentDecision.current) { reject(new WorkflowError("invalid_response")); return; }
            const abort = () => {
              if (consentDecision.current?.jobId !== jobId) return;
              consentDecision.current = null;
              setScreenshotConsent(null);
              reject(new DOMException("Aborted", "AbortError"));
            };
            signal.addEventListener("abort", abort, { once: true });
            consentDecision.current = {
              jobId,
              resolve: (approved) => { signal.removeEventListener("abort", abort); resolve(approved); },
              reject: (cause) => { signal.removeEventListener("abort", abort); reject(cause); },
            };
            setScreenshotConsent({ jobId });
          }),
          requestScreenshot: (jobId, signal) => new Promise<SemanticScreenshot>((resolve, reject) => {
            if (signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
            if (screenshotRequest.current) { reject(new WorkflowError("invalid_response")); return; }
            const abort = () => {
              if (screenshotRequest.current?.jobId !== jobId) return;
              screenshotRequest.current = null;
              reject(new DOMException("Aborted", "AbortError"));
            };
            signal.addEventListener("abort", abort, { once: true });
            screenshotRequest.current = {
              jobId,
              resolve: (value) => { signal.removeEventListener("abort", abort); resolve(value); },
              reject: (cause) => { signal.removeEventListener("abort", abort); reject(cause); },
            };
            postToFigma({ type: "semantic-screenshot-export", attempt: attempt.current });
          }),
        };
        const operation = request.mode === "create"
          ? client.runCreate(message.manifest, message.resources, { templateId: request.templateId, projectName: request.projectName }, onStage, workflowOptions)
          : client.runUpdate(message.manifest, message.resources, request.archive, onStage, workflowOptions);
        void operation.then((next) => {
          setResult(next);
          setStage({ stage: "ready", progress: 100 });
          setError("");
        }).catch((cause: unknown) => {
          setStage(null);
          setErrorRetryable(true);
          setError(safeError(cause));
        }).finally(() => {
          consentDecision.current = null;
          screenshotRequest.current = null;
          workflowAbort.current = null;
          setScreenshotConsent(null);
          running.current = false;
          setWorkflowRunning(false);
        });
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [client, postToFigma]);

  const refreshSelection = () => {
    setError("");
    setErrorRetryable(false);
    postToFigma({ type: "selection-preflight" });
  };
  const decideScreenshot = (approved: boolean) => {
    const pending = consentDecision.current;
    if (!pending) return;
    consentDecision.current = null;
    setScreenshotConsent(null);
    pending.resolve(approved);
  };
  const generate = () => {
    if (!readyToGenerate) return;
    const request: WorkflowRequest | null = mode === "create" && selectedOption
      ? { mode, templateId: selectedOption.templateId, projectName: projectName.trim() }
      : mode === "update" && archive
        ? { mode, archive }
        : null;
    if (!request) return;
    setResult(null);
    setError("");
    setErrorRetryable(false);
    setStage({ stage: "uploading", progress: 0 });
    const nextAttempt = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    attempt.current = nextAttempt;
    exportRequested.current = true;
    pendingWorkflow.current = request;
    setWaitingForExport(true);
    postToFigma({ type: "selection-export", attempt: nextAttempt });
  };
  const chooseVersion = (value: string) => {
    setFairyguiVersion(value);
    const first = options.find((option) => option.fairyguiVersion === value);
    setTargetPlatform(first?.targetPlatform ?? "");
  };
  const download = () => {
    if (!result) return;
    const url = URL.createObjectURL(result.blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = result.downloadName;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return <main className="project-workflow" aria-label="FairyGUI 工程工作流">
    <section className="workflow-step" aria-labelledby="selection-step-title">
      <h1 id="selection-step-title">1. Figma 选择</h1>
      <p>{selection?.sendable ? `已准备当前选择（${selection.nodeCount} 个图层，${selection.assetCount} 个资源）。` : "请选择要生成 FairyGUI 工程的图层。"}</p>
      <button className="secondary-button" type="button" disabled={controlsLocked} onClick={refreshSelection}>刷新选择</button>
      {selection?.warnings.map((warning, index) => <p className="message message-warning" role="alert" key={`${warning.code}-${index}`}>{warning.message}</p>)}
    </section>

    <section className="workflow-step" aria-labelledby="mode-step-title">
      <h2 id="mode-step-title">2. 新建或更新</h2>
      {!updateOnly && <fieldset className="workflow-mode"><legend>操作方式</legend>
        <label><input type="radio" name="workflow-mode" checked={mode === "create"} disabled={controlsLocked} onChange={() => setMode("create")} /> 新建工程</label>
        <label><input type="radio" name="workflow-mode" checked={mode === "update"} disabled={controlsLocked} onChange={() => setMode("update")} /> 更新现有工程</label>
      </fieldset>}
      {mode === "create" ? <div className="workflow-fields">
        <label>工程名称<input value={projectName} required disabled={controlsLocked} onChange={(event) => setProjectName(event.target.value)} /></label>
        <label>FairyGUI 版本<select value={fairyguiVersion} required disabled={controlsLocked} onChange={(event) => chooseVersion(event.target.value)}><option value="" disabled>请选择版本</option>{versions.map((version) => <option value={version} key={version}>{version}</option>)}</select></label>
        <label>目标平台<select value={targetPlatform} required disabled={controlsLocked} onChange={(event) => setTargetPlatform(event.target.value)}><option value="" disabled>请选择平台</option>{platforms.map((platform) => <option value={platform} key={platform}>{platform}</option>)}</select></label>
      </div> : <div className="workflow-fields">
        <label>现有 FairyGUI 工程压缩包<input type="file" accept={PROJECT_ARCHIVE_ACCEPT} required disabled={controlsLocked} onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          setArchive(file);
          setErrorRetryable(false);
          setError(file && !validArchive(file) ? "请选择有效的 FairyGUI 工程压缩包。" : "");
        }} /></label>
        <p>原始压缩包不会被修改；生成结果将作为新的 ZIP 下载文件提供。</p>
      </div>}
    </section>

    <section className="workflow-step" aria-labelledby="running-step-title">
      <h2 id="running-step-title">3. 生成与检查</h2>
      <button className="primary-button" type="button" disabled={!readyToGenerate} onClick={generate}>生成工程</button>
      {(waitingForExport || stage) && <div className="workflow-progress"><progress value={stage?.progress ?? 0} max="100">{stage?.progress ?? 0}%</progress><output role="status">{waitingForExport ? "正在读取当前选择" : `${stageLabels[stage!.stage]} ${stage!.progress}%`}</output></div>}
      {screenshotConsent && <div className="message message-warning" role="group" aria-label="截图上传授权">
        <p>仅靠图层结构无法可靠判断部分组件。是否允许上传当前选择的截图辅助识别？</p>
        <button className="primary-button" type="button" onClick={() => decideScreenshot(true)}>允许并继续</button>
        <button className="secondary-button" type="button" onClick={() => decideScreenshot(false)}>不上传，按规则继续</button>
      </div>}
      {optionsError && <div className="message message-error" role="alert"><p>{optionsError}</p><button className="secondary-button" type="button" onClick={loadOptions}>重试</button></div>}
      {error && <div className="message message-error" role="alert"><p>{error}</p>{errorRetryable && <button className="secondary-button" type="button" onClick={generate}>重试</button>}</div>}
      {result?.package.diagnostics.map((diagnostic, index) => <p className={`message message-${diagnostic.severity.toLowerCase()}`} role={diagnostic.severity === "ERROR" ? "alert" : undefined} key={`${diagnostic.code}-${index}`}>{diagnostic.message}</p>)}
    </section>

    <section className="workflow-step" aria-labelledby="download-step-title">
      <h2 id="download-step-title">4. 下载工程</h2>
      {result ? <><p>工程已生成，可下载 {result.downloadName}。</p><button className="primary-button" type="button" onClick={download}>下载工程</button></> : <p>完成生成与检查后，可在这里下载新的 FairyGUI 工程。</p>}
    </section>
  </main>;
}

function isWriterClient(client: ProjectWorkflowClientLike): client is ProjectWorkflowClientLike & WriterClientLike {
  return ["createNewProjectCandidate", "reviewNewProject", "adjustNewProject", "regenerateNewProject", "approveNewProject", "rejectNewProject", "downloadNewProject", "newProjectPreview"].every((name) => typeof client[name as keyof ProjectWorkflowClientLike] === "function");
}

function isHifiClient(client: ProjectWorkflowClientLike): client is ProjectWorkflowClientLike & HifiClientLike {
  return ["uploadProject", "hifiTargets", "createHifiReplacement", "hifiMapping", "decideHifiMapping", "buildHifiReplacement", "reviewHifiReplacement", "approveHifiReplacement", "rejectHifiReplacement", "downloadHifiReplacement"].every((name) => typeof client[name as keyof ProjectWorkflowClientLike] === "function");
}

export function ProjectWorkflowPage({ client, postToFigma = postToParent, defaultMode = "legacy" }: { client: ProjectWorkflowClientLike; postToFigma?: (message: UiMessage) => void; defaultMode?: "legacy" | "writer" }) {
  const [showUpdate, setShowUpdate] = useState(false);
  const [productMode, setProductMode] = useState<"new" | "hifi">("new");
  if (defaultMode === "writer") {
    if (!isWriterClient(client)) return <main className="writer-shell"><p role="alert">Writer 客户端不可用。</p></main>;
    if (showUpdate) return <ExistingProjectUpdatePanel client={client} postToFigma={postToFigma} onBack={() => setShowUpdate(false)} />;
    if (productMode === "hifi") {
      if (!isHifiClient(client)) return <main className="writer-shell"><p role="alert">HIFI 替换客户端不可用。</p><button type="button" onClick={() => setProductMode("new")}>返回新建工程</button></main>;
      return <HifiReplacementPanel client={client} postToFigma={postToFigma} onOpenNew={() => setProductMode("new")} />;
    }
    return <NewProjectWriterPanel
      client={client}
      postToFigma={postToFigma}
      onOpenUpdate={() => setShowUpdate(true)}
      onOpenHifi={HIFI_REPLACEMENT_VISIBLE ? () => setProductMode("hifi") : undefined}
    />;
  }
  return <LegacyProjectWorkflowPage client={client} postToFigma={postToFigma} />;
}
