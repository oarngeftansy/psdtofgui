import { describe, expect, it, vi } from "vitest";
import { SelectionUploader } from "./upload";

const manifest = {
  version: 1 as const,
  display_name: "Checkout",
  top_level_nodes: [{ id: "node-1", name: "Checkout", type: "FRAME", bounds: { x: 0, y: 0, width: 1, height: 1 }, children: [], resource_keys: ["asset-1"] }],
  resources: [{ key: "asset-1", mime_type: "image/png" as const, size: 0 }],
  warnings: [],
};

describe("selection upload transaction", () => {
  it("uses the existing idempotent session route order, 64 KiB request bodies, and safe errors", async () => {
    const uploadId = "b".repeat(32);
    const fetchImpl = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ version: 1, upload_id: uploadId }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ version: 1, state: "manifest_received" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ version: 1, state: "resources_pending" })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ version: 1, selection_id: "a".repeat(32), display_name: "Checkout", top_level_summaries: [], preview_urls: [], warnings: [] })));
    const uploader = new SelectionUploader({ serverOrigin: "https://fgui.test/base", pluginToken: "plugin-token", fetchImpl });
    const progress = vi.fn();

    const result = await uploader.send(manifest, [{ key: "asset-1", mime_type: "image/png", bytes: new Uint8Array(65 * 1024) }], "idempotency-key", progress);

    expect(result.selection_id).toBe("a".repeat(32));
    expect(fetchImpl.mock.calls.map((call: unknown[]) => call[0])).toEqual([
      "https://fgui.test/v1/figma/selections/uploads",
      `https://fgui.test/v1/figma/selections/uploads/${uploadId}/manifest`,
      `https://fgui.test/v1/figma/selections/uploads/${uploadId}/resources/asset-1`,
      `https://fgui.test/v1/figma/selections/uploads/${uploadId}/commit`,
    ]);
    expect(fetchImpl.mock.calls.every((call: unknown[]) => new Headers((call[1] as RequestInit).headers).get("X-Figma-Plugin-Token") === "plugin-token")).toBe(true);
    expect(fetchImpl.mock.calls[2]?.[1]?.body).toBeInstanceOf(Uint8Array);
    expect(progress).toHaveBeenLastCalledWith({ completed: 1, total: 1 });
    expect(JSON.stringify(fetchImpl.mock.calls)).not.toContain("raw:");
  });

  it("never exposes server detail, plugin tokens, or opaque IDs in its error", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: "selection_too_large", message: "plugin-token raw:node" } }), { status: 400 }));
    const uploader = new SelectionUploader({ serverOrigin: "https://fgui.test", pluginToken: "plugin-token", fetchImpl });

    await expect(uploader.send(manifest, [], "idempotency-key")).rejects.toThrow("选择内容过大");
    await expect(uploader.send(manifest, [], "idempotency-key")).rejects.not.toThrow(/plugin-token|raw:node/);
  });

  it("retries only create and never repeats a failed manifest PUT", async () => {
    const uploadId = "c".repeat(32);
    const fetchImpl = vi.fn()
      .mockRejectedValueOnce(new Error("lost create response"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ version: 1, upload_id: uploadId }), { status: 201 }))
      .mockRejectedValueOnce(new Error("lost manifest response"));
    const uploader = new SelectionUploader({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    await expect(uploader.send(manifest, [{ key: "asset-1", mime_type: "image/png", bytes: new Uint8Array([1]) }], "stable-key")).rejects.toThrow();
    expect(fetchImpl.mock.calls.map((call: unknown[]) => call[0])).toEqual([
      "https://fgui.test/v1/figma/selections/uploads", "https://fgui.test/v1/figma/selections/uploads", `https://fgui.test/v1/figma/selections/uploads/${uploadId}/manifest`,
    ]);
  });

  it("forwards AbortSignal and uses only direct plugin authentication", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchImpl = vi.fn(async (_url: string, init: RequestInit) => {
      expect(init.signal).toBe(controller.signal);
      throw new DOMException("Aborted", "AbortError");
    });
    const uploader = new SelectionUploader({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    await expect(uploader.send(manifest, [], "key", undefined, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(JSON.stringify(fetchImpl.mock.calls)).not.toContain("Authorization");
  });

  it.each([
    [null],
    [{ version: 1, upload_id: "short" }],
    [{ version: 1, upload_id: "d".repeat(32) }, []],
    [{ version: 1, upload_id: "d".repeat(32) }, { version: 1, state: "manifest_received" }, { version: 1, state: "wrong" }],
    [{ version: 1, upload_id: "d".repeat(32) }, { version: 1, state: "manifest_received" }, { version: 1, state: "resources_pending" }, { version: 1, selection_id: "short" }],
    [{ version: 1, upload_id: "d".repeat(32) }, { version: 1, state: "manifest_received" }, { version: 1, state: "resources_pending" }, { version: 1, selection_id: "e".repeat(32), display_name: "Quiz", top_level_summaries: [{ name: 4, type: "FRAME" }], preview_urls: [], warnings: [] }],
  ])("maps malformed successful response sequence %# to invalid_response", async (...payloads: unknown[]) => {
    const fetchImpl = vi.fn();
    for (const payload of payloads) fetchImpl.mockResolvedValueOnce(new Response(JSON.stringify(payload), { status: 200 }));
    const uploader = new SelectionUploader({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    const call = uploader.send(manifest, [{ key: "asset-1", mime_type: "image/png", bytes: new Uint8Array([1]) }], "key");
    await expect(call).rejects.toMatchObject({ name: "SelectionUploadError", code: "invalid_response" });
    await expect(call).rejects.not.toBeInstanceOf(TypeError);
  });
});
