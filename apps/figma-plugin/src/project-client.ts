import type { ExportedResource } from "./assets";
import { MAX_SEMANTIC_SCREENSHOT_BYTES } from "./contracts";
import type { SelectionManifest } from "./selection";
import { parseSelectionView, SelectionUploadError, SelectionUploader, type FetchLike, type SelectionView } from "./upload";

export type WorkflowErrorCode = "network" | "invalid_zip" | "invalid_psd" | "psd_too_large" | "unknown_template" | "validation" | "selection_invalid" | "conversion_conflict" | "conversion_failed" | "package_failed" | "unauthorized" | "aborted" | "timeout" | "invalid_response" | "review_required" | "stale_candidate" | "hifi_mapping_stale" | "hifi_target_stale" | "hifi_candidate_stale" | "hifi_build_failed" | "hifi_mapping_incomplete" | "hifi_editor_checks_incomplete" | "hifi_download_blocked" | "hifi_review_budget_exceeded" | "hifi_in_place_raster_unsupported" | "hifi_mapping_policy_stale" | "hifi_mapping_coverage_incomplete" | "hifi_incompatible_mapping" | "hifi_nested_visual_mapping_required" | "hifi_shared_scope_violation" | "hifi_shared_instance_conflict" | "hifi_nested_geometry_unverified" | "hifi_instance_override_conflict" | "hifi_type_conversion_not_authorized" | "psd_stroke_only_raster_unsupported";
const PROJECT_ARCHIVE_SUFFIXES = [".tar.gz", ".zip", ".rar", ".7z", ".tar", ".tgz"] as const;
export const PROJECT_ARCHIVE_ACCEPT = ".zip,.rar,.7z,.tar,.tar.gz,.tgz,application/zip,application/x-zip-compressed,application/vnd.rar,application/x-rar-compressed,application/x-7z-compressed,application/x-tar,application/gzip,application/x-gzip";

export function isSupportedProjectArchive(project: File): boolean {
  const name = project.name.toLowerCase();
  return PROJECT_ARCHIVE_SUFFIXES.some((suffix) => name.endsWith(suffix));
}
export type WorkflowStageName = "uploading" | "parsing" | "converting" | "checking" | "awaiting_screenshot_consent" | "packaging" | "ready" | "failed";
export type WorkflowStage = { stage: WorkflowStageName; progress: number };
export type WorkflowStageCallback = (stage: WorkflowStage) => void;
export type ProjectOption = { templateId: string; fairyguiVersion: string; targetPlatform: string; displayName: string };
export type ProjectView = { projectId: string; displayName: string; packages: Array<{ name: string; resourceCount: number }> };
export type JobView = { jobId: string; projectId: string; status: string };
export type DiagnosticView = { code: string; severity: "ERROR" | "WARNING" | "INFO"; message: string; nodeId?: string; path?: string; ruleId?: string; ruleVersion?: number };
export type PackageStage = "uploading" | "parsing" | "converting" | "checking" | "awaiting_screenshot_consent" | "packaging" | "ready" | "failed";
export type PackageView = { jobId: string; status: PackageStage; stage: PackageStage; progress: number; downloadName?: string; sha256?: string; screenshotReason?: string; diagnostics: DiagnosticView[] };
export type DownloadedPackage = { blob: Blob; downloadName: string };
export type WorkflowResult = DownloadedPackage & { project: ProjectView; selection: SelectionView; job: JobView; package: PackageView };
export type SemanticScreenshot = { mimeType: "image/png"; bytes: Uint8Array };
export type ScreenshotConsentRequest = { jobId: string; reason: string; signal: AbortSignal };
export type ScreenshotWorkflowCallbacks = {
  onScreenshotConsent(request: ScreenshotConsentRequest): Promise<boolean>;
  requestScreenshot(jobId: string, signal: AbortSignal): Promise<SemanticScreenshot>;
};
type NoScreenshotWorkflowCallbacks = { onScreenshotConsent?: never; requestScreenshot?: never };
export type WorkflowRunOptions = (ScreenshotWorkflowCallbacks | NoScreenshotWorkflowCallbacks) & { signal?: AbortSignal; timeoutMs?: number };
export type WaitForPackageOptions = WorkflowRunOptions & { onStage?: WorkflowStageCallback };
export type NewProjectStage = "converting" | "checking" | "packaging" | "awaiting_review" | "adjusting" | "regenerating" | "approved" | "rejected" | "failed";
export type NewProjectCandidate = {
  buildId: string;
  generation: number;
  status: NewProjectStage;
  stage: NewProjectStage;
  progress: number;
  downloadName?: string;
  sha256?: string;
  byteSize?: number;
  artifactReady?: boolean;
  diagnostics: readonly DiagnosticView[];
};
export type NewProjectAdjustmentStrategy = "preserve-editable" | "rasterize-subtree" | "include-contained-definition";
export type NewProjectDispositionLevel = "native" | "raster_preserved" | "editable_risk" | "blocked";
export type NewProjectDispositionReason = "native_structure" | "native_text" | "native_shape" | "native_image" | "native_component" | "native_instance_structure" | "native_vector_resource" | "native_static_layout" | "rasterized_vector" | "gradient_paint" | "visual_effect" | "blend_mode" | "multiple_paints" | "mask_composite" | "instance_composite" | "visual_style" | "unrepresentable_transform" | "rich_text_runs" | "text_style_properties" | "component_definition_missing" | "interaction_unsupported" | "resource_missing";
export type NewProjectDispositionDetails = Readonly<{ runCount: number; preservedProperties: readonly string[]; unsupportedProperties: readonly string[] }>;
export type NewProjectConversionDisposition = { version: 1; id: string; sourceNodeId: string; sourceName: string; sourceType: string; level: NewProjectDispositionLevel; reason: NewProjectDispositionReason; defaultStrategy?: NewProjectAdjustmentStrategy; allowedStrategies: NewProjectAdjustmentStrategy[]; visualImpact: "unchanged" | "visual_preserved" | "may_differ"; editabilityImpact: "unchanged" | "subtree_not_editable" | "text_not_editable" | "vector_path_not_editable" | "layout_reflow_not_editable"; componentImpact: "unchanged" | "instance_not_reusable"; blocksApproval: boolean; details: NewProjectDispositionDetails | null };
export type NewProjectImageReview = { resourceId: string; sourceNodeId: string; label: string; evidenceKind: "source-image" | "generated-only"; sourcePreviewUrl?: string; generatedAssetUrl: string; width: number; height: number; nineSlice: boolean; cropBoundsMatch: boolean; transparencyPreserved: boolean };
export type NewProjectComponentReview = { componentId: string; label: string; evidenceKind: "rendered" | "structured-summary"; renderedPreviewUrl?: string; objectCount: number; textCount: number; resourceRefs: number; componentRefs: number; hierarchyValid: boolean; geometryValid: boolean; textValid: boolean };
export type NewProjectPackageReview = { packageName: string; fairyguiVersion: "6.1.4"; publishTarget: "unity"; componentsAdded: number; resourcesAdded: number; componentNames: string[]; resourceNames: string[]; resourceClosureValid: boolean; namingConflicts: string[]; integrityValid: boolean };
export type NewProjectCheck = { id: string; severity: "ERROR" | "WARNING" | "INFO"; message: string; issueId: string; issueKind?: "raster-fallback" | "definition-missing"; uirNodeId?: string; sourceNodeId?: string; actionable: boolean; allowedStrategies: NewProjectAdjustmentStrategy[] };
export type NewProjectReview = { version: 1; buildId: string; generation: number; dispositions: NewProjectConversionDisposition[]; imageReviews: NewProjectImageReview[]; componentReviews: NewProjectComponentReview[]; packageReview: NewProjectPackageReview; checks: NewProjectCheck[]; warningIds: string[]; approvable: boolean };
export type NewProjectRunResult = { selection: SelectionView; candidate: NewProjectCandidate };
export type HifiTargetRef = { version: 1; projectId: string; projectFingerprint: string; packageId: string; packageName: string; directory: string; componentId: string; componentName: string; componentRelativePath: string };
export type HifiComponentOption = { resourceId: string; name: string; relativePath: string; selectable: boolean; reason?: string };
export type HifiDirectoryOption = { path: string; selectable: boolean; reason?: string; components: HifiComponentOption[] };
export type HifiPackageOption = { packageId: string; name: string; directories: HifiDirectoryOption[] };
export type HifiProjectTree = { projectId: string; projectFingerprint: string; packages: HifiPackageOption[] };
export type HifiMappingAction = "accept" | "retarget" | "keep_old" | "add_visual" | "exception" | "preserve_structure";
export type HifiMappingItem = {
  itemId: string;
  oldObjectId?: string;
  oldName?: string;
  oldObjectType?: string;
  oldResourceId?: string;
  defaultVisible?: boolean;
  preserveRuntimeText?: boolean;
  figmaNodeId?: string;
  figmaName?: string;
  status: "matched" | "suggested" | "uncertain" | "fgui_only" | "hifi_added" | "blocked" | "structural" | "out_of_scope" | "occluded";
  score: number;
  action?: HifiMappingAction;
  candidates: string[];
  oldBounds?: [number, number, number, number];
  figmaBounds?: [number, number, number, number];
  ownedSourceIds?: string[];
  ownedGroupId?: string;
  retainedSourceIds?: string[];
  generatedState?: boolean;
  graphConversionProven?: boolean;
  positionAuthoritative?: boolean;
  outOfScope?: boolean;
  occluded?: boolean;
  visualEcho?: boolean;
  compositeGroupId?: string;
  compositeSourceIds?: string[];
  visualDisposition?: "preserve" | "retire" | "other_state" | "structural";
  logicalBoundsPolicy?: "preserve" | "resize";
};
export type HifiMappingDraft = { policyRevision?: number; mappingRevision: number; unresolvedCount: number; oldCanvasSize?: { width: number; height: number }; sourceCanvasSize?: { width: number; height: number }; items: HifiMappingItem[] };
const HIFI_POLICY_REVISION = 27;
export type HifiReplacement = { sessionId: string; status: "mapping" | "building" | "review_ready" | "approved" | "rejected" | "failed" | "superseded"; selectionId: string; target: HifiTargetRef; mappingRevision: number; unresolvedCount: number; artifactReady: boolean };
export type HifiObjectDiff = { itemId: string; kind: "changed" | "added" | "kept" | "exception"; oldObjectId?: string; oldName?: string; figmaNodeId?: string; figmaName?: string; changedFields: string[]; summary: string };
export type HifiReplacementReview = { sessionId: string; mappingRevision: number; changedFiles: Array<{ relativePath: string; operation: "create" | "replace"; summary: string }>; objectDiffs: HifiObjectDiff[]; protectedChecksPassed: boolean; parseCoverageComplete: boolean; approvable: boolean; candidateSha256?: string; warnings: string[]; editorCheckRequired: boolean };
export type HifiEditorVerification = { sessionId: string; candidateSha256: string; editorFound: boolean; editorVersion?: "6.1.4"; projectOpened: boolean; componentOpened: boolean; renderCaptured: boolean; screenshotUrl?: string; screenshotSha256?: string; screenshotWidth?: number; screenshotHeight?: number; expectedWidth: number; expectedHeight: number; fullFrame: boolean; meanPixelDifference?: number; approvable: boolean; warnings: string[] };
export type HifiReplacementStart = { project: ProjectView; selection: SelectionView; replacement: HifiReplacement; mapping: HifiMappingDraft };
export type HifiPsdReplacementStart = { project: ProjectView; replacement: HifiReplacement; mapping: HifiMappingDraft };
export type HifiExportMode = "package" | "overwrite";
export type HifiPsdResume = HifiPsdReplacementStart & { source: PsdSource; tree: HifiProjectTree; stale: boolean };
export type PsdInspection = {
  sourceName: string;
  byteSize: number;
  sha256: string;
  width: number;
  height: number;
  depth: 8 | 16;
  colorMode: "RGB";
  layerCount: number;
  kindCounts: Record<string, number>;
  textLayerCount: number;
  smartObjectCount: number;
  adjustmentLayerCount: number;
  effectLayerCount: number;
  blockingIssues: string[];
  warnings: string[];
};
export type PsdLayer = {
  id: string;
  nativeId?: number;
  parentId?: string;
  documentIndex: number;
  siblingIndex: number;
  name: string;
  path: string[];
  kind: string;
  bounds: [number, number, number, number];
  visible: boolean;
  effectiveVisible: boolean;
  opacity: number;
  blendMode: string;
  clipping: boolean;
  text?: string;
  hasPixelMask: boolean;
  hasVectorMask: boolean;
  hasEffects: boolean;
  textStyle?: PsdTextStyle;
  effects: PsdLayerEffect[];
};
export type PsdTextRun = { start: number; length: number; fontName?: string; fontSize?: number; fauxBold: boolean; fauxItalic: boolean; leading?: number; tracking?: number; fillRgba?: [number, number, number, number] };
export type PsdTextStyle = { runs: PsdTextRun[]; transform: [number, number, number, number, number, number]; paragraphJustification?: number; antiAlias?: number };
export type PsdLayerEffect = { kind: string; enabled: boolean; blendMode?: string; opacity?: number; colorRgba?: [number, number, number, number]; size?: number; angle?: number; distance?: number; spread?: number; choke?: number; position?: string };
export type PsdSource = { sourceId: string; inspection: PsdInspection; layers: PsdLayer[] };
export type FixedFontStatus = {
  family: string;
  postscriptName: string;
  sourceFilename: string;
  sha256: string;
  installed: boolean;
  matchedFilename?: string;
};

