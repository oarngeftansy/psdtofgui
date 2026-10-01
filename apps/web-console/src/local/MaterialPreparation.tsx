import type {
  FixedFontStatus,
  HifiProjectTree,
  ProjectView,
  PsdSource,
} from "../../../figma-plugin/src/project-client";
import { PROJECT_ARCHIVE_ACCEPT } from "../../../figma-plugin/src/project-client";
import {
  HifiTargetPicker,
  type HifiTargetSelection,
} from "../figma/HifiTargetPicker";

export function MaterialPreparation({
  project,
  tree,
  selectedList,
  fonts,
  fontError,
  psdSource,
  compositeUrl,
  previewError,
  projectBusy,
  psdBusy,
  projectError,
  onProject,
  onPsd,
  onToggle,
  onRetryPreview,
  onRetryFonts,
}: {
  project?: ProjectView;
  tree?: HifiProjectTree;
  selectedList: HifiTargetSelection[];
  fonts: FixedFontStatus[];
  fontError: string;
  psdSource?: PsdSource;
  compositeUrl?: string;
  previewError: string;
  projectBusy: boolean;
  psdBusy: boolean;
  projectError: string;
  onProject(file?: File): void;
  onPsd(file?: File): void;
  onToggle(selection: HifiTargetSelection): void;
  onRetryPreview(): void;
  onRetryFonts(): void;
}) {
  const inspection = psdSource?.inspection;
  const installed = fonts.filter((font) => font.installed).length;
  const visibleText =
    psdSource?.layers.filter(
      (layer) => layer.kind.toLowerCase() === "type" && layer.effectiveVisible,
    ) ?? [];
  const styled = visibleText.filter((layer) => layer.textStyle?.runs.length);
  const runs = styled.flatMap((layer) => layer.textStyle?.runs ?? []);
  return (
    <>
      <div className="local-page-heading">
        <h2>准备替换材料</h2>
        <p>导入旧工程，选择需要更新的组件，再添加对应的 PSD 设计稿。</p>
      </div>
      <div className="local-prepare-layout">
        <div className="local-materials">
          <section className="local-hifi-card">
            <div className="local-hifi-card-title">
              <div>
                <span>01</span>
                <h2>旧 FairyGUI 工程</h2>
              </div>
              <small>
                {projectBusy ? "正在读取…" : project ? "已导入" : "必需"}
              </small>
            </div>
            <label className="local-hifi-file">
              旧 FairyGUI 工程压缩包
              <input
                type="file"
                onClick={(event) => {
                  event.currentTarget.value = "";
                }}
                accept={PROJECT_ARCHIVE_ACCEPT}
                disabled={projectBusy || psdBusy}
                onChange={(event) => {
                  onProject(event.currentTarget.files?.[0]);
                }}
              />
            </label>
            {project && (
              <p className="local-hifi-file-name">{project.displayName}</p>
            )}
            {projectError && (
              <p className="local-hifi-error" role="alert">
                {projectError}
              </p>
            )}
            {tree && project ? (
              <HifiTargetPicker
                key={project.projectId}
                project={project}
                tree={tree}
                values={selectedList}
                disabled={projectBusy || psdBusy}
                onToggle={onToggle}
              />
            ) : (
              <div className="local-empty">
                <strong>选择要更新的组件</strong>
                <p>导入工程后，这里会显示包、目录和根组件。</p>
              </div>
            )}
          </section>
          <section className="local-hifi-card">
            <div className="local-hifi-card-title">
              <div>
                <span>02</span>
                <h2>HIFI PSD</h2>
              </div>
              <small>
                {psdBusy ? "处理中…" : inspection ? "已导入" : "必需"}
              </small>
            </div>
            <label className="local-hifi-file">
              HIFI PSD
              <input
                type="file"
                onClick={(event) => {
                  event.currentTarget.value = "";
                }}
                accept=".psd,image/vnd.adobe.photoshop,image/x-photoshop"
                disabled={psdBusy || projectBusy}
                onChange={(event) => {
                  onPsd(event.currentTarget.files?.[0]);
                }}
              />
            </label>
            {inspection && (
              <div className="local-psd-summary">
                <strong>{inspection.sourceName}</strong>
                <p>
                  {inspection.width} × {inspection.height} · {inspection.depth}
                  -bit {inspection.colorMode}
                </p>
                <p>
                  {inspection.layerCount} 个图层 · {inspection.textLayerCount}{" "}
                  个文字层 · {inspection.smartObjectCount} 个智能对象
                </p>
                <p>PSD 已保存在本机，后续映射不会重复上传。</p>
                {visibleText.length > 0 && (
                  <p className="local-text-evidence">
                    可见文字样式 {styled.length} / {visibleText.length} 层 ·
                    字体名称 {runs.filter((run) => run.fontName).length} /{" "}
                    {runs.length} 个运行已解析
                  </p>
                )}
              </div>
            )}
          </section>
          <section className="local-hifi-card local-font-card">
            <div className="local-hifi-card-title">
              <div>
                <span>03</span>
                <h2>固定字体</h2>
              </div>
              <strong
                className={
                  fonts.length > 0 && installed === fonts.length
                    ? "is-ok"
                    : "is-warn"
                }
              >
                {fonts.length
                  ? `固定字体 ${installed} / ${fonts.length}`
                  : fontError
                    ? "检查失败"
                    : "正在检查…"}
              </strong>
            </div>
            <p>自动检查本机字体，供文字图层转换使用。</p>
            <ul>
              {fonts.map((font) => (
                <li key={font.sha256}>
                  <span
                    className={font.installed ? "is-installed" : "is-missing"}
                  >
                    {font.installed ? "✓" : "!"}
                  </span>
                  <div>
                    <strong>{font.family}</strong>
                    <small>
                      {font.installed
                        ? font.postscriptName
                        : "未安装，请安装字体后重新检查"}
                    </small>
                  </div>
                </li>
              ))}
            </ul>
            {fontError && (
              <p className="local-hifi-error" role="alert">
                {fontError}
              </p>
            )}
            {(fontError || installed < fonts.length) && (
              <button
                type="button"
                className="secondary-button"
                onClick={onRetryFonts}
              >
                重新检查字体
              </button>
            )}
          </section>
        </div>
        <aside className="local-source-preview" aria-label="PSD 设计稿预览">
          <div className="local-preview-heading">
            <h2>设计稿预览</h2>
            <span>
              {inspection
                ? `${inspection.width} × ${inspection.height}`
                : "等待 PSD"}
            </span>
          </div>
          {compositeUrl ? (
            <figure className="local-psd-composite">
              <a
                href={compositeUrl}
                target="_blank"
                rel="noreferrer"
                aria-label="打开 PSD 原图"
              >
                <img src={compositeUrl} alt="PSD 合成基准图" />
              </a>
              <figcaption>PSD 内嵌合成基准 · 点击查看原图</figcaption>
            </figure>
          ) : (
            <div className="local-preview-empty">
              <span aria-hidden="true">PSD</span>
              <strong>
                {psdBusy
                  ? "正在准备设计稿…"
                  : inspection
                    ? "预览暂不可用"
                    : "在这里核对设计稿"}
              </strong>
              <p>导入 PSD 后显示完整画面，便于确认来源和尺寸。</p>
            </div>
          )}
          {previewError && (
            <div className="local-hifi-error" role="alert">
              <p>{previewError}</p>
              <button
                type="button"
                className="secondary-button"
                disabled={psdBusy}
                onClick={onRetryPreview}
              >
                重新加载预览
              </button>
            </div>
          )}
        </aside>
      </div>
    </>
  );
}
