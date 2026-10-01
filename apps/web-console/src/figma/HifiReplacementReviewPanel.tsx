import { useState } from "react";
import type {
  HifiEditorVerification,
  HifiReplacementReview,
} from "../../../figma-plugin/src/project-client";

export type HifiEditorCheckState = {
  layout: boolean;
  references: boolean;
  interactions: boolean;
};

const KIND_LABELS = {
  changed: "修改",
  added: "新增",
  kept: "保留",
  exception: "例外",
} as const;

export function HifiReplacementReviewPanel({
  review,
  checks,
  busy,
  verification,
  editorScreenshotUrl,
  onChecksChange,
  onDownloadCandidate,
  onVerifyEditor,
  onReject,
}: {
  review: HifiReplacementReview;
  checks: HifiEditorCheckState;
  busy: boolean;
  verification?: HifiEditorVerification;
  editorScreenshotUrl?: string;
  onChecksChange(checks: HifiEditorCheckState): void;
  onDownloadCandidate(): void;
  onVerifyEditor?(): void;
  onReject(reason: string): void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  return (
    <>
      <section className="hifi-review-card">
        <h2>候选差异审核</h2>
        <p
          className={
            review.protectedChecksPassed
              ? "hifi-check-ok"
              : "writer-inline-error"
          }
        >
          {review.protectedChecksPassed
            ? "✓ XML 结构保护检查通过；实际交互与各状态仍需验证"
            : "程序行为保护检查未通过"}
        </p>
        {review.candidateSha256 && (
          <p className="hifi-candidate-hash">
            <strong>候选 SHA-256</strong>
            <code>{review.candidateSha256}</code>
          </p>
        )}
        <h3>对象差异</h3>
        {review.objectDiffs.map((item) => (
          <div className={`hifi-diff-row is-${item.kind}`} key={item.itemId}>
            <strong>{KIND_LABELS[item.kind]}</strong>
            <span>
              {item.oldName ?? "新增对象"} →{" "}
              {item.figmaName ?? "无对应 HIFI 对象"}
            </span>
            <small>{item.summary}</small>
          </div>
        ))}
        <h3>文件差异</h3>
        {review.changedFiles.map((file) => (
          <div className="hifi-diff-row" key={file.relativePath}>
            <strong>{file.operation === "replace" ? "修改" : "新增"}</strong>
            <span>{file.relativePath}</span>
            <small>{file.summary}</small>
          </div>
        ))}
        {review.warnings
          .filter((warning) => !warning.includes("_require_equivalence_check"))
          .map((warning) => (
            <p className="writer-inline-note" key={warning}>
              {warning}
            </p>
          ))}
      </section>
      <section className="hifi-editor-check">
        <h2>FairyGUI Editor 检查</h2>
        <p>
          应用会把此哈希对应的候选工程直接交给 FairyGUI
          6.1.4，打开目标组件并保存截图证据。
        </p>
        <div className="hifi-editor-buttons">
          {onVerifyEditor && (
            <button
              className="primary-button"
              type="button"
              disabled={busy}
              onClick={onVerifyEditor}
            >
              {busy ? "正在启动 Editor…" : "自动打开并截图"}
            </button>
          )}
          <button
            className="secondary-button"
            type="button"
            disabled={busy}
            onClick={onDownloadCandidate}
          >
            下载候选 ZIP
          </button>
        </div>
        {verification && (
          <div className="hifi-editor-evidence" role="status">
            <div className="hifi-editor-evidence-summary">
              <strong>
                {verification.renderCaptured
                  ? "已取得 Editor 渲染证据"
                  : verification.editorFound
                    ? "Editor 未完成截图"
                    : "未检测到 FairyGUI Editor"}
              </strong>
              <span className={verification.approvable ? "is-ok" : "is-warn"}>
                {verification.approvable
                  ? "整帧渲染验证通过"
                  : "截图不完整 · 视觉审核仍锁定"}
              </span>
            </div>
            {verification.screenshotWidth && verification.screenshotHeight && (
              <p>
                截图 {verification.screenshotWidth} ×{" "}
                {verification.screenshotHeight}；目标{" "}
                {verification.expectedWidth} × {verification.expectedHeight}
              </p>
            )}
            {editorScreenshotUrl && (
              <figure>
                <img
                  src={editorScreenshotUrl}
                  alt="FairyGUI Editor 候选渲染截图"
                />
                <figcaption>
                  候选工程在 FairyGUI Editor 6.1.4 中的实际渲染
                </figcaption>
              </figure>
            )}
            {verification.warnings.map((warning) => (
              <p className="writer-inline-note" key={warning}>
                {warning}
              </p>
            ))}
          </div>
        )}
        <label>
          <input
            type="checkbox"
            disabled={busy}
            checked={checks.layout}
            onChange={(event) =>
              onChecksChange({ ...checks, layout: event.currentTarget.checked })
            }
          />{" "}
          布局与图层顺序正确
        </label>
        <label>
          <input
            type="checkbox"
            disabled={busy}
            checked={checks.references}
            onChange={(event) =>
              onChecksChange({
                ...checks,
                references: event.currentTarget.checked,
              })
            }
          />{" "}
          图片与共享组件引用正常
        </label>
        <label>
          <input
            type="checkbox"
            disabled={busy}
            checked={checks.interactions}
            onChange={(event) =>
              onChecksChange({
                ...checks,
                interactions: event.currentTarget.checked,
              })
            }
          />{" "}
          Controller、Gear、Transition 正常
        </label>
      </section>
      {rejecting && (
        <section className="hifi-reject-card">
          <label>
            退回原因
            <textarea
              aria-label="退回原因"
              value={reason}
              maxLength={500}
              onChange={(event) => setReason(event.currentTarget.value)}
            />
          </label>
          <button
            type="button"
            className="secondary-button"
            disabled={!reason.trim() || busy}
            onClick={() => onReject(reason.trim())}
          >
            确认退回
          </button>
        </section>
      )}
      <button
        className="secondary-button"
        type="button"
        disabled={busy}
        onClick={() => setRejecting(!rejecting)}
      >
        退回候选
      </button>
    </>
  );
}

export function canDeliverHifi(
  review: HifiReplacementReview,
  checks: HifiEditorCheckState,
  verification?: HifiEditorVerification,
) {
  return Boolean(
    review.candidateSha256 &&
    review.approvable &&
    review.parseCoverageComplete &&
    review.protectedChecksPassed &&
    verification?.approvable &&
    verification.fullFrame &&
    verification.candidateSha256 === review.candidateSha256 &&
    checks.layout &&
    checks.references &&
    checks.interactions,
  );
}

export function HifiReplacementReviewActions({
  review,
  checks,
  verification,
  busy,
  onReturn,
  onApprove,
  approveLabel = "确认并交付 ZIP",
}: {
  review: HifiReplacementReview;
  checks: HifiEditorCheckState;
  verification?: HifiEditorVerification;
  busy: boolean;
  onReturn(): void;
  onApprove(): void;
  approveLabel?: string;
}) {
  const canApprove = canDeliverHifi(review, checks, verification) && !busy;
  const reason = !review.protectedChecksPassed
    ? "结构保护检查未通过，请返回修改。"
    : !verification?.approvable ||
        !verification.fullFrame ||
        verification.candidateSha256 !== review.candidateSha256
      ? "请先完成 Editor 整帧验证，再确认三项检查。"
      : !review.approvable || !review.parseCoverageComplete
        ? "候选仍有未通过的验证，请查看审核提示。"
        : !(checks.layout && checks.references && checks.interactions)
          ? "请确认布局、资源引用与交互三项检查。"
          : "审核已完成，可以交付。";
  return (
    <>
      <p className="hifi-delivery-status" role="status">
        {reason}
      </p>
      <button
        className="secondary-button"
        type="button"
        disabled={busy}
        onClick={onReturn}
      >
        返回对齐修改
      </button>
      <button
        className="primary-button"
        type="button"
        disabled={!canApprove}
        onClick={onApprove}
      >
        {busy ? "处理中…" : approveLabel}
      </button>
    </>
  );
}
