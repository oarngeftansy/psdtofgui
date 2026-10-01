import { useEffect, useMemo, useState, type CSSProperties } from "react";
import type {
  HifiMappingAction,
  HifiMappingDraft,
  HifiMappingItem,
} from "../../../figma-plugin/src/project-client";

type PreviewBounds = [number, number, number, number];
type CanvasSize = { width: number; height: number };

function focusViewport(bounds: PreviewBounds): PreviewBounds {
  const size = Math.min(1, Math.max(0.24, bounds[2] * 3.2, bounds[3] * 3.2));
  const centerX = bounds[0] + bounds[2] / 2;
  const centerY = bounds[1] + bounds[3] / 2;
  return [
    Math.max(0, Math.min(1 - size, centerX - size / 2)),
    Math.max(0, Math.min(1 - size, centerY - size / 2)),
    size,
    size,
  ];
}

function intersects(bounds: PreviewBounds, viewport: PreviewBounds): boolean {
  return (
    bounds[0] < viewport[0] + viewport[2] &&
    bounds[0] + bounds[2] > viewport[0] &&
    bounds[1] < viewport[1] + viewport[3] &&
    bounds[1] + bounds[3] > viewport[1]
  );
}

function MappingCanvas({
  side,
  items,
  currentId,
  onSelect,
  psdPreviewUrl,
  oldPreviewUrl,
  canvasSize,
  focused,
}: {
  side: "old" | "figma";
  items: HifiMappingItem[];
  currentId?: string;
  onSelect(itemId: string): void;
  psdPreviewUrl?: string;
  oldPreviewUrl?: string;
  canvasSize?: CanvasSize;
  focused: boolean;
}) {
  const current = items.find((item) => item.itemId === currentId);
  const currentBounds =
    current && (side === "old" ? current.oldBounds : current.figmaBounds);
  const missingCurrent = current && !currentBounds;
  const sideName = side === "old" ? "旧 FGUI" : "HIFI";
  const viewport: PreviewBounds =
    focused && currentBounds ? focusViewport(currentBounds) : [0, 0, 1, 1];
  const nearby = currentBounds
    ? items
          .filter((item) => item.itemId !== currentId)
          .map((item) => ({
            item,
            bounds: side === "old" ? item.oldBounds : item.figmaBounds,
          }))
          .filter(
            (
              entry,
            ): entry is { item: HifiMappingItem; bounds: PreviewBounds } =>
              Boolean(entry.bounds && intersects(entry.bounds, viewport)),
          )
          .sort((left, right) => {
            const centerX = currentBounds[0] + currentBounds[2] / 2;
            const centerY = currentBounds[1] + currentBounds[3] / 2;
            const leftDistance =
              Math.abs(left.bounds[0] + left.bounds[2] / 2 - centerX) +
              Math.abs(left.bounds[1] + left.bounds[3] / 2 - centerY);
            const rightDistance =
              Math.abs(right.bounds[0] + right.bounds[2] / 2 - centerX) +
              Math.abs(right.bounds[1] + right.bounds[3] / 2 - centerY);
            return leftDistance - rightDistance;
          })
          .slice(0, 12)
          .map((entry) => entry.item)
      : [];
  const previewItems = focused ? (current ? [...nearby, current] : []) : items;
  return (
    <div
      className="hifi-canvas-wrap"
      style={
        canvasSize
          ? ({
              "--canvas-ratio": canvasSize.width / canvasSize.height,
            } as CSSProperties)
          : undefined
      }
    >
      <div
        className="hifi-canvas"
        aria-label={`${sideName} 结构`}
        style={
          canvasSize
            ? { aspectRatio: `${canvasSize.width} / ${canvasSize.height}` }
            : undefined
        }
      >
        {side === "figma" && psdPreviewUrl && (
          <img
            className="hifi-canvas-psd"
            src={psdPreviewUrl}
            alt="HIFI PSD 实际画面"
            style={{
              left: `${(-viewport[0] / viewport[2]) * 100}%`,
              top: `${(-viewport[1] / viewport[3]) * 100}%`,
              width: `${100 / viewport[2]}%`,
              height: `${100 / viewport[3]}%`,
            }}
          />
        )}
        {side === "old" && oldPreviewUrl && currentBounds && (
          <img
            className="hifi-canvas-psd"
            src={oldPreviewUrl}
            alt="旧 FGUI 对象资源图"
            style={{
              left: `${((currentBounds[0] - viewport[0]) / viewport[2]) * 100}%`,
              top: `${((currentBounds[1] - viewport[1]) / viewport[3]) * 100}%`,
              width: `${(currentBounds[2] / viewport[2]) * 100}%`,
              height: `${(currentBounds[3] / viewport[3]) * 100}%`,
            }}
          />
        )}
        {previewItems.map((item) => {
            const bounds = side === "old" ? item.oldBounds : item.figmaBounds;
            if (!bounds) return null;
            const name = side === "old" ? item.oldName : item.figmaName;
            const left = ((bounds[0] - viewport[0]) / viewport[2]) * 100;
            const top = ((bounds[1] - viewport[1]) / viewport[3]) * 100;
            const width = (bounds[2] / viewport[2]) * 100;
            const height = (bounds[3] / viewport[3]) * 100;
            return (
              <button
                type="button"
                aria-label={`${sideName} · ${name ?? item.itemId}`}
                className={`hifi-canvas-object ${item.itemId === currentId ? "is-active" : ""}`}
                style={{
                  left: `${left}%`,
                  top: `${top}%`,
                  width: `${Math.max(width, 4)}%`,
                  height: `${Math.max(height, 4)}%`,
                }}
                onClick={() => onSelect(item.itemId)}
                key={`${side}-${item.itemId}`}
              />
            );
          })}
        {missingCurrent && (
          <div
            className="hifi-canvas-empty is-active"
            data-testid={`${side === "old" ? "fgui" : "hifi"}-focus`}
          >
            此侧无对应对象
          </div>
        )}
        {current && !missingCurrent && (
          <div
            className="hifi-focus-label"
            data-testid={`${side === "old" ? "fgui" : "hifi"}-focus`}
            aria-label={`${sideName} · ${side === "old" ? current.oldName : current.figmaName}`}
          />
        )}
      </div>
      <div className="hifi-canvas-caption">
        <strong>
          {(current &&
            (side === "old" ? current.oldName : current.figmaName)) ||
            "无对应对象"}
        </strong>
        <small>
          {side === "old" && current?.oldObjectType
            ? `旧对象类型：${current.oldObjectType}${oldPreviewUrl ? " · 原资源图" : " · 暂无渲染预览"}`
            : side === "figma" && psdPreviewUrl
              ? "PSD 合成图中的实际位置"
              : "未提供画面预览，当前显示对象边界"}
        </small>
      </div>
    </div>
  );
}

