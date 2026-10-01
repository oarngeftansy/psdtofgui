import { useEffect, useMemo, useRef, useState } from "react";
import type { ExportedResource } from "../../../figma-plugin/src/assets";
import { MAX_REVIEW_PREVIEW_BYTES, type MainToUiMessage, type UiToMainMessage } from "../../../figma-plugin/src/contracts";
import type { NewProjectAdjustmentStrategy, NewProjectCandidate, NewProjectReview, ProjectWorkflowClient, WorkflowError } from "../../../figma-plugin/src/project-client";
import type { SelectionManifest, SelectionPreflight } from "../../../figma-plugin/src/selection";
import { NewProjectReviewPanel, suggestedReviewDispositions, type WriterPresentationStep } from "./NewProjectReviewPanel";
import { useNewProjectReviewPreviews } from "./useNewProjectReviewPreviews";

export type WriterClientLike = Pick<ProjectWorkflowClient, "createNewProjectCandidate" | "reviewNewProject" | "adjustNewProject" | "regenerateNewProject" | "approveNewProject" | "rejectNewProject" | "downloadNewProject" | "newProjectPreview">;
export type WriterUiState = "idle" | "exporting" | "running" | "reviewing" | "adjusting" | "regenerating" | "approving" | "rejected" | "failed" | "ready";
type WriterMessage = MainToUiMessage;
type WriterPostMessage = ((message: UiToMainMessage) => void);

const inlineStages: Array<[string, string]> = [["selection", "读取选择"], ["converting", "转换结构"], ["checking", "统一检查"], ["packaging", "打包候选"]];
const PROJECT_NAME_PATTERN = /^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$/u;
const PROJECT_NAME_ERROR = "工程名称仅支持中文、英文、数字、下划线和连字符，长度 1–64。";

function safeError(error: unknown): string {
  const code = (error as Partial<WorkflowError> | null)?.code;
  if (code === "aborted") return "操作已取消。";
  if (code === "timeout") return "处理超时，请重试。";
  if (code === "stale_candidate") return "候选工程已失效，请刷新选择后重新生成。";
  if (code === "review_required") return "请完成当前候选的统一检查。";
  if (code === "invalid_response") return "服务返回的数据无法识别，请刷新插件后重试。";
  if (code === "validation") return "工程名称格式不正确。仅支持中文、英文、数字、下划线和连字符，长度 1–64。";
  if (code === "selection_invalid") return "当前选择的数据未通过校验，请刷新选择后重试。";
  return "生成失败，请重试。";
}

