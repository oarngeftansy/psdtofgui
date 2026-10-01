import { useState } from "react";
import type { NewProjectAdjustmentStrategy, NewProjectReview } from "../../../figma-plugin/src/project-client";

export type WriterPresentationStep = "automatic" | "review" | "confirm";

const strategyLabels: Record<NewProjectAdjustmentStrategy, string> = {
  "preserve-editable": "保留可编辑结构",
  "rasterize-subtree": "栅格化子树",
  "include-contained-definition": "包含完整组件定义",
};

const reasonLabels = {
  native_structure: "层级与当前几何已保留",
  native_text: "文字保持可编辑",
  native_shape: "图形样式保持可编辑",
  native_image: "图片资源已保留",
  native_component: "组件结构已保留",
  native_instance_structure: "实例内部结构已展开",
  native_vector_resource: "SVG 矢量资源已保留",
  native_static_layout: "静态布局与子对象已保留",
  rasterized_vector: "矢量外观已按图片保真",
  gradient_paint: "渐变已按画面保真",
  visual_effect: "复杂阴影或效果已按画面保真",
  blend_mode: "混合模式已按画面保真",
  multiple_paints: "多重填充已按画面保真",
  mask_composite: "遮罩已按画面保真",
  instance_composite: "组件实例已按画面保真",
  visual_style: "视觉样式已按画面保真",
  unrepresentable_transform: "变换无法原生表达",
  rich_text_runs: "富文本包含多段样式",
  text_style_properties: "文本包含待确认排版属性",
  component_definition_missing: "缺少可验证的组件定义",
  interaction_unsupported: "交互暂不支持转换",
  resource_missing: "转换所需资源缺失",
} as const;

const richTextPropertyLabels: Record<string, string> = {
  content: "文字内容",
  color: "逐段颜色",
  fontSize: "逐段字号",
  fontCandidates: "逐段字体或字重",
  fontWeight: "逐段字重",
  strokeColor: "逐段描边",
  strokeSize: "逐段描边",
  paragraphAlignment: "逐段对齐",
  ubbEncoding: "富文本编码",
};

type ReviewDisposition = NewProjectReview["dispositions"][number];
type ReviewGroup = readonly ReviewDisposition[];
type AutomaticExpansion = Readonly<{ buildId: string; reasons: ReadonlySet<ReviewDisposition["reason"]> }>;

const emptyAutomaticExpansionReasons: ReadonlySet<ReviewDisposition["reason"]> = new Set();

function hasVerifiedRasterEvidence(review: NewProjectReview, item: ReviewDisposition): boolean {
  if (item.level !== "raster_preserved") return false;
  const evidence = review.imageReviews.find((candidate) => candidate.sourceNodeId === item.sourceNodeId);
  return evidence?.evidenceKind === "source-image"
    && Boolean(evidence.sourcePreviewUrl)
    && evidence.cropBoundsMatch
    && evidence.transparencyPreserved;
}

export function suggestedReviewDispositions(review: NewProjectReview): ReviewDisposition[] {
  return review.dispositions.filter((item) => item.level === "blocked"
    || item.level === "editable_risk"
    || item.level === "raster_preserved" && !hasVerifiedRasterEvidence(review, item));
}

function automaticDispositions(review: NewProjectReview): ReviewDisposition[] {
  return review.dispositions.filter((item) => item.level === "native" || hasVerifiedRasterEvidence(review, item));
}

type ReviewExplanation = { detected: string; action: string; impact: string };

