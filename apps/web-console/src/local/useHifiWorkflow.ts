import { useEffect, useMemo, useState } from "react";

import type {
  FixedFontStatus,
  HifiEditorVerification,
  HifiExportMode,
  HifiMappingAction,
  HifiMappingDraft,
  HifiMappingItem,
  HifiProjectTree,
  HifiReplacement,
  HifiReplacementReview,
  HifiTargetRef,
  ProjectView,
  ProjectWorkflowClient,
  PsdSource,
} from "../../../figma-plugin/src/project-client";
import {
  selectedHifiTarget,
  type HifiTargetSelection,
} from "../figma/HifiTargetPicker";
import {
  canDeliverHifi,
  type HifiEditorCheckState,
} from "../figma/HifiReplacementReviewPanel";

export type LocalHifiClientLike = Pick<
  ProjectWorkflowClient,
  | "resumePsdHifiReplacement"
  | "fixedFonts"
  | "uploadProject"
  | "hifiTargets"
  | "uploadPsd"
  | "psdComposite"
  | "projectAssetThumbnail"
  | "createPsdHifiReplacement"
  | "createPsdHifiReplacementBatch"
  | "hifiMapping"
  | "decideHifiMapping"
  | "buildHifiReplacement"
  | "reviewHifiReplacement"
  | "verifyHifiReplacementInEditor"
  | "hifiEditorScreenshot"
  | "approveHifiReplacement"
  | "rejectHifiReplacement"
  | "downloadHifiReplacement"
>;

type Stage = "prepare" | "sessions" | "mapping" | "review" | "delivered";
const EMPTY_CHECKS: HifiEditorCheckState = {
  layout: false,
  references: false,
  interactions: false,
};