type Wait = (milliseconds: number, signal?: AbortSignal) => Promise<void>;
type RecordValue = Record<string, unknown>;
const WRITER_PROJECT_NAME_PATTERN = /^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$/u;

function selectionUploadWorkflowCode(code: string): WorkflowErrorCode {
  if (code === "network") return "network";
  if (code === "unauthorized") return "unauthorized";
  if (code === "invalid_response") return "invalid_response";
  return "selection_invalid";
}

function writerProjectName(value: string): string {
  const normalized = value.trim();
  if (!WRITER_PROJECT_NAME_PATTERN.test(normalized)) throw new WorkflowError("validation");
  return normalized;
}

function targetPackage(project: ProjectView): string | undefined {
  const featurePackages = project.packages.filter(({ name }) => !/^(?:base\d*|common|icons?)$/iu.test(name));
  return (featurePackages.length === 1 ? featurePackages[0] : project.packages[0])?.name;
}

const messages: Record<WorkflowErrorCode, string> = {
  network: "无法连接内网服务，请检查网络后重试",
  invalid_zip: "工程 ZIP 无效、已损坏或不是 FairyGUI 工程",
  invalid_psd: "PSD 文件无效、已损坏或不是受支持的 PSD",
  psd_too_large: "PSD 文件超过本地检查上限",
  unknown_template: "所选工程模板不可用，请刷新后重试",
  validation: "提交内容未通过检查，请修正后重试",
  selection_invalid: "当前选择的数据未通过校验，请刷新选择后重试",
  conversion_conflict: "当前设计与工程存在冲突，请检查后重试",
  conversion_failed: "工程创建或更新失败，请检查设计内容后重试",
  package_failed: "工程打包失败，请重试",
  unauthorized: "插件未获服务授权，请联系管理员",
  aborted: "操作已取消",
  timeout: "处理超时，请重试",
  invalid_response: "服务返回的数据无法识别，请重试",
  review_required: "请先完整检查并确认当前候选工程",
  stale_candidate: "候选工程已失效，请基于当前选择重新生成",
  hifi_mapping_stale: "映射已更新，请重新加载当前结果",
  hifi_target_stale: "旧工程或目标组件已变化，请重新选择",
  hifi_candidate_stale: "HIFI 候选已失效，请重新生成",
  hifi_build_failed: "HIFI 候选生成失败，请检查映射后重试",
  hifi_mapping_incomplete: "仍有映射项未处理",
  hifi_review_budget_exceeded: "超过整页最多 5 个对象的人工判断上限，需先完善自动映射",
  hifi_in_place_raster_unsupported: "该旧对象无法原位承载位图；已阻止生成覆盖层",
  hifi_mapping_policy_stale: "安全规则已更新，请重新进入盘点；旧候选不能直接交付",
  hifi_mapping_coverage_incomplete: "仍有可见 PSD 图层未落实到旧对象，例外不能替代完整映射",
  hifi_incompatible_mapping: "对象类型不兼容，不能建立此对应关系",
  hifi_nested_visual_mapping_required: "需要先映射组件内部视觉和状态，不能只移动外层组件",
  hifi_shared_scope_violation: "共享组件会影响未选中的页面，已阻止写入。",
  hifi_shared_instance_conflict: "共享组件的不同实例要求不同视觉，不能写成同一份资源。",
  hifi_nested_geometry_unverified: "嵌套对象的缩放或旋转坐标尚未验证，不能写入。",
  hifi_instance_override_conflict: "替换与既有按钮标题或图标实例参数冲突，已阻止写入。",
  hifi_type_conversion_not_authorized: "该对象没有原位改类型许可，已阻止转换。",
  psd_stroke_only_raster_unsupported: "当前渲染器会错误填满此描边图层，已阻止导出；需要修复并验证图层渲染。",
  hifi_editor_checks_incomplete: "FairyGUI Editor 检查尚未完成",
  hifi_download_blocked: "当前 HIFI 工程尚未满足下载条件",
};

export class WorkflowError extends Error {
  constructor(readonly code: WorkflowErrorCode) { super(messages[code]); this.name = "WorkflowError"; }
}

function record(value: unknown): RecordValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new WorkflowError("invalid_response");
  return value as RecordValue;
}

function exactRecord(value: unknown, keys: readonly string[]): RecordValue {
  const data = record(value);
  const actual = Object.keys(data).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) throw new WorkflowError("invalid_response");
  return data;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) throw new WorkflowError("invalid_response");
  return value;
}

function identifier(value: unknown): string {
  const result = requiredString(value);
  if (!/^[0-9a-f]{32}$/.test(result)) throw new WorkflowError("invalid_response");
  return result;
}

function hifiSourceIdentifier(value: unknown): string {
  const result = requiredString(value);
  if (!/^(?:[0-9a-f]{32}|[0-9a-f]{64})$/.test(result)) throw new WorkflowError("invalid_response");
  return result;
}

function optionalString(value: unknown): string | undefined {
  if (value == null) return undefined;
  return requiredString(value);
}

function natural(value: unknown): number {
  if (!Number.isInteger(value) || Number(value) < 0) throw new WorkflowError("invalid_response");
  return value as number;
}

function positive(value: unknown): number {
  const result = natural(value);
  if (!result) throw new WorkflowError("invalid_response");
  return result;
}

function exactString(value: unknown, allowed: readonly string[]): string {
  const result = requiredString(value);
  if (!allowed.includes(result)) throw new WorkflowError("invalid_response");
  return result;
}

function optionalFiniteNumber(value: unknown): number | undefined {
  if (value === null) return undefined;
  if (typeof value !== "number" || !Number.isFinite(value)) throw new WorkflowError("invalid_response");
  return value;
}

function parseDiagnostic(value: unknown): DiagnosticView {
  const data = record(value);
  if (!["ERROR", "WARNING", "INFO"].includes(String(data.severity))) throw new WorkflowError("invalid_response");
  if (data.rule_version != null && (!Number.isInteger(data.rule_version) || Number(data.rule_version) < 0)) throw new WorkflowError("invalid_response");
  return {
    code: requiredString(data.code), severity: data.severity as DiagnosticView["severity"], message: requiredString(data.message),
    ...(optionalString(data.node_id) ? { nodeId: data.node_id as string } : {}), ...(optionalString(data.path) ? { path: data.path as string } : {}),
    ...(optionalString(data.rule_id) ? { ruleId: data.rule_id as string } : {}), ...(data.rule_version != null ? { ruleVersion: data.rule_version as number } : {}),
  };
}

function parseProject(value: unknown): ProjectView {
  const data = record(value);
  if (data.version !== 1 || !Array.isArray(data.packages)) throw new WorkflowError("invalid_response");
  const result: ProjectView = {
    projectId: identifier(data.project_id),
    displayName: requiredString(data.display_name),
    packages: data.packages.map((item) => {
      const itemData = record(item);
      if (!Number.isInteger(itemData.resource_count) || Number(itemData.resource_count) < 0) throw new WorkflowError("invalid_response");
      return { name: requiredString(itemData.name), resourceCount: itemData.resource_count as number };
    }),
  };
  if (!result.packages.length || new Set(result.packages.map((item) => item.name)).size !== result.packages.length) throw new WorkflowError("invalid_response");
  return result;
}

function parseJob(value: unknown, expectedProjectId?: string): JobView {
  const data = record(value);
  const statuses = new Set(["created", "ready_for_review", "conversion_failed", "approved", "applying", "applied", "failed", "rejected"]);
  if (data.version !== 1 || !statuses.has(String(data.status))) throw new WorkflowError("invalid_response");
  const result = { jobId: identifier(data.job_id), projectId: identifier(data.project_id), status: data.status as string };
  if (expectedProjectId && result.projectId !== expectedProjectId) throw new WorkflowError("invalid_response");
  return result;
}

function parsePackage(value: unknown, expectedJobId?: string): PackageView {
  const data = record(value);
  const valid = new Set<PackageStage>(["uploading", "parsing", "converting", "checking", "awaiting_screenshot_consent", "packaging", "ready", "failed"]);
  if (data.version !== 1 || !valid.has(data.status as PackageStage) || data.status !== data.stage || !Number.isInteger(data.progress) || Number(data.progress) < 0 || Number(data.progress) > 100 || !Array.isArray(data.diagnostics)) throw new WorkflowError("invalid_response");
  const result: PackageView = { jobId: identifier(data.job_id), status: data.status as PackageStage, stage: data.stage as PackageStage, progress: data.progress as number, diagnostics: data.diagnostics.map(parseDiagnostic) };
  if (expectedJobId && result.jobId !== expectedJobId) throw new WorkflowError("invalid_response");
  if (data.download_name != null) result.downloadName = requiredString(data.download_name);
  if (data.screenshot_reason != null) {
    const reason = requiredString(data.screenshot_reason).trim();
    if (!reason || reason.length > 240) throw new WorkflowError("invalid_response");
    result.screenshotReason = reason;
  }
  if (result.status === "awaiting_screenshot_consent" && !result.screenshotReason) throw new WorkflowError("invalid_response");
  if (data.sha256 != null) {
    if (typeof data.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(data.sha256)) throw new WorkflowError("invalid_response");
    result.sha256 = data.sha256;
  }
  if (result.status === "ready" && (!result.downloadName || !validWindowsZipName(result.downloadName) || !result.sha256 || result.progress !== 100)) throw new WorkflowError("invalid_response");
  return result;
}

const NEW_PROJECT_STAGES: readonly NewProjectStage[] = ["converting", "checking", "packaging", "awaiting_review", "adjusting", "regenerating", "approved", "rejected", "failed"];
const ADJUSTMENT_STRATEGIES: readonly NewProjectAdjustmentStrategy[] = ["preserve-editable", "rasterize-subtree", "include-contained-definition"];
const DISPOSITION_LEVELS: readonly NewProjectDispositionLevel[] = ["native", "raster_preserved", "editable_risk", "blocked"];
const DISPOSITION_REASONS: readonly NewProjectDispositionReason[] = ["native_structure", "native_text", "native_shape", "native_image", "native_component", "native_instance_structure", "native_vector_resource", "native_static_layout", "rasterized_vector", "gradient_paint", "visual_effect", "blend_mode", "multiple_paints", "mask_composite", "instance_composite", "visual_style", "unrepresentable_transform", "rich_text_runs", "text_style_properties", "component_definition_missing", "interaction_unsupported", "resource_missing"];
const MAX_RICH_TEXT_RUN_COUNT = 9_999_999_999;
const MAX_DISPOSITION_DETAIL_PROPERTIES = 32;
const PRESERVED_TEXT_PROPERTIES = new Set(["content"]);
const UNSUPPORTED_TEXT_PROPERTIES = new Set([
  "bound_variables", "fontCandidates", "fontSize", "fontWeight", "font_name", "font_size", "hyperlink", "indentation",
  "letter_spacing", "line_height", "list_options", "list_spacing", "normalized_text_runs_invalid", "open_type_features",
  "paragraphAlignment", "paragraph_indent", "paragraph_spacing", "rest_text_style_overrides", "strokeColor", "strokeSize",
  "styled_text_segment_invalid", "styled_text_segments_invalid", "styled_text_segments_unavailable", "text_auto_resize", "text_case", "text_decoration",
  "text_run_fill", "text_style_overrides", "ubbEncoding", "unrecognized_run_style",
]);

