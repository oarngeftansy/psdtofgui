import type { ExportedResource } from "./assets";
import type { SelectionManifest, SelectionResource, SelectionWarning } from "./selection";

export type SelectionView = { version: 1; selection_id: string; display_name: string; top_level_summaries: Array<{ name: string; type: string }>; preview_urls: string[]; warnings: SelectionWarning[] };
export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export class SelectionUploadError extends Error {
  constructor(message: string, readonly code = "selection_upload_failed") { super(message); this.name = "SelectionUploadError"; }
}

const invalidResponse = () => new SelectionUploadError("服务返回的数据无法识别，请重试", "invalid_response");
const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);

function uploadId(value: unknown): string {
  if (!isRecord(value) || value.version !== 1 || typeof value.upload_id !== "string" || !/^[0-9a-f]{32}$/.test(value.upload_id)) throw invalidResponse();
  return value.upload_id;
}

function uploadState(value: unknown, expected: "manifest_received" | "resources_pending"): void {
  if (!isRecord(value) || value.version !== 1 || value.state !== expected) throw invalidResponse();
}

export function parseSelectionView(value: unknown): SelectionView {
  if (!isRecord(value) || value.version !== 1 || typeof value.selection_id !== "string" || !/^[0-9a-f]{32}$/.test(value.selection_id) || typeof value.display_name !== "string" || !Array.isArray(value.top_level_summaries) || !Array.isArray(value.preview_urls) || !Array.isArray(value.warnings)) throw invalidResponse();
  if (!value.top_level_summaries.every((item) => isRecord(item) && typeof item.name === "string" && typeof item.type === "string") || !value.preview_urls.every((item) => typeof item === "string") || !value.warnings.every((item) => isRecord(item) && typeof item.code === "string" && typeof item.message === "string")) throw invalidResponse();
  return value as SelectionView;
}

export function safeUploadMessage(code: unknown): string {
  if (code === "selection_too_large") return "选择内容过大";
  if (code === "unauthorized") return "插件未获服务授权，请联系管理员";
  return "上传未完成，请重试";
}

function withActualSizes(manifest: SelectionManifest, resources: readonly ExportedResource[]): SelectionManifest {
  const byKey = new Map(resources.map((resource) => [resource.key, resource]));
  const declared: SelectionResource[] = manifest.resources.map((resource) => {
    const exported = byKey.get(resource.key);
    if (!exported || exported.mime_type !== resource.mime_type) throw new SelectionUploadError("上传未完成，请重试");
    return { ...resource, size: exported.bytes.byteLength };
  });
  return { ...manifest, resources: declared };
}

export class SelectionUploader {
  constructor(private readonly options: { serverOrigin: string; pluginToken: string; fetchImpl?: FetchLike }) {}

  async send(manifest: SelectionManifest, resources: readonly ExportedResource[], idempotencyKey: string, onProgress: (progress: { completed: number; total: number }) => void = () => {}, signal?: AbortSignal): Promise<SelectionView> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const headers = { "X-Figma-Plugin-Token": this.options.pluginToken };
    const request = async (url: string, init: RequestInit, retry = false): Promise<unknown> => {
      let response: Response | undefined;
      for (let attempt = 0; attempt < (retry ? 2 : 1); attempt += 1) {
        try { response = await fetchImpl(new URL(url, this.options.serverOrigin).toString(), { ...init, signal, headers: { ...headers, ...(init.headers ?? {}) } }); } catch (error) {
          if (signal?.aborted || error instanceof DOMException && error.name === "AbortError") throw error;
          if (attempt === (retry ? 1 : 0)) throw new SelectionUploadError("无法连接内网服务，请检查网络后重试", "network");
          continue;
        }
        if (response.ok) {
          try { return await response.json(); } catch { throw invalidResponse(); }
        }
        if (response.status < 500 || attempt === 1) break;
      }
      const payload = await response?.json().catch(() => null);
      const detail = isRecord(payload) && isRecord(payload.detail) ? payload.detail : undefined;
      const code = detail?.code;
      const safeCode = response?.status === 401 ? "unauthorized" : code;
      throw new SelectionUploadError(safeUploadMessage(safeCode), typeof safeCode === "string" ? safeCode : "selection_upload_failed");
    };
    const created = uploadId(await request("/v1/figma/selections/uploads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: 1, idempotency_key: idempotencyKey }) }, true));
    const completeManifest = withActualSizes(manifest, resources);
    uploadState(await request(`/v1/figma/selections/uploads/${created}/manifest`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(completeManifest) }), "manifest_received");
    for (let index = 0; index < resources.length; index += 1) {
      const resource = resources[index]!;
      uploadState(await request(`/v1/figma/selections/uploads/${created}/resources/${resource.key}`, { method: "PUT", headers: { "Content-Type": resource.mime_type }, body: resource.bytes as unknown as BodyInit }), "resources_pending");
      onProgress({ completed: index + 1, total: resources.length });
    }
    return parseSelectionView(await request(`/v1/figma/selections/uploads/${created}/commit`, { method: "POST" }, true));
  }
}
