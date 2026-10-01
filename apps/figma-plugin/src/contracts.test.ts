import { describe, expect, it } from "vitest";
import { isUiToMainMessage, MAX_REVIEW_PREVIEW_BYTES } from "./contracts";

function png(): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82]);
  new DataView(bytes.buffer).setUint32(16, 100);
  new DataView(bytes.buffer).setUint32(20, 80);
  return bytes;
}

describe("plugin message contracts", () => {
  it("accepts only a bounded typed create-review-area request", () => {
    const valid = { type: "create-review-area", attempt: "review-1", nodeId: "12:34", previewBytes: png(), previewWidth: 100, previewHeight: 80 };
    expect(isUiToMainMessage(valid)).toBe(true);
    expect(isUiToMainMessage({ ...valid, nodeId: "" })).toBe(false);
    expect(isUiToMainMessage({ ...valid, attempt: "x".repeat(129) })).toBe(false);
    expect(isUiToMainMessage({ ...valid, previewBytes: [] })).toBe(false);
    expect(isUiToMainMessage({ ...valid, previewBytes: new Uint8Array(MAX_REVIEW_PREVIEW_BYTES + 1) })).toBe(false);
    expect(isUiToMainMessage({ ...valid, previewWidth: 0 })).toBe(false);
    expect(isUiToMainMessage({ ...valid, previewHeight: 1.5 })).toBe(false);
  });
});