const STATUS_LABELS: Record<HifiMappingItem["status"], string> = {
  matched: "已对应",
  suggested: "建议对应",
  uncertain: "待判断",
  fgui_only: "仅旧工程",
  hifi_added: "PSD 未对应",
  blocked: "PSD 容器待对应",
  structural: "非绘制结构已保留",
  out_of_scope: "范围外（跨包共享组件）",
  occluded: "已遮挡（上层不透明全幅图层）",
};

export function HifiMappingPanel({
  mapping,
  currentItemId,
  busy,
  onCurrentChange,
  onDecision,
  onLocate,
  psdPreviewUrl,
  oldPreviewUrl,
  allowVisualAddition = true,
  allowKeepOld = true,
  reviewLimit = Infinity,
}: {
  mapping: HifiMappingDraft;
  currentItemId?: string;
  busy: boolean;
  onCurrentChange(itemId: string): void;
  onDecision(
    item: HifiMappingItem,
    action: HifiMappingAction,
    figmaNodeId?: string,
  ): void;
  onLocate?(nodeId: string): void;
  psdPreviewUrl?: string;
  oldPreviewUrl?: string;
  allowVisualAddition?: boolean;
  allowKeepOld?: boolean;
  reviewLimit?: number;
}) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [oldChoices, setOldChoices] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<string>();
  const [scope, setScope] = useState<"pending" | "all">("pending");
  const [focused, setFocused] = useState(false);
  const structuralCount = mapping.items.filter(
    (item) => item.action === "preserve_structure" && !item.outOfScope,
  ).length;
  const outOfScopeCount = mapping.items.filter(
    (item) => item.outOfScope,
  ).length;
  const occludedCount = mapping.items.filter((item) => item.occluded).length;
  const ownedVisuals = mapping.items.filter(
    (item) => item.ownedSourceIds && item.ownedSourceIds.length > 0,
  );
  const pendingItems = mapping.items.filter((item) => !item.action);
  const pendingByStatus = Object.entries(STATUS_LABELS)
    .map(([status, label]) => ({
      status,
      label,
      count: pendingItems.filter((item) => item.status === status).length,
    }))
    .filter((group) => group.count > 0);
  const overBudget = pendingItems.length > reviewLimit;
  const activeScope =
    scope === "pending" && pendingItems.length ? "pending" : "all";
  const visibleItems = overBudget
    ? pendingItems.slice(0, reviewLimit)
    : activeScope === "pending"
      ? pendingItems
      : mapping.items;
  const selectItem = (itemId: string) => {
    if (!visibleItems.some((item) => item.itemId === itemId)) setScope("all");
    onCurrentChange(itemId);
  };
  const currentIndex = Math.max(
    0,
    visibleItems.findIndex((item) => item.itemId === currentItemId),
  );
  const current = visibleItems[currentIndex];
  useEffect(() => {
    if (current && current.itemId !== currentItemId)
      onCurrentChange(current.itemId);
  }, [current?.itemId, currentItemId, onCurrentChange]);
  const figmaNames = useMemo(
    () =>
      new Map(
        mapping.items
          .filter((item) => item.figmaNodeId)
          .map((item) => [
            item.figmaNodeId!,
            item.figmaName ?? item.figmaNodeId!,
          ]),
      ),
    [mapping.items],
  );
  if (!current) return <p>当前工程没有可映射对象。</p>;
  const selectedCandidate =
    choices[current.itemId] ?? current.figmaNodeId ?? current.candidates[0];
  const availableOldItems = mapping.items.filter(
    (item) => item.oldObjectId && (!item.action || item.action === "keep_old"),
  );
  const selectedOldItem =
    availableOldItems.find(
      (item) => item.itemId === oldChoices[current.itemId],
    ) ?? availableOldItems[0];
  const hasOld = Boolean(current.oldObjectId);
  const hasFigma = Boolean(current.figmaNodeId);
  const canPickLayer = hasOld && current.candidates.length > 0;
  const canPickOld = !hasOld && hasFigma && availableOldItems.length > 0;
  const pickerOpen = picked === current.itemId;
  const canConfirm = hasOld && hasFigma && current.status !== "blocked";
  const unmappedOld = current.status === "fgui_only" && hasOld && !hasFigma;
  const canSkipBlocked =
    current.status === "blocked" && (!hasOld || allowKeepOld);
  const noWayOut = current.status === "blocked" && hasOld && !allowKeepOld;
  return (
    <>
      <section className="hifi-mapping-heading">
        <div>
          <h2>组件对齐工作台</h2>
          <p>
            右侧显示 PSD 实际画面；先建立旧对象与 PSD
            内容的一对一对应，再判断真正新增的内容。
          </p>
        </div>
        <strong>{mapping.unresolvedCount} 项待处理</strong>
      </section>
      {pendingItems.length > 0 && (
        <p className="local-hifi-note" aria-label="待处理记录分类">
          待处理是审计记录数，不等于独立人工决策：
          {pendingByStatus
            .map((group) => `${group.label} ${group.count}`)
            .join(" · ")}
          。应先修复可复用的归属与状态规则，再核对少量真正歧义。
        </p>
      )}
      {pendingItems.some((item) => item.defaultVisible === false) && (
        <p className="local-hifi-note">
          其中{" "}
          {pendingItems.filter((item) => item.defaultVisible === false).length}{" "}
          条旧对象在控制器默认页不可见，应按非默认状态视觉核对，不能直接判定为
          PSD 删除。
        </p>
      )}
      {structuralCount > 0 && (
        <p className="local-hifi-note">
          已自动保留 {structuralCount}{" "}
          项非绘制结构，写入时核验旧结构完整不变；子对象和 PSD 视觉仍分别核验。
        </p>
      )}
      {outOfScopeCount > 0 && (
        <p className="local-hifi-note">
          范围外 {outOfScopeCount}{" "}
          项来自其他包的共享组件，本轮不替换其视觉；结构、行为与实例参数仍受保护，不占用人工判断。
        </p>
      )}
      {occludedCount > 0 && (
        <p className="local-hifi-note">
          已遮挡 {occludedCount}{" "}
          项被上层不透明全幅图层完全覆盖，不参与最终画面，也不要求对应。
        </p>
      )}
      {ownedVisuals.length > 0 && (
        <p className="local-hifi-note">
          已验证 {ownedVisuals.length} 组多图层视觉的逐叶归属，合计{" "}
          {ownedVisuals.reduce(
            (total, item) => total + (item.ownedSourceIds?.length ?? 0),
            0,
          )}{" "}
          层；各组文字独立保留，候选仍须通过原位写入和 Editor 验证。
        </p>
      )}
      {overBudget && (
        <p className="local-hifi-error" role="status">
          当前有 {pendingItems.length} 条映射记录无法自动判定，超过最多{" "}
          {reviewLimit} 个对象的人工判断上限。下方只展示前 {reviewLimit}{" "}
          个诊断示例，暂不能逐项批准或生成候选；需要先提高自动映射的可靠性。
        </p>
      )}
      {mapping.items.some((item) => item.oldObjectType === "component") && (
        <p className="local-hifi-note">
          旧工程清单已展开组件实例；共享定义、实例参数和状态证据仍分别检查，待处理记录数不代表已通过验收。
        </p>
      )}
      {!overBudget && (
        <div
          className="hifi-mapping-filter"
          role="group"
          aria-label="映射项目范围"
        >
          <button
            type="button"
            className={activeScope === "pending" ? "is-active" : ""}
            aria-pressed={activeScope === "pending"}
            disabled={!pendingItems.length}
            onClick={() => setScope("pending")}
          >
            待确认 {pendingItems.length}
          </button>
          <button
            type="button"
            className={activeScope === "all" ? "is-active" : ""}
            aria-pressed={activeScope === "all"}
            onClick={() => setScope("all")}
          >
            全部 {mapping.items.length}
          </button>
        </div>
      )}
      <div className="hifi-alignment-layout">
        <section className="hifi-structure" aria-label="结构视图">
          <h3>结构视图</h3>
          <div
            className="hifi-mapping-filter"
            role="group"
            aria-label="预览范围"
          >
            <button
              type="button"
              className={!focused ? "is-active" : ""}
              aria-pressed={!focused}
              onClick={() => setFocused(false)}
            >
              完整页面
            </button>
            <button
              type="button"
              className={focused ? "is-active" : ""}
              aria-pressed={focused}
              onClick={() => setFocused(true)}
            >
              聚焦当前对象
            </button>
          </div>
          <div className="hifi-structure-labels">
            <span>旧 FGUI · 对象结构</span>
            <span>HIFI · PSD 实际画面</span>
          </div>
          <div className="hifi-canvas-pair">
            <MappingCanvas
              side="old"
              items={mapping.items}
              currentId={current.itemId}
              onSelect={selectItem}
              oldPreviewUrl={oldPreviewUrl}
              canvasSize={mapping.oldCanvasSize}
              focused={focused}
            />
            <MappingCanvas
              side="figma"
              items={mapping.items}
              currentId={current.itemId}
              onSelect={selectItem}
              psdPreviewUrl={psdPreviewUrl}
              canvasSize={mapping.sourceCanvasSize}
              focused={focused}
            />
          </div>
        </section>
        <aside className="hifi-object-inspector" aria-label="对象映射与处理">
          <div className="hifi-item-nav">
            <button
              type="button"
              disabled={currentIndex === 0}
              onClick={() =>
                onCurrentChange(visibleItems[currentIndex - 1].itemId)
              }
            >
              上一项
            </button>
            <strong>
              {currentIndex + 1} / {visibleItems.length}
            </strong>
            <button
              type="button"
              disabled={currentIndex === visibleItems.length - 1}
              onClick={() =>
                onCurrentChange(visibleItems[currentIndex + 1].itemId)
              }
            >
              下一项
            </button>
          </div>
          <ol className="hifi-mapping-list">
            {visibleItems.map((item) => (
              <li key={item.itemId}>
                <button
                  type="button"
                  className={item.itemId === current.itemId ? "is-active" : ""}
                  aria-pressed={item.itemId === current.itemId}
                  onClick={() => onCurrentChange(item.itemId)}
                >
                  <span>{item.oldName ?? "待找旧对象"}</span>
                  <span>→</span>
                  <span>
                    {item.figmaName ??
                      (item.action === "preserve_structure"
                        ? "非绘制结构已保留"
                        : item.action === "keep_old"
                          ? "保留旧对象"
                          : "尚未找到对应")}
                  </span>
                  <small>
                    {STATUS_LABELS[item.status]}
                    {item.oldObjectId
                      ? ` · 匹配分 ${Math.round(item.score * 100)}/100`
                      : ""}
                  </small>
                </button>
              </li>
            ))}
          </ol>
          <section className="hifi-current-card">
            <div>
              <strong>{current.oldName ?? "HIFI 新增视觉"}</strong>
              <span> → </span>
              <strong>
                {current.figmaName ??
                  (current.action === "keep_old"
                    ? "旧对象保留"
                    : "尚未找到对应")}
              </strong>
            </div>
            {current.ownedSourceIds && current.ownedSourceIds.length > 0 && (
              <p className="writer-inline-note">
                该旧对象将承载 {current.ownedSourceIds.length} 个 PSD
                视觉叶图层的合成资源；{current.retainedSourceIds?.length ?? 0}{" "}
                个文字叶图层仍由原文字对象承载。
              </p>
            )}
            {current.defaultVisible === false && (
              <p className="writer-inline-note">
                此对象在控制器默认页不可见；保留原有状态逻辑，并单独验收非默认页的新视觉。
              </p>
            )}
            {current.status === "blocked" && (
              <p className="writer-inline-note">
                {current.preserveRuntimeText
                  ? "已按实例传入的文字精确找到 PSD 图层，但共享组件各实例的布局不同，须生成实例视觉变体并验证后才能写入。"
                  : "该节点是容器、组件实例或其他非静态叶子，当前不会自动写入 FGUI。"}
              </p>
            )}
            {!overBudget && current.action !== "preserve_structure" && (
              <div className="hifi-decision-actions">
                <p className="hifi-decision-prompt">这一项怎么处理？</p>
                <div
                  className="hifi-decision-choices"
                  role="group"
                  aria-label="处理方式"
                >
                  {canConfirm && (
                    <button
                      className="primary-button compact"
                      type="button"
                      disabled={busy}
                      onClick={() => onDecision(current, "accept")}
                    >
                      对 · 就是这个图层
                    </button>
                  )}
                  {(canPickLayer || canPickOld) && (
                    <button
                      className="secondary-button compact"
                      type="button"
                      aria-pressed={pickerOpen}
                      disabled={busy}
                      onClick={() =>
                        setPicked(pickerOpen ? undefined : current.itemId)
                      }
                    >
                      {pickerOpen ? "收起列表" : "换 · 选另一个"}
                    </button>
                  )}
                  {unmappedOld && (
                    <p className="writer-inline-note">
                      PSD 没有画这个旧对象。列为例外会保留对象与程序逻辑，
                      并隐藏其默认视觉，使画面与设计稿一致。
                    </p>
                  )}
                  {current.status === "hifi_added" &&
                    allowVisualAddition &&
                    current.action !== "add_visual" && (
                      <button
                        className="secondary-button compact"
                        type="button"
                        disabled={busy}
                        onClick={() => onDecision(current, "add_visual")}
                      >
                        这是新增画面
                      </button>
                    )}
                  {current.status === "hifi_added" && !allowVisualAddition && (
                    <p className="writer-inline-note">
                      PSD
                      替换须先找到旧对象；独立新增需要明确归属，当前不能直接新增。
                    </p>
                  )}
                  {current.status === "hifi_added" &&
                    current.action !== "exception" && (
                      <button
                        className="secondary-button compact"
                        type="button"
                        disabled={busy}
                        onClick={() => onDecision(current, "exception")}
                      >
                        跳 · 先不处理
                      </button>
                    )}
                  {canSkipBlocked && current.action !== "exception" && (
                    <button
                      className="primary-button compact"
                      type="button"
                      disabled={busy}
                      onClick={() => onDecision(current, "exception")}
                    >
                      跳 · 列为例外，人工处理
                    </button>
                  )}
                  {unmappedOld && current.action !== "exception" && (
                    <button
                      className="primary-button compact"
                      type="button"
                      disabled={busy}
                      onClick={() => onDecision(current, "exception")}
                    >
                      跳 · PSD 没画它，隐藏旧视觉
                    </button>
                  )}
                  {allowKeepOld && hasOld && current.action !== "keep_old" && (
                    <button
                      className="secondary-button compact"
                      type="button"
                      disabled={busy}
                      onClick={() => onDecision(current, "keep_old")}
                    >
                      跳 · 保留旧对象不替换
                    </button>
                  )}
                  {noWayOut && (
                    <p className="writer-inline-note">
                      该项是容器且挂着旧对象；PSD
                      替换不允许保留旧图，界面内无法决定，需要先解决容器结构。
                    </p>
                  )}
                  {hasOld &&
                    hasFigma &&
                    !allowKeepOld &&
                    current.status !== "hifi_added" && (
                      <p className="writer-inline-note">
                        PSD
                        替换要求每个旧对象都换成新画面；这一项不能跳过，也不能保留旧图。
                      </p>
                    )}
                </div>
                {pickerOpen && canPickLayer && (
                  <label className="hifi-candidate-select">
                    换成哪个图层
                    <select
                      aria-label="换成哪个图层"
                      value={selectedCandidate}
                      disabled={busy}
                      onChange={(event) =>
                        setChoices({
                          ...choices,
                          [current.itemId]: event.currentTarget.value,
                        })
                      }
                    >
                      {current.candidates.map((nodeId) => (
                        <option value={nodeId} key={nodeId}>
                          {figmaNames.get(nodeId) ?? nodeId}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {pickerOpen && canPickLayer && selectedCandidate && (
                  <button
                    className="primary-button compact"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      onDecision(current, "retarget", selectedCandidate)
                    }
                  >
                    改成这一层
                  </button>
                )}
                {pickerOpen && canPickOld && selectedOldItem && (
                  <label className="hifi-candidate-select">
                    对应的旧 FGUI 对象
                    <select
                      aria-label="对应的旧 FGUI 对象"
                      value={selectedOldItem.itemId}
                      disabled={busy}
                      onChange={(event) =>
                        setOldChoices({
                          ...oldChoices,
                          [current.itemId]: event.currentTarget.value,
                        })
                      }
                    >
                      {availableOldItems.map((item) => (
                        <option value={item.itemId} key={item.itemId}>
                          {item.oldName}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {pickerOpen && canPickOld && selectedOldItem && (
                  <button
                    className="primary-button compact"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      onDecision(
                        selectedOldItem,
                        "retarget",
                        current.figmaNodeId,
                      )
                    }
                  >
                    建立一对一对应
                  </button>
                )}
                {current.figmaNodeId && onLocate && (
                  <button
                    className="secondary-button compact"
                    type="button"
                    onClick={() => onLocate(current.figmaNodeId!)}
                  >
                    定位到来源图层
                  </button>
                )}
              </div>
            )}
          </section>
        </aside>
      </div>
    </>
  );
}