function downloadBlob(download: { blob: Blob; downloadName: string }) {
  const url = URL.createObjectURL(download.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = download.downloadName;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function errorText(error: unknown): string {
  const code = (error as { code?: string } | null)?.code;
  if (code === "invalid_zip") return "旧 FairyGUI 工程压缩包无效。";
  if (code === "hifi_in_place_raster_unsupported")
    return "该旧对象无法原位承载位图，已阻止生成覆盖层。";
  if (code === "hifi_nested_visual_mapping_required")
    return "组件内部视觉和状态尚未对应，不能生成候选。";
  if (code === "hifi_shared_scope_violation")
    return "共享组件会影响未选中的页面，已阻止写入。";
  if (code === "hifi_shared_instance_conflict")
    return "共享组件的不同实例要求不同视觉，不能写成同一份资源。";
  if (code === "hifi_nested_geometry_unverified")
    return "嵌套对象的缩放或旋转坐标尚未验证，不能写入。";
  if (code === "hifi_instance_override_conflict")
    return "替换与既有按钮标题或图标实例参数冲突，已阻止写入。";
  if (code === "hifi_type_conversion_not_authorized")
    return "该对象没有原位改类型许可，已阻止转换。";
  if (code === "psd_stroke_only_raster_unsupported")
    return "当前渲染器会错误填满此描边图层，已阻止导出；需要修复并验证图层渲染。";
  if (code === "invalid_psd") return "PSD 文件无效或无法解析。";
  if (code === "psd_too_large") return "PSD 文件超过本地检查上限。";
  if (code === "psd_source_unavailable")
    return "本机保存的 PSD 来源已损坏或丢失，请重新导入。";
  return "本地处理失败，请检查文件后重试。";
}

export function useHifiWorkflow(client: LocalHifiClientLike) {
  const [fonts, setFonts] = useState<FixedFontStatus[]>([]);
  const [project, setProject] = useState<ProjectView>();
  const [tree, setTree] = useState<HifiProjectTree>();
  const [selectedList, setSelectedList] = useState<HifiTargetSelection[]>([]);
  const [sessions, setSessions] = useState<HifiReplacement[]>([]);
  const [exportMode, setExportMode] = useState<HifiExportMode>("package");
  const [psdSource, setPsdSource] = useState<PsdSource>();
  const [compositeUrl, setCompositeUrl] = useState<string>();
  const [oldPreviewUrl, setOldPreviewUrl] = useState<string>();
  const [stage, setStage] = useState<Stage>("prepare");
  const [replacement, setReplacement] = useState<HifiReplacement>();
  const [mapping, setMapping] = useState<HifiMappingDraft>();
  const [review, setReview] = useState<HifiReplacementReview>();
  const [editorVerification, setEditorVerification] =
    useState<HifiEditorVerification>();
  const [editorScreenshotUrl, setEditorScreenshotUrl] = useState<string>();
  const [checks, setChecks] = useState<HifiEditorCheckState>(EMPTY_CHECKS);
  const [currentItemId, setCurrentItemId] = useState<string>();
  const [projectBusy, setProjectBusy] = useState(false);
  const [psdBusy, setPsdBusy] = useState(false);
  const [projectError, setProjectError] = useState("");
  const [error, setError] = useState("");
  const [restoreNote, setRestoreNote] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [fontError, setFontError] = useState("");
  const [operation, setOperation] = useState("");
  const targets = useMemo(
    () =>
      project && tree
        ? selectedList
            .map((item) => selectedHifiTarget(project, tree, item))
            .filter((item): item is HifiTargetRef => Boolean(item))
        : [],
    [project, selectedList, tree],
  );
  const installedFonts = fonts.filter((font) => font.installed).length;
  const inspection = psdSource?.inspection;
  const ready = Boolean(
    targets.length > 0 &&
    psdSource &&
    fonts.length > 0 &&
    installedFonts === fonts.length,
  );
  const toggleTarget = (selection: HifiTargetSelection) => {
    const matches = (item: HifiTargetSelection) =>
      item[0] === selection[0] &&
      item[1] === selection[1] &&
      item[2] === selection[2];
    clearSession();
    setSelectedList((current) =>
      current.some(matches)
        ? current.filter((item) => !matches(item))
        : [...current, selection],
    );
  };

  useEffect(() => {
    let active = true;
    let saved: string | null = null;
    try {
      saved =
        window.location.hash.match(/^#session=([0-9a-f]{32})$/)?.[1] ??
        localStorage.getItem("hifi-last-session");
    } catch {
      /* Storage may be disabled. */
    }
    if (!saved || !/^[0-9a-f]{32}$/.test(saved)) return;
    setPsdBusy(true);
    setOperation("正在恢复上次会话…");
    void client
      .resumePsdHifiReplacement(saved)
      .then(async (restored) => {
        if (!active) return;
        setProject(restored.project);
        setTree(restored.tree);
        setPsdSource(restored.source);
        const pi = restored.tree.packages.findIndex(
          (p) => p.packageId === restored.replacement.target.packageId,
        );
        const di =
          restored.tree.packages[pi]?.directories.findIndex(
            (d) => d.path === restored.replacement.target.directory,
          ) ?? -1;
        const ci =
          restored.tree.packages[pi]?.directories[di]?.components.findIndex(
            (c) => c.resourceId === restored.replacement.target.componentId,
          ) ?? -1;
        if (pi >= 0 && di >= 0 && ci >= 0) setSelectedList([[pi, di, ci]]);
        setChecks(EMPTY_CHECKS);
        if (restored.stale) {
          setStage("prepare");
          setRestoreNote(
            "上次会话的安全规则已过期，材料已就位但没有可用映射。重新盘点需要重新解析 PSD，耗时较长；确认后再点“进入盘点与映射”。",
          );
        } else {
          setReplacement(restored.replacement);
          setSessions([restored.replacement]);
          setMapping(restored.mapping);
          setCurrentItemId(
            restored.mapping.items.find((item) => !item.action)?.itemId ??
              restored.mapping.items[0]?.itemId,
          );
          if (restored.replacement.status === "approved") {
            setStage("delivered");
          } else if (restored.replacement.status === "review_ready") {
            const restoredReview = await client.reviewHifiReplacement(
              restored.replacement.sessionId,
            );
            if (!active) return;
            setReview(restoredReview);
            setStage("review");
          } else {
            setStage("mapping");
          }
          setRestoreNote(
            restored.replacement.status === "approved"
              ? "上次会话已交付，可再次下载正式工程。"
              : "已恢复本机会话，材料无需重新上传。审核勾选需重新确认。",
          );
        }
        try {
          const composite = await client.psdComposite(restored.source.sourceId);
          if (active && typeof URL.createObjectURL === "function")
            setCompositeUrl(URL.createObjectURL(composite));
        } catch {
          if (active)
            setPreviewError("PSD 预览加载失败，材料已保留。请重试加载预览。");
        }
      })
      .catch((cause) => {
        if (active)
          setError(`无法恢复上次会话：${errorText(cause)}可重新选择材料。`);
      })
      .finally(() => {
        if (active) setPsdBusy(false);
      });
    return () => {
      active = false;
    };
  }, [client]);
  useEffect(() => {
    if (replacement) {
      window.history.replaceState(
        null,
        "",
        `#session=${replacement.sessionId}`,
      );
      try {
        localStorage.setItem("hifi-last-session", replacement.sessionId);
      } catch {
        /* Storage may be disabled. */
      }
    }
  }, [replacement]);

  useEffect(() => {
    const controller = new AbortController();
    void client
      .fixedFonts(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setFonts(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFontError("字体检查失败，请重试。");
      });
    return () => controller.abort();
  }, [client]);
  useEffect(
    () => () => {
      if (compositeUrl) URL.revokeObjectURL(compositeUrl);
    },
    [compositeUrl],
  );
  useEffect(() => {
    const current = mapping?.items.find(
      (item) => item.itemId === currentItemId,
    );
    if (
      !project ||
      current?.oldObjectType !== "image" ||
      !current.oldResourceId
    ) {
      setOldPreviewUrl(undefined);
      return;
    }
    setOldPreviewUrl(undefined);
    const controller = new AbortController();
    let objectUrl: string | undefined;
    void client
      .projectAssetThumbnail(
        project.projectId,
        current.oldResourceId,
        controller.signal,
      )
      .then((blob) => {
        if (!controller.signal.aborted) {
          objectUrl = URL.createObjectURL(blob);
          setOldPreviewUrl(objectUrl);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setOldPreviewUrl(undefined);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, project, mapping, currentItemId]);
  useEffect(
    () => () => {
      if (editorScreenshotUrl) URL.revokeObjectURL(editorScreenshotUrl);
    },
    [editorScreenshotUrl],
  );

  useEffect(() => {
    if (replacement)
      setSessions((current) =>
        current.map((session) =>
          session.sessionId === replacement.sessionId ? replacement : session,
        ),
      );
  }, [replacement]);

  const clearSession = () => {
    setSessions([]);
    setReplacement(undefined);
    setMapping(undefined);
    setReview(undefined);
    setEditorVerification(undefined);
    setEditorScreenshotUrl(undefined);
    setChecks(EMPTY_CHECKS);
    setCurrentItemId(undefined);
    setStage("prepare");
    setError("");
    setRestoreNote("");
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
    try {
      localStorage.removeItem("hifi-last-session");
    } catch {
      /* Storage may be disabled. */
    }
  };

  const retryPreview = async () => {
    if (!psdSource || psdBusy) return;
    setPsdBusy(true);
    setOperation("正在加载 PSD 预览…");
    setPreviewError("");
    try {
      setCompositeUrl(
        URL.createObjectURL(await client.psdComposite(psdSource.sourceId)),
      );
    } catch {
      setPreviewError("PSD 预览加载失败，材料已保留。请重试加载预览。");
    } finally {
      setPsdBusy(false);
    }
  };

  const retryFonts = async () => {
    setFontError("");
    setFonts([]);
    try {
      setFonts(await client.fixedFonts());
    } catch {
      setFontError("字体检查失败，请重试。");
    }
  };

  const chooseProject = async (file?: File) => {
    if (!file) return;
    clearSession();
    setProject(undefined);
    setTree(undefined);
    setSelectedList([]);
    setProjectError("");
    setProjectBusy(true);
    try {
      const uploaded = await client.uploadProject(file);
      const targets = await client.hifiTargets(uploaded.projectId);
      setProject(uploaded);
      setTree(targets);
    } catch (cause) {
      setProjectError(`${errorText(cause)}请检查文件并重新选择。`);
    } finally {
      setProjectBusy(false);
    }
  };

  const choosePsd = async (file?: File) => {
    if (!file) return;
    clearSession();
    setPsdSource(undefined);
    setCompositeUrl(undefined);
    setPreviewError("");
    setPsdBusy(true);
    setOperation("正在解析 PSD 并保存材料…");
    try {
      const uploaded = await client.uploadPsd(file);
      setPsdSource(uploaded);
      try {
        const composite = await client.psdComposite(uploaded.sourceId);
        if (typeof URL.createObjectURL === "function")
          setCompositeUrl(URL.createObjectURL(composite));
      } catch {
        setPreviewError("PSD 预览加载失败，材料已保留。请重试加载预览。");
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const enterSession = (session: HifiReplacement, draft: HifiMappingDraft) => {
    setReplacement(session);
    setMapping(draft);
    setReview(undefined);
    setEditorVerification(undefined);
    setEditorScreenshotUrl(undefined);
    setChecks(EMPTY_CHECKS);
    setCurrentItemId(
      draft.items.find((item) => !item.action)?.itemId ??
        draft.items[0]?.itemId,
    );
    setStage("mapping");
  };

  const startMapping = async () => {
    if (
      !ready ||
      !project ||
      !targets.length ||
      !psdSource ||
      projectBusy ||
      psdBusy
    )
      return;
    setPsdBusy(true);
    setOperation("正在盘点图层并建立映射…");
    setError("");
    try {
      if (targets.length === 1) {
        const started = await client.createPsdHifiReplacement(
          psdSource.sourceId,
          project,
          targets[0],
        );
        enterSession(started.replacement, started.mapping);
      } else {
        setSessions(
          await client.createPsdHifiReplacementBatch(
            psdSource.sourceId,
            targets,
          ),
        );
        setStage("sessions");
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const openSession = async (session: HifiReplacement) => {
    setPsdBusy(true);
    setOperation("正在打开会话…");
    setError("");
    try {
      enterSession(session, await client.hifiMapping(session.sessionId));
      if (session.status === "approved") {
        setExportMode("package");
        setStage("delivered");
      } else if (session.status === "review_ready") {
        setReview(await client.reviewHifiReplacement(session.sessionId));
        setStage("review");
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const decide = async (
    item: HifiMappingItem,
    action: HifiMappingAction,
    nodeId?: string,
  ) => {
    if (!replacement || !mapping || psdBusy) return;
    setPsdBusy(true);
    setOperation("正在保存映射…");
    setError("");
    try {
      const next = await client.decideHifiMapping(
        replacement.sessionId,
        mapping.mappingRevision,
        item.itemId,
        action,
        nodeId,
      );
      const nextMapping = await client.hifiMapping(next.sessionId);
      setReplacement(next);
      setMapping(nextMapping);
      setCurrentItemId(
        nextMapping.items.find((entry) => !entry.action)?.itemId ?? item.itemId,
      );
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const decideBatch = async (
    kind: "suggested" | "hifi_added" | "blocked" | "fgui_only",
  ) => {
    if (!replacement || !mapping || psdBusy) return;
    setPsdBusy(true);
    setOperation("正在批量保存映射…");
    setError("");
    try {
      let nextReplacement = replacement;
      let nextMapping = mapping;
      const itemIds = mapping.items
        .filter((item) => item.action === undefined && item.status === kind)
        .map((item) => item.itemId);
      for (const itemId of itemIds) {
        const item = nextMapping.items.find((entry) => entry.itemId === itemId);
        if (!item || item.action !== undefined) continue;
        const action: HifiMappingAction =
          kind === "suggested"
            ? "accept"
            : kind === "hifi_added"
              ? "add_visual"
              : "exception"; // blocked / fgui_only -> exception
        nextReplacement = await client.decideHifiMapping(
          nextReplacement.sessionId,
          nextMapping.mappingRevision,
          item.itemId,
          action,
        );
        nextMapping = await client.hifiMapping(nextReplacement.sessionId);
        setReplacement(nextReplacement);
        setMapping(nextMapping);
      }
      setReplacement(nextReplacement);
      setMapping(nextMapping);
      setCurrentItemId(
        nextMapping.items.find((entry) => !entry.action)?.itemId ??
          nextMapping.items[0]?.itemId,
      );
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const build = async () => {
    if (!replacement || !mapping || mapping.unresolvedCount || psdBusy) return;
    setPsdBusy(true);
    setOperation("正在生成审核候选…");
    setError("");
    try {
      const built = await client.buildHifiReplacement(
        replacement.sessionId,
        mapping.mappingRevision,
      );
      const nextReview = await client.reviewHifiReplacement(
        replacement.sessionId,
      );
      setReplacement(built);
      setReview(nextReview);
      setEditorVerification(undefined);
      setEditorScreenshotUrl(undefined);
      setChecks(EMPTY_CHECKS);
      setStage("review");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const download = async (candidate: boolean) => {
    if (!replacement) return;
    try {
      downloadBlob(
        await client.downloadHifiReplacement(replacement.sessionId, candidate),
      );
    } catch (cause) {
      setError(errorText(cause));
    }
  };

  const verifyEditor = async () => {
    if (!replacement || psdBusy) return;
    setPsdBusy(true);
    setOperation("正在打开 Editor 并检查画面…");
    setError("");
    try {
      const verification = await client.verifyHifiReplacementInEditor(
        replacement.sessionId,
      );
      setEditorVerification(verification);
      setEditorScreenshotUrl(undefined);
      setChecks(EMPTY_CHECKS);
      setReview(await client.reviewHifiReplacement(replacement.sessionId));
      if (verification.screenshotUrl) {
        const screenshot = await client.hifiEditorScreenshot(
          replacement.sessionId,
        );
        setEditorScreenshotUrl(URL.createObjectURL(screenshot));
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const approve = async () => {
    if (
      !replacement ||
      !review ||
      !canDeliverHifi(review, checks, editorVerification) ||
      !review.candidateSha256 ||
      psdBusy
    )
      return;
    setPsdBusy(true);
    setOperation("正在确认交付…");
    setError("");
    try {
      const approved = await client.approveHifiReplacement(
        replacement.sessionId,
        review.candidateSha256,
        exportMode,
      );
      setReplacement(approved);
      setStage("delivered");
      if (exportMode === "package") {
        downloadBlob(
          await client.downloadHifiReplacement(approved.sessionId, false),
        );
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  const reject = async (reason: string) => {
    if (!replacement || psdBusy) return;
    setPsdBusy(true);
    setOperation("正在退回候选…");
    setError("");
    try {
      const rejected = await client.rejectHifiReplacement(
        replacement.sessionId,
        reason,
      );
      setReplacement(rejected);
      setMapping(await client.hifiMapping(replacement.sessionId));
      setStage("mapping");
      setReview(undefined);
      setEditorVerification(undefined);
      setEditorScreenshotUrl(undefined);
      setChecks(EMPTY_CHECKS);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPsdBusy(false);
    }
  };

  return {
    fonts,
    project,
    tree,
    selectedList,
    setSelectedList,
    sessions,
    exportMode,
    setExportMode,
    psdSource,
    compositeUrl,
    oldPreviewUrl,
    stage,
    setStage,
    replacement,
    mapping,
    review,
    editorVerification,
    editorScreenshotUrl,
    checks,
    setChecks,
    currentItemId,
    setCurrentItemId,
    projectBusy,
    psdBusy,
    projectError,
    error,
    restoreNote,
    previewError,
    fontError,
    operation,
    targets,
    installedFonts,
    inspection,
    ready,
    toggleTarget,
    clearSession,
    retryPreview,
    retryFonts,
    chooseProject,
    choosePsd,
    startMapping,
    openSession,
    decide,
    decideBatch,
    build,
    download,
    verifyEditor,
    approve,
    reject,
    EMPTY_CHECKS,
  };
}
