import { afterEach, describe, expect, it, vi } from "vitest";
import { startPlugin } from "./code";

function selectedNode(overrides: Record<string, unknown> = {}) {
  return {
    id: "raw:node-id",
    name: "Checkout",
    type: "FRAME",
    visible: true,
    absoluteBoundingBox: { x: 0, y: 0, width: 320, height: 180 },
    absoluteTransform: [[1, 0, 0], [0, 1, 0]],
    children: [],
    ...overrides,
  } as unknown as SceneNode;
}

function png(width = 320, height = 180, size = 24): Uint8Array {
  const bytes = new Uint8Array(size);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82]);
  new DataView(bytes.buffer).setUint32(16, width);
  new DataView(bytes.buffer).setUint32(20, height);
  return bytes;
}

function runtime(selection: readonly SceneNode[], frameOverrides: Record<string, unknown> = {}, pageChildren: readonly SceneNode[] = selection) {
  const listeners = new Map<string, () => void>();
  const currentPage = { selection, children: pageChildren };
  const frameChildren: SceneNode[] = [];
  const frame = {
    name: "Temporary isolated screenshot",
    type: "FRAME",
    x: 0,
    y: 0,
    fills: [{ type: "SOLID" }],
    layoutMode: "HORIZONTAL",
    clipsContent: false,
    resize: vi.fn(),
    appendChild: vi.fn((node: SceneNode) => { frameChildren.push(node); }),
    exportAsync: vi.fn().mockResolvedValue(png()),
    remove: vi.fn(),
    ...frameOverrides,
  };
  return {
    listeners,
    currentPage,
    frame,
    frameChildren,
    createFrame: vi.fn(() => frame),
    showUI: vi.fn(),
    on: vi.fn((event: string, listener: () => void) => listeners.set(event, listener)),
    ui: {
      onmessage: undefined as ((message: unknown, props: OnMessageProperties) => void) | undefined,
      postMessage: vi.fn(),
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("Figma selection bridge", () => {
  it("starts pairing-free and reports the current selection", () => {
    vi.stubGlobal("__html__", "<html></html>");
    const figmaRuntime = runtime([selectedNode()]);

    startPlugin(figmaRuntime);

    expect(figmaRuntime.showUI).toHaveBeenCalledWith("<html></html>", { width: 640, height: 800 });
    expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-preflight",
        preflight: expect.objectContaining({ sendable: true, nodeCount: 1 }),
      }),
      { origin: "*" },
    );
    expect(figmaRuntime.on).toHaveBeenCalledWith("selectionchange", expect.any(Function));
    expect(JSON.stringify(figmaRuntime.ui.postMessage.mock.calls)).not.toMatch(/pairing|credential/i);
  });

  it("locates a server-declared review node on explicit request", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const node = selectedNode();
    const figmaRuntime = {
      ...runtime([]),
      getNodeByIdAsync: vi.fn().mockResolvedValue(node),
      viewport: { scrollAndZoomIntoView: vi.fn() },
    };
    startPlugin(figmaRuntime);

    figmaRuntime.ui.onmessage?.({ type: "locate-node", nodeId: "raw:node-id", attempt: "locate-1" }, {} as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.currentPage.selection).toEqual([node]));
    expect(figmaRuntime.viewport.scrollAndZoomIntoView).toHaveBeenCalledWith([node]);
  });

  it("creates a separate review frame to the right without mutating the source", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clone = selectedNode({ x: 0, y: 0, remove: vi.fn() });
    const source = selectedNode({ clone: vi.fn(() => clone) });
    const userFrame = selectedNode({ name: "FairyGUI 待审核", type: "FRAME", getPluginData: vi.fn().mockReturnValue(""), remove: vi.fn() });
    const generated = { name: "Generated preview", type: "RECTANGLE", x: 0, y: 0, resize: vi.fn(), fills: [], remove: vi.fn() };
    const reviewChildren: unknown[] = [];
    const reviewFrame = {
      id: "review-frame",
      name: "",
      type: "FRAME",
      x: 0,
      y: 0,
      fills: [],
      layoutMode: "NONE",
      clipsContent: false,
      resize: vi.fn(),
      appendChild: vi.fn((node: unknown) => reviewChildren.push(node)),
      exportAsync: vi.fn().mockResolvedValue(png()),
      getPluginData: vi.fn().mockReturnValue(""),
      setPluginData: vi.fn(),
      remove: vi.fn(),
    };
    const figmaRuntime = {
      ...runtime([source], {}, [source, userFrame]),
      getNodeByIdAsync: vi.fn().mockResolvedValue(source),
      createFrame: vi.fn(() => reviewFrame),
      createRectangle: vi.fn(() => generated),
      createImage: vi.fn(() => ({ hash: "image-hash" })),
      viewport: { scrollAndZoomIntoView: vi.fn() },
    };
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage?.({ type: "create-review-area", attempt: "review-1", nodeId: "raw:node-id", previewBytes: png(), previewWidth: 320, previewHeight: 180 }, {} as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "review-area-created", attempt: "review-1" },
      { origin: "*" },
    ));
    expect(source.clone).toHaveBeenCalledOnce();
    expect(source).toEqual(expect.objectContaining({ absoluteBoundingBox: { x: 0, y: 0, width: 320, height: 180 } }));
    expect(reviewFrame.name).toBe("FairyGUI 待审核");
    expect(reviewFrame.setPluginData).toHaveBeenCalledWith("figma-to-fgui.review-area", "v1");
    expect(userFrame.remove).not.toHaveBeenCalled();
    expect(reviewFrame.x).toBe(480);
    expect(reviewChildren).toEqual([clone, generated]);
    expect(generated.fills).toEqual([{ type: "IMAGE", imageHash: "image-hash", scaleMode: "FIT" }]);
    expect(figmaRuntime.viewport.scrollAndZoomIntoView).toHaveBeenCalledWith([reviewFrame]);
  });

  it("refreshes the preflight when the Figma selection changes", () => {
    vi.stubGlobal("__html__", "<html></html>");
    const figmaRuntime = runtime([selectedNode()]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.currentPage.selection = [selectedNode({ name: "Cart" })];
    figmaRuntime.listeners.get("selectionchange")!();

    expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-changed",
        preflight: expect.objectContaining({
          sendable: true,
          manifest: expect.objectContaining({ display_name: "Cart" }),
        }),
      }),
      { origin: "*" },
    );
  });

  it("returns a stable empty-selection error when export is requested", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const figmaRuntime = runtime([]);
    startPlugin(figmaRuntime);

    expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-preflight",
        preflight: expect.objectContaining({
          sendable: false,
          warnings: [{ code: "selection_empty", message: "请选择要导出的图层" }],
        }),
      }),
      { origin: "*" },
    );

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "empty-attempt" }, { origin: "null" } as OnMessageProperties);
    await Promise.resolve();

    expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "empty-attempt", code: "selection_empty" },
      { origin: "*" },
    );
  });

  it("exports the prepared snapshot when the live selection changes mid-export", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    let finishExport!: (bytes: Uint8Array) => void;
    const exporting = new Promise<Uint8Array>((resolve) => { finishExport = resolve; });
    const figmaRuntime = runtime([
      selectedNode({ name: "Original", type: "VECTOR", exportAsync: () => exporting }),
    ]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "snapshot-attempt", mode: "writer" }, { origin: "null" } as OnMessageProperties);
    await Promise.resolve();
    figmaRuntime.currentPage.selection = [selectedNode({ name: "Replacement" })];
    figmaRuntime.listeners.get("selectionchange")!();
    finishExport(new Uint8Array([1, 2, 3]));
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "selection-export", attempt: "snapshot-attempt" }),
      { origin: "*" },
    ));

    expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-export",
        attempt: "snapshot-attempt",
        manifest: expect.objectContaining({ display_name: "Original" }),
        resources: [{ key: "asset-1", mime_type: "image/png", bytes: new Uint8Array([1, 2, 3]) }],
      }),
      { origin: "*" },
    );
  });

  it("returns a deterministic safe code when a resource export fails", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const figmaRuntime = runtime([
      selectedNode({
        name: "Broken",
        type: "VECTOR",
        exportAsync: () => Promise.reject(new Error("C:\\private\\credential")),
      }),
    ]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "failed-attempt" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "failed-attempt", code: "resource_direct_export_failed" },
      { origin: "*" },
    ));

    expect(JSON.stringify(figmaRuntime.ui.postMessage.mock.calls)).not.toMatch(/private|credential/i);
  });

  it("keeps the uploaded manifest MIME aligned with direct vector PNG export", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const exportAsync = vi.fn(async ({ format }: { format: string }) => {
      return new Uint8Array([7, 8]);
    });
    const figmaRuntime = runtime([
      selectedNode({ name: "Union", type: "BOOLEAN_OPERATION", fills: [], exportAsync }),
    ]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "fallback-attempt" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-export",
        manifest: expect.objectContaining({ resources: [expect.objectContaining({ mime_type: "image/png" })] }),
        resources: [{ key: "asset-1", mime_type: "image/png", bytes: new Uint8Array([7, 8]) }],
      }),
      { origin: "*" },
    ));
    expect(exportAsync.mock.calls.map(([settings]) => settings.format)).toEqual(["PNG"]);
  });

  it("does not clone or export a hidden visual selection", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clone = selectedNode({ visible: false, x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() });
    const hidden = selectedNode({
      name: "Hidden vector",
      type: "VECTOR",
      visible: false,
      absoluteBoundingBox: { x: 30, y: 40, width: 50, height: 60 },
      absoluteTransform: [[1, 0, 30], [0, 1, 40]],
      clone: vi.fn(() => clone),
    });
    const figmaRuntime = runtime([hidden], { exportAsync: vi.fn().mockResolvedValue(png(50, 60)) });
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "hidden-resource" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "hidden-resource", code: "selection_empty" },
      { origin: "*" },
    ));
    expect(hidden.clone).not.toHaveBeenCalled();
    expect(clone.visible).toBe(false);
    expect(figmaRuntime.frame.remove).not.toHaveBeenCalled();
  });

  it("exports a partially rendered PNG on a full layout-sized transparent canvas", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clone = selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() });
    const partial = selectedNode({
      name: "Partially visible card art",
      type: "RECTANGLE",
      fills: [{ type: "IMAGE", imageHash: "card" }],
      absoluteBoundingBox: { x: 100, y: 200, width: 300, height: 400 },
      absoluteRenderBounds: { x: 250, y: 200, width: 150, height: 400 },
      absoluteTransform: [[1, 0, 100], [0, 1, 200]],
      clone: vi.fn(() => clone),
    });
    const figmaRuntime = runtime([partial], { exportAsync: vi.fn().mockResolvedValue(png(300, 400)) });
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "full-layout-resource" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-export",
        attempt: "full-layout-resource",
        manifest: expect.objectContaining({ top_level_nodes: [expect.objectContaining({ bounds: { x: 100, y: 200, width: 300, height: 400 } })] }),
        resources: [{ key: "asset-1", mime_type: "image/png", bytes: png(300, 400) }],
      }),
      { origin: "*" },
    ));
    expect(partial.clone).toHaveBeenCalledOnce();
    expect(figmaRuntime.frame.resize).toHaveBeenCalledWith(300, 400);
    expect(clone.relativeTransform).toEqual([[1, 0, 0], [0, 1, 0]]);
  });

  it("bakes finite scale and skew transforms into isolated resource PNGs", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clone = selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() });
    const transformed = selectedNode({
      name: "Scaled resource",
      type: "RECTANGLE",
      fills: [{ type: "IMAGE", imageHash: "scaled" }],
      absoluteBoundingBox: { x: 20, y: 30, width: 200, height: 100 },
      absoluteRenderBounds: { x: 25, y: 35, width: 180, height: 80 },
      absoluteTransform: [[1.5, 0.25, 20], [0.1, 0.75, 30]],
      clone: vi.fn(() => clone),
    });
    const figmaRuntime = runtime([transformed], { exportAsync: vi.fn().mockResolvedValue(png(200, 100)) });
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "scaled-resource" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "selection-export", attempt: "scaled-resource" }),
      { origin: "*" },
    ));
    expect(clone.relativeTransform).toEqual([[1.5, 0.25, 0], [0.1, 0.75, 0]]);
    expect(figmaRuntime.frame.resize).toHaveBeenCalledWith(200, 100);
  });

  it("uniformly downsamples an oversized resource canvas without changing layout bounds", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clone = selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() });
    const large = selectedNode({
      name: "Large art",
      type: "RECTANGLE",
      fills: [{ type: "IMAGE", imageHash: "large" }],
      absoluteBoundingBox: { x: 0, y: 0, width: 6000, height: 3000 },
      absoluteRenderBounds: { x: 3000, y: 0, width: 3000, height: 1000 },
      absoluteTransform: [[1, 0, 0], [0, 1, 0]],
      clone: vi.fn(() => clone),
    });
    const figmaRuntime = runtime([large], { exportAsync: vi.fn().mockResolvedValue(png(4096, 2048)) });
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "large-resource" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-export",
        attempt: "large-resource",
        manifest: expect.objectContaining({ top_level_nodes: [expect.objectContaining({ bounds: { x: 0, y: 0, width: 6000, height: 3000 } })] }),
      }),
      { origin: "*" },
    ));
    expect(figmaRuntime.frame.resize).toHaveBeenCalledWith(6000, 3000);
    expect(figmaRuntime.frame.exportAsync).toHaveBeenCalledWith({ format: "PNG", constraint: { type: "SCALE", value: 4096 / 6000 } });
  });

  it("uses the direct PNG dimensions when Figma exposes no usable node bounds", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const exportAsync = vi.fn().mockResolvedValue(png(640, 360));
    const unbounded = selectedNode({
      name: "Exportable unbounded art",
      type: "BOOLEAN_OPERATION",
      fills: [],
      width: undefined,
      height: undefined,
      absoluteBoundingBox: null,
      absoluteRenderBounds: null,
      absoluteTransform: [[1, 0, 25], [0, 1, 35]],
      exportAsync,
    });
    const figmaRuntime = runtime([unbounded]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "unbounded-resource" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-export",
        attempt: "unbounded-resource",
        manifest: expect.objectContaining({ top_level_nodes: [expect.objectContaining({ bounds: { x: 25, y: 35, width: 640, height: 360 } })] }),
      }),
      { origin: "*" },
    ));
    expect(exportAsync).toHaveBeenCalledWith({ format: "PNG" });
    expect(figmaRuntime.createFrame).not.toHaveBeenCalled();
  });

  it("keeps a subtree crossing frame bounds structural and untrimmed", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clone = selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() });
    const crossing = selectedNode({
      name: "Crossing card",
      type: "GROUP",
      absoluteBoundingBox: { x: 260, y: 20, width: 100, height: 80 },
      absoluteTransform: [[1, 0, 260], [0, 1, 20]],
      clone: vi.fn(() => clone),
    });
    const viewport = selectedNode({
      name: "Viewport",
      clipsContent: true,
      absoluteBoundingBox: { x: 0, y: 0, width: 320, height: 180 },
      children: [crossing],
    });
    const figmaRuntime = runtime([viewport], { exportAsync: vi.fn().mockResolvedValue(png(60, 80)) });
    startPlugin(figmaRuntime);
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "clip-fragment" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "selection-export",
        attempt: "clip-fragment",
        manifest: expect.objectContaining({
          top_level_nodes: [expect.objectContaining({ children: [expect.objectContaining({ bounds: { x: 260, y: 20, width: 100, height: 80 }, resource_keys: [] })] })],
        }),
        resources: [],
      }),
      { origin: "*" },
    ));
    expect(figmaRuntime.createFrame).not.toHaveBeenCalled();
    expect(crossing.clone).not.toHaveBeenCalled();
  });

  it("exports only the attempt-bound selection as a bounded PNG", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const exportAsync = vi.fn().mockResolvedValue(png());
    const node = selectedNode({ exportAsync });
    const figmaRuntime = runtime([node]);
    startPlugin(figmaRuntime);

    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "a1" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "selection-export", attempt: "a1" }),
      { origin: "*" },
    ));
    exportAsync.mockClear();
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "a1" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "semantic-screenshot-export", attempt: "a1", mimeType: "image/png", bytes: png() },
      { origin: "*" },
    ));
    expect(exportAsync).toHaveBeenCalledOnce();
    expect(exportAsync).toHaveBeenCalledWith({ format: "PNG", constraint: { type: "SCALE", value: 1 } });
    expect(JSON.stringify(figmaRuntime.ui.postMessage.mock.calls)).not.toMatch(/children|credential|raw:node/i);
  });

  it("exports only cloned selected roots in an isolated union-bounds frame", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const firstTransform = [[0.8660254, -0.5, 12], [0.5, 0.8660254, 34]];
    const secondTransform = [[0.9659258, 0.258819, 160], [-0.258819, 0.9659258, 5]];
    const firstClone = selectedNode({ name: "First clone", x: 700, y: 800, rotation: 30, relativeTransform: [[1, 0, 700], [0, 1, 800]], remove: vi.fn() });
    const secondClone = selectedNode({ name: "Second clone", x: 900, y: 1000, rotation: -15, relativeTransform: [[1, 0, 900], [0, 1, 1000]], remove: vi.fn() });
    const first = selectedNode({ name: "First", x: 12, y: 34, rotation: 30, absoluteTransform: firstTransform, absoluteRenderBounds: { x: -10, y: 20, width: 100, height: 80 }, exportAsync: vi.fn().mockResolvedValue(png()), clone: vi.fn(() => firstClone) });
    const second = selectedNode({ name: "Second", x: 56, y: 78, rotation: -15, absoluteTransform: secondTransform, absoluteRenderBounds: { x: 150, y: -5, width: 50, height: 25 }, exportAsync: vi.fn().mockResolvedValue(png()), clone: vi.fn(() => secondClone) });
    const secret = selectedNode({ name: "SECRET-not-selected", clone: vi.fn(() => { throw new Error("secret cloned"); }) });
    const originalSelection = [first, second] as const;
    const figmaRuntime = runtime(originalSelection, {}, [first, secret, second]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "multi" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "selection-export", attempt: "multi" }),
      { origin: "*" },
    ));
    const reorderedSelection = [second, first] as const;
    figmaRuntime.currentPage.selection = reorderedSelection;
    figmaRuntime.ui.postMessage.mockClear();
    figmaRuntime.createFrame.mockClear();
    figmaRuntime.frame.resize.mockClear();
    figmaRuntime.frame.appendChild.mockClear();
    figmaRuntime.frame.exportAsync.mockClear();
    figmaRuntime.frame.remove.mockClear();
    figmaRuntime.frameChildren.length = 0;
    vi.mocked(first.clone).mockClear();
    vi.mocked(second.clone).mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "multi" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "semantic-screenshot-export", attempt: "multi", mimeType: "image/png", bytes: png() },
      { origin: "*" },
    ));
    expect(figmaRuntime.createFrame).toHaveBeenCalledOnce();
    expect(figmaRuntime.frame).toMatchObject({ x: -10, y: -5, fills: [], layoutMode: "NONE", clipsContent: true });
    expect(figmaRuntime.frame.resize).toHaveBeenCalledWith(210, 105);
    expect(figmaRuntime.frame.appendChild.mock.calls.map(([node]) => node)).toEqual([firstClone, secondClone]);
    expect(figmaRuntime.frameChildren).toEqual([firstClone, secondClone]);
    expect(secret.clone).not.toHaveBeenCalled();
    expect(first.clone).toHaveBeenCalledOnce();
    expect(second.clone).toHaveBeenCalledOnce();
    expect(firstClone).toMatchObject({ rotation: 30, relativeTransform: [[0.8660254, -0.5, 22], [0.5, 0.8660254, 39]] });
    expect(secondClone).toMatchObject({ rotation: -15, relativeTransform: [[0.9659258, 0.258819, 170], [-0.258819, 0.9659258, 10]] });
    expect(figmaRuntime.frame.exportAsync).toHaveBeenCalledWith({ format: "PNG", constraint: { type: "SCALE", value: 1 } });
    expect(firstClone.remove).not.toHaveBeenCalled();
    expect(secondClone.remove).not.toHaveBeenCalled();
    expect(figmaRuntime.frame.remove).toHaveBeenCalledOnce();
    expect(figmaRuntime.currentPage.selection).toBe(reorderedSelection);
    expect(first).toMatchObject({ x: 12, y: 34, rotation: 30 });
    expect(second).toMatchObject({ x: 56, y: 78, rotation: -15 });
  });

  it("rejects overlapping roots before temporary content regardless of selection order", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const behind = selectedNode({ name: "Behind", absoluteRenderBounds: { x: 0, y: 0, width: 120, height: 100 }, clone: vi.fn() });
    const front = selectedNode({ name: "Front", absoluteRenderBounds: { x: 40, y: 30, width: 100, height: 80 }, clone: vi.fn() });
    const figmaRuntime = runtime([front, behind]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "overlap-reversed" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "overlap-reversed" }), { origin: "*" }));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "overlap-reversed" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "overlap-reversed", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(figmaRuntime.createFrame).not.toHaveBeenCalled();
    expect(front.clone).not.toHaveBeenCalled();
    expect(behind.clone).not.toHaveBeenCalled();
    expect(figmaRuntime.ui.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "semantic-screenshot-export" }), expect.anything());
  });

  it("allows roots that only touch at an edge", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clones = [
      selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() }),
      selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() }),
    ];
    const left = selectedNode({ absoluteRenderBounds: { x: 0, y: 0, width: 100, height: 100 }, clone: vi.fn(() => clones[0]!) });
    const right = selectedNode({ absoluteTransform: [[1, 0, 100], [0, 1, 20]], absoluteRenderBounds: { x: 100, y: 20, width: 80, height: 60 }, clone: vi.fn(() => clones[1]!) });
    const figmaRuntime = runtime([right, left]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "edge-touch" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "edge-touch" }), { origin: "*" }));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "edge-touch" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "semantic-screenshot-export", attempt: "edge-touch", mimeType: "image/png", bytes: png() },
      { origin: "*" },
    ));
    expect(figmaRuntime.createFrame).toHaveBeenCalledOnce();
    expect(figmaRuntime.frame.appendChild).toHaveBeenCalledTimes(2);
  });

  it("cleans the isolated frame and every created clone when clone or export fails", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const firstClone = selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() });
    const first = selectedNode({ clone: vi.fn(() => firstClone) });
    const cloneFailure = selectedNode({ absoluteBoundingBox: { x: 400, y: 0, width: 320, height: 180 }, absoluteTransform: [[1, 0, 400], [0, 1, 0]], clone: vi.fn(() => { throw new Error("clone failed"); }) });
    const cloneRuntime = runtime([first, cloneFailure]);
    startPlugin(cloneRuntime);
    cloneRuntime.ui.onmessage!({ type: "selection-export", attempt: "clone-failure" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(cloneRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "clone-failure" }), { origin: "*" }));
    cloneRuntime.ui.postMessage.mockClear();

    cloneRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "clone-failure" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(cloneRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "clone-failure", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(firstClone.remove).not.toHaveBeenCalled();
    expect(cloneRuntime.frame.remove).toHaveBeenCalledOnce();
    expect(cloneRuntime.frame.exportAsync).not.toHaveBeenCalled();

    const exportClones = [
      selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() }),
      selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() }),
    ];
    const roots = exportClones.map((clone, index) => selectedNode({ name: `Root ${index}`, absoluteBoundingBox: { x: index * 400, y: 0, width: 320, height: 180 }, absoluteTransform: [[1, 0, index * 400], [0, 1, 0]], clone: vi.fn(() => clone) }));
    const exportRuntime = runtime(roots, { exportAsync: vi.fn().mockRejectedValue(new Error("export failed")) });
    startPlugin(exportRuntime);
    exportRuntime.ui.onmessage!({ type: "selection-export", attempt: "export-failure" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(exportRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "export-failure" }), { origin: "*" }));
    exportRuntime.ui.postMessage.mockClear();

    exportRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "export-failure" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(exportRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "export-failure", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(exportClones.every((clone) => vi.mocked(clone.remove).mock.calls.length === 0)).toBe(true);
    expect(exportRuntime.frame.remove).toHaveBeenCalledOnce();
  });

  it("rejects multi-root export when an absolute transform cannot be isolated", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const supported = selectedNode({ clone: vi.fn() });
    const unsupported = selectedNode({ absoluteBoundingBox: { x: 400, y: 0, width: 320, height: 180 }, absoluteTransform: undefined, clone: vi.fn() });
    const figmaRuntime = runtime([supported, unsupported]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "unsupported-transform" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "unsupported-transform" }), { origin: "*" }));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "unsupported-transform" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "unsupported-transform", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(figmaRuntime.createFrame).not.toHaveBeenCalled();
    expect(supported.clone).not.toHaveBeenCalled();
    expect(unsupported.clone).not.toHaveBeenCalled();
  });

  it("removes a created clone when its positioning interface is unsupported", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const invalidClone = selectedNode({ x: 0, y: 0, relativeTransform: undefined, remove: vi.fn() });
    const roots = [
      selectedNode({ clone: vi.fn(() => invalidClone) }),
      selectedNode({ absoluteBoundingBox: { x: 400, y: 0, width: 320, height: 180 }, absoluteTransform: [[1, 0, 400], [0, 1, 0]], clone: vi.fn() }),
    ];
    const figmaRuntime = runtime(roots);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "unsupported-clone" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "unsupported-clone" }), { origin: "*" }));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "unsupported-clone" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "unsupported-clone", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(invalidClone.remove).toHaveBeenCalledOnce();
    expect(figmaRuntime.frame.remove).toHaveBeenCalledOnce();
    expect(figmaRuntime.frame.exportAsync).not.toHaveBeenCalled();
  });

  it.each([
    ["nested scale", [[2, 0, 10], [0, 0.5, 20]]],
    ["skew", [[1, 0.25, 10], [0, 1, 20]]],
  ])("rejects %s transforms before creating temporary content", async (_label, absoluteTransform) => {
    vi.stubGlobal("__html__", "<html></html>");
    const transformed = selectedNode({ absoluteTransform, clone: vi.fn() });
    const sibling = selectedNode({ absoluteBoundingBox: { x: 400, y: 0, width: 320, height: 180 }, absoluteTransform: [[1, 0, 400], [0, 1, 0]], clone: vi.fn() });
    const figmaRuntime = runtime([transformed, sibling]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "non-rigid" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "non-rigid" }), { origin: "*" }));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "non-rigid" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "non-rigid", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(figmaRuntime.createFrame).not.toHaveBeenCalled();
    expect(transformed.clone).not.toHaveBeenCalled();
    expect(sibling.clone).not.toHaveBeenCalled();
    expect(figmaRuntime.ui.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "semantic-screenshot-export" }), expect.anything());
  });

  it("withholds successful bytes when isolated-frame cleanup fails", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const clones = [
      selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() }),
      selectedNode({ x: 0, y: 0, relativeTransform: [[1, 0, 0], [0, 1, 0]], remove: vi.fn() }),
    ];
    const roots = clones.map((clone, index) => selectedNode({ absoluteBoundingBox: { x: index * 400, y: 0, width: 320, height: 180 }, absoluteTransform: [[1, 0, index * 400], [0, 1, 0]], clone: vi.fn(() => clone) }));
    const figmaRuntime = runtime(roots, { remove: vi.fn(() => { throw new Error("frame remains"); }) });
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "cleanup-failure" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "cleanup-failure" }), { origin: "*" }));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "cleanup-failure" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "cleanup-failure", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(figmaRuntime.frame.exportAsync).toHaveBeenCalledOnce();
    expect(figmaRuntime.frame.remove).toHaveBeenCalledOnce();
    expect(clones.every((clone) => vi.mocked(clone.remove).mock.calls.length === 1)).toBe(true);
    expect(figmaRuntime.ui.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "semantic-screenshot-export" }), expect.anything());
  });

  it("reports a safe error when an independent clone cleanup throws", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const invalidClone = selectedNode({ x: 0, y: 0, relativeTransform: undefined, remove: vi.fn(() => { throw new Error("clone remains"); }) });
    const roots = [
      selectedNode({ clone: vi.fn(() => invalidClone) }),
      selectedNode({ absoluteBoundingBox: { x: 400, y: 0, width: 320, height: 180 }, absoluteTransform: [[1, 0, 400], [0, 1, 0]], clone: vi.fn() }),
    ];
    const figmaRuntime = runtime(roots);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "clone-cleanup-failure" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "clone-cleanup-failure" }), { origin: "*" }));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "clone-cleanup-failure" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "clone-cleanup-failure", code: "selection_export_failed" },
      { origin: "*" },
    ));
    expect(invalidClone.remove).toHaveBeenCalledOnce();
    expect(figmaRuntime.ui.postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: "semantic-screenshot-export" }), expect.anything());
  });

  it("refuses a screenshot when the attempt snapshot is empty or the live selection changed", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const originalExport = vi.fn().mockResolvedValue(new Uint8Array([1]));
    const original = selectedNode({ exportAsync: originalExport });
    const figmaRuntime = runtime([original]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "a1" }, { origin: "null" } as OnMessageProperties);
    await Promise.resolve();
    figmaRuntime.currentPage.selection = [selectedNode({ name: "Other", exportAsync: vi.fn() })];
    figmaRuntime.listeners.get("selectionchange")!();
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "a1" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "a1", code: "selection_changed" },
      { origin: "*" },
    ));
    expect(originalExport).not.toHaveBeenCalledWith(expect.objectContaining({ format: "PNG", constraint: expect.anything() }));

    const emptyRuntime = runtime([]);
    startPlugin(emptyRuntime);
    emptyRuntime.ui.postMessage.mockClear();
    emptyRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "missing" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(emptyRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "missing", code: "selection_empty" },
      { origin: "*" },
    ));
  });

  it("treats duplicate live members as a changed selection", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const first = selectedNode({ name: "First", clone: vi.fn() });
    const second = selectedNode({ name: "Second", absoluteBoundingBox: { x: 400, y: 0, width: 320, height: 180 }, absoluteTransform: [[1, 0, 400], [0, 1, 0]], clone: vi.fn() });
    const figmaRuntime = runtime([first, second]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "duplicate-live" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "selection-export", attempt: "duplicate-live" }), { origin: "*" }));
    figmaRuntime.currentPage.selection = [first, first];
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "duplicate-live" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "duplicate-live", code: "selection_changed" },
      { origin: "*" },
    ));
    expect(figmaRuntime.createFrame).not.toHaveBeenCalled();
    expect(first.clone).not.toHaveBeenCalled();
    expect(second.clone).not.toHaveBeenCalled();
  });

  it("rejects screenshot dimensions and output bytes beyond the semantic limits", async () => {
    vi.stubGlobal("__html__", "<html></html>");
    const oversizedDimensions = runtime([selectedNode({
      absoluteBoundingBox: { x: 0, y: 0, width: 4097, height: 1 },
      exportAsync: vi.fn(),
    })]);
    startPlugin(oversizedDimensions);
    oversizedDimensions.ui.onmessage!({ type: "selection-export", attempt: "dimensions" }, { origin: "null" } as OnMessageProperties);
    await Promise.resolve();
    oversizedDimensions.ui.postMessage.mockClear();
    oversizedDimensions.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "dimensions" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(oversizedDimensions.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "dimensions", code: "selection_too_large" },
      { origin: "*" },
    ));

    const oversized = png(320, 180, 1_024_001);
    const oversizedBytes = runtime([selectedNode({ exportAsync: vi.fn().mockResolvedValue(oversized) })]);
    startPlugin(oversizedBytes);
    oversizedBytes.ui.onmessage!({ type: "selection-export", attempt: "bytes" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(oversizedBytes.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "selection-export", attempt: "bytes" }),
      { origin: "*" },
    ));
    oversizedBytes.ui.postMessage.mockClear();
    oversizedBytes.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "bytes" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(oversizedBytes.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "bytes", code: "selection_too_large" },
      { origin: "*" },
    ));
  });

  it.each([
    ["truncated signature", new Uint8Array([137, 80, 78, 71]), "selection_export_failed"],
    ["malformed IHDR length", (() => { const bytes = png(); bytes[11] = 12; return bytes; })(), "selection_export_failed"],
    ["zero IHDR dimension", png(0, 100), "selection_export_failed"],
    ["oversized IHDR dimension", png(4097, 1), "selection_too_large"],
    ["oversized IHDR pixel area", png(4096, 4096), "selection_too_large"],
  ])("rejects %s before posting screenshot bytes", async (_label, bytes, code) => {
    vi.stubGlobal("__html__", "<html></html>");
    const figmaRuntime = runtime([selectedNode({ exportAsync: vi.fn().mockResolvedValue(bytes) })]);
    startPlugin(figmaRuntime);
    figmaRuntime.ui.onmessage!({ type: "selection-export", attempt: "invalid-png" }, { origin: "null" } as OnMessageProperties);
    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "selection-export", attempt: "invalid-png" }),
      { origin: "*" },
    ));
    figmaRuntime.ui.postMessage.mockClear();

    figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "invalid-png" }, { origin: "null" } as OnMessageProperties);

    await vi.waitFor(() => expect(figmaRuntime.ui.postMessage).toHaveBeenCalledWith(
      { type: "selection-error", attempt: "invalid-png", code },
      { origin: "*" },
    ));
    expect(figmaRuntime.ui.postMessage).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "semantic-screenshot-export" }),
      expect.anything(),
    );
  });
});