function downloadBlob(download: { blob: Blob; downloadName: string }) {
  const url = URL.createObjectURL(download.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = download.downloadName;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  window.setTimeout(() => {
    anchor.remove();
    URL.revokeObjectURL(url);
  }, 1_000);
}

export function NewProjectWriterPanel({ client, postToFigma, onOpenUpdate, onOpenHifi }: { client: WriterClientLike; postToFigma: WriterPostMessage; onOpenUpdate?: () => void; onOpenHifi?: () => void }) {
  const [selection, setSelection] = useState<SelectionPreflight | null>(null);
  const [projectName, setProjectName] = useState("");
  const [uiState, setUiState] = useState<WriterUiState>("idle");
  const [serverStage, setServerStage] = useState("selection");
  const [candidate, setCandidate] = useState<NewProjectCandidate>();
  const [review, setReview] = useState<NewProjectReview>();
  const [warningAcknowledged, setWarningAcknowledged] = useState(false);
  const [invalidatedGenerations, setInvalidatedGenerations] = useState<number[]>([]);
  const [error, setError] = useState("");
  const [diagnostic, setDiagnostic] = useState("");
  const [diagnosticCopied, setDiagnosticCopied] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [selectionNotice, setSelectionNotice] = useState("");
  const [presentationStep, setPresentationStep] = useState<WriterPresentationStep>("automatic");
  const [reviewIndex, setReviewIndex] = useState(0);
  const [copyState, setCopyState] = useState<"idle" | "copying" | "copied" | "failed">("idle");
  const attempt = useRef("");
  const pendingName = useRef("");
  const controller = useRef<AbortController | undefined>(undefined);
  const hasCandidate = useRef(false);
  const candidateRef = useRef<NewProjectCandidate | undefined>(undefined);
  const operationToken = useRef(0);
  const locateAttempt = useRef("");
  const reviewAreaAttempt = useRef("");
  const mounted = useRef(true);

  const active = ["exporting", "running", "adjusting", "regenerating", "approving"].includes(uiState);
  const { previewObjects, previewBlobs, failedPreviewPaths, previewState } = useNewProjectReviewPreviews(client, candidate, review);
  const warningsSatisfied = Boolean(review && (review.warningIds.length === 0 || warningAcknowledged));
  const canApprove = Boolean(candidate && review && candidate.status === "awaiting_review" && candidate.artifactReady && review.approvable && warningsSatisfied && previewState === "ready");

  useEffect(() => {
    mounted.current = true;
    postToFigma({ type: "selection-preflight" });
    const receive = (event: MessageEvent<{ pluginMessage?: WriterMessage }>) => {
      const message = event.data?.pluginMessage;
      if (!message) return;
      if (message.type === "review-area-created") {
        if (message.attempt === reviewAreaAttempt.current) { reviewAreaAttempt.current = ""; setCopyState("copied"); }
        return;
      }
      if (message.type === "selection-error" && message.attempt === reviewAreaAttempt.current) {
        reviewAreaAttempt.current = "";
        setCopyState("failed");
        return;
      }
      if (message.type === "selection-preflight" || message.type === "selection-changed") {
        const initiatedLocate = message.type === "selection-changed" && Boolean(message.locateAttempt) && message.locateAttempt === locateAttempt.current;
        if (initiatedLocate) locateAttempt.current = "";
        if (message.type === "selection-changed" && !initiatedLocate && controller.current) {
          operationToken.current += 1;
          const stale = candidateRef.current;
          controller.current.abort();
          if (stale) void client.rejectNewProject(stale).catch(() => undefined);
          setCandidate(undefined);
          setReview(undefined);
          setWarningAcknowledged(false);
          setInvalidatedGenerations([]);
          setPresentationStep("automatic");
          setReviewIndex(0);
          setCopyState("idle");
          setError("");
          setUiState("idle");
          setSelectionNotice("生成期间选择已变化，本次生成已取消并清除。");
        } else if (message.type === "selection-changed" && !initiatedLocate && hasCandidate.current) {
          operationToken.current += 1;
          const stale = candidateRef.current;
          if (stale) void client.rejectNewProject(stale).catch(() => undefined);
          setCandidate(undefined); setReview(undefined); setUiState("idle");
          setPresentationStep("automatic"); setReviewIndex(0); setCopyState("idle");
          setSelectionNotice("Figma 选择已变化，旧候选已失效并清除。");
        }
        setSelection(message.preflight);
        return;
      }
      if (message.type !== "selection-export" && message.type !== "selection-error") return;
      if (message.attempt !== attempt.current) return;
      if (message.type === "selection-error") {
        setUiState("failed");
        const localErrors: Record<string, string> = {
          resource_canvas_invalid: "某个图片资源缺少有效的完整画布尺寸。",
          resource_transform_unavailable: "某个图片资源缺少可用的 Figma 变换矩阵。",
          resource_clone_unavailable: "某个图片资源不支持隔离克隆导出。",
          resource_clone_invalid: "某个图片资源的隔离副本变换无效。",
          resource_png_too_large: "某个隔离导出的 PNG 超过尺寸限制。",
          resource_png_invalid: "某个隔离导出的 PNG 数据无效。",
          resource_canvas_cleanup_failed: "临时资源画布清理失败，请关闭并重新打开插件。",
          resource_canvas_create_failed: "Figma 无法创建临时资源画布。",
          resource_clone_failed: "Figma 无法克隆某个图片资源。",
          resource_clone_attach_failed: "Figma 无法把某个资源副本放入临时画布。",
          resource_clone_position_failed: "Figma 无法在临时画布中还原某个资源的位置或角度。",
          resource_canvas_export_failed: "Figma 无法导出某个完整尺寸的临时资源画布。",
          resource_direct_export_failed: "Figma 无法直接导出某个图片资源。",
        };
        setError(message.code === "selection_changed" ? "当前选择已变化，请刷新后重试。" : localErrors[message.code] ?? `读取当前选择失败（${message.code}），请重试。`);
        return;
      }
      attempt.current = "";
      void runCandidate(message.manifest, message.resources);
    };
    window.addEventListener("message", receive);
    return () => {
      mounted.current = false;
      controller.current?.abort();
      window.removeEventListener("message", receive);
    };
  }, [postToFigma]);

  useEffect(() => { hasCandidate.current = Boolean(candidate); candidateRef.current = candidate; }, [candidate]);

  const runCandidate = async (manifest: SelectionManifest, resources: readonly ExportedResource[]) => {
    const token = ++operationToken.current;
    const current = new AbortController();
    controller.current?.abort();
    controller.current = current;
    setUiState("running");
    setServerStage("converting");
    try {
      const result = await client.createNewProjectCandidate(manifest, resources, pendingName.current, {
        signal: current.signal,
        onStage: (next) => { if (!current.signal.aborted) setServerStage(next.stage); },
      });
      if (current.signal.aborted || !mounted.current || token !== operationToken.current) return;
      setCandidate(result.candidate);
      if (result.candidate.status === "failed") {
        setError(result.candidate.diagnostics.map((item) => item.message).join("；") || "候选工程未通过生成检查。");
        setUiState("failed");
        return;
      }
      setUiState("reviewing");
      setServerStage("checking");
      const nextReview = await client.reviewNewProject(result.candidate, current.signal);
      if (current.signal.aborted || !mounted.current || token !== operationToken.current) return;
      setReview(nextReview);
      setWarningAcknowledged(false);
      setPresentationStep("automatic");
      setReviewIndex(0);
      setCopyState("idle");
      setUiState("reviewing");
      setError("");
    } catch (cause) {
      if (!mounted.current || token !== operationToken.current) return;
      if (current.signal.aborted) {
        setUiState("idle");
        setError("");
      } else {
        const code = (cause as Partial<WorkflowError> | null)?.code ?? "conversion_failed";
        setUiState("failed");
        setError(safeError(cause));
        const diagnosticStage = code === "validation" ? "创建候选" : code === "selection_invalid" ? "读取选择" : serverStage;
        setDiagnostic(`阶段：${diagnosticStage} · 错误码：${code}`);
        setDiagnosticCopied(false);
      }
    } finally {
      if (controller.current === current) controller.current = undefined;
    }
  };

  const begin = () => {
    const normalizedName = projectName.trim();
    if (!selection?.sendable || !normalizedName || active) return;
    if (!PROJECT_NAME_PATTERN.test(normalizedName)) {
      setError(PROJECT_NAME_ERROR);
      return;
    }
    setCandidate(undefined);
    setReview(undefined);
    setWarningAcknowledged(false);
    setInvalidatedGenerations([]);
    setPresentationStep("automatic");
    setReviewIndex(0);
    setCopyState("idle");
    setError("");
    setDiagnostic("");
    setDiagnosticCopied(false);
    setSelectionNotice("");
    pendingName.current = normalizedName;
    attempt.current = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    setUiState("exporting");
    setServerStage("selection");
    postToFigma({ type: "selection-export", attempt: attempt.current, mode: "writer" });
  };

  const refreshSelection = () => {
    if (active) return;
    setCandidate(undefined);
    setReview(undefined);
    setWarningAcknowledged(false);
    setInvalidatedGenerations([]);
    setPresentationStep("automatic");
    setReviewIndex(0);
    setCopyState("idle");
    setError("");
    setSelectionNotice("当前选择已刷新，旧候选已清除。");
    setUiState("idle");
    postToFigma({ type: "selection-preflight" });
  };

  const cancel = () => {
    operationToken.current += 1;
    attempt.current = "";
    const stale = candidateRef.current;
    controller.current?.abort();
    controller.current = undefined;
    if (stale) void client.rejectNewProject(stale).catch(() => undefined);
    setCandidate(undefined);
    setReview(undefined);
    setPresentationStep("automatic");
    setReviewIndex(0);
    setCopyState("idle");
    setUiState("idle");
    setError("");
  };

  const adjust = async (checkId: string, strategy: NewProjectAdjustmentStrategy) => {
    if (!candidate || !review) return;
    const token = ++operationToken.current;
    const current = new AbortController();
    controller.current = current;
    setUiState("adjusting");
    try {
      const adjusted = await client.adjustNewProject(candidate, review, checkId, strategy, current.signal);
      if (current.signal.aborted || !mounted.current || token !== operationToken.current) return;
      setCandidate(adjusted);
    } catch (cause) {
      if (!mounted.current || token !== operationToken.current) return;
      if (current.signal.aborted) { setUiState("idle"); setError(""); return; }
      setError(safeError(cause));
      setUiState("failed");
    } finally { if (token === operationToken.current && controller.current === current) controller.current = undefined; }
  };

  const regenerate = async () => {
    if (!candidate) return;
    const token = ++operationToken.current;
    const previous = candidate;
    const current = new AbortController();
    controller.current = current;
    setUiState("regenerating");
    setServerStage("regenerating");
    try {
      const next = await client.regenerateNewProject(previous, { signal: current.signal, onStage: (stage) => {
        if (!current.signal.aborted && token === operationToken.current) {
          candidateRef.current = stage;
          setCandidate(stage);
          setServerStage(stage.stage);
        }
      } });
      if (current.signal.aborted || !mounted.current || token !== operationToken.current) return;
      const nextReview = await client.reviewNewProject(next, current.signal);
      if (current.signal.aborted || !mounted.current || token !== operationToken.current) return;
      setInvalidatedGenerations((items) => [...items, previous.generation]);
      setCandidate(next);
      setReview(nextReview);
      setWarningAcknowledged(false);
      setPresentationStep("automatic");
      setReviewIndex(0);
      setCopyState("idle");
      setUiState("reviewing");
    } catch (cause) {
      if (!mounted.current || token !== operationToken.current) return;
      if (current.signal.aborted) { setUiState("idle"); setError(""); return; }
      setError(safeError(cause)); setUiState("failed");
    }
    finally { if (token === operationToken.current && controller.current === current) controller.current = undefined; }
  };

  const approveAndDownload = async () => {
    if (!candidate || !review || !canApprove) return;
    const token = ++operationToken.current;
    const current = new AbortController();
    controller.current = current;
    setUiState("approving");
    try {
      const approved = await client.approveNewProject(candidate, review, review.warningIds, current.signal);
      if (current.signal.aborted || !mounted.current || token !== operationToken.current) return;
      setCandidate(approved);
      const download = await client.downloadNewProject(approved, current.signal);
      if (current.signal.aborted || !mounted.current || token !== operationToken.current) return;
      downloadBlob(download);
      setUiState("ready");
    } catch (cause) {
      if (!mounted.current || token !== operationToken.current) return;
      if (current.signal.aborted) { setUiState("idle"); setError(""); return; }
      setError(safeError(cause)); setUiState("failed");
    }
    finally { if (token === operationToken.current && controller.current === current) controller.current = undefined; }
  };

  const reject = async () => {
    if (!candidate) return;
    const current = new AbortController();
    controller.current = current;
    try { setCandidate(await client.rejectNewProject(candidate, current.signal)); setUiState("rejected"); }
    catch (cause) { setError(safeError(cause)); setUiState("failed"); }
    finally { if (controller.current === current) controller.current = undefined; }
  };

  const redownload = async () => {
    if (!candidate) return;
    try { downloadBlob(await client.downloadNewProject(candidate)); }
    catch (cause) { setError(safeError(cause)); setUiState("failed"); }
  };

  const copyToFigma = async (sourceNodeId: string, generatedPreviewUrl?: string, previewWidth?: number, previewHeight?: number) => {
    const blob = generatedPreviewUrl ? previewBlobs[generatedPreviewUrl] : undefined;
    if (!blob || blob.type !== "image/png" || blob.size < 24 || blob.size > MAX_REVIEW_PREVIEW_BYTES
      || !Number.isInteger(previewWidth) || !Number.isInteger(previewHeight) || Number(previewWidth) <= 0 || Number(previewHeight) <= 0) {
      setCopyState("failed");
      return;
    }
    setCopyState("copying");
    const currentAttempt = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    reviewAreaAttempt.current = currentAttempt;
    try {
      const previewBytes = new Uint8Array(await blob.arrayBuffer());
      if (reviewAreaAttempt.current !== currentAttempt || previewBytes.length !== blob.size || previewBytes.length > MAX_REVIEW_PREVIEW_BYTES) return;
      postToFigma({ type: "create-review-area", attempt: currentAttempt, nodeId: sourceNodeId, previewBytes, previewWidth: Number(previewWidth), previewHeight: Number(previewHeight) });
    } catch {
      if (reviewAreaAttempt.current === currentAttempt) { reviewAreaAttempt.current = ""; setCopyState("failed"); }
    }
  };

  const primary = useMemo(() => {
    if (uiState === "adjusting") return <button className="primary-button" type="button" disabled={candidate?.status !== "adjusting"} onClick={regenerate}>重新生成候选</button>;
    if (uiState === "reviewing") return <button className="primary-button" type="button" disabled={!canApprove} onClick={approveAndDownload}>确认并下载 ZIP</button>;
    if (uiState === "ready") return <button className="primary-button" type="button" onClick={redownload}>再次下载</button>;
    if (uiState === "failed") return <button className="primary-button" type="button" onClick={begin}>重试</button>;
    if (uiState === "rejected") return <button className="primary-button" type="button" onClick={begin}>生成新候选</button>;
    const labels: Partial<Record<WriterUiState, string>> = { exporting: "正在读取选择", running: "正在生成候选", regenerating: "正在重新生成", approving: "正在确认" };
    return <button className="primary-button" type="button" disabled={active || !selection?.sendable || !projectName.trim()} onClick={begin}>{labels[uiState] ?? "生成候选工程"}</button>;
  // callbacks intentionally consume the current candidate/review snapshot.
  }, [active, canApprove, candidate, projectName, review, selection?.sendable, uiState]);

  const reviewVisible = Boolean(review && candidate && !["idle", "failed", "rejected"].includes(uiState));
  const reviewItemCount = review ? suggestedReviewDispositions(review).length : 0;
  const reviewStepAllowed = Boolean(review && review.approvable && warningsSatisfied && previewState === "ready");

  return <main className="writer-shell" data-presentation-step={reviewVisible ? presentationStep : "setup"} aria-label="新建 FairyGUI 工程 Writer">
    <header className="writer-header"><div><p className="writer-eyebrow">Figma → FairyGUI</p><h1>{reviewVisible ? selection?.manifest?.display_name ?? "新建工程" : "新建工程"}</h1></div>{reviewVisible ? <p className="writer-header-selection">{selection?.nodeCount ?? 0} 个图层 · {selection?.assetCount ?? 0} 个资源</p> : <div className="writer-header-tools">{onOpenHifi && <nav className="writer-mode-tabs" aria-label="产品功能"><button type="button" role="tab" className="is-active" aria-selected="true">新建工程</button><button type="button" role="tab" aria-selected="false" onClick={onOpenHifi}>HIFI 替换</button></nav>}<div className="writer-overflow"><button type="button" className="icon-button" aria-label="更多操作" aria-expanded={menuOpen} onClick={() => setMenuOpen((value) => !value)}>•••</button>{menuOpen && <div role="menu"><button role="menuitem" type="button" onClick={onOpenUpdate}>更新现有工程</button></div>}</div></div>}</header>
    {reviewVisible && <div className="writer-presentation-rail" aria-label="审核流程"><span className={presentationStep === "automatic" ? "is-current" : "is-done"}>自动转换</span><span className={presentationStep === "review" ? "is-current" : presentationStep === "confirm" ? "is-done" : ""}>建议审核</span><span className={presentationStep === "confirm" ? "is-current" : ""}>确认下载</span></div>}
    <div className="writer-step-scroll">
      {!reviewVisible && <>
        <section className="writer-selection" aria-labelledby="writer-selection-title"><div><h2 id="writer-selection-title">当前选择</h2><strong>{selection?.manifest?.display_name ?? "未命名选择"}</strong><p>{selection?.sendable ? `${selection.nodeCount} 个图层 · ${selection.assetCount} 个资源` : "请选择要生成的图层"}</p><div className="writer-blueprint-thumbnail" aria-label="结构蓝图缩略图">{selection?.manifest?.top_level_nodes.slice(0, 3).map((node) => <span key={node.id}>{node.type.slice(0, 1)}</span>)}</div><p className="writer-blueprint">结构蓝图</p>{selection?.manifest?.top_level_nodes.slice(0, 2).map((node) => <p className="writer-blueprint" key={node.id}>{node.name} · {node.type}</p>)}</div><button className="secondary-button compact" type="button" disabled={active} onClick={refreshSelection}>刷新选择</button></section>
        {selection && selection.warnings.length > 0 && <div role="status" className="writer-selection-summary"><strong>已自动处理 {selection.warnings.length} 项视觉兼容问题</strong><p>{Array.from(new Set(selection.warnings.map((warning) => warning.message))).slice(0, 3).join("；")}{selection.warnings.length > 3 ? "。详细结果会在候选审核中按类型汇总。" : ""}</p></div>}
        {selectionNotice && <p className="writer-inline-note" role="status">{selectionNotice}</p>}
        <section className="writer-setup" aria-label="工程设置"><label>工程名称<input required value={projectName} disabled={active || Boolean(candidate)} onChange={(event) => setProjectName(event.currentTarget.value)} placeholder="例如 InventoryUI" /></label><div className="writer-pills"><span>FairyGUI 6.1.4</span><span>新建独立工程</span></div><details><summary>可选设置</summary><div className="writer-pills"><span>Unity</span></div></details></section>
        {active && <><ol className="writer-stages" aria-label="生成阶段">{inlineStages.map(([id, label]) => <li className={id === serverStage ? "is-current" : ""} key={id}>{label}</li>)}</ol>{candidate && <p role="status">服务器阶段：{candidate.stage} · {candidate.progress}%</p>}</>}
      </>}
      {invalidatedGenerations.map((generation) => <p className="writer-invalidated" role="status" key={generation}>候选 v{generation} 已失效，不可确认或下载。</p>)}
      {reviewVisible && review && <NewProjectReviewPanel review={review} step={presentationStep} reviewIndex={reviewIndex} onReviewIndexChange={(index) => { reviewAreaAttempt.current = ""; setReviewIndex(index); setCopyState("idle"); }} previewObjects={previewObjects} failedPreviewPaths={failedPreviewPaths} warningAcknowledged={warningAcknowledged} onWarningAcknowledged={setWarningAcknowledged} copyState={copyState} disabled={active || uiState === "adjusting" && candidate?.status !== "adjusting"} onLocate={(nodeId) => { locateAttempt.current = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}`; postToFigma({ type: "locate-node", nodeId, attempt: locateAttempt.current }); }} onAdjust={adjust} onCopyReviewArea={(item, url, width, height) => { void copyToFigma(item.sourceNodeId, url, width, height); }} />}
      {reviewVisible && review && presentationStep === "confirm" && review.warningIds.length > 0 && !warningAcknowledged && <div className="writer-approval-blocker" role="status"><strong>待完成：请确认转换警告</strong><label className="writer-ack"><input type="checkbox" checked={warningAcknowledged} disabled={active} onChange={(event) => setWarningAcknowledged(event.currentTarget.checked)} /> 我已查看图示和影响，并接受当前转换方案</label></div>}
      {uiState === "ready" && candidate?.downloadName && <div className="writer-ready-summary" role="status"><strong>{candidate.downloadName}</strong><p>SHA-256 {candidate.sha256?.slice(0, 12)}… · {candidate.byteSize} bytes</p></div>}
      {uiState === "rejected" && <p className="writer-terminal" role="status">当前候选已拒绝，不会提供下载。</p>}
      {error && <p className="writer-inline-error" role="alert">{error}</p>}
      {diagnostic && <div className="writer-inline-note"><p>{diagnostic}</p><button className="secondary-button compact" type="button" onClick={() => { void navigator.clipboard?.writeText(diagnostic).then(() => setDiagnosticCopied(true)).catch(() => setDiagnosticCopied(false)); }}>{diagnosticCopied ? "已复制" : "复制诊断信息"}</button></div>}
      {previewState === "pending" && review && <p role="status">正在验证预览证据…</p>}
      {previewState === "failed" && <p className="writer-inline-error" role="alert">预览证据加载失败，已阻止确认；请重试生成。</p>}
    </div>
    <footer className="writer-actions">
      {active ? <><button className="secondary-button" type="button" onClick={cancel}>取消</button>{primary}</> : reviewVisible && uiState === "reviewing" ? <>
        <button className="secondary-button" type="button" onClick={presentationStep === "automatic" ? reject : () => setPresentationStep(presentationStep === "review" ? "automatic" : reviewItemCount ? "review" : "automatic")}>{presentationStep === "automatic" ? "拒绝候选" : "返回上一步"}</button>
        {presentationStep === "automatic" && <button className="primary-button" type="button" onClick={() => setPresentationStep(reviewItemCount ? "review" : "confirm")}>{reviewItemCount ? "查看建议审核" : "查看最终检查"}</button>}
        {presentationStep === "review" && <button className="primary-button" type="button" disabled={!reviewStepAllowed} onClick={() => setPresentationStep("confirm")}>确认审核结果</button>}
        {presentationStep === "confirm" && <button className="primary-button" type="button" disabled={!canApprove} onClick={approveAndDownload}>确认并下载 ZIP</button>}
      </> : primary}
    </footer>
  </main>;
}