function parseNewProjectDiagnostic(value: unknown): DiagnosticView {
  const data = exactRecord(value, ["code", "severity", "message", "node_id", "path", "rule_id", "rule_version", "evidence", "suggested_action", "blocks_binding"]);
  if (!Array.isArray(data.evidence) || !data.evidence.every((item) => typeof item === "string") || typeof data.blocks_binding !== "boolean") throw new WorkflowError("invalid_response");
  return parseDiagnostic(data);
}

function parseNewProjectCandidate(value: unknown, expectedBuildId?: string, expectedGeneration?: number): NewProjectCandidate {
  const data = exactRecord(value, ["version", "build_id", "generation", "status", "stage", "progress", "download_name", "sha256", "byte_size", "artifact_ready", "diagnostics"]);
  if (data.version !== 1 || data.status !== data.stage || !NEW_PROJECT_STAGES.includes(data.stage as NewProjectStage) || typeof data.artifact_ready !== "boolean" || !Array.isArray(data.diagnostics)) throw new WorkflowError("invalid_response");
  const buildId = identifier(data.build_id);
  if (expectedBuildId && buildId !== expectedBuildId) throw new WorkflowError("stale_candidate");
  const generation = positive(data.generation);
  if (expectedGeneration != null && generation !== expectedGeneration) throw new WorkflowError("stale_candidate");
  const progress = natural(data.progress);
  if (progress > 100) throw new WorkflowError("invalid_response");
  const metadata = [data.download_name, data.sha256, data.byte_size];
  if (metadata.some((item) => item == null) !== metadata.every((item) => item == null)) throw new WorkflowError("invalid_response");
  const result: NewProjectCandidate = {
    buildId,
    generation: positive(generation),
    status: data.status as NewProjectStage,
    stage: data.stage as NewProjectStage,
    progress,
    artifactReady: data.artifact_ready,
    diagnostics: data.diagnostics.map(parseNewProjectDiagnostic),
  };
  if (data.download_name != null) {
    const name = requiredString(data.download_name);
    if (!validWindowsZipName(name) || !name.endsWith("-FairyGUI.zip")) throw new WorkflowError("invalid_response");
    result.downloadName = name;
    if (typeof data.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(data.sha256)) throw new WorkflowError("invalid_response");
    result.sha256 = data.sha256;
    result.byteSize = natural(data.byte_size);
  }
  if (result.artifactReady !== Boolean(result.downloadName) || result.status === "approved" && !result.artifactReady) throw new WorkflowError("invalid_response");
  return result;
}

function nullableUrl(value: unknown): string | undefined {
  if (value == null) return undefined;
  const result = requiredString(value);
  if (!/^\/v1\/[A-Za-z0-9_./-]+$/.test(result) || result.includes("..") || result.includes("//")) throw new WorkflowError("invalid_response");
  return result;
}

function dispositionProperties(value: unknown, registered: ReadonlySet<string>): readonly string[] {
  if (!Array.isArray(value) || !value.length || value.length > MAX_DISPOSITION_DETAIL_PROPERTIES || !value.every((item) => typeof item === "string" && item.length > 0 && registered.has(item))) throw new WorkflowError("invalid_response");
  const properties = value as string[];
  if (new Set(properties).size !== properties.length || properties.join("\0") !== [...properties].sort().join("\0")) throw new WorkflowError("invalid_response");
  return properties;
}

function parseDispositionDetails(value: unknown): NewProjectDispositionDetails | null {
  if (value === null) return null;
  const details = exactRecord(value, ["runCount", "preservedProperties", "unsupportedProperties"]);
  if (!Number.isSafeInteger(details.runCount) || Number(details.runCount) < 1 || Number(details.runCount) > MAX_RICH_TEXT_RUN_COUNT) throw new WorkflowError("invalid_response");
  return {
    runCount: details.runCount as number,
    preservedProperties: dispositionProperties(details.preservedProperties, PRESERVED_TEXT_PROPERTIES),
    unsupportedProperties: dispositionProperties(details.unsupportedProperties, UNSUPPORTED_TEXT_PROPERTIES),
  };
}

function parseConversionDisposition(value: unknown): NewProjectConversionDisposition {
  const item = exactRecord(value, ["version", "id", "sourceNodeId", "sourceName", "sourceType", "level", "reason", "defaultStrategy", "allowedStrategies", "visualImpact", "editabilityImpact", "componentImpact", "blocksApproval", "details"]);
  if (item.version !== 1 || !/^disposition:[0-9a-f]{16}$/.test(String(item.id)) || !Array.isArray(item.allowedStrategies) || typeof item.blocksApproval !== "boolean") throw new WorkflowError("invalid_response");
  const level = exactString(item.level, DISPOSITION_LEVELS) as NewProjectDispositionLevel;
  const reason = exactString(item.reason, DISPOSITION_REASONS) as NewProjectDispositionReason;
  const allowedStrategies = item.allowedStrategies.map((strategy) => exactString(strategy, ADJUSTMENT_STRATEGIES) as NewProjectAdjustmentStrategy);
  const defaultStrategy = item.defaultStrategy == null ? undefined : exactString(item.defaultStrategy, ADJUSTMENT_STRATEGIES) as NewProjectAdjustmentStrategy;
  const details = parseDispositionDetails(item.details);
  const richTextRisk = level === "editable_risk" && (reason === "rich_text_runs" || reason === "text_style_properties");
  const legacyRasterRichTextRisk = richTextRisk
    && item.sourceType === "TEXT"
    && defaultStrategy === "rasterize-subtree"
    && allowedStrategies.length === 2
    && allowedStrategies[0] === "rasterize-subtree"
    && allowedStrategies[1] === "preserve-editable"
    && item.visualImpact === "visual_preserved"
    && item.editabilityImpact === "text_not_editable"
    && item.componentImpact === "unchanged"
    && item.blocksApproval === false;
  if (new Set(allowedStrategies).size !== allowedStrategies.length || Boolean(defaultStrategy) !== (allowedStrategies.length > 0) || defaultStrategy && !allowedStrategies.includes(defaultStrategy) || item.blocksApproval !== (level === "blocked") || details !== null && !richTextRisk || details === null && richTextRisk && !legacyRasterRichTextRisk) throw new WorkflowError("invalid_response");
  return {
    version: 1,
    id: item.id as string,
    sourceNodeId: requiredString(item.sourceNodeId),
    sourceName: requiredString(item.sourceName),
    sourceType: exactString(item.sourceType, ["FRAME", "GROUP", "TRANSFORM_GROUP", "COMPONENT", "SECTION", "INSTANCE", "RECTANGLE", "ELLIPSE", "VECTOR", "BOOLEAN_OPERATION", "STAR", "LINE", "POLYGON", "TEXT", "IMAGE"]),
    level,
    reason,
    ...(defaultStrategy ? { defaultStrategy } : {}),
    allowedStrategies,
    visualImpact: exactString(item.visualImpact, ["unchanged", "visual_preserved", "may_differ"]) as NewProjectConversionDisposition["visualImpact"],
    editabilityImpact: exactString(item.editabilityImpact, ["unchanged", "subtree_not_editable", "text_not_editable", "vector_path_not_editable", "layout_reflow_not_editable"]) as NewProjectConversionDisposition["editabilityImpact"],
    componentImpact: exactString(item.componentImpact, ["unchanged", "instance_not_reusable"]) as NewProjectConversionDisposition["componentImpact"],
    blocksApproval: item.blocksApproval,
    details,
  };
}

function parseNewProjectReview(value: unknown, expectedBuildId: string, expectedGeneration?: number): NewProjectReview {
  const data = exactRecord(value, ["version", "build_id", "generation", "dispositions", "image_reviews", "component_reviews", "package_review", "checks", "warning_ids", "approvable"]);
  if (data.version !== 1 || identifier(data.build_id) !== expectedBuildId || !Array.isArray(data.dispositions) || !Array.isArray(data.image_reviews) || !Array.isArray(data.component_reviews) || !Array.isArray(data.checks) || !Array.isArray(data.warning_ids) || typeof data.approvable !== "boolean") throw new WorkflowError("invalid_response");
  const generation = positive(data.generation);
  if (expectedGeneration != null && generation !== expectedGeneration) throw new WorkflowError("stale_candidate");
  const imageReviews = data.image_reviews.map((value): NewProjectImageReview => {
    const item = exactRecord(value, ["resource_id", "source_node_id", "label", "evidence_kind", "source_preview_url", "generated_asset_url", "width", "height", "nine_slice", "crop_bounds_match", "transparency_preserved"]);
    const evidenceKind = exactString(item.evidence_kind, ["source-image", "generated-only"]) as NewProjectImageReview["evidenceKind"];
    if (typeof item.nine_slice !== "boolean" || typeof item.crop_bounds_match !== "boolean" || typeof item.transparency_preserved !== "boolean") throw new WorkflowError("invalid_response");
    const sourcePreviewUrl = nullableUrl(item.source_preview_url);
    const generatedAssetUrl = nullableUrl(item.generated_asset_url);
    if (!generatedAssetUrl) throw new WorkflowError("invalid_response");
    if ((evidenceKind === "source-image") !== Boolean(sourcePreviewUrl)) throw new WorkflowError("invalid_response");
    return { resourceId: requiredString(item.resource_id), sourceNodeId: requiredString(item.source_node_id), label: requiredString(item.label), evidenceKind, ...(sourcePreviewUrl ? { sourcePreviewUrl } : {}), generatedAssetUrl, width: natural(item.width), height: natural(item.height), nineSlice: item.nine_slice, cropBoundsMatch: item.crop_bounds_match, transparencyPreserved: item.transparency_preserved };
  });
  const componentReviews = data.component_reviews.map((value): NewProjectComponentReview => {
    const item = exactRecord(value, ["component_id", "label", "evidence_kind", "rendered_preview_url", "object_count", "text_count", "resource_refs", "component_refs", "hierarchy_valid", "geometry_valid", "text_valid"]);
    const kind = exactString(item.evidence_kind, ["rendered", "structured-summary"]) as NewProjectComponentReview["evidenceKind"];
    const url = nullableUrl(item.rendered_preview_url);
    if ((kind === "rendered") !== Boolean(url)) throw new WorkflowError("invalid_response");
    if (typeof item.hierarchy_valid !== "boolean" || typeof item.geometry_valid !== "boolean" || typeof item.text_valid !== "boolean") throw new WorkflowError("invalid_response");
    return { componentId: requiredString(item.component_id), label: requiredString(item.label), evidenceKind: kind, ...(url ? { renderedPreviewUrl: url } : {}), objectCount: natural(item.object_count), textCount: natural(item.text_count), resourceRefs: natural(item.resource_refs), componentRefs: natural(item.component_refs), hierarchyValid: item.hierarchy_valid, geometryValid: item.geometry_valid, textValid: item.text_valid };
  });
  const packageData = exactRecord(data.package_review, ["package_name", "fairy_gui_version", "publish_target", "components_added", "resources_added", "component_names", "resource_names", "resource_closure_valid", "naming_conflicts", "integrity_valid"]);
  if (packageData.fairy_gui_version !== "6.1.4" || packageData.publish_target !== "unity" || typeof packageData.resource_closure_valid !== "boolean" || typeof packageData.integrity_valid !== "boolean" || !Array.isArray(packageData.naming_conflicts) || !Array.isArray(packageData.component_names) || !Array.isArray(packageData.resource_names)) throw new WorkflowError("invalid_response");
  const packageReview: NewProjectPackageReview = { packageName: requiredString(packageData.package_name), fairyguiVersion: "6.1.4", publishTarget: "unity", componentsAdded: natural(packageData.components_added), resourcesAdded: natural(packageData.resources_added), componentNames: packageData.component_names.map(requiredString), resourceNames: packageData.resource_names.map(requiredString), resourceClosureValid: packageData.resource_closure_valid, namingConflicts: packageData.naming_conflicts.map(requiredString), integrityValid: packageData.integrity_valid };
  const checks = data.checks.map((value): NewProjectCheck => {
    const item = exactRecord(value, ["id", "severity", "message", "issue_id", "issue_kind", "uir_node_id", "source_node_id", "actionable", "allowed_strategies"]);
    if (!/^review:[0-9a-f]{16}$/.test(String(item.id)) || !/^review:[0-9a-f]{16}$/.test(String(item.issue_id)) || !["ERROR", "WARNING", "INFO"].includes(String(item.severity)) || typeof item.actionable !== "boolean" || !Array.isArray(item.allowed_strategies)) throw new WorkflowError("invalid_response");
    const allowedStrategies = item.allowed_strategies.map((strategy) => exactString(strategy, ADJUSTMENT_STRATEGIES) as NewProjectAdjustmentStrategy);
    const issueKind = item.issue_kind == null ? undefined : exactString(item.issue_kind, ["raster-fallback", "definition-missing"]) as NewProjectCheck["issueKind"];
    if (new Set(allowedStrategies).size !== allowedStrategies.length || item.actionable !== (allowedStrategies.length > 0) || item.actionable && (item.uir_node_id == null || item.source_node_id == null || !issueKind)) throw new WorkflowError("invalid_response");
    return { id: item.id as string, severity: item.severity as NewProjectCheck["severity"], message: requiredString(item.message), issueId: item.issue_id as string, ...(issueKind ? { issueKind } : {}), ...(optionalString(item.uir_node_id) ? { uirNodeId: item.uir_node_id as string } : {}), ...(optionalString(item.source_node_id) ? { sourceNodeId: item.source_node_id as string } : {}), actionable: item.actionable, allowedStrategies };
  });
  const warningIds = data.warning_ids.map((item) => requiredString(item));
  if (new Set(warningIds).size !== warningIds.length || warningIds.join("\0") !== checks.filter((item) => item.severity === "WARNING").map((item) => item.id).join("\0") || data.approvable && (!packageReview.resourceClosureValid || !packageReview.integrityValid || packageReview.namingConflicts.length > 0 || imageReviews.some((item) => !item.sourcePreviewUrl || !item.cropBoundsMatch || !item.transparencyPreserved) || componentReviews.some((item) => !item.hierarchyValid || !item.geometryValid || !item.textValid) || checks.some((item) => item.severity === "ERROR"))) throw new WorkflowError("invalid_response");
  const dispositions = data.dispositions.map(parseConversionDisposition);
  if (new Set(dispositions.map((item) => item.id)).size !== dispositions.length || data.approvable && dispositions.some((item) => item.blocksApproval)) throw new WorkflowError("invalid_response");
  return { version: 1, buildId: expectedBuildId, generation, dispositions, imageReviews, componentReviews, packageReview, checks, warningIds, approvable: data.approvable };
}

