import { useEffect, useState } from "react";
import { ProjectWorkflowClient as WorkflowClient } from "../../figma-plugin/src/project-client";
import { HifiMappingPanel } from "./figma/HifiMappingPanel";
import {
  HifiReplacementReviewActions,
  HifiReplacementReviewPanel,
} from "./figma/HifiReplacementReviewPanel";
import { MaterialPreparation } from "./local/MaterialPreparation";
import {
  useHifiWorkflow,
  type LocalHifiClientLike,
} from "./local/useHifiWorkflow";
export type { LocalHifiClientLike } from "./local/useHifiWorkflow";

async function bootstrapLocalClient(): Promise<LocalHifiClientLike> {
  const response = await fetch("/v1/local/bootstrap", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("local bootstrap unavailable");
  const payload = (await response.json()) as {
    version?: unknown;
    access_token?: unknown;
  };
  if (
    payload.version !== 1 ||
    typeof payload.access_token !== "string" ||
    payload.access_token.length < 32
  )
    throw new Error("invalid local bootstrap");
  return new WorkflowClient({
    serverOrigin: window.location.origin,
    pluginToken: payload.access_token,
  });
}

export function App({ client }: { client?: LocalHifiClientLike } = {}) {
  const [resolved, setResolved] = useState<LocalHifiClientLike | undefined>(
    client,
  );
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (client) {
      setFailed(false);
      setResolved(client);
      return;
    }
    let active = true;
    void bootstrapLocalClient()
      .then((value) => {
        if (active) setResolved(value);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [client]);
  if (failed)
    return (
      <main className="local-hifi-startup">
        <h1>PSD → FairyGUI</h1>
        <p>本地服务没有启用独立应用模式，请重新运行启动脚本。</p>
      </main>
    );
  if (!resolved)
    return (
      <main className="local-hifi-startup">
        <h1>PSD → FairyGUI</h1>
        <p>正在连接本地服务…</p>
      </main>
    );
  return <LocalHifiApp client={resolved} />;
}

function LocalHifiApp({ client }: { client: LocalHifiClientLike }) {
  const {
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
  } = useHifiWorkflow(client);
  return (
    <main className={`local-hifi-app stage-${stage}`}>
      <header className="local-hifi-header">
        <div>
          <p>本地视觉替换 · 验证版</p>
          <h1>PSD → FairyGUI</h1>
        </div>
        <span>所有材料仅在本机处理</span>
      </header>
      <ol className="local-hifi-steps" aria-label="工作流">
        {["准备材料", "盘点映射", "候选与 Editor 审核", "确认交付"].map(
          (label, index) => {
            const current =
              stage === "prepare"
                ? 0
                : stage === "sessions" || stage === "mapping"
                  ? 1
                  : stage === "review"
                    ? 2
                    : 3;
            return (
              <li
                key={label}
                className={
                  index === current
                    ? "is-current"
                    : index < current
                      ? "is-done"
                      : ""
                }
                aria-current={index === current ? "step" : undefined}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                {label}
              </li>
            );
          },
        )}
      </ol>
      {stage !== "prepare" && project && (
        <div className="local-context">
          <span>
            当前工程 <strong>{project.displayName}</strong>
          </span>
          <span>
            目标组件{" "}
            <strong>
              {replacement?.target.componentName ??
                `${targets.length} 个根组件`}
            </strong>
          </span>
          <span>
            来源 <strong>{inspection?.sourceName}</strong>
          </span>
        </div>
      )}
      {psdBusy && (
        <p className="local-progress" role="status">
          {operation}
        </p>
      )}
      {stage !== "prepare" && previewError && (
        <p className="local-hifi-error" role="alert">
          {previewError}{" "}
          <button
            type="button"
            className="secondary-button"
            disabled={psdBusy}
            onClick={() => void retryPreview()}
          >
            重新加载预览
          </button>
        </p>
      )}
      {stage === "prepare" && (
        <MaterialPreparation
          project={project}
          tree={tree}
          selectedList={selectedList}
          fonts={fonts}
          fontError={fontError}
          psdSource={psdSource}
          compositeUrl={compositeUrl}
          previewError={previewError}
          projectBusy={projectBusy}
          psdBusy={psdBusy}
          projectError={projectError}
          onProject={(file) => void chooseProject(file)}
          onPsd={(file) => void choosePsd(file)}
          onToggle={toggleTarget}
          onRetryPreview={() => void retryPreview()}
          onRetryFonts={() => void retryFonts()}
        />
      )}
      {restoreNote && (
        <p className="local-hifi-note" role="status">
          {restoreNote}
        </p>
      )}
      {stage === "mapping" && mapping && (
        <section className="local-mapping-workspace">
          <div className="local-mapping-batch" aria-label="批量映射操作">
            <div>
              <strong>批量处理建议项</strong>
              <p>以下为算法建议，仍需核对具体对象后确认。</p>
            </div>
            <div className="local-hifi-action-buttons">
              <button
                type="button"
                className="secondary-button"
                disabled={
                  psdBusy ||
                  !mapping.items.some(
                    (item) =>
                      item.action === undefined && item.status === "suggested",
                  )
                }
                onClick={() => void decideBatch("suggested")}
              >
                接受建议对应{" "}
                {
                  mapping.items.filter(
                    (item) =>
                      item.action === undefined && item.status === "suggested",
                  ).length
                }
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={
                  psdBusy ||
                  !mapping.items.some(
                    (item) =>
                      item.action === undefined && item.status === "fgui_only",
                  )
                }
                onClick={() => void decideBatch("fgui_only")}
              >
                未绘制对象列为例外{" "}
                {
                  mapping.items.filter(
                    (item) =>
                      item.action === undefined && item.status === "fgui_only",
                  ).length
                }
              </button>
            </div>
          </div>
          <HifiMappingPanel
            mapping={mapping}
            currentItemId={currentItemId}
            busy={psdBusy}
            onCurrentChange={setCurrentItemId}
            onDecision={(item, action, nodeId) =>
              void decide(item, action, nodeId)
            }
            psdPreviewUrl={compositeUrl}
            oldPreviewUrl={oldPreviewUrl}
            allowVisualAddition={false}
            allowKeepOld={false}
          />
        </section>
      )}
      {stage === "sessions" && (
        <section className="local-hifi-card">
          <div className="local-hifi-card-title">
            <div>
              <span>02</span>
              <h2>批量会话</h2>
            </div>
            <strong>{sessions.length} 个目标</strong>
          </div>
          <p>
            PSD 只解析一次，每个目标一个独立会话；逐个完成映射、审核与交付。
          </p>
          <ul className="hifi-session-list">
            {sessions.map((session) => (
              <li key={session.sessionId}>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={psdBusy}
                  onClick={() => void openSession(session)}
                >
                  {session.target.packageName} / {session.target.directory} /{" "}
                  {session.target.componentName}
                </button>
                <small>
                  {session.unresolvedCount} 项待确认 ·{" "}
                  {
                    {
                      mapping: "映射中",
                      building: "生成中",
                      review_ready: "待审核",
                      approved: "已交付",
                      rejected: "已退回",
                      failed: "处理失败",
                      superseded: "已过期",
                    }[session.status]
                  }
                </small>
              </li>
            ))}
          </ul>
        </section>
      )}
      {stage === "review" && review && (
        <section className="local-review-workspace">
          <section className="local-hifi-card local-export-card">
            <div className="local-hifi-card-title">
              <div>
                <span>04</span>
                <h2>交付方式</h2>
              </div>
              <strong>
                {exportMode === "overwrite" ? "覆盖旧工程" : "导出新版"}
              </strong>
            </div>
            <label>
              <input
                type="radio"
                name="hifi-export-mode"
                checked={exportMode === "package"}
                disabled={psdBusy}
                onChange={() => setExportMode("package")}
              />{" "}
              导出新版工程 ZIP，旧工程保持不变
            </label>
            <label>
              <input
                type="radio"
                name="hifi-export-mode"
                checked={exportMode === "overwrite"}
                disabled={psdBusy}
                onChange={() => setExportMode("overwrite")}
              />{" "}
              覆盖本机旧工程，历史版本仍可回退
            </label>
            <p className="writer-inline-note">
              两种方式写入的内容完全相同，且都必须与已审核候选的哈希逐字节一致；覆盖不会跳过任何校验。
            </p>
          </section>
          <HifiReplacementReviewPanel
            review={review}
            checks={checks}
            busy={psdBusy}
            verification={editorVerification}
            editorScreenshotUrl={editorScreenshotUrl}
            onChecksChange={setChecks}
            onDownloadCandidate={() => void download(true)}
            onVerifyEditor={() => void verifyEditor()}
            onReject={(reason) => void reject(reason)}
          />
        </section>
      )}
      {stage === "delivered" && (
        <section className="hifi-delivered" role="status">
          <strong>
            {exportMode === "overwrite"
              ? "已覆盖本机旧工程"
              : "已交付 HIFI 替换工程"}
          </strong>
          <p>
            {exportMode === "overwrite"
              ? "本机旧工程已更新为审核通过的内容，写入字节与候选哈希一致；此前的工程版本仍保留在本机记录中。"
              : "正式工程已确认，可下载 ZIP；内容与审核候选一致。"}
          </p>
          {sessions.length > 1 && (
            <p>批量进度：可回到“批量会话”继续下一个目标。</p>
          )}
        </section>
      )}
      {error && (
        <p className="local-hifi-error" role="alert">
          {error}
        </p>
      )}
      <footer className="local-hifi-actions">
        {stage === "prepare" && (
          <>
            <div>
              <strong>{ready ? "材料已就绪" : "等待必需材料"}</strong>
              <p>
                {projectBusy
                  ? "正在读取旧 FairyGUI 工程…"
                  : projectError
                    ? "请重新选择旧 FairyGUI 工程压缩包"
                    : !project
                      ? "请先导入旧 FairyGUI 工程压缩包"
                      : !targets.length
                        ? "请展开工程目录并勾选一个或多个根组件"
                        : !psdSource
                          ? "请导入 HIFI PSD"
                          : fontError
                            ? "字体检查失败，请在固定字体区域重试"
                            : !fonts.length
                              ? "正在检查固定字体…"
                              : installedFonts !== fonts.length
                                ? "固定字体缺失，请安装后重新检查"
                                : targets.length > 1
                                  ? `将为 ${targets.length} 个根组件分别建立替换会话，PSD 只解析一次。`
                                  : "下一步将直接比较 PSD 图层与目标 FGUI 组件，不经过 Figma。"}
              </p>
            </div>
            <button
              type="button"
              disabled={!ready || projectBusy || psdBusy}
              onClick={() =>
                replacement && mapping
                  ? setStage("mapping")
                  : sessions.length
                    ? setStage("sessions")
                    : void startMapping()
              }
            >
              {replacement && mapping
                ? "继续当前映射"
                : sessions.length
                  ? "查看批量会话"
                  : targets.length > 1
                    ? `为 ${targets.length} 个目标建立会话`
                    : "进入盘点与映射"}
            </button>
          </>
        )}
        {stage === "sessions" && (
          <>
            <div>
              <strong>
                {
                  sessions.filter((session) => session.status !== "approved")
                    .length
                }{" "}
                个会话待处理
              </strong>
              <p>逐个进入映射与交付；PSD 材料已复用，不会重复解析。</p>
            </div>
            <button
              type="button"
              className="secondary-button"
              disabled={psdBusy}
              onClick={() => setStage("prepare")}
            >
              返回材料页
            </button>
          </>
        )}
        {stage === "mapping" && (
          <>
            <div>
              <strong>{mapping?.unresolvedCount ?? 0} 项待确认</strong>
              <p>
                {(mapping?.unresolvedCount ?? 0) > 5
                  ? `还有 ${mapping?.unresolvedCount} 条记录；请逐项审核，候选仍须全部安全归属后生成。`
                  : mapping?.unresolvedCount
                    ? "在对象清单中选择记录，核对画面后确认对应关系。"
                    : "映射已确认，可以生成候选进行审核。"}
              </p>
            </div>
            <div className="local-hifi-action-buttons">
              {sessions.length > 1 && (
                <button
                  type="button"
                  className="secondary-button"
                  disabled={psdBusy}
                  onClick={() => setStage("sessions")}
                >
                  回到批量会话
                </button>
              )}
              <button
                type="button"
                className="secondary-button"
                disabled={psdBusy}
                onClick={() => setStage("prepare")}
              >
                返回材料页
              </button>
              <button
                type="button"
                disabled={Boolean(mapping?.unresolvedCount) || psdBusy}
                onClick={() => void build()}
              >
                {psdBusy ? "处理中…" : "生成审核候选"}
              </button>
            </div>
          </>
        )}
        {stage === "review" && review && (
          <HifiReplacementReviewActions
            approveLabel={
              exportMode === "overwrite" ? "确认并覆盖旧工程" : "确认并交付 ZIP"
            }
            review={review}
            checks={checks}
            verification={editorVerification}
            busy={psdBusy}
            onReturn={() => {
              setStage("mapping");
              setChecks(EMPTY_CHECKS);
            }}
            onApprove={() => void approve()}
          />
        )}
        {stage === "delivered" && (
          <>
            {sessions.length > 1 && (
              <button
                type="button"
                className="secondary-button"
                disabled={psdBusy}
                onClick={() => setStage("sessions")}
              >
                回到批量会话
              </button>
            )}
            {exportMode === "package" && (
              <button
                type="button"
                disabled={psdBusy}
                onClick={() => void download(false)}
              >
                再次下载正式 ZIP
              </button>
            )}
            <button
              type="button"
              className="secondary-button"
              disabled={psdBusy}
              onClick={() => {
                clearSession();
                setSelectedList([]);
              }}
            >
              开始下一次替换
            </button>
          </>
        )}
      </footer>
    </main>
  );
}
