import { useEffect, useMemo, useRef, useState } from "react";
import type { HifiMappingAction, HifiMappingDraft, HifiMappingItem, HifiProjectTree, HifiReplacement, HifiReplacementReview, HifiTargetRef, ProjectView, ProjectWorkflowClient, PsdInspection } from "../../../figma-plugin/src/project-client";
import { PROJECT_ARCHIVE_ACCEPT } from "../../../figma-plugin/src/project-client";
import type { MainToUiMessage, UiToMainMessage } from "../../../figma-plugin/src/contracts";
import type { SelectionPreflight } from "../../../figma-plugin/src/selection";
import { HifiMappingPanel } from "./HifiMappingPanel";
import { HifiReplacementReviewActions, HifiReplacementReviewPanel, type HifiEditorCheckState } from "./HifiReplacementReviewPanel";
import { HifiTargetPicker, selectedHifiTarget, type HifiTargetSelection } from "./HifiTargetPicker";
import { HifiSourcePicker, type HifiSourceMode } from "./HifiSourcePicker";

export type HifiClientLike = Pick<ProjectWorkflowClient, "inspectPsd" | "uploadProject" | "hifiTargets" | "createHifiReplacement" | "hifiMapping" | "decideHifiMapping" | "buildHifiReplacement" | "reviewHifiReplacement" | "approveHifiReplacement" | "rejectHifiReplacement" | "downloadHifiReplacement">;
type Stage = "prepare" | "mapping" | "review" | "delivered";
const EMPTY_CHECKS: HifiEditorCheckState = { layout: false, references: false, interactions: false };