function errorCode(status: number, code: unknown): WorkflowErrorCode {
  if (status === 401 || status === 403) return "unauthorized";
  if (["new_project_download_blocked", "new_project_approval_blocked"].includes(String(code))) return "review_required";
  if (["new_project_adjustment_conflict", "new_project_generation_stale", "new_project_regeneration_conflict", "new_project_review_unavailable", "new_project_state_conflict", "new_project_artifact_invalid"].includes(String(code))) return "stale_candidate";
  if (code === "template_not_found") return "unknown_template";
  if (code === "invalid_psd") return "invalid_psd";
  if (code === "psd_too_large") return "psd_too_large";
  if (["hifi_mapping_stale", "hifi_target_stale", "hifi_candidate_stale", "hifi_build_failed", "hifi_mapping_incomplete", "hifi_editor_checks_incomplete", "hifi_download_blocked", "hifi_review_budget_exceeded", "hifi_in_place_raster_unsupported", "hifi_mapping_policy_stale", "hifi_mapping_coverage_incomplete", "hifi_incompatible_mapping", "hifi_nested_visual_mapping_required", "hifi_shared_scope_violation", "hifi_shared_instance_conflict", "hifi_nested_geometry_unverified", "hifi_instance_override_conflict", "hifi_type_conversion_not_authorized", "psd_stroke_only_raster_unsupported"].includes(String(code))) return code as WorkflowErrorCode;
  if (["invalid_archive", "invalid_zip", "invalid_fgui_project", "archive_too_large"].includes(String(code))) return "invalid_zip";
  if (code === "package_request_conflict") return "conversion_conflict";
  if (String(code).startsWith("package_")) return "package_failed";
  if (["project_mismatch", "artifact_integrity"].includes(String(code)) || status === 409) return "conversion_conflict";
  if (["invalid_project_name", "invalid_template_request", "invalid_package_request"].includes(String(code)) || status === 400 || status === 422) return "validation";
  return "conversion_failed";
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) throw new WorkflowError("invalid_response");
  const result = value.map(requiredString);
  if (new Set(result).size !== result.length) throw new WorkflowError("invalid_response");
  return result;
}

function parsePsdInspection(value: unknown): PsdInspection {
  const data = exactRecord(value, ["source_name", "byte_size", "sha256", "width", "height", "depth", "color_mode", "layer_count", "kind_counts", "text_layer_count", "smart_object_count", "adjustment_layer_count", "effect_layer_count", "blocking_issues", "warnings"]);
  if (data.depth !== 8 && data.depth !== 16 || data.color_mode !== "RGB" || typeof data.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(data.sha256)) throw new WorkflowError("invalid_response");
  const kindData = record(data.kind_counts);
  const kindCounts = Object.fromEntries(Object.entries(kindData).map(([kind, count]) => [requiredString(kind), natural(count)]));
  const layerCount = natural(data.layer_count);
  if (Object.values(kindCounts).reduce((sum, count) => sum + count, 0) !== layerCount) throw new WorkflowError("invalid_response");
  return {
    sourceName: requiredString(data.source_name), byteSize: positive(data.byte_size), sha256: data.sha256,
    width: positive(data.width), height: positive(data.height), depth: data.depth, colorMode: data.color_mode,
    layerCount, kindCounts, textLayerCount: natural(data.text_layer_count), smartObjectCount: natural(data.smart_object_count),
    adjustmentLayerCount: natural(data.adjustment_layer_count), effectLayerCount: natural(data.effect_layer_count),
    blockingIssues: stringList(data.blocking_issues), warnings: stringList(data.warnings),
  };
}

function parsePsdSource(value: unknown): PsdSource {
  const data = exactRecord(value, ["version", "source_id", "inspection", "layers"]);
  if (data.version !== 1 || typeof data.source_id !== "string" || !/^[0-9a-f]{64}$/.test(data.source_id) || !Array.isArray(data.layers)) throw new WorkflowError("invalid_response");
  const inspection = parsePsdInspection(data.inspection);
  if (inspection.sha256 !== data.source_id || inspection.layerCount !== data.layers.length) throw new WorkflowError("invalid_response");
  const layers = data.layers.map((value): PsdLayer => {
    const item = exactRecord(value, ["id", "native_id", "parent_id", "document_index", "sibling_index", "name", "path", "kind", "bounds", "visible", "effective_visible", "opacity", "blend_mode", "clipping", "text", "has_pixel_mask", "has_vector_mask", "has_effects", "text_style", "effects"]);
    const id = requiredString(item.id);
    if (!id.startsWith(`psd-layer:${data.source_id}:`) || item.native_id !== null && (!Number.isInteger(item.native_id) || (item.native_id as number) < 0)) throw new WorkflowError("invalid_response");
    const parentId = optionalStringValue(item.parent_id);
    if (!Array.isArray(item.path) || !Array.isArray(item.bounds) || item.bounds.length !== 4 || !item.bounds.every((coordinate) => typeof coordinate === "number" && Number.isFinite(coordinate))) throw new WorkflowError("invalid_response");
    if (typeof item.visible !== "boolean" || typeof item.effective_visible !== "boolean" || typeof item.clipping !== "boolean" || typeof item.has_pixel_mask !== "boolean" || typeof item.has_vector_mask !== "boolean" || typeof item.has_effects !== "boolean") throw new WorkflowError("invalid_response");
    const opacity = natural(item.opacity);
    if (opacity > 255) throw new WorkflowError("invalid_response");
    if (item.text !== null && typeof item.text !== "string") throw new WorkflowError("invalid_response");
    const text = item.text as string | null;
    let textStyle: PsdTextStyle | undefined;
    if (item.text_style !== null) {
      const style = exactRecord(item.text_style, ["runs", "transform", "paragraph_justification", "anti_alias"]);
      if (!Array.isArray(style.runs) || !Array.isArray(style.transform) || style.transform.length !== 6 || !style.transform.every((entry) => typeof entry === "number" && Number.isFinite(entry))) throw new WorkflowError("invalid_response");
      const optionalNatural = (value: unknown) => value === null ? undefined : natural(value);
      textStyle = {
        runs: style.runs.map((value) => {
          const run = exactRecord(value, ["start", "length", "font_name", "font_size", "faux_bold", "faux_italic", "leading", "tracking", "fill_rgba"]);
          if (typeof run.faux_bold !== "boolean" || typeof run.faux_italic !== "boolean") throw new WorkflowError("invalid_response");
          if (run.fill_rgba !== null && (!Array.isArray(run.fill_rgba) || run.fill_rgba.length !== 4 || !run.fill_rgba.every((entry) => typeof entry === "number" && Number.isFinite(entry)))) throw new WorkflowError("invalid_response");
          return {
            start: natural(run.start), length: natural(run.length), fontName: optionalStringValue(run.font_name), fontSize: optionalFiniteNumber(run.font_size),
            fauxBold: run.faux_bold, fauxItalic: run.faux_italic, leading: optionalFiniteNumber(run.leading), tracking: optionalFiniteNumber(run.tracking),
            ...(run.fill_rgba === null ? {} : { fillRgba: run.fill_rgba as [number, number, number, number] }),
          };
        }),
        transform: style.transform as [number, number, number, number, number, number],
        paragraphJustification: optionalNatural(style.paragraph_justification), antiAlias: optionalNatural(style.anti_alias),
      };
    }
    if (!Array.isArray(item.effects)) throw new WorkflowError("invalid_response");
    const effects = item.effects.map((value): PsdLayerEffect => {
      const effect = exactRecord(value, ["kind", "enabled", "blend_mode", "opacity", "color_rgba", "size", "angle", "distance", "spread", "choke", "position"]);
      if (typeof effect.enabled !== "boolean") throw new WorkflowError("invalid_response");
      if (effect.color_rgba !== null && (!Array.isArray(effect.color_rgba) || effect.color_rgba.length !== 4 || !effect.color_rgba.every((entry) => typeof entry === "number" && Number.isFinite(entry)))) throw new WorkflowError("invalid_response");
      return {
        kind: requiredString(effect.kind), enabled: effect.enabled,
        ...(optionalStringValue(effect.blend_mode) ? { blendMode: effect.blend_mode as string } : {}),
        ...(optionalFiniteNumber(effect.opacity) === undefined ? {} : { opacity: effect.opacity as number }),
        ...(effect.color_rgba === null ? {} : { colorRgba: effect.color_rgba as [number, number, number, number] }),
        ...(optionalFiniteNumber(effect.size) === undefined ? {} : { size: effect.size as number }),
        ...(optionalFiniteNumber(effect.angle) === undefined ? {} : { angle: effect.angle as number }),
        ...(optionalFiniteNumber(effect.distance) === undefined ? {} : { distance: effect.distance as number }),
        ...(optionalFiniteNumber(effect.spread) === undefined ? {} : { spread: effect.spread as number }),
        ...(optionalFiniteNumber(effect.choke) === undefined ? {} : { choke: effect.choke as number }),
        ...(optionalStringValue(effect.position) ? { position: effect.position as string } : {}),
      };
    });
    return {
      id, ...(item.native_id === null ? {} : { nativeId: item.native_id as number }), ...(parentId ? { parentId } : {}),
      documentIndex: natural(item.document_index), siblingIndex: natural(item.sibling_index), name: requiredString(item.name),
      path: item.path.map(requiredString), kind: requiredString(item.kind), bounds: item.bounds as [number, number, number, number],
      visible: item.visible, effectiveVisible: item.effective_visible, opacity, blendMode: requiredString(item.blend_mode),
      clipping: item.clipping, ...(text === null ? {} : { text }), hasPixelMask: item.has_pixel_mask,
      hasVectorMask: item.has_vector_mask, hasEffects: item.has_effects, ...(textStyle ? { textStyle } : {}), effects,
    };
  });
  if (new Set(layers.map((layer) => layer.id)).size !== layers.length) throw new WorkflowError("invalid_response");
  const ids = new Set(layers.map((layer) => layer.id));
  if (layers.some((layer) => layer.parentId !== undefined && !ids.has(layer.parentId))) throw new WorkflowError("invalid_response");
  return { sourceId: data.source_id, inspection, layers };
}

