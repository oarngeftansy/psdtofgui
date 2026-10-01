import type { ExportedResource } from "./assets";
import type { SelectionManifest, SelectionPreflight } from "./selection";

export const MAX_SEMANTIC_SCREENSHOT_BYTES = 1000 * 1024;
export const MAX_REVIEW_PREVIEW_BYTES = 2 * 1024 * 1024;

export type MainToUiMessage =
  | { type: "selection-preflight"; preflight: SelectionPreflight }
  | { type: "selection-changed"; preflight: SelectionPreflight; locateAttempt?: string }
  | { type: "selection-export"; attempt: string; manifest: SelectionManifest; resources: ExportedResource[] }
  | { type: "semantic-screenshot-export"; attempt: string; mimeType: "image/png"; bytes: Uint8Array }
  | { type: "review-area-created"; attempt: string }
  | { type: "selection-error"; attempt: string; code: string };

export type UiToMainMessage =
  | { type: "selection-preflight" }
  | { type: "selection-export"; attempt: string; mode?: "writer" }
  | { type: "semantic-screenshot-export"; attempt: string }
  | { type: "locate-node"; nodeId: string; attempt: string }
  | { type: "create-review-area"; attempt: string; nodeId: string; previewBytes: Uint8Array; previewWidth: number; previewHeight: number };

export function isUiToMainMessage(value: unknown): value is UiToMainMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as { type?: unknown; attempt?: unknown; mode?: unknown; nodeId?: unknown; previewBytes?: unknown; previewWidth?: unknown; previewHeight?: unknown };
  const boundedIdentity = typeof message.nodeId === "string" && message.nodeId.length > 0 && message.nodeId.length <= 256
    && typeof message.attempt === "string" && message.attempt.length > 0 && message.attempt.length <= 128;
  if (message.type === "create-review-area") return boundedIdentity
    && message.previewBytes instanceof Uint8Array
    && message.previewBytes.length >= 24
    && message.previewBytes.length <= MAX_REVIEW_PREVIEW_BYTES
    && Number.isInteger(message.previewWidth) && Number(message.previewWidth) > 0 && Number(message.previewWidth) <= 4096
    && Number.isInteger(message.previewHeight) && Number(message.previewHeight) > 0 && Number(message.previewHeight) <= 4096;
  return message.type === "selection-preflight" || (
    ((message.type === "selection-export" && (message.mode === undefined || message.mode === "writer")) || message.type === "semantic-screenshot-export")
    && typeof message.attempt === "string"
    && message.attempt.length > 0
  ) || (message.type === "locate-node" && boundedIdentity);
}
