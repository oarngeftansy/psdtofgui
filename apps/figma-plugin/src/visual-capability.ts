export type ExportStrategy = "native" | "vector_asset" | "image_asset" | "composite_png" | "skip";
export type RasterReason =
  | "instance_composite"
  | "mask_composite"
  | "gradient_paint"
  | "visual_effect"
  | "blend_mode"
  | "multiple_paints"
  | "visual_style"
  | "unrepresentable_transform"
  | "rich_text_runs";

export type VisualCapability = {
  strategy: ExportStrategy;
  mimeType: "image/png" | "image/svg+xml" | null;
  reasons: RasterReason[];
};

export type VisualNode = {
  type: string;
  clipsContent?: boolean;
  blendMode?: unknown;
  fills?: unknown;
  strokes?: unknown;
  effects?: unknown;
  children?: readonly VisualNode[];
  isMask?: boolean;
  maskType?: unknown;
  opacity?: unknown;
  cornerRadius?: unknown;
  topLeftRadius?: unknown;
  topRightRadius?: unknown;
  bottomLeftRadius?: unknown;
  strokeWeight?: unknown;
  bottomRightRadius?: unknown;
  rotation?: unknown;
  relativeTransform?: unknown;
};

export type NativeMaskDescriptor = {
  kind: "rectangle" | "roundedRectangle" | "image";
  maskIndex: number;
  contentIndexes: number[];
  cornerRadii?: [number, number, number, number];
};

// Ellipses include Figma arcs, rings, and partial sweeps whose path geometry is
// not available in FairyGUI's native ellipse graph. Export the entire family
// through Figma so angle, inner radius, stroke shape, and bounds stay exact.
const VECTOR_TYPES = new Set(["VECTOR", "BOOLEAN_OPERATION", "STAR", "LINE", "POLYGON", "ELLIPSE"]);
const VISUAL_EFFECT_TYPES = new Set(["DROP_SHADOW", "INNER_SHADOW", "LAYER_BLUR", "BACKGROUND_BLUR"]);

type VisualRecord = { type?: unknown; visible?: unknown };

function validColor(paint: VisualRecord, allowAlpha: boolean): boolean {
  const record = paint as Record<string, unknown>;
  const color = record.color;
  if (!color || typeof color !== "object") return false;
  const channels = color as Record<string, unknown>;
  if (!["r", "g", "b"].every((key) => typeof channels[key] === "number" && Number.isFinite(channels[key]))) return false;
  const colorAlpha = channels.a === undefined ? 1 : channels.a;
  const paintOpacity = record.opacity === undefined ? 1 : record.opacity;
  if (typeof colorAlpha !== "number" || !Number.isFinite(colorAlpha) || typeof paintOpacity !== "number" || !Number.isFinite(paintOpacity)) return false;
  return allowAlpha || (colorAlpha === 1 && paintOpacity === 1);
}

function visibleRecords(value: unknown): VisualRecord[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is VisualRecord => {
      if (!entry || typeof entry !== "object" || (entry as VisualRecord).visible === false) return false;
      const record = entry as Record<string, unknown>;
      if (record.opacity === 0) return false;
      const color = record.color;
      return !(color && typeof color === "object" && (color as Record<string, unknown>).a === 0);
    })
    : [];
}