const explanations: Record<ReviewDisposition["reason"], ReviewExplanation> = {
  native_structure: { detected: "结构可直接转换。", action: "无需人工处理。", impact: "层级与当前位置、尺寸保持可编辑；不声称保留响应式重排。" },
  native_text: { detected: "文本样式可直接转换。", action: "无需人工处理。", impact: "文字内容与可表达样式保持可编辑。" },
  native_shape: { detected: "图形样式可直接转换。", action: "无需人工处理。", impact: "纯色、描边与圆角保持可编辑。" },
  native_image: { detected: "已生成可替换的 FairyGUI 图片资源。", action: "无需人工处理。", impact: "图片层的位置与尺寸可编辑，不声称像素内容可编辑。" },
  native_component: { detected: "组件结构可直接转换。", action: "无需人工处理。", impact: "实例结构或组件引用继续可用。" },
  native_instance_structure: { detected: "该 Figma 实例的内部层级可读，已按普通结构展开。", action: "无需转图。", impact: "内部对象保持可编辑，但不再是可复用的 FairyGUI 实例引用。" },
  native_vector_resource: { detected: "复杂矢量已保留为安全、自包含的 SVG 资源。", action: "无需转为 PNG。", impact: "可无损缩放并替换资源，但不能在 FairyGUI 中编辑路径锚点。" },
  native_static_layout: { detected: "已按 Figma 当前计算后的几何保留容器与子对象。", action: "无需转图或人工审核。", impact: "对象仍可编辑，但 Figma Auto Layout 的自动重排规则不会迁移。" },
  rasterized_vector: { detected: "Writer 已将任意矢量路径导出为真实 PNG 资源。", action: "审核该最小矢量节点的图像证据。", impact: "外观保留，但矢量路径不再可编辑。" },
  gradient_paint: { detected: "检测到 FairyGUI 6.1.4 不能等价描述的渐变参数。", action: "建议只把渐变绘制层导出为图片，其他文字和布局继续保留。", impact: "渐变颜色停靠点不能在 FairyGUI 中单独调整。" },
  visual_effect: { detected: "检测到无法等价转换的阴影、模糊或背景效果。", action: "建议只合成承载该效果的最小视觉层。", impact: "该效果参数不能单独编辑，但周围结构不受影响。" },
  blend_mode: { detected: "检测到依赖上下层像素的混合模式。", action: "建议合成混合范围内的最小图层组。", impact: "合成范围内的图层不能再分别调整混合关系。" },
  multiple_paints: { detected: "检测到同一图层叠加了多层填充或描边。", action: "优先保留可表达的基础样式，仅对无法表达的叠加结果转图。", impact: "被合成的叠加样式不能逐层编辑。" },
  mask_composite: { detected: "检测到当前 Writer 无法直接编码的蒙版或裁剪组合。", action: "建议提升为独立子组件；仍无法表达时才局部转图。", impact: "只有最终转图的蒙版区域会失去内部编辑能力。" },
  instance_composite: { detected: "这是一个尚未确认 FairyGUI 组件定义的 Figma 实例。", action: "优先保留可读取的内部图层，或关联到已映射组件。", impact: "只有完全无法读取结构时，才需要把实例局部转图。" },
  visual_style: { detected: "检测到尚未完整映射的视觉样式。", action: "先转换纯色、描边、圆角等可表达属性，再审核剩余差异。", impact: "不应因为存在样式就把整个图层转成图片。" },
  unrepresentable_transform: { detected: "检测到包含倾斜、矩阵或其他非标准变换。", action: "优先保留节点与层级，并换算 FairyGUI 可表达的位置、缩放和旋转。", impact: "只有无法换算的几何外观才需要局部转图。" },
  rich_text_runs: { detected: "检测到同一文本内存在两种或更多字符样式。", action: "默认保留为可编辑文本，并标出无法逐段对应的样式。", impact: "确认转图后才会失去逐字编辑能力；单一样式文本不属于此项。" },
  text_style_properties: { detected: "检测到行高、字距或自动尺寸等尚未验证的文本属性。", action: "保留为可编辑纯文本，并逐项列出可能变化的属性。", impact: "文字内容仍可编辑，但列出的排版属性可能与 Figma 不同。" },
  component_definition_missing: { detected: "实例引用的组件定义没有随本次选择提供，也未命中组件映射。", action: "可包含组件定义、选择已有映射，或保留可读取的内部结构。", impact: "在定义确认前不能保证它作为可复用组件引用。" },
  interaction_unsupported: { detected: "检测到 FairyGUI Writer 尚未转换的原型交互。", action: "保留视觉结构，并单独列出需要在 FairyGUI 中补建的交互。", impact: "交互不会自动生效，但不应因此把视觉内容转成图片。" },
  resource_missing: { detected: "转换计划引用的图片或组件资源没有完整上传。", action: "补齐缺失资源后重新生成。", impact: "资源补齐前不能确认画面或下载工程。" },
};

function MissingPreview({ failed = false }: { failed?: boolean }) {
  return <div className="writer-missing-preview" role="status">{failed ? "预览加载失败" : "此项没有真实预览"}</div>;
}