function parseFixedFonts(value: unknown): FixedFontStatus[] {
  const data = exactRecord(value, ["version", "fonts"]);
  if (data.version !== 1 || !Array.isArray(data.fonts)) throw new WorkflowError("invalid_response");
  const fonts = data.fonts.map((value): FixedFontStatus => {
    const item = exactRecord(value, ["family", "postscript_name", "source_filename", "sha256", "installed", "matched_filename"]);
    if (typeof item.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(item.sha256) || typeof item.installed !== "boolean") throw new WorkflowError("invalid_response");
    const matchedFilename = optionalString(item.matched_filename);
    if (item.installed !== Boolean(matchedFilename)) throw new WorkflowError("invalid_response");
    return {
      family: requiredString(item.family), postscriptName: requiredString(item.postscript_name),
      sourceFilename: requiredString(item.source_filename), sha256: item.sha256,
      installed: item.installed, ...(matchedFilename ? { matchedFilename } : {}),
    };
  });
  if (new Set(fonts.map((font) => font.sha256)).size !== fonts.length) throw new WorkflowError("invalid_response");
  return fonts;
}

function optionalStringValue(value: unknown): string | undefined {
  return value == null ? undefined : requiredString(value);
}

function boundsTuple(value: unknown): [number, number, number, number] | undefined {
  if (value == null) return undefined;
  if (!Array.isArray(value) || value.length !== 4 || !value.every((item) => typeof item === "number" && Number.isFinite(item) && item >= 0 && item <= 1)) throw new WorkflowError("invalid_response");
  return value as [number, number, number, number];
}

function canvasSize(value: unknown): { width: number; height: number } | undefined {
  if (value == null) return undefined;
  if (!Array.isArray(value) || value.length !== 2 || !value.every((item) => typeof item === "number" && Number.isFinite(item) && item > 0)) throw new WorkflowError("invalid_response");
  return { width: value[0], height: value[1] };
}

function parseHifiTarget(value: unknown): HifiTargetRef {
  const item = exactRecord(value, ["version", "project_id", "project_fingerprint", "package_id", "package_name", "directory", "component_id", "component_name", "component_relative_path"]);
  if (item.version !== 1 || typeof item.project_fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(item.project_fingerprint)) throw new WorkflowError("invalid_response");
  return {
    version: 1,
    projectId: identifier(item.project_id),
    projectFingerprint: item.project_fingerprint,
    packageId: requiredString(item.package_id),
    packageName: requiredString(item.package_name),
    directory: requiredString(item.directory),
    componentId: requiredString(item.component_id),
    componentName: requiredString(item.component_name),
    componentRelativePath: requiredString(item.component_relative_path),
  };
}

function parseHifiTree(value: unknown, projectId: string): HifiProjectTree {
  const data = exactRecord(value, ["version", "project_id", "project_fingerprint", "packages"]);
  if (data.version !== 1 || identifier(data.project_id) !== projectId || typeof data.project_fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(data.project_fingerprint) || !Array.isArray(data.packages)) throw new WorkflowError("invalid_response");
  return {
    projectId,
    projectFingerprint: data.project_fingerprint,
    packages: data.packages.map((value) => {
      const item = exactRecord(value, ["version", "package_id", "name", "directories"]);
      if (item.version !== 1 || !Array.isArray(item.directories)) throw new WorkflowError("invalid_response");
      return {
        packageId: requiredString(item.package_id),
        name: requiredString(item.name),
        directories: item.directories.map((value) => {
          const directory = exactRecord(value, ["version", "path", "selectable", "reason", "components"]);
          if (directory.version !== 1 || !Array.isArray(directory.components) || typeof directory.selectable !== "boolean") throw new WorkflowError("invalid_response");
          return {
            path: requiredString(directory.path),
            selectable: directory.selectable,
            reason: optionalStringValue(directory.reason),
            components: directory.components.map((value) => {
              const component = exactRecord(value, ["version", "resource_id", "name", "relative_path", "selectable", "reason"]);
              if (component.version !== 1 || typeof component.selectable !== "boolean") throw new WorkflowError("invalid_response");
              return { resourceId: requiredString(component.resource_id), name: requiredString(component.name), relativePath: requiredString(component.relative_path), selectable: component.selectable, reason: optionalStringValue(component.reason) };
            }),
          };
        }),
      };
    }),
  };
}

function parseHifiReplacement(value: unknown, expectedId?: string): HifiReplacement {
  const data = exactRecord(value, ["version", "session_id", "status", "selection_id", "target", "mapping_revision", "unresolved_count", "artifact_ready"]);
  const sessionId = identifier(data.session_id);
  if (data.version !== 1 || expectedId && sessionId !== expectedId || !["mapping", "building", "review_ready", "approved", "rejected", "failed", "superseded"].includes(String(data.status)) || typeof data.artifact_ready !== "boolean") throw new WorkflowError("invalid_response");
  return { sessionId, status: data.status as HifiReplacement["status"], selectionId: hifiSourceIdentifier(data.selection_id), target: parseHifiTarget(data.target), mappingRevision: positive(data.mapping_revision), unresolvedCount: natural(data.unresolved_count), artifactReady: data.artifact_ready };
}

function parseHifiMapping(value: unknown): HifiMappingDraft {
  const data = exactRecord(value, ["version", ...("policy_revision" in record(value) ? ["policy_revision"] : []), ...("old_canvas_size" in record(value) ? ["old_canvas_size"] : []), ...("source_canvas_size" in record(value) ? ["source_canvas_size"] : []), "mapping_revision", "items", "unresolved_count"]);
  if (data.version !== 1 || !Array.isArray(data.items)) throw new WorkflowError("invalid_response");
  const items = data.items.map((value): HifiMappingItem => {
    const item = exactRecord(value, ["version", "old_object_id", "old_name", "old_object_type", "old_resource_id", "figma_node_id", "figma_name", "status", "score", "evidence", "action", "candidates", "old_bounds", "figma_bounds", "item_id", ...("owned_source_ids" in record(value) ? ["owned_source_ids"] : []), ...("owned_group_id" in record(value) ? ["owned_group_id"] : []), ...("retained_source_ids" in record(value) ? ["retained_source_ids"] : []), ...("generated_state" in record(value) ? ["generated_state"] : []), ...("default_visible" in record(value) ? ["default_visible"] : []), ...("preserve_runtime_text" in record(value) ? ["preserve_runtime_text"] : []), ...("graph_conversion_proven" in record(value) ? ["graph_conversion_proven"] : []), ...("out_of_scope" in record(value) ? ["out_of_scope"] : []), ...("occluded" in record(value) ? ["occluded"] : []), ...("visual_echo" in record(value) ? ["visual_echo"] : []), ...("composite_group_id" in record(value) ? ["composite_group_id"] : []), ...("composite_source_ids" in record(value) ? ["composite_source_ids"] : []), ...("visual_disposition" in record(value) ? ["visual_disposition"] : []), ...("logical_bounds_policy" in record(value) ? ["logical_bounds_policy"] : [])]);
    if (item.version !== 1 || !["matched", "suggested", "uncertain", "fgui_only", "hifi_added", "blocked", "structural", "out_of_scope", "occluded"].includes(String(item.status)) || typeof item.score !== "number" || !Array.isArray(item.candidates)) throw new WorkflowError("invalid_response");
    const evidence = exactRecord(item.evidence, ["version", "name_score", "position_score", "size_score", "type_score", "parent_score", "order_score", ...("position_authoritative" in record(item.evidence) ? ["position_authoritative"] : [])]);
    if (evidence.version !== 1 || !["name_score", "position_score", "size_score", "type_score", "parent_score", "order_score"].every((key) => typeof evidence[key] === "number" && Number(evidence[key]) >= 0 && Number(evidence[key]) <= 1) || evidence.position_authoritative !== undefined && typeof evidence.position_authoritative !== "boolean") throw new WorkflowError("invalid_response");
    const action = item.action == null ? undefined : exactString(item.action, ["accept", "retarget", "keep_old", "add_visual", "exception", "preserve_structure"]) as HifiMappingAction;
    if (item.owned_source_ids !== undefined && !Array.isArray(item.owned_source_ids) || item.retained_source_ids !== undefined && !Array.isArray(item.retained_source_ids)) throw new WorkflowError("invalid_response");
    if (item.generated_state !== undefined && typeof item.generated_state !== "boolean") throw new WorkflowError("invalid_response");
    if (item.graph_conversion_proven !== undefined && typeof item.graph_conversion_proven !== "boolean") throw new WorkflowError("invalid_response");
    if (item.out_of_scope !== undefined && typeof item.out_of_scope !== "boolean") throw new WorkflowError("invalid_response");
    if (item.occluded !== undefined && typeof item.occluded !== "boolean") throw new WorkflowError("invalid_response");
    if (item.default_visible !== undefined && item.default_visible !== null && typeof item.default_visible !== "boolean") throw new WorkflowError("invalid_response");
    if (item.preserve_runtime_text !== undefined && typeof item.preserve_runtime_text !== "boolean") throw new WorkflowError("invalid_response");
    if (item.visual_echo !== undefined && typeof item.visual_echo !== "boolean") throw new WorkflowError("invalid_response");
    if (item.composite_source_ids !== undefined && !Array.isArray(item.composite_source_ids)) throw new WorkflowError("invalid_response");
    if (item.visual_disposition !== undefined && !["preserve", "retire", "other_state", "structural"].includes(String(item.visual_disposition))) throw new WorkflowError("invalid_response");
    if (item.logical_bounds_policy !== undefined && !["preserve", "resize"].includes(String(item.logical_bounds_policy))) throw new WorkflowError("invalid_response");
    return { itemId: requiredString(item.item_id), oldObjectId: optionalStringValue(item.old_object_id), oldName: optionalStringValue(item.old_name), oldObjectType: optionalStringValue(item.old_object_type), oldResourceId: optionalStringValue(item.old_resource_id), defaultVisible: item.default_visible as boolean | undefined, preserveRuntimeText: item.preserve_runtime_text as boolean | undefined, figmaNodeId: optionalStringValue(item.figma_node_id), figmaName: optionalStringValue(item.figma_name), status: item.status as HifiMappingItem["status"], score: item.score, action, candidates: item.candidates.map(requiredString), oldBounds: boundsTuple(item.old_bounds), figmaBounds: boundsTuple(item.figma_bounds), ownedSourceIds: (item.owned_source_ids as unknown[] | undefined)?.map(requiredString), ownedGroupId: optionalStringValue(item.owned_group_id), retainedSourceIds: (item.retained_source_ids as unknown[] | undefined)?.map(requiredString), generatedState: item.generated_state as boolean | undefined, graphConversionProven: item.graph_conversion_proven as boolean | undefined, positionAuthoritative: evidence.position_authoritative as boolean | undefined, outOfScope: item.out_of_scope as boolean | undefined, occluded: item.occluded as boolean | undefined, visualEcho: item.visual_echo as boolean | undefined, compositeGroupId: optionalStringValue(item.composite_group_id), compositeSourceIds: (item.composite_source_ids as unknown[] | undefined)?.map(requiredString), visualDisposition: item.visual_disposition as HifiMappingItem["visualDisposition"], logicalBoundsPolicy: item.logical_bounds_policy as HifiMappingItem["logicalBoundsPolicy"] };
  });
  return { policyRevision: data.policy_revision === undefined ? 0 : natural(data.policy_revision), mappingRevision: positive(data.mapping_revision), unresolvedCount: natural(data.unresolved_count), oldCanvasSize: canvasSize(data.old_canvas_size), sourceCanvasSize: canvasSize(data.source_canvas_size), items };
}