function downloadBlob(download: { blob: Blob; downloadName: string }) {
  const url = URL.createObjectURL(download.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = download.downloadName;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function workflowCode(error: unknown): string | undefined {
  return (error as { code?: string } | null)?.code;
}

function errorMessage(error: unknown): string {
  const code = workflowCode(error);
  if (code === "invalid_zip") return "工程 ZIP 无效或无法解析。";
  if (code === "selection_invalid") return "当前 Figma 选择无法上传，请刷新后重试。";
  if (code === "hifi_mapping_stale") return "映射已在其他操作中更新，已重新加载最新结果。";
  if (code === "hifi_target_stale") return "旧工程或目标组件已经变化，请重新选择目标。";
  if (code === "hifi_editor_checks_incomplete") return "三项 FairyGUI Editor 检查必须全部完成。";
  if (["conversion_conflict", "stale_candidate", "hifi_candidate_stale"].includes(code ?? "")) return "工程、映射或候选已变化，请重新检查。";
  if (code === "unauthorized") return "插件没有访问本地服务的权限。";
  if (code === "aborted") return "操作已取消。";
  return "HIFI 替换操作失败，请重试。";
}

export function HifiReplacementPanel({ client, postToFigma, onOpenNew }: { client: HifiClientLike; postToFigma(message: UiToMainMessage): void; onOpenNew(): void }) {
  const [selection, setSelection] = useState<SelectionPreflight | null>(null);
  const [sourceMode, setSourceMode] = useState<HifiSourceMode>("psd");
  const [psdInspection, setPsdInspection] = useState<PsdInspection>();
  const [archive, setArchive] = useState<File>();
  const [project, setProject] = useState<ProjectView>();
  const [tree, setTree] = useState<HifiProjectTree>();
  const [selected, setSelected] = useState<HifiTargetSelection>();
  const [stage, setStage] = useState<Stage>("prepare");
  const [mapping, setMapping] = useState<HifiMappingDraft>();
  const [replacement, setReplacement] = useState<HifiReplacement>();
  const [review, setReview] = useState<HifiReplacementReview>();
  const [currentItemId, setCurrentItemId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [checks, setChecks] = useState<HifiEditorCheckState>(EMPTY_CHECKS);
  const attempt = useRef("");
  const pendingTarget = useRef<HifiTargetRef | undefined>(undefined);
  const controller = useRef<AbortController | undefined>(undefined);
  const operationToken = useRef(0);
  const mounted = useRef(true);
  const projectRef = useRef<ProjectView | undefined>(undefined);
  const stageRef = useRef<Stage>("prepare");
  projectRef.current = project;
  stageRef.current = stage;

  const target = useMemo(() => project && tree ? selectedHifiTarget(project, tree, selected) : undefined, [project, selected, tree]);
  const singleRoot = selection?.manifest?.top_level_nodes.length === 1;
  const startOperation = () => {
    controller.current?.abort();
    const active = new AbortController();
    controller.current = active;
    const token = ++operationToken.current;
    setBusy(true);
    return { active, token };
  };
  const isCurrent = (active: AbortController, token: number) => mounted.current && !active.signal.aborted && operationToken.current === token;
  const finishOperation = (active: AbortController, token: number) => {
    if (isCurrent(active, token)) setBusy(false);
    if (controller.current === active) controller.current = undefined;
  };
  const cancelOperation = () => {
    controller.current?.abort();
    controller.current = undefined;
    attempt.current = "";
    operationToken.current += 1;
    setBusy(false);
    setError("操作已取消。");
  };

  useEffect(() => {
    mounted.current = true;
    postToFigma({ type: "selection-preflight" });
    const receive = (event: MessageEvent<{ pluginMessage?: MainToUiMessage }>) => {
      const message = event.data?.pluginMessage;
      if (!message) return;
      if (message.type === "selection-preflight" || message.type === "selection-changed") {
        setSelection(message.preflight);
        if (message.type === "selection-changed" && stageRef.current !== "prepare") {
          controller.current?.abort();
          operationToken.current += 1;
          setBusy(false);
          setStage("prepare");
          setReplacement(undefined);
          setMapping(undefined);
          setReview(undefined);
          setChecks(EMPTY_CHECKS);
          setError("Figma 选择已变化，旧映射和候选已失效；请重新开始对齐。");
        }
        return;
      }
      if (message.type !== "selection-export" && message.type !== "selection-error" || message.attempt !== attempt.current) return;
      const active = controller.current;
      const token = operationToken.current;
      if (!active) return;
      if (message.type === "selection-error") {
        setBusy(false);
        setError("读取当前 Figma 选择失败，请重试。");
        return;
      }
      const activeTarget = pendingTarget.current;
      const activeProject = projectRef.current;
      if (!activeProject || !activeTarget) return;
      void client.createHifiReplacement(message.manifest, message.resources, activeProject, activeTarget, active.signal)
        .then((result) => {
          if (!isCurrent(active, token)) return;
          setReplacement(result.replacement);
          setMapping(result.mapping);
          setCurrentItemId(result.mapping.items.find((item) => !item.action)?.itemId ?? result.mapping.items[0]?.itemId);
          setStage("mapping");
          setError("");
        })
        .catch((cause) => { if (isCurrent(active, token)) setError(errorMessage(cause)); })
        .finally(() => finishOperation(active, token));
    };
    window.addEventListener("message", receive);
    return () => { mounted.current = false; controller.current?.abort(); window.removeEventListener("message", receive); };
  }, [client, postToFigma]);

  const chooseArchive = async (file: File | undefined) => {
    setArchive(file);
    setProject(undefined);
    setTree(undefined);
    setSelected(undefined);
    setReplacement(undefined);
    setMapping(undefined);
    setReview(undefined);
    setError("");
    if (!file) return;
    const { active, token } = startOperation();
    try {
      const uploaded = await client.uploadProject(file, active.signal);
      const targets = await client.hifiTargets(uploaded.projectId, active.signal);
      if (!isCurrent(active, token)) return;
      setProject(uploaded);
      setTree(targets);
    } catch (cause) {
      if (isCurrent(active, token)) setError(errorMessage(cause));
    } finally { finishOperation(active, token); }
  };

  const choosePsd = async (file: File | undefined) => {
    setPsdInspection(undefined);
    setError("");
    if (!file) return;
    const { active, token } = startOperation();
    try {
      const report = await client.inspectPsd(file, active.signal);
      if (isCurrent(active, token)) setPsdInspection(report);
    } catch (cause) {
      if (isCurrent(active, token)) setError(errorMessage(cause));
    } finally { finishOperation(active, token); }
  };

  const start = () => {
    if (!target || !selection?.sendable || busy) return;
    startOperation();
    pendingTarget.current = target;
    attempt.current = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    setError("");
    postToFigma({ type: "selection-export", attempt: attempt.current });
  };

  const decide = async (item: HifiMappingItem, action: HifiMappingAction, figmaNodeId?: string) => {
    if (!replacement || !mapping || busy) return;
    const { active, token } = startOperation();
    try {
      const next = await client.decideHifiMapping(replacement.sessionId, mapping.mappingRevision, item.itemId, action, figmaNodeId, active.signal);
      const nextMapping = await client.hifiMapping(next.sessionId, active.signal);
      if (!isCurrent(active, token)) return;
      setReplacement(next);
      setMapping(nextMapping);
      setReview(undefined);
      setChecks(EMPTY_CHECKS);
      setCurrentItemId(nextMapping.items.find((entry) => !entry.action)?.itemId ?? item.itemId);
      setError("");
    } catch (cause) {
      if (!isCurrent(active, token)) return;
      if (workflowCode(cause) === "hifi_mapping_stale") {
        try {
          const latest = await client.hifiMapping(replacement.sessionId, active.signal);
          if (isCurrent(active, token)) setMapping(latest);
        } catch { /* Keep the original conflict message. */ }
      }
      if (isCurrent(active, token)) setError(errorMessage(cause));
    } finally { finishOperation(active, token); }
  };

  const build = async () => {
    if (!replacement || !mapping || mapping.unresolvedCount || busy) return;
    const { active, token } = startOperation();
    try {
      const built = await client.buildHifiReplacement(replacement.sessionId, mapping.mappingRevision, active.signal);
      const nextReview = await client.reviewHifiReplacement(replacement.sessionId, active.signal);
      if (!isCurrent(active, token)) return;
      setReplacement(built);
      setReview(nextReview);
      setChecks(EMPTY_CHECKS);
      setStage("review");
      setError("");
    } catch (cause) {
      if (isCurrent(active, token)) setError(errorMessage(cause));
    } finally { finishOperation(active, token); }
  };

  const download = async (candidate: boolean) => {
    if (!replacement) return;
    try { downloadBlob(await client.downloadHifiReplacement(replacement.sessionId, candidate)); }
    catch (cause) { setError(errorMessage(cause)); }
  };

  const approve = async () => {
    if (!replacement || !review?.approvable || !review.candidateSha256 || busy) return;
    const { active, token } = startOperation();
    try {
      const approved = await client.approveHifiReplacement(replacement.sessionId, review.candidateSha256, "package", active.signal);
      const artifact = await client.downloadHifiReplacement(approved.sessionId, false, active.signal);
      if (!isCurrent(active, token)) return;
      downloadBlob(artifact);
      setReplacement(approved);
      setStage("delivered");
      setError("");
    } catch (cause) {
      if (isCurrent(active, token)) setError(errorMessage(cause));
    } finally { finishOperation(active, token); }
  };

  const reject = async (reason: string) => {
    if (!replacement || busy) return;
    const { active, token } = startOperation();
    try {
      await client.rejectHifiReplacement(replacement.sessionId, reason, active.signal);
      if (!isCurrent(active, token)) return;
      setStage("prepare");
      setReplacement(undefined);
      setMapping(undefined);
      setReview(undefined);
      setChecks(EMPTY_CHECKS);
      setError("本次候选已放弃，可重新选择目标并开始映射。");
    } catch (cause) {
      if (isCurrent(active, token)) setError(errorMessage(cause));
    } finally { finishOperation(active, token); }
  };

  return <main className="writer-shell hifi-shell" aria-label="HIFI 替换">
    <header className="writer-header"><div><p className="writer-eyebrow">Figma → FairyGUI</p><h1>HIFI 替换</h1></div><nav className="writer-mode-tabs" aria-label="产品功能"><button type="button" role="tab" aria-selected="false" onClick={onOpenNew}>新建工程</button><button type="button" role="tab" className="is-active" aria-selected="true">HIFI 替换</button></nav></header>
    <ol className="writer-presentation-rail hifi-rail" aria-label="替换流程"><li className={stage === "prepare" ? "is-current" : "is-done"}>1 准备材料</li><li className={stage === "mapping" ? "is-current" : ["review", "delivered"].includes(stage) ? "is-done" : ""}>2 对齐组件</li><li className={["review", "delivered"].includes(stage) ? "is-current" : ""}>3 审核交付</li></ol>
    <div className="writer-step-scroll">
      {stage === "prepare" && <><HifiSourcePicker mode={sourceMode} selection={selection} inspection={psdInspection} busy={busy} onModeChange={(mode) => { setSourceMode(mode); setError(""); }} onPsdChange={(file) => void choosePsd(file)} onRefreshSelection={() => postToFigma({ type: "selection-preflight" })} />{sourceMode === "figma" && selection?.sendable && !singleRoot && <p className="writer-inline-error" role="alert">HIFI 替换一次只能选择一个根节点。</p>}<section className="writer-setup hifi-project-source"><label>旧 FairyGUI 工程压缩包<input type="file" accept={PROJECT_ARCHIVE_ACCEPT} disabled={busy} onChange={(event) => void chooseArchive(event.currentTarget.files?.[0])} /></label>{archive && <p>{archive.name}</p>}{busy && !tree && <p role="status">正在读取工程目录…</p>}</section>{tree && project && <HifiTargetPicker project={project} tree={tree} value={selected} disabled={busy} onChange={setSelected} />}</>}
      {stage === "mapping" && mapping && <HifiMappingPanel mapping={mapping} currentItemId={currentItemId} busy={busy} onCurrentChange={setCurrentItemId} onDecision={(item, action, nodeId) => void decide(item, action, nodeId)} onLocate={(nodeId) => postToFigma({ type: "locate-node", nodeId, attempt: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}` })} />}
      {stage === "review" && review && <HifiReplacementReviewPanel review={review} checks={checks} busy={busy} onChecksChange={setChecks} onDownloadCandidate={() => void download(true)} onReject={(reason) => void reject(reason)} />}
      {stage === "delivered" && <section className="hifi-delivered" role="status"><strong>已交付 HIFI 替换工程</strong><p>正式 ZIP 已下载，内容与审核候选哈希一致。</p></section>}
      {error && <p className="writer-inline-error" role="alert">{error}</p>}
    </div>
    <footer className="writer-actions">{busy && <button className="secondary-button" type="button" onClick={cancelOperation}>取消当前操作</button>}{stage === "prepare" && <button className="primary-button" type="button" disabled={sourceMode !== "figma" || !target || !selection?.sendable || !singleRoot || busy} onClick={start}>{busy ? "正在准备…" : "开始盘点与映射"}</button>}{stage === "mapping" && <><button className="secondary-button" type="button" disabled={busy} onClick={() => { setStage("prepare"); setReplacement(undefined); setMapping(undefined); }}>返回目标选择</button><button className="primary-button" type="button" disabled={Boolean(mapping?.unresolvedCount) || busy} onClick={() => void build()}>{busy ? "正在生成候选…" : "生成候选工程"}</button></>}{stage === "review" && review && <HifiReplacementReviewActions review={review} checks={checks} busy={busy} onReturn={() => { setStage("mapping"); setChecks(EMPTY_CHECKS); }} onApprove={() => void approve()} />}{stage === "delivered" && replacement && <button className="primary-button" type="button" onClick={() => void download(false)}>再次下载</button>}</footer>
  </main>;
}