function RichTextSummary({ item }: { item: ReviewDisposition }) {
  if (item.reason !== "rich_text_runs" && item.reason !== "text_style_properties") return null;
  if (item.details) return <section className="writer-rich-text-summary" aria-label="富文本转换摘要">
    <p><strong>{item.details.runCount} 个文本片段</strong></p>
    <p>已保留：{item.details.preservedProperties.map((property) => richTextPropertyLabels[property] ?? property).join("、")}</p>
    <p>无法等价表达：{item.details.unsupportedProperties.map((property) => richTextPropertyLabels[property] ?? property).join("、")}</p>
    <p>文字仍保持可编辑；栅格化为可选操作，选择后文本将不再可编辑。</p>
  </section>;
  if (item.level === "raster_preserved") return <section className="writer-rich-text-summary" aria-label="旧版富文本策略摘要">
    <p><strong>旧版富文本策略</strong></p>
    <p>历史候选未提供文本片段明细；已按明确的栅格化策略处理，文本不再可编辑。</p>
  </section>;
  return null;
}

export function NewProjectReviewPanel({
  review,
  step,
  reviewIndex,
  onReviewIndexChange,
  warningAcknowledged,
  onWarningAcknowledged,
  onLocate,
  onAdjust,
  onCopyReviewArea,
  copyState = "idle",
  previewObjects = {},
  failedPreviewPaths = [],
  disabled = false,
}: {
  review: NewProjectReview;
  step: WriterPresentationStep;
  reviewIndex: number;
  onReviewIndexChange(index: number): void;
  warningAcknowledged: boolean;
  onWarningAcknowledged(value: boolean): void;
  onLocate(nodeId: string): void;
  onAdjust(checkId: string, strategy: NewProjectAdjustmentStrategy): void;
  onCopyReviewArea(item: ReviewDisposition, generatedPreviewUrl?: string, previewWidth?: number, previewHeight?: number): void;
  copyState?: "idle" | "copying" | "copied" | "failed";
  previewObjects?: Readonly<Record<string, string>>;
  failedPreviewPaths?: readonly string[];
  disabled?: boolean;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [automaticExpansion, setAutomaticExpansion] = useState<AutomaticExpansion>(() => ({ buildId: review.buildId, reasons: new Set() }));
  const expandedAutomaticGroups = automaticExpansion.buildId === review.buildId
    ? automaticExpansion.reasons
    : emptyAutomaticExpansionReasons;
  const automatic = automaticDispositions(review);
  const automaticGroups = (Object.keys(reasonLabels) as ReviewDisposition["reason"][])
    .map((reason) => ({ reason, items: automatic.filter((item) => item.reason === reason) }))
    .filter((group) => group.items.length > 0);
  const reviewRank: Record<ReviewDisposition["level"], number> = {
    blocked: 0,
    editable_risk: 1,
    raster_preserved: 2,
    native: 3,
  };
  const orderedReviewItems = suggestedReviewDispositions(review)
    .sort((left, right) => reviewRank[left.level] - reviewRank[right.level]);
  const reviewGroups = orderedReviewItems.reduce<ReviewGroup[]>((groups, item) => {
    if (item.level !== "editable_risk") return [...groups, [item]];
    const details = item.details;
    const signature = JSON.stringify({
      reason: item.reason,
      preserved: details?.preservedProperties ?? [],
      unsupported: details?.unsupportedProperties ?? [],
      runCount: details?.runCount ?? null,
      defaultStrategy: item.defaultStrategy,
      allowedStrategies: item.allowedStrategies,
      visualImpact: item.visualImpact,
      editabilityImpact: item.editabilityImpact,
      componentImpact: item.componentImpact,
    });
    const existing = groups.find((group) => group[0]?.level === "editable_risk" && group[0] && JSON.stringify({
      reason: group[0].reason,
      preserved: group[0].details?.preservedProperties ?? [],
      unsupported: group[0].details?.unsupportedProperties ?? [],
      runCount: group[0].details?.runCount ?? null,
      defaultStrategy: group[0].defaultStrategy,
      allowedStrategies: group[0].allowedStrategies,
      visualImpact: group[0].visualImpact,
      editabilityImpact: group[0].editabilityImpact,
      componentImpact: group[0].componentImpact,
    }) === signature);
    if (!existing) return [...groups, [item]];
    return groups.map((group) => group === existing ? [...group, item] : group);
  }, []);
  const boundedIndex = Math.max(0, Math.min(reviewGroups.length - 1, reviewIndex));
  const currentGroup = reviewGroups[boundedIndex];
  const current = currentGroup?.[0];
  const evidence = current ? review.imageReviews.find((item) => item.sourceNodeId === current.sourceNodeId) : undefined;

  if (step === "automatic") return <section className="writer-step-screen" aria-labelledby="writer-automatic-title">
    <div className="writer-step-heading"><p className="writer-eyebrow">转换完成</p><h2 id="writer-automatic-title">先看已经处理好的内容</h2></div>
    <div className="writer-automatic-list">
      {automaticGroups.map(({ reason, items }) => {
        const expanded = expandedAutomaticGroups.has(reason);
        const visibleItems = expanded ? items : items.slice(0, 5);
        const remaining = items.length - 5;
        return <section className="writer-automatic-group" key={reason} aria-labelledby={`writer-automatic-${reason}`}>
          <h3 id={`writer-automatic-${reason}`}>{reasonLabels[reason]} · {items.length} 项</h3>
          <ul className="writer-automatic-rows">{visibleItems.map((item) => <li className="writer-automatic-row" key={item.id}>{item.sourceName} · {item.sourceType}</li>)}</ul>
          {remaining > 0 && <button
            className="writer-automatic-toggle"
            type="button"
            aria-expanded={expanded}
            onClick={() => setAutomaticExpansion((current) => {
              const reasons = current.buildId === review.buildId ? new Set(current.reasons) : new Set<ReviewDisposition["reason"]>();
              if (reasons.has(reason)) reasons.delete(reason);
              else reasons.add(reason);
              return { buildId: review.buildId, reasons };
            })}
          >{expanded ? "收起" : `展开其余 ${remaining} 项`}</button>}
        </section>;
      })}
    </div>
    {automatic.length === 0 && <p>没有可自动确认的转换项。</p>}
  </section>;

  if (step === "review" && current) {
    const sourceObject = evidence?.sourcePreviewUrl ? previewObjects[evidence.sourcePreviewUrl] : undefined;
    const generatedObject = evidence ? previewObjects[evidence.generatedAssetUrl] : undefined;
    const hasVisualComparison = evidence?.evidenceKind === "source-image" && Boolean(evidence.sourcePreviewUrl);
    const sourcePreviewFailed = Boolean(evidence?.sourcePreviewUrl && failedPreviewPaths.includes(evidence.sourcePreviewUrl));
    const generatedPreviewFailed = Boolean(evidence && failedPreviewPaths.includes(evidence.generatedAssetUrl));
    const matchingChecks = review.checks.filter((check) => check.sourceNodeId === current.sourceNodeId);
    const visibleChecks = currentGroup.length === 1 ? (matchingChecks.length ? matchingChecks : reviewGroups.length === 1 ? review.checks : []) : [];
    return <section className="writer-step-screen" aria-labelledby="writer-review-title">
      <div className="writer-step-heading"><p className="writer-eyebrow">建议审核</p><h2 id="writer-review-title">逐项确认转换结果</h2><p>有真实预览时展示对比；没有时明确标记，不使用占位图冒充结果。</p></div>
      <div className="writer-review-nav"><strong>{boundedIndex + 1} / {reviewGroups.length}</strong><div><button type="button" disabled={boundedIndex === 0} onClick={() => onReviewIndexChange(boundedIndex - 1)}>上一项</button><button type="button" disabled={boundedIndex === reviewGroups.length - 1} onClick={() => onReviewIndexChange(boundedIndex + 1)}>下一项</button></div></div>
      <article className={`writer-illustrated-review is-${current.level}`}>
        <div className="writer-review-title"><div><h3>{currentGroup.length > 1 ? `${currentGroup.length} 个同类文本图层` : current.sourceName}</h3><p>{current.sourceType} · {reasonLabels[current.reason]}</p></div><span className="writer-status-pill">{current.level === "blocked" ? "必须处理" : "建议确认"}</span></div>
        {currentGroup.length > 1 && <section className="writer-rich-text-summary" aria-label="同类文本图层摘要"><p><strong>统一处理 · {currentGroup.length} 项</strong></p><p>{currentGroup.slice(0, 5).map((item) => item.sourceName).join("、")}{currentGroup.length > 5 ? ` 等 ${currentGroup.length} 项` : ""}</p><p>这些节点的风险属性和当前转换方案完全相同，只需确认一次。</p></section>}
        {hasVisualComparison && <div className="writer-portrait-compare">
          <div><div className="writer-preview-label"><strong>Figma 原图</strong><span>{sourceObject ? "来源证据" : "无可用截图"}</span></div><div className="writer-portrait-preview">{sourceObject ? <img src={sourceObject} alt={`${current.sourceName} Figma 原图`} /> : <MissingPreview failed={sourcePreviewFailed} />}</div></div>
          <div><div className="writer-preview-label"><strong>FairyGUI 结果</strong><span>{generatedObject ? "转换预览" : "无可用截图"}</span></div><div className="writer-portrait-preview">{generatedObject ? <img src={generatedObject} alt={`${current.sourceName} FairyGUI 结果`} /> : <MissingPreview failed={generatedPreviewFailed} />}</div></div>
        </div>}
        {current.level === "blocked" && !hasVisualComparison && <div className="writer-no-evidence-status" role="status">无法显示图像对比：没有 sourceNodeId 匹配的图像证据。</div>}
        <RichTextSummary item={current} />
        <div className="writer-review-reason">
          <div><strong>检测结果</strong><p>{explanations[current.reason].detected}</p></div>
          <div><strong>建议处理</strong><p>{explanations[current.reason].action}</p></div>
          <div><strong>影响</strong><p>{explanations[current.reason].impact}</p></div>
        </div>
        <div className="writer-review-controls"><button type="button" disabled={disabled} onClick={() => onLocate(current.sourceNodeId)}>{currentGroup.length > 1 ? "定位首个图层" : "定位到图层"}</button>{visibleChecks.flatMap((check) => check.allowedStrategies.map((strategy) => <button type="button" disabled={disabled} key={`${check.id}:${strategy}`} onClick={() => onAdjust(check.id, strategy)}>{strategyLabels[strategy]}</button>))}</div>
      </article>
      {review.warningIds.length > 0 && <label className="writer-ack"><input type="checkbox" checked={warningAcknowledged} disabled={disabled} onChange={(event) => onWarningAcknowledged(event.currentTarget.checked)} /> 我已查看图示和影响，并接受当前转换方案</label>}
      {generatedObject && <div className="writer-copy-area"><div><strong>需要在 Figma 中继续讨论？</strong><p>{copyState === "copied" ? "已放到当前画板右侧" : copyState === "copying" ? "正在创建独立审核区…" : copyState === "failed" ? "创建失败，请重试" : "不会修改原画板"}</p></div><button type="button" disabled={disabled || copyState === "copying"} onClick={() => onCopyReviewArea(current, evidence?.generatedAssetUrl, evidence?.width, evidence?.height)}>复制到 Figma 审核区</button></div>}
    </section>;
  }

  return <section className="writer-step-screen" aria-labelledby="writer-confirm-title">
    <div className="writer-step-heading"><p className="writer-eyebrow">确认下载</p><h2 id="writer-confirm-title">工程已经可以交付</h2><p>审核结论与工程闭包都已完成。确认后下载可直接打开的 FairyGUI 工程。</p></div>
    <section className="writer-final-card"><div className="writer-review-heading"><h3>最终检查</h3><span className="writer-method is-native">全部通过</span></div><ul><li>✓ {automatic.length} 项自动转换完成</li><li>✓ {reviewGroups.length} 组建议审核已确认（覆盖 {orderedReviewItems.length} 项）</li><li>✓ {orderedReviewItems.filter((item) => item.level === "blocked").length} 项必须处理</li><li>✓ 资源与 XML 闭包{review.packageReview.integrityValid ? "通过" : "未通过"}</li></ul></section>
    <button id="writer-engineering-details" className="secondary-button writer-details-button" type="button" aria-expanded={detailsOpen} onClick={() => setDetailsOpen((value) => !value)}>工程详情</button>
    {detailsOpen && <div className="writer-engineering-panel"><p>FairyGUI {review.packageReview.fairyguiVersion} · {review.packageReview.publishTarget}</p><p>{review.packageReview.componentsAdded} 个组件 · {review.packageReview.resourcesAdded} 个资源</p><p>新增组件：{review.packageReview.componentNames.join("、") || "无"}</p><p>新增资源：{review.packageReview.resourceNames.join("、") || "无"}</p><p>{review.packageReview.resourceClosureValid ? "资源闭包通过" : "资源闭包失败"} · {review.packageReview.integrityValid ? "完整性通过" : "完整性失败"}</p></div>}
  </section>;
}