function parseHifiReview(value: unknown, sessionId: string): HifiReplacementReview {
  const data = exactRecord(value, ["version", ...("policy_revision" in record(value) ? ["policy_revision"] : []), "session_id", "mapping_revision", "target", "changed_files", "object_diffs", "protected_checks_passed", "parse_coverage_complete", "approvable", "candidate_sha256", "warnings", "editor_check_required"]);
  if (data.version !== 1 || identifier(data.session_id) !== sessionId || !Array.isArray(data.changed_files) || !Array.isArray(data.object_diffs) || !Array.isArray(data.warnings) || typeof data.protected_checks_passed !== "boolean" || typeof data.parse_coverage_complete !== "boolean" || typeof data.approvable !== "boolean" || typeof data.editor_check_required !== "boolean" || data.candidate_sha256 != null && (typeof data.candidate_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(data.candidate_sha256))) throw new WorkflowError("invalid_response");
  parseHifiTarget(data.target);
  return {
    sessionId,
    mappingRevision: positive(data.mapping_revision),
    changedFiles: data.changed_files.map((value) => {
      const item = exactRecord(value, ["version", "relative_path", "operation", "before_sha256", "after_sha256", "summary"]);
      if (item.version !== 1 || typeof item.after_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(item.after_sha256) || item.before_sha256 != null && (typeof item.before_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(item.before_sha256))) throw new WorkflowError("invalid_response");
      return { relativePath: requiredString(item.relative_path), operation: exactString(item.operation, ["create", "replace"]) as "create" | "replace", summary: requiredString(item.summary) };
    }),
    objectDiffs: data.object_diffs.map((value) => {
      const item = exactRecord(value, ["version", "item_id", "kind", "old_object_id", "old_name", "figma_node_id", "figma_name", "changed_fields", "summary"]);
      if (item.version !== 1 || !Array.isArray(item.changed_fields)) throw new WorkflowError("invalid_response");
      return { itemId: requiredString(item.item_id), kind: exactString(item.kind, ["changed", "added", "kept", "exception"]) as HifiObjectDiff["kind"], oldObjectId: optionalStringValue(item.old_object_id), oldName: optionalStringValue(item.old_name), figmaNodeId: optionalStringValue(item.figma_node_id), figmaName: optionalStringValue(item.figma_name), changedFields: item.changed_fields.map(requiredString), summary: requiredString(item.summary) };
    }),
    protectedChecksPassed: data.protected_checks_passed,
    parseCoverageComplete: data.parse_coverage_complete,
    approvable: data.approvable && data.policy_revision === HIFI_POLICY_REVISION,
    candidateSha256: optionalStringValue(data.candidate_sha256),
    warnings: data.warnings.map(requiredString),
    editorCheckRequired: data.editor_check_required,
  };
}

function parseHifiEditorVerification(value: unknown, sessionId: string): HifiEditorVerification {
  const data = exactRecord(value, ["version", "session_id", "candidate_sha256", "editor_found", "editor_version", "project_opened", "component_opened", "render_captured", "screenshot_url", "screenshot_sha256", "screenshot_width", "screenshot_height", "expected_width", "expected_height", "full_frame", "mean_pixel_difference", "approvable", "warnings"]);
  const sha256 = (item: unknown, optional = false): string | undefined => {
    if (optional && item == null) return undefined;
    if (typeof item !== "string" || !/^[0-9a-f]{64}$/.test(item)) throw new WorkflowError("invalid_response");
    return item;
  };
  const optionalSize = (item: unknown): number | undefined => item == null ? undefined : positive(item);
  const optionalDifference = data.mean_pixel_difference == null ? undefined : data.mean_pixel_difference;
  if (data.version !== 1 || identifier(data.session_id) !== sessionId || !Array.isArray(data.warnings) || !["editor_found", "project_opened", "component_opened", "render_captured", "full_frame", "approvable"].every((key) => typeof data[key] === "boolean") || data.editor_version != null && data.editor_version !== "6.1.4" || optionalDifference != null && (typeof optionalDifference !== "number" || optionalDifference < 0 || optionalDifference > 1)) throw new WorkflowError("invalid_response");
  return {
    sessionId,
    candidateSha256: sha256(data.candidate_sha256)!,
    editorFound: data.editor_found as boolean,
    editorVersion: data.editor_version as "6.1.4" | undefined,
    projectOpened: data.project_opened as boolean,
    componentOpened: data.component_opened as boolean,
    renderCaptured: data.render_captured as boolean,
    screenshotUrl: nullableUrl(data.screenshot_url),
    screenshotSha256: sha256(data.screenshot_sha256, true),
    screenshotWidth: optionalSize(data.screenshot_width),
    screenshotHeight: optionalSize(data.screenshot_height),
    expectedWidth: positive(data.expected_width),
    expectedHeight: positive(data.expected_height),
    fullFrame: data.full_frame as boolean,
    meanPixelDifference: optionalDifference as number | undefined,
    approvable: data.approvable as boolean,
    warnings: data.warnings.map(requiredString),
  };
}

function abortError(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || error instanceof DOMException && error.name === "AbortError";
}

const defaultWait: Wait = (milliseconds, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
  const finish = () => { signal?.removeEventListener("abort", abort); resolve(); };
  const timer = setTimeout(finish, milliseconds);
  const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
  signal?.addEventListener("abort", abort, { once: true });
});

const SCREENSHOT_FALLBACK_TIMEOUT_MS = 1000;

export class ProjectWorkflowClient {
  private readonly fetchImpl: FetchLike;
  private readonly wait: Wait;
  private readonly pollIntervalMs: number;

  constructor(private readonly config: { serverOrigin: string; pluginToken: string; fetchImpl?: FetchLike; wait?: Wait; pollIntervalMs?: number }) {
    this.fetchImpl = config.fetchImpl ?? fetch;
    this.wait = config.wait ?? defaultWait;
    this.pollIntervalMs = config.pollIntervalMs ?? 1000;
  }