function positiveRadius(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function radius(value: unknown, fallback: number): number | null {
  if (value === undefined) return fallback;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/**
 * Return a project-neutral native mask recipe only for the deliberately small
 * subset whose Figma sibling semantics can be represented without flattening.
 */
export function nativeMaskDescriptor(node: VisualNode): NativeMaskDescriptor | null {
  if (!["GROUP", "FRAME", "COMPONENT"].includes(node.type)) return null;
  const children = node.children ?? [];
  const maskIndexes = children.flatMap((child, index) => child.isMask === true ? [index] : []);
  if (maskIndexes.length !== 1 || maskIndexes[0] !== 0 || children.length < 2) return null;
  const mask = children[0];
  if (mask.type !== "RECTANGLE" || (mask.children?.length ?? 0) > 0) return null;
  if (mask.maskType === "LUMINANCE" || (typeof mask.opacity === "number" && mask.opacity !== 1)) return null;
  if (visibleRecords(mask.effects).length || visibleRecords(mask.strokes).length) return null;
  if (typeof mask.blendMode === "string" && mask.blendMode !== "NORMAL" && mask.blendMode !== "PASS_THROUGH") return null;
  const fills = visibleRecords(mask.fills);
  if (fills.length !== 1) return null;
  const fillType = fills[0]?.type;
  if (typeof fillType === "string" && fillType.startsWith("GRADIENT_")) return null;
  if (fillType === "IMAGE") {
    if (mask.maskType === "VECTOR") return null;
    return { kind: "image", maskIndex: 0, contentIndexes: children.slice(1).map((_child, index) => index + 1) };
  }
  if (fillType !== "SOLID") return null;
  const fill = fills[0] as Record<string, unknown>;
  const color = fill.color;
  if ((typeof fill.opacity === "number" && fill.opacity !== 1)
    || (color && typeof color === "object" && typeof (color as Record<string, unknown>).a === "number" && (color as Record<string, unknown>).a !== 1)) return null;
  const general = typeof mask.cornerRadius === "number" && Number.isFinite(mask.cornerRadius) && mask.cornerRadius >= 0 ? mask.cornerRadius : 0;
  const cornerRadii = [
    radius(mask.topLeftRadius, general),
    radius(mask.topRightRadius, general),
    radius(mask.bottomRightRadius, general),
    radius(mask.bottomLeftRadius, general),
  ];
  if (cornerRadii.some((value) => value === null)) return null;
  const resolvedRadii = cornerRadii as [number, number, number, number];
  const rounded = resolvedRadii.some(positiveRadius);
  return {
    kind: rounded ? "roundedRectangle" : "rectangle",
    maskIndex: 0,
    contentIndexes: children.slice(1).map((_child, index) => index + 1),
    ...(rounded ? { cornerRadii: resolvedRadii } : {}),
  };
}

function hasUnrepresentableTransform(node: VisualNode): boolean {
  if (typeof node.rotation === "number" && Number.isFinite(node.rotation) && node.rotation !== 0) return true;
  const value = node.relativeTransform;
  if (!Array.isArray(value) || value.length !== 2) return false;
  const first = value[0];
  const second = value[1];
  if (!Array.isArray(first) || first.length !== 3 || !Array.isArray(second) || second.length !== 3) return true;
  const [a, c] = first;
  const [b, d] = second;
  return ![a, b, c, d].every((item) => typeof item === "number" && Number.isFinite(item))
    || Math.abs(Number(a) - 1) > 1e-6 || Math.abs(Number(b)) > 1e-6
    || Math.abs(Number(c)) > 1e-6 || Math.abs(Number(d) - 1) > 1e-6;
}

function isEditableGraph(node: VisualNode, fills: VisualRecord[], strokes: VisualRecord[], _isRoot: boolean): boolean {
  const leafShape = ["RECTANGLE", "ELLIPSE"].includes(node.type) && (node.children?.length ?? 0) === 0;
  const rootContainerShape = ["FRAME", "COMPONENT"].includes(node.type) && (fills.length > 0 || strokes.length > 0);
  if (!leafShape && !rootContainerShape) return false;
  if (fills.length > 1 || strokes.length > 1 || [...fills, ...strokes].some((paint) => paint.type !== "SOLID")) return false;
  if (fills.some((paint) => !validColor(paint, true)) || strokes.some((paint) => !validColor(paint, false))) return false;
  if (strokes.length > 0 && (typeof node.strokeWeight !== "number" || !Number.isFinite(node.strokeWeight) || node.strokeWeight < 0)) return false;
  if (node.type === "ELLIPSE") return true;
  const general = typeof node.cornerRadius === "number" && Number.isFinite(node.cornerRadius) && node.cornerRadius >= 0 ? node.cornerRadius : 0;
  const corners = [node.topLeftRadius, node.topRightRadius, node.bottomRightRadius, node.bottomLeftRadius].map((value) => value === undefined ? general : value);
  return corners.every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0);
}

export function classifyVisualNode(node: VisualNode, context: { isRoot: boolean; hasComplexTextRuns?: boolean; hasStyleReferences?: boolean }): VisualCapability {
  if (node.type === "VIDEO") return { strategy: "skip", mimeType: null, reasons: [] };
  // Figma renders every arbitrary vector-family node into its transparent,
  // axis-aligned PNG. Keeping this decision ahead of transform/effect analysis
  // prevents a rotated vector from entering the generic composite path and
  // receiving its source rotation a second time in FairyGUI.
  if (VECTOR_TYPES.has(node.type)) return { strategy: "vector_asset", mimeType: "image/png", reasons: [] };

  // An INSTANCE with a readable child tree is structural source material, not
  // one atomic visual. Instance-level fills/effects/style references and its
  // transform are commonly inherited summaries of the main component. Using
  // them to flatten the instance destroys every editable descendant and also
  // creates one duplicate PNG per occurrence. Preserve the instance boundary;
  // unsupported visuals are decided on the smallest actual child that owns
  // them. Opaque instances (no children) still use instance_composite below.
  if (node.type === "INSTANCE" && (node.children?.length ?? 0) > 0) {
    return { strategy: "native", mimeType: null, reasons: [] };
  }

  // A readable Figma container is a hierarchy boundary, not an atomic visual
  // leaf. Container-level effects, blend summaries, style references and
  // transforms must not erase its descendants. Preserve the tree and let each
  // smallest visual child choose its own PNG/native representation. Direct
  // masks stay on the dedicated mask path below because those children form
  // one visual operation rather than an ordinary stack.
  if (["FRAME", "GROUP", "COMPONENT", "SECTION"].includes(node.type)
    && (node.children?.length ?? 0) > 0
    && !(node.children ?? []).some((child) => child.isMask === true)) {
    if (visibleRecords(node.fills).some((paint) => paint.type === "IMAGE")) {
      return { strategy: "image_asset", mimeType: "image/png", reasons: [] };
    }
    return { strategy: "native", mimeType: null, reasons: [] };
  }

  const fills = visibleRecords(node.fills);
  const strokes = visibleRecords(node.strokes);
  const effects = visibleRecords(node.effects);
  const reasons: RasterReason[] = [];

  if (node.type === "INSTANCE" && (node.children?.length ?? 0) === 0) reasons.push("instance_composite");
  // Rectangular Frame/Component clipping is preserved as structure at every
  // depth. The Writer lifts nested clips into generated internal components
  // whose roots carry FairyGUI's verified overflow="hidden" encoding.
  if ((node.children ?? []).some((child) => child.isMask === true) && !nativeMaskDescriptor(node)) reasons.push("mask_composite");
  if ([...fills, ...strokes].some((paint) => typeof paint.type === "string" && paint.type.startsWith("GRADIENT_"))) reasons.push("gradient_paint");
  if (effects.some((effect) => typeof effect.type === "string" && VISUAL_EFFECT_TYPES.has(effect.type))) reasons.push("visual_effect");
  if (typeof node.blendMode === "string" && node.blendMode !== "NORMAL" && node.blendMode !== "PASS_THROUGH") reasons.push("blend_mode");
  if (fills.length > 1 || strokes.length > 1) reasons.push("multiple_paints");
  if (reasons.length === 0 && node.type !== "TEXT" && !VECTOR_TYPES.has(node.type) && node.isMask !== true && !isEditableGraph(node, fills, strokes, context.isRoot) && (context.hasStyleReferences || fills.some((paint) => paint.type === "SOLID") || strokes.length > 0)) reasons.push("visual_style");
  if (hasUnrepresentableTransform(node)) reasons.push("unrepresentable_transform");
  if (node.type === "TEXT" && context.hasComplexTextRuns) reasons.push("rich_text_runs");

  if (reasons.length === 1 && reasons[0] === "rich_text_runs") {
    return { strategy: "native", mimeType: null, reasons };
  }
  if (reasons.length) return { strategy: "composite_png", mimeType: "image/png", reasons };
  if (fills.some((paint) => paint.type === "IMAGE")) return { strategy: "image_asset", mimeType: "image/png", reasons: [] };
  return { strategy: "native", mimeType: null, reasons: [] };
}
