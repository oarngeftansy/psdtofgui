import type { PsdInspection } from "../../../figma-plugin/src/project-client";
import type { SelectionPreflight } from "../../../figma-plugin/src/selection";

export type HifiSourceMode = "psd" | "figma";

export function HifiSourcePicker({ mode, selection, inspection, busy, onModeChange, onPsdChange, onRefreshSelection }: {
  mode: HifiSourceMode;
  selection: SelectionPreflight | null;
  inspection?: PsdInspection;
  busy: boolean;
  onModeChange(mode: HifiSourceMode): void;
  onPsdChange(file?: File): void;
  onRefreshSelection(): void;
}) {
  return <section className="hifi-source-picker" aria-labelledby="hifi-source-title">
    <div className="hifi-section-heading"><div><h2 id="hifi-source-title">HIFI 来源</h2><p>PSD 为默认输入，也可以直接使用当前 Figma 框选。</p></div></div>
    <div className="hifi-source-options" role="radiogroup" aria-label="HIFI 来源">
      <label className={mode === "psd" ? "is-selected" : ""}><input aria-label="PSD 文件" type="radio" name="hifi-source" checked={mode === "psd"} disabled={busy} onChange={() => onModeChange("psd")} /><span><strong>PSD 文件</strong><small>本地解析并检查图层、文字、位深与无损阻断项</small></span></label>
      <label className={mode === "figma" ? "is-selected" : ""}><input aria-label="当前 Figma 框选" type="radio" name="hifi-source" checked={mode === "figma"} disabled={busy} onChange={() => onModeChange("figma")} /><span><strong>当前 Figma 框选</strong><small>使用页面中已完成的单个 HIFI 根 Frame</small></span></label>
    </div>
    {mode === "psd" ? <div className="hifi-source-detail">
      <label className="hifi-file-field">选择 PSD 文件<input type="file" accept=".psd,image/vnd.adobe.photoshop,image/x-photoshop" disabled={busy} onChange={(event) => onPsdChange(event.currentTarget.files?.[0])} /></label>
      {busy && !inspection && <p role="status">正在本机读取 PSD 结构…</p>}
      {inspection && <div className="hifi-psd-report" role="status">
        <div><strong>{inspection.sourceName}</strong><span>{inspection.width} × {inspection.height} · {inspection.depth}-bit {inspection.colorMode}</span></div>
        <p>{inspection.layerCount} 个图层 · {inspection.textLayerCount} 个文字层 · {inspection.smartObjectCount} 个智能对象</p>
        {inspection.blockingIssues.length > 0 ? <p className="is-blocked">{inspection.blockingIssues.length} 项无损阻断：必须先完成等价转换检查，当前不能进入映射。</p> : <p className="is-ready">PSD 结构检查通过，可以创建 Figma 根 Frame。</p>}
      </div>}
    </div> : <section className="writer-selection"><div><h2>当前 HIFI 选择</h2><strong>{selection?.manifest?.display_name ?? "未选择"}</strong><p>{selection?.sendable ? `${selection.nodeCount} 个图层 · ${selection.assetCount} 个资源` : "请在 Figma 中选择 HIFI 根节点"}</p></div><button className="secondary-button compact" type="button" disabled={busy} onClick={onRefreshSelection}>刷新选择</button></section>}
  </section>;
}