  private async response(path: string, init: RequestInit): Promise<Response> {
    const request = { ...init, headers: { "X-Figma-Plugin-Token": this.config.pluginToken, ...(init.headers ?? {}) } };
    const attempts = init.method === "GET" ? 2 : 1;
    const fetchImpl = this.fetchImpl;
    let response: Response | undefined;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try { response = await fetchImpl.call(globalThis, new URL(path, this.config.serverOrigin).toString(), request); }
      catch (error) {
        if (abortError(error, init.signal ?? undefined)) throw new WorkflowError("aborted");
        if (attempt + 1 === attempts) throw new WorkflowError("network");
        continue;
      }
      if (response.ok) return response;
      if (response.status < 500 || attempt + 1 === attempts) break;
    }
    let code: unknown;
    try { code = record(await response!.json()).detail; code = record(code).code; } catch { code = undefined; }
    throw new WorkflowError(errorCode(response!.status, code));
  }

  private async json(path: string, init: RequestInit): Promise<unknown> {
    const response = await this.response(path, init);
    try { return await response.json(); } catch { throw new WorkflowError("invalid_response"); }
  }

  private async beforeDeadline<T>(deadline: number, outerSignal: AbortSignal | undefined, operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
    if (outerSignal?.aborted) throw new WorkflowError("aborted");
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new WorkflowError("timeout");
    const controller = new AbortController();
    let timedOut = false;
    let rejectControl: (error: WorkflowError) => void = () => {};
    const control = new Promise<never>((_resolve, reject) => { rejectControl = reject; });
    const abort = () => { controller.abort(); rejectControl(new WorkflowError("aborted")); };
    outerSignal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => { timedOut = true; controller.abort(); rejectControl(new WorkflowError("timeout")); }, remaining);
    try { return await Promise.race([operation(controller.signal), control]); }
    catch (error) {
      if (timedOut) throw new WorkflowError("timeout");
      if (outerSignal?.aborted) throw new WorkflowError("aborted");
      throw error;
    } finally {
      clearTimeout(timer);
      outerSignal?.removeEventListener("abort", abort);
    }
  }

  private async bestEffortScreenshotFallback(jobId: string, outerSignal?: AbortSignal): Promise<void> {
    if (outerSignal?.aborted) return;
    try {
      await this.beforeDeadline(
        Date.now() + SCREENSHOT_FALLBACK_TIMEOUT_MS,
        outerSignal,
        (signal) => this.screenshotConsent(jobId, false, signal),
      );
    } catch {
      // Cleanup failure must not replace the original timeout reported to the caller.
    }
  }

  async options(signal?: AbortSignal): Promise<ProjectOption[]> {
    const data = record(await this.json("/v1/figma/project-options", { method: "GET", signal }));
    if (data.version !== 1 || !Array.isArray(data.options)) throw new WorkflowError("invalid_response");
    const options = data.options.map((value) => {
      const item = record(value);
      return { templateId: requiredString(item.template_id), fairyguiVersion: requiredString(item.fairygui_version), targetPlatform: requiredString(item.target_platform), displayName: requiredString(item.display_name) };
    });
    if (new Set(options.map((item) => item.templateId)).size !== options.length) throw new WorkflowError("invalid_response");
    return options;
  }

  async createProject(params: { templateId: string; projectName: string }, signal?: AbortSignal): Promise<ProjectView> {
    return parseProject(await this.json("/v1/projects/from-template", { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, template_id: params.templateId, project_name: params.projectName }) }));
  }

  async uploadProject(project: File, signal?: AbortSignal): Promise<ProjectView> {
    if (!isSupportedProjectArchive(project)) throw new WorkflowError("invalid_zip");
    const body = new FormData();
    body.append("project", project);
    return parseProject(await this.json("/v1/projects/uploads", { method: "POST", signal, body }));
  }

  async inspectPsd(psd: File, signal?: AbortSignal): Promise<PsdInspection> {
    if (!psd.name.toLowerCase().endsWith(".psd")) throw new WorkflowError("invalid_psd");
    const body = new FormData();
    body.append("psd", psd);
    return parsePsdInspection(await this.json("/v1/hifi-sources/psd/inspect", { method: "POST", signal, body }));
  }

  async uploadPsd(psd: File, signal?: AbortSignal): Promise<PsdSource> {
    if (!psd.name.toLowerCase().endsWith(".psd")) throw new WorkflowError("invalid_psd");
    const body = new FormData();
    body.append("psd", psd);
    return parsePsdSource(await this.json("/v1/hifi-sources/psd", { method: "POST", signal, body }));
  }

  async psdComposite(sourceId: string, signal?: AbortSignal): Promise<Blob> {
    if (!/^[0-9a-f]{64}$/.test(sourceId)) throw new WorkflowError("validation");
    const response = await this.response(`/v1/hifi-sources/psd/${sourceId}/composite`, { method: "GET", signal });
    if (response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "image/png" || response.headers.get("X-PSD-Source-SHA256") !== sourceId) throw new WorkflowError("invalid_response");
    const blob = await response.blob();
    if (!blob.size) throw new WorkflowError("invalid_response");
    return blob;
  }

  async projectAssetThumbnail(projectId: string, assetId: string, signal?: AbortSignal): Promise<Blob> {
    if (!/^[0-9a-f]{32}$/.test(projectId) || !/^[A-Za-z0-9_.:-]{1,128}$/.test(assetId)) throw new WorkflowError("validation");
    const response = await this.response(`/v1/projects/${projectId}/assets/${encodeURIComponent(assetId)}/thumbnail`, { method: "GET", signal });
    if (response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "image/webp") throw new WorkflowError("invalid_response");
    return response.blob();
  }

  async fixedFonts(signal?: AbortSignal): Promise<FixedFontStatus[]> {
    return parseFixedFonts(await this.json("/v1/hifi-sources/fonts", { method: "GET", signal }));
  }

  async hifiTargets(projectId: string, signal?: AbortSignal): Promise<HifiProjectTree> {
    return parseHifiTree(await this.json(`/v1/projects/${encodeURIComponent(projectId)}/hifi-targets`, { method: "GET", signal }), projectId);
  }

  async createHifiReplacement(
    manifest: SelectionManifest,
    resources: readonly ExportedResource[],
    project: ProjectView,
    target: HifiTargetRef,
    signal?: AbortSignal,
  ): Promise<HifiReplacementStart> {
    const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    let selection: SelectionView;
    try {
      selection = parseSelectionView(await new SelectionUploader({ serverOrigin: this.config.serverOrigin, pluginToken: this.config.pluginToken, fetchImpl: this.fetchImpl }).send(manifest, resources, idempotencyKey, undefined, signal));
    } catch (error) {
      if (error instanceof SelectionUploadError) throw new WorkflowError(selectionUploadWorkflowCode(error.code));
      throw error;
    }
    const replacement = parseHifiReplacement(await this.json("/v1/hifi-replacements", {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: 1, project_id: project.projectId, selection_id: selection.selection_id, target: {
        version: 1,
        project_id: target.projectId,
        project_fingerprint: target.projectFingerprint,
        package_id: target.packageId,
        package_name: target.packageName,
        directory: target.directory,
        component_id: target.componentId,
        component_name: target.componentName,
        component_relative_path: target.componentRelativePath,
      }, idempotency_key: idempotencyKey }),
    }));
    const mapping = await this.hifiMapping(replacement.sessionId, signal);
    return { project, selection, replacement, mapping };
  }

  async createPsdHifiReplacement(
    sourceId: string,
    project: ProjectView,
    target: HifiTargetRef,
    signal?: AbortSignal,
  ): Promise<HifiPsdReplacementStart> {
    if (!/^[0-9a-f]{64}$/.test(sourceId)) throw new WorkflowError("validation");
    const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    const replacement = parseHifiReplacement(await this.json("/v1/hifi-replacements/from-psd", {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: 1, project_id: project.projectId, psd_source_id: sourceId, target: {
        version: 1,
        project_id: target.projectId,
        project_fingerprint: target.projectFingerprint,
        package_id: target.packageId,
        package_name: target.packageName,
        directory: target.directory,
        component_id: target.componentId,
        component_name: target.componentName,
        component_relative_path: target.componentRelativePath,
      }, idempotency_key: idempotencyKey }),
    }));
    const mapping = await this.hifiMapping(replacement.sessionId, signal);
    return { project, replacement, mapping };
  }

  async resumePsdHifiReplacement(sessionId: string, signal?: AbortSignal): Promise<HifiPsdResume> {
    if (!/^[0-9a-f]{32}$/.test(sessionId)) throw new WorkflowError("validation");
    const replacement = parseHifiReplacement(await this.json(`/v1/hifi-replacements/${sessionId}`, { method: "GET", signal }), sessionId);
    if (!/^[0-9a-f]{64}$/.test(replacement.selectionId)) throw new WorkflowError("validation");
    const [project, source, tree, mapping] = await Promise.all([
      this.json(`/v1/projects/${replacement.target.projectId}`, { method: "GET", signal }).then(parseProject),
      this.json(`/v1/hifi-sources/psd/${replacement.selectionId}`, { method: "GET", signal }).then(parsePsdSource),
      this.hifiTargets(replacement.target.projectId, signal), this.hifiMapping(sessionId, signal),
    ]);
    // Re-analysing a large PSD is expensive, so a stale policy never restarts
    // on its own; the operator starts the re-inventory explicitly.
    const stale = mapping.policyRevision !== HIFI_POLICY_REVISION
      || ["approved", "rejected", "superseded"].includes(replacement.status);
    return { project, source, tree, replacement, mapping, stale };
  }

  async hifiMapping(sessionId: string, signal?: AbortSignal): Promise<HifiMappingDraft> {
    return parseHifiMapping(await this.json(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/mapping`, { method: "GET", signal }));
  }

  async decideHifiMapping(sessionId: string, mappingRevision: number, itemId: string, action: HifiMappingAction, figmaNodeId?: string, signal?: AbortSignal): Promise<HifiReplacement> {
    return parseHifiReplacement(await this.json(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/mapping-decisions`, {
      method: "POST", signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: 1, mapping_revision: mappingRevision, item_id: itemId, action, ...(action === "retarget" ? { figma_node_id: figmaNodeId } : {}) }),
    }), sessionId);
  }

  async buildHifiReplacement(sessionId: string, mappingRevision: number, signal?: AbortSignal): Promise<HifiReplacement> {
    return parseHifiReplacement(await this.json(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/build`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, mapping_revision: mappingRevision }) }), sessionId);
  }

  async reviewHifiReplacement(sessionId: string, signal?: AbortSignal): Promise<HifiReplacementReview> {
    return parseHifiReview(await this.json(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/review`, { method: "GET", signal }), sessionId);
  }

  async verifyHifiReplacementInEditor(sessionId: string, signal?: AbortSignal): Promise<HifiEditorVerification> {
    return parseHifiEditorVerification(await this.json(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/editor-verify`, { method: "POST", signal }), sessionId);
  }

  async hifiEditorScreenshot(sessionId: string, signal?: AbortSignal): Promise<Blob> {
    const response = await this.response(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/editor-screenshot`, { method: "GET", signal });
    if (response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "image/png") throw new WorkflowError("invalid_response");
    const blob = await response.blob();
    if (!blob.size) throw new WorkflowError("invalid_response");
    return blob;
  }

  async approveHifiReplacement(sessionId: string, candidateSha256: string, exportMode: HifiExportMode = "package", signal?: AbortSignal): Promise<HifiReplacement> {
    if (!/^[0-9a-f]{64}$/.test(candidateSha256)) throw new WorkflowError("validation");
    return parseHifiReplacement(await this.json(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/approve`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, layout_checked: true, references_checked: true, interactions_checked: true, editor_version: "6.1.4", candidate_sha256: candidateSha256, export_mode: exportMode }) }), sessionId);
  }

  async createPsdHifiReplacementBatch(sourceId: string, targets: readonly HifiTargetRef[], signal?: AbortSignal): Promise<HifiReplacement[]> {
    if (!/^[0-9a-f]{64}$/.test(sourceId)) throw new WorkflowError("validation");
    if (!targets.length) throw new WorkflowError("validation");
    const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    const payload = await this.json("/v1/hifi-replacements/from-psd-batch", {
      method: "POST", signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: 1, psd_source_id: sourceId, idempotency_key: idempotencyKey, targets: targets.map((target) => ({
        version: 1,
        project_id: target.projectId,
        project_fingerprint: target.projectFingerprint,
        package_id: target.packageId,
        package_name: target.packageName,
        directory: target.directory,
        component_id: target.componentId,
        component_name: target.componentName,
        component_relative_path: target.componentRelativePath,
      })) }),
    });
    if (!Array.isArray(payload)) throw new WorkflowError("invalid_response");
    return payload.map((item) => parseHifiReplacement(item));
  }

  async rejectHifiReplacement(sessionId: string, reason: string, signal?: AbortSignal): Promise<HifiReplacement> {
    return parseHifiReplacement(await this.json(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}/reject`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, reason }) }), sessionId);
  }

  async downloadHifiReplacement(sessionId: string, candidate: boolean, signal?: AbortSignal): Promise<DownloadedPackage> {
    const suffix = candidate ? "/candidate/download" : "/download";
    const response = await this.response(`/v1/hifi-replacements/${encodeURIComponent(sessionId)}${suffix}`, { method: "GET", signal });
    if (response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "application/zip") throw new WorkflowError("invalid_response");
    const blob = await response.blob();
    if (!blob.size) throw new WorkflowError("invalid_response");
    return { blob, downloadName: safeDownloadName(response.headers.get("Content-Disposition")) };
  }

  async createJob(selectionId: string, project: ProjectView, packageName: string, signal?: AbortSignal): Promise<JobView> {
    const path = `/v1/figma/selections/${encodeURIComponent(selectionId)}/projects/${encodeURIComponent(project.projectId)}/jobs`;
    const job = parseJob(await this.json(path, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, selection_id: selectionId, project_id: project.projectId, package_name: packageName }) }), project.projectId);
    if (job.status !== "ready_for_review") throw new WorkflowError("conversion_failed");
    return job;
  }

  async buildPackage(jobId: string, mode: "create" | "update", projectName: string, signal?: AbortSignal): Promise<PackageView> {
    return parsePackage(await this.json(`/v1/jobs/${encodeURIComponent(jobId)}/package`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, mode, project_name: projectName }) }), jobId);
  }

  async screenshotConsent(jobId: string, approved: boolean, signal?: AbortSignal): Promise<PackageView> {
    return parsePackage(await this.json(`/v1/jobs/${encodeURIComponent(jobId)}/semantic-screenshot-consent`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: 1, approved }),
    }), jobId);
  }

  async startNewProject(selectionId: string, projectName: string, signal?: AbortSignal): Promise<NewProjectCandidate> {
    const normalizedProjectName = writerProjectName(projectName);
    const data = await this.json(`/v1/figma/selections/${encodeURIComponent(selectionId)}/new-fgui-projects`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: 1, project_name: normalizedProjectName }),
    });
    return parseNewProjectCandidate(data);
  }

  async getNewProject(buildId: string, generation: number, signal?: AbortSignal): Promise<NewProjectCandidate> {
    return parseNewProjectCandidate(await this.json(`/v1/new-fgui-projects/${encodeURIComponent(buildId)}`, { method: "GET", signal }), buildId, generation);
  }

  async waitForNewProject(candidate: NewProjectCandidate, options: { signal?: AbortSignal; timeoutMs?: number; onStage?: (candidate: NewProjectCandidate) => void } = {}): Promise<NewProjectCandidate> {
    const deadline = Date.now() + (options.timeoutMs ?? 5 * 60_000);
    let current = candidate;
    while (!["awaiting_review", "approved", "rejected", "failed"].includes(current.status)) {
      current = await this.beforeDeadline(deadline, options.signal, (signal) => this.getNewProject(current.buildId, current.generation, signal));
      options.onStage?.(current);
      if (["awaiting_review", "approved", "rejected", "failed"].includes(current.status)) break;
      await this.beforeDeadline(deadline, options.signal, (signal) => this.wait(this.pollIntervalMs, signal));
    }
    return current;
  }

  async createNewProjectCandidate(manifest: SelectionManifest, resources: readonly ExportedResource[], projectName: string, options: { signal?: AbortSignal; timeoutMs?: number; onStage?: (candidate: NewProjectCandidate) => void } = {}): Promise<NewProjectRunResult> {
    const normalizedProjectName = writerProjectName(projectName);
    const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    const deadline = Date.now() + (options.timeoutMs ?? 5 * 60_000);
    let selection: SelectionView;
    try {
      selection = await this.beforeDeadline(deadline, options.signal, async (signal) => parseSelectionView(await new SelectionUploader({ serverOrigin: this.config.serverOrigin, pluginToken: this.config.pluginToken, fetchImpl: this.fetchImpl }).send(manifest, resources, idempotencyKey, undefined, signal)));
    } catch (error) {
      if (abortError(error, options.signal)) throw new WorkflowError("aborted");
      if (error instanceof SelectionUploadError) throw new WorkflowError(selectionUploadWorkflowCode(error.code));
      throw error;
    }
    const started = await this.beforeDeadline(deadline, options.signal, (signal) => this.startNewProject(selection.selection_id, normalizedProjectName, signal));
    options.onStage?.(started);
    const candidate = await this.waitForNewProject(started, { ...options, timeoutMs: Math.max(0, deadline - Date.now()) });
    return { selection, candidate };
  }

  async reviewNewProject(candidate: NewProjectCandidate, signal?: AbortSignal): Promise<NewProjectReview> {
    if (candidate.status !== "awaiting_review" && candidate.status !== "adjusting") throw new WorkflowError("review_required");
    return parseNewProjectReview(await this.json(`/v1/new-fgui-projects/${encodeURIComponent(candidate.buildId)}/review`, { method: "GET", signal }), candidate.buildId, candidate.generation);
  }

  async adjustNewProject(candidate: NewProjectCandidate, review: NewProjectReview, checkId: string, strategy: NewProjectAdjustmentStrategy, signal?: AbortSignal): Promise<NewProjectCandidate> {
    if (candidate.buildId !== review.buildId || candidate.generation !== review.generation) throw new WorkflowError("stale_candidate");
    const check = review.checks.find((item) => item.id === checkId);
    if (!check?.uirNodeId || !check.allowedStrategies.includes(strategy)) throw new WorkflowError("validation");
    const payload = { version: 1, candidate_id: candidate.buildId, generation: candidate.generation, issue_id: check.issueId, uir_node_id: check.uirNodeId, strategy };
    return parseNewProjectCandidate(await this.json(`/v1/new-fgui-projects/${encodeURIComponent(candidate.buildId)}/adjustments`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }), candidate.buildId, candidate.generation);
  }

  async regenerateNewProject(candidate: NewProjectCandidate, options: { signal?: AbortSignal; onStage?: (candidate: NewProjectCandidate) => void } = {}): Promise<NewProjectCandidate> {
    if (candidate.status !== "adjusting") throw new WorkflowError("review_required");
    const started = parseNewProjectCandidate(await this.json(`/v1/new-fgui-projects/${encodeURIComponent(candidate.buildId)}/regenerate`, { method: "POST", signal: options.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, generation: candidate.generation }) }), undefined, candidate.generation + 1);
    options.onStage?.(started);
    return this.waitForNewProject(started, options);
  }

  async approveNewProject(candidate: NewProjectCandidate, review: NewProjectReview, warningIds: readonly string[], signal?: AbortSignal): Promise<NewProjectCandidate> {
    if (candidate.buildId !== review.buildId || candidate.generation !== review.generation || !review.approvable || warningIds.join("\0") !== review.warningIds.join("\0")) throw new WorkflowError("review_required");
    return parseNewProjectCandidate(await this.json(`/v1/new-fgui-projects/${encodeURIComponent(candidate.buildId)}/approve`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, generation: candidate.generation, warning_ids: warningIds }) }), candidate.buildId, candidate.generation);
  }

  async rejectNewProject(candidate: NewProjectCandidate, signal?: AbortSignal): Promise<NewProjectCandidate> {
    return parseNewProjectCandidate(await this.json(`/v1/new-fgui-projects/${encodeURIComponent(candidate.buildId)}/reject`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, generation: candidate.generation }) }), candidate.buildId, candidate.generation);
  }

  async newProjectPreview(buildId: string, path: string, signal?: AbortSignal): Promise<Blob> {
    const prefix = `/v1/new-fgui-projects/${buildId}/previews/`;
    const ownedSelectionPreview = /^\/v1\/figma\/selections\/[0-9a-f]{32}\/previews\/[0-9]+$/.test(path);
    const ownedSelectionResource = /^\/v1\/figma\/selections\/[0-9a-f]{32}\/resources\/[A-Za-z0-9_-]{1,128}$/.test(path);
    if (!/^[0-9a-f]{32}$/.test(buildId) || (!path.startsWith(prefix) && !ownedSelectionPreview && !ownedSelectionResource) || path.includes("..") || path.includes("//")) throw new WorkflowError("invalid_response");
    const response = await this.response(path, { method: "GET", signal });
    const mediaType = response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
    if (!mediaType || !["image/png", "image/jpeg", "image/webp", "image/svg+xml"].includes(mediaType)) throw new WorkflowError("invalid_response");
    const blob = await response.blob();
    if (!blob.size) throw new WorkflowError("invalid_response");
    return blob;
  }

  async downloadNewProject(candidate: NewProjectCandidate, signal?: AbortSignal): Promise<DownloadedPackage> {
    if (candidate.status !== "approved" || !candidate.downloadName || candidate.byteSize == null || !candidate.sha256) throw new WorkflowError("review_required");
    const response = await this.response(`/v1/new-fgui-projects/${encodeURIComponent(candidate.buildId)}/download`, { method: "GET", signal });
    if (response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "application/zip") throw new WorkflowError("invalid_response");
    const downloadName = safeDownloadName(response.headers.get("Content-Disposition"));
    if (downloadName !== candidate.downloadName) throw new WorkflowError("invalid_response");
    const blob = await response.blob();
    if (blob.size !== candidate.byteSize || await sha256Hex(new Uint8Array(await blob.arrayBuffer())) !== candidate.sha256) throw new WorkflowError("invalid_response");
    return { blob, downloadName };
  }

  async requestSemanticScreenshot(jobId: string, screenshot: SemanticScreenshot, signal?: AbortSignal): Promise<PackageView> {
    if (screenshot.mimeType !== "image/png" || !screenshot.bytes.length || screenshot.bytes.length > MAX_SEMANTIC_SCREENSHOT_BYTES) throw new WorkflowError("validation");
    return parsePackage(await this.json(`/v1/jobs/${encodeURIComponent(jobId)}/semantic-screenshot`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "image/png" },
      body: screenshot.bytes as BodyInit,
    }), jobId);
  }

  async waitForPackage(jobId: string, options: WaitForPackageOptions = {}): Promise<PackageView> {
    const deadline = Date.now() + (options.timeoutMs ?? 5 * 60_000);
    let screenshotDecisionHandled = false;
    let screenshotFallbackPending = false;
    while (true) {
      const current = await this.beforeDeadline(deadline, options.signal, async (signal) => parsePackage(await this.json(`/v1/jobs/${encodeURIComponent(jobId)}/package`, { method: "GET", signal }), jobId));
      options.onStage?.({ stage: current.stage, progress: current.progress });
      if (current.status === "ready") return current;
      if (current.status === "failed") throw new WorkflowError("package_failed");
      if (current.status !== "awaiting_screenshot_consent") screenshotFallbackPending = false;
      if (current.status === "awaiting_screenshot_consent" && screenshotFallbackPending) {
        try {
          await this.beforeDeadline(deadline, options.signal, (signal) => this.screenshotConsent(jobId, false, signal));
          screenshotFallbackPending = false;
        } catch (error) {
          if (options.signal?.aborted || error instanceof WorkflowError && error.code === "aborted") throw new WorkflowError("aborted");
          if (error instanceof WorkflowError && error.code === "timeout") {
            await this.bestEffortScreenshotFallback(jobId, options.signal);
            throw error;
          }
        }
        if (!screenshotFallbackPending) continue;
      }
      if (current.status === "awaiting_screenshot_consent" && !screenshotDecisionHandled) {
        if (!options.onScreenshotConsent || !options.requestScreenshot) throw new WorkflowError("invalid_response");
        const approved = await this.beforeDeadline(deadline, options.signal, (signal) => options.onScreenshotConsent!({ jobId, reason: current.screenshotReason!, signal }));
        await this.beforeDeadline(deadline, options.signal, (signal) => this.screenshotConsent(jobId, approved, signal));
        screenshotDecisionHandled = true;
        if (approved) {
          try {
            const screenshot = await this.beforeDeadline(deadline, options.signal, (signal) => options.requestScreenshot!(jobId, signal));
            await this.beforeDeadline(deadline, options.signal, (signal) => this.requestSemanticScreenshot(jobId, screenshot, signal));
          } catch (error) {
            if (options.signal?.aborted || error instanceof WorkflowError && error.code === "aborted") throw new WorkflowError("aborted");
            if (error instanceof WorkflowError && error.code === "timeout") {
              await this.bestEffortScreenshotFallback(jobId, options.signal);
              throw error;
            }
            screenshotFallbackPending = true;
            try {
              await this.beforeDeadline(deadline, options.signal, (signal) => this.screenshotConsent(jobId, false, signal));
              screenshotFallbackPending = false;
            } catch (fallbackError) {
              if (options.signal?.aborted || fallbackError instanceof WorkflowError && fallbackError.code === "aborted") throw new WorkflowError("aborted");
              if (fallbackError instanceof WorkflowError && fallbackError.code === "timeout") {
                await this.bestEffortScreenshotFallback(jobId, options.signal);
                throw fallbackError;
              }
            }
          }
        }
        continue;
      }
      try { await this.beforeDeadline(deadline, options.signal, (signal) => this.wait(this.pollIntervalMs, signal)); }
      catch (error) {
        if (error instanceof WorkflowError) throw error;
        throw new WorkflowError(abortError(error, options.signal) ? "aborted" : "network");
      }
    }
  }

  async downloadPackage(jobId: string, signal?: AbortSignal): Promise<DownloadedPackage> {
    const response = await this.response(`/v1/jobs/${encodeURIComponent(jobId)}/package/download`, { method: "GET", signal });
    if (response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "application/zip") throw new WorkflowError("invalid_response");
    let blob: Blob;
    try { blob = await response.blob(); } catch (error) { throw new WorkflowError(abortError(error, signal) ? "aborted" : "network"); }
    return { blob, downloadName: safeDownloadName(response.headers.get("Content-Disposition")) };
  }

  async runCreate(manifest: SelectionManifest, resources: readonly ExportedResource[], params: { templateId: string; projectName: string }, onStage: WorkflowStageCallback = () => {}, options: WorkflowRunOptions = {}): Promise<WorkflowResult> {
    onStage({ stage: "uploading", progress: 10 });
    return this.run("create", manifest, resources, await this.createProject(params, options.signal), params.projectName, onStage, options);
  }

  async runUpdate(manifest: SelectionManifest, resources: readonly ExportedResource[], archive: File, onStage: WorkflowStageCallback = () => {}, options: WorkflowRunOptions = {}): Promise<WorkflowResult> {
    onStage({ stage: "uploading", progress: 10 });
    const project = await this.uploadProject(archive, options.signal);
    return this.run("update", manifest, resources, project, safeProjectName(archive.name.replace(/(?:\.tar\.gz|\.zip|\.rar|\.7z|\.tar|\.tgz)$/i, ""), targetPackage(project)), onStage, options);
  }

  private async run(mode: "create" | "update", manifest: SelectionManifest, resources: readonly ExportedResource[], project: ProjectView, projectName: string, onStage: WorkflowStageCallback, options: WorkflowRunOptions): Promise<WorkflowResult> {
    const signal = options.signal;
    const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    let selection: SelectionView;
    try { selection = parseSelectionView(await new SelectionUploader({ serverOrigin: this.config.serverOrigin, pluginToken: this.config.pluginToken, fetchImpl: this.fetchImpl }).send(manifest, resources, idempotencyKey, undefined, signal)); }
    catch (error) {
      if (abortError(error, signal)) throw new WorkflowError("aborted");
      if (error instanceof SelectionUploadError) throw new WorkflowError(selectionUploadWorkflowCode(error.code));
      throw error;
    }
    onStage({ stage: "parsing", progress: 35 });
    const packageName = targetPackage(project);
    if (!packageName) throw new WorkflowError("invalid_response");
    onStage({ stage: "converting", progress: 50 });
    const job = await this.createJob(selection.selection_id, project, packageName, signal);
    onStage({ stage: "checking", progress: 70 });
    const started = await this.buildPackage(job.jobId, mode, projectName, signal);
    onStage({ stage: started.stage, progress: started.progress });
    if (started.status === "failed") throw new WorkflowError("package_failed");
    const finished = started.status === "ready" ? started : await this.waitForPackage(job.jobId, { ...options, signal, onStage });
    const download = await this.downloadPackage(job.jobId, signal);
    onStage({ stage: "ready", progress: 100 });
    return { ...download, project, selection, job, package: finished };
  }
}

