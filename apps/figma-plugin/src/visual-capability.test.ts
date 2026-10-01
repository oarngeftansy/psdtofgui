import { describe, expect, it } from "vitest";
import { classifyVisualNode } from "./visual-capability";

function node(overrides: Record<string, unknown> = {}) {
  return { type: "FRAME", children: [], ...overrides };
}

describe("visual capability classification", () => {
  it.each([
    ["plain text", node({ type: "TEXT", fills: [{ type: "SOLID" }] }), false, "native", null, []],
    ["mixed text runs", node({ type: "TEXT", fills: [{ type: "SOLID" }] }), false, "native", null, ["rich_text_runs"]],
    ["plain container", node(), false, "native", null, []],
    ["editable solid rectangle", node({ type: "RECTANGLE", fills: [{ type: "SOLID", color: { r: 1, g: 0, b: 0 } }], strokes: [{ type: "SOLID", color: { r: 0, g: 0, b: 0 } }], strokeWeight: 1 }), false, "native", null, []],
    ["ellipse", node({ type: "ELLIPSE", fills: [{ type: "SOLID", color: { r: 1, g: 0, b: 0 } }] }), false, "vector_asset", "image/png", []],
    ["translucent rectangle stroke", node({ type: "RECTANGLE", strokes: [{ type: "SOLID", color: { r: 0, g: 0, b: 0, a: 0.5 } }], strokeWeight: 1 }), false, "composite_png", "image/png", ["visual_style"]],
    ["nonuniform rectangle corners", node({ type: "RECTANGLE", fills: [{ type: "SOLID", color: { r: 1, g: 0, b: 0 } }], topLeftRadius: 2, topRightRadius: 4 }), false, "native", null, []],
    ["vector", node({ type: "VECTOR", fills: [{ type: "SOLID" }] }), false, "vector_asset", "image/png", []],
    ["boolean", node({ type: "BOOLEAN_OPERATION" }), false, "vector_asset", "image/png", []],
    ["star", node({ type: "STAR" }), false, "vector_asset", "image/png", []],
    ["line", node({ type: "LINE" }), false, "vector_asset", "image/png", []],
    ["polygon", node({ type: "POLYGON" }), false, "vector_asset", "image/png", []],
    ["image", node({ type: "RECTANGLE", fills: [{ type: "IMAGE" }] }), false, "image_asset", "image/png", []],
    ["instance", node({ type: "INSTANCE" }), false, "composite_png", "image/png", ["instance_composite"]],
    ["instance with readable children", node({ type: "INSTANCE", children: [node({ type: "TEXT" })] }), false, "native", null, []],
    ["frame with readable children and an effect", node({ type: "FRAME", effects: [{ type: "DROP_SHADOW" }], children: [node({ type: "TEXT" })] }), false, "native", null, []],
    ["group with readable children and a blend mode", node({ type: "GROUP", blendMode: "MULTIPLY", children: [node({ type: "TEXT" })] }), false, "native", null, []],
    ["component with readable children and a transform", node({ type: "COMPONENT", rotation: 20, children: [node({ type: "TEXT" })] }), false, "native", null, []],
    ["simple rectangle mask group", node({ type: "GROUP", children: [node({ type: "RECTANGLE", isMask: true, fills: [{ type: "SOLID" }] }), node({ type: "TEXT" })] }), false, "native", null, []],
    ["complex mask group", node({ type: "GROUP", children: [node({ type: "VECTOR", isMask: true }), node({ type: "TEXT" })] }), false, "composite_png", "image/png", ["mask_composite"]],
    ["luminance mask", node({ type: "GROUP", children: [node({ type: "RECTANGLE", isMask: true, maskType: "LUMINANCE", fills: [{ type: "SOLID" }] }), node({ type: "TEXT" })] }), false, "composite_png", "image/png", ["mask_composite"]],
    ["vector image mask", node({ type: "GROUP", children: [node({ type: "RECTANGLE", isMask: true, maskType: "VECTOR", fills: [{ type: "IMAGE" }] }), node({ type: "TEXT" })] }), false, "composite_png", "image/png", ["mask_composite"]],
    ["translucent mask", node({ type: "FRAME", children: [node({ type: "RECTANGLE", isMask: true, opacity: 0.5, fills: [{ type: "SOLID" }] }), node({ type: "TEXT" })] }), false, "composite_png", "image/png", ["mask_composite"]],
    ["stroke-only mask", node({ type: "COMPONENT", children: [node({ type: "RECTANGLE", isMask: true, fills: [], strokes: [{ type: "SOLID" }] }), node({ type: "TEXT" })] }), false, "composite_png", "image/png", ["mask_composite"]],
    ["internal clip", node({ clipsContent: true }), false, "native", null, []],
    ["root clip", node({ clipsContent: true }), true, "native", null, []],
    ["root frame with editable background", node({ fills: [{ type: "SOLID", color: { r: 0.2, g: 0.3, b: 0.4 } }], children: [node({ type: "TEXT" })] }), true, "native", null, []],
    ["nested frame with editable background", node({ fills: [{ type: "SOLID", color: { r: 0.2, g: 0.3, b: 0.4 } }], children: [node({ type: "TEXT" })] }), false, "native", null, []],
    ["gradient", node({ fills: [{ type: "GRADIENT_LINEAR" }] }), false, "composite_png", "image/png", ["gradient_paint"]],
    ["hidden gradient", node({ fills: [{ type: "GRADIENT_LINEAR", visible: false }] }), false, "native", null, []],
    ["transparent gradient", node({ fills: [{ type: "GRADIENT_LINEAR", opacity: 0 }] }), false, "native", null, []],
    ["shadow", node({ effects: [{ type: "DROP_SHADOW" }] }), false, "composite_png", "image/png", ["visual_effect"]],
    ["blur", node({ effects: [{ type: "LAYER_BLUR" }] }), false, "composite_png", "image/png", ["visual_effect"]],
    ["hidden effect", node({ effects: [{ type: "DROP_SHADOW", visible: false }] }), false, "native", null, []],
    ["blend", node({ blendMode: "MULTIPLY" }), false, "composite_png", "image/png", ["blend_mode"]],
    ["pass through", node({ blendMode: "PASS_THROUGH" }), false, "native", null, []],
    ["multiple fills", node({ fills: [{ type: "SOLID" }, { type: "SOLID" }] }), false, "composite_png", "image/png", ["multiple_paints"]],
    ["video", node({ type: "VIDEO" }), false, "skip", null, []],
  ])("classifies %s", (_name, input, isRoot, strategy, mimeType, reasons) => {
    expect(classifyVisualNode(input, { isRoot, hasComplexTextRuns: _name === "mixed text runs" })).toEqual({ strategy, mimeType, reasons });
  });

  it("deduplicates combined reasons in stable priority order", () => {
    expect(classifyVisualNode(node({
      type: "INSTANCE",
      clipsContent: true,
      fills: [{ type: "GRADIENT_LINEAR" }, { type: "SOLID" }],
      effects: [{ type: "INNER_SHADOW" }, { type: "BACKGROUND_BLUR" }],
      blendMode: "SCREEN",
    }), { isRoot: false }).reasons).toEqual([
      "instance_composite",
      "gradient_paint",
      "visual_effect",
      "blend_mode",
      "multiple_paints",
    ]);
  });
});