function safeProjectName(preferred: string, fallback?: string): string {
  for (const value of [preferred, fallback ?? ""]) {
    const safe = value.replace(/[^\w\-\u4e00-\u9fff]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 64);
    if (safe) return safe;
  }
  return "FairyGUI";
}

function validWindowsZipName(value: string): boolean {
  if (!value || value.length > 180 || value.normalize("NFC") !== value || !value.toLowerCase().endsWith(".zip") || value.includes("/") || value.includes("\\") || value.includes("..") || /[\u0000-\u001f\u007f-\u009f<>:"|?*\u200e\u200f\u202a-\u202e\u2066-\u2069]/u.test(value)) return false;
  const stem = value.slice(0, -4);
  if (!stem || /[ .]$/.test(stem)) return false;
  return !/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i.test(stem.split(".", 1)[0]!);
}

function safeDownloadName(contentDisposition: string | null): string {
  let candidate: string | undefined;
  const encoded = contentDisposition?.match(/filename\*\s*=\s*[^']*''([^;]+)/i)?.[1];
  try { if (encoded) candidate = decodeURIComponent(encoded.trim().replace(/^"|"$/g, "")); } catch { candidate = undefined; }
  candidate ??= contentDisposition?.match(/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i)?.slice(1).find(Boolean)?.trim();
  if (!candidate || !validWindowsZipName(candidate)) return "FairyGUI-project.zip";
  return candidate;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  if (!globalThis.crypto?.subtle) return sha256Fallback(bytes);
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  const digest = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", copy.buffer));
  return Array.from(digest, (value) => value.toString(16).padStart(2, "0")).join("");
}

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function sha256Fallback(bytes: Uint8Array): string {
  const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const bitLength = BigInt(bytes.length) * 8n;
  for (let index = 0; index < 8; index += 1) padded[paddedLength - 1 - index] = Number((bitLength >> BigInt(index * 8)) & 0xffn);
  const hash = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const words = new Uint32Array(64);
  const rotate = (value: number, amount: number) => (value >>> amount) | (value << (32 - amount));
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      const start = offset + index * 4;
      words[index] = ((padded[start]! << 24) | (padded[start + 1]! << 16) | (padded[start + 2]! << 8) | padded[start + 3]!) >>> 0;
    }
    for (let index = 16; index < 64; index += 1) {
      const x = words[index - 15]!; const y = words[index - 2]!;
      words[index] = (words[index - 16]! + (rotate(x, 7) ^ rotate(x, 18) ^ (x >>> 3)) + words[index - 7]! + (rotate(y, 17) ^ rotate(y, 19) ^ (y >>> 10))) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index += 1) {
      const sum1 = (rotate(e!, 6) ^ rotate(e!, 11) ^ rotate(e!, 25));
      const temp1 = (h! + sum1 + ((e! & f!) ^ (~e! & g!)) + SHA256_K[index]! + words[index]!) >>> 0;
      const sum0 = (rotate(a!, 2) ^ rotate(a!, 13) ^ rotate(a!, 22));
      const temp2 = (sum0 + ((a! & b!) ^ (a! & c!) ^ (b! & c!))) >>> 0;
      [a, b, c, d, e, f, g, h] = [(temp1 + temp2) >>> 0, a, b, c, (d! + temp1) >>> 0, e, f, g];
    }
    const state = [a!, b!, c!, d!, e!, f!, g!, h!];
    for (let index = 0; index < 8; index += 1) hash[index] = (hash[index]! + state[index]!) >>> 0;
  }
  return Array.from(hash, (value) => value.toString(16).padStart(8, "0")).join("");
}
