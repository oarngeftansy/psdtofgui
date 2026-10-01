import { classifyVisualNode, nativeMaskDescriptor, type VisualCapability, type VisualNode } from "./visual-capability";
import { parseNineSliceAnnotation, type NineSliceParseResult } from "./nine-slice";

export type SelectionWarning = { code: string; message: string };
export type SelectionResource = { key: string; mime_type: "image/png" | "image/svg+xml"; size: number };
export type SerializedSelectionNode = {
  id: string;
  name: string;
  type: string;
  bounds: { x: number; y: number; width: number; height: number };
  children: SerializedSelectionNode[];
  rotation?: number;
  visible?: boolean;
  opacity?: number;
  source_order?: number;
  text?: string;
  properties?: Record<string, unknown>;
  style?: Record<string, unknown>;
  resource_keys: string[];
};
export type SelectionManifest = { version: 1; display_name: string; top_level_nodes: SerializedSelectionNode[]; resources: SelectionResource[]; warnings: SelectionWarning[] };
export type SelectionPreflight = { manifest: SelectionManifest | null; nodeCount: number; assetCount: number; estimatedBytes: number; warnings: SelectionWarning[]; sendable: boolean };

const MAX_TOP_LEVEL = 20;
const MAX_NODES = 5000;
const MAX_RESOURCE_BYTES = 25 * 1024 * 1024;
const MAX_SESSION_BYTES = 200 * 1024 * 1024;
const MAX_DEPTH = 32;
const MAX_PROPERTIES = 128;
const MAX_STRING = 64 * 1024;
const MAX_VALUES = 100_000;
const STYLE_REFERENCE_KEYS = ["fillStyleId", "strokeStyleId", "effectStyleId", "textStyleId"] as const;

export type FigmaTransform = readonly [readonly [number, number, number], readonly [number, number, number]];
export type FigmaSceneNode = { name: string; type: string; width?: number; height?: number; visible?: boolean; clipsContent?: boolean; isMask?: boolean; absoluteTransform?: FigmaTransform; absoluteRenderBounds?: { x: number; y: number; width: number; height: number } | null; absoluteBoundingBox?: { x: number; y: number; width: number; height: number } | null; children?: readonly FigmaSceneNode[]; locked?: boolean; componentProperties?: Record<string, { value?: unknown }>; prototypeStartNode?: unknown; reactions?: readonly unknown[] };
type SceneLike = FigmaSceneNode;

export class SelectionExportError extends Error {
  constructor(public readonly code: "selection_too_large" | "selection_empty" | "selection_export_failed") {
    super(code === "selection_too_large" ? "选择内容过大" : code === "selection_empty" ? "请选择要导出的图层" : "选择导出失败");
    this.name = "SelectionExportError";
  }
}

function imageReference(node: SceneLike, localOrder: number): string | null {
  if (node.type === "VIDEO") return null;
  const fills = (node as { fills?: unknown }).fills;
  if (!Array.isArray(fills)) return null;
  const image = fills.find((fill) => fill && typeof fill === "object" && (fill as { type?: unknown }).type === "IMAGE") as { imageHash?: unknown; imageRef?: unknown } | undefined;
  if (!image) return null;
  const source = typeof image.imageHash === "string" ? `hash:${image.imageHash}` : typeof image.imageRef === "string" ? `ref:${image.imageRef}` : `local:${localOrder}`;
  // imageHash identifies the source bitmap, not the rendered resource. Two
  // nodes may use the same bitmap with different crop/scale parameters, while
  // byte-for-byte equivalent nodes should share one FairyGUI resource.
  const renderSignature = JSON.stringify({
    fills,
    width: typeof node.width === "number" && Number.isFinite(node.width) ? node.width : null,
    height: typeof node.height === "number" && Number.isFinite(node.height) ? node.height : null,
    opacity: typeof (node as Record<string, unknown>).opacity === "number" ? (node as Record<string, unknown>).opacity : 1,
  });
  return `${source}:${renderSignature}`;
}

function validBounds(value: { x: number; y: number; width: number; height: number } | null | undefined) {
  return value && [value.x, value.y, value.width, value.height].every(Number.isFinite) && value.width > 0 && value.height > 0
    ? { x: value.x, y: value.y, width: value.width, height: value.height }
    : null;
}

function transformedLocalBounds(node: SceneLike) {
  const width = node.width;
  const height = node.height;
  const transform = node.absoluteTransform;
  if (typeof width !== "number" || !Number.isFinite(width) || width <= 0
    || typeof height !== "number" || !Number.isFinite(height) || height <= 0
    || !transform || transform.length !== 2 || transform.some((row) => row.length !== 3 || !row.every(Number.isFinite))) return null;
  const corners = [[0, 0], [width, 0], [0, height], [width, height]].map(([x, y]) => ({
    x: transform[0][0] * x! + transform[0][1] * y! + transform[0][2],
    y: transform[1][0] * x! + transform[1][1] * y! + transform[1][2],
  }));
  const xs = corners.map((point) => point.x);
  const ys = corners.map((point) => point.y);
  const left = Math.min(...xs);
  const top = Math.min(...ys);
  return { x: left, y: top, width: Math.max(...xs) - left, height: Math.max(...ys) - top };
}

function bounds(node: SceneLike) {
  // Some Figma-derived nodes temporarily expose no usable layout box even
  // though their rendered box and PNG export are valid. Prefer the logical
  // box, but do not turn that API gap into a zero-sized FairyGUI object.
  return validBounds(node.absoluteBoundingBox) ?? validBounds(node.absoluteRenderBounds) ?? validBounds(transformedLocalBounds(node)) ?? { x: 0, y: 0, width: 0, height: 0 };
}

function renderedBounds(node: SceneLike) {
  return validBounds(node.absoluteRenderBounds) ?? validBounds(node.absoluteBoundingBox) ?? validBounds(transformedLocalBounds(node)) ?? { x: 0, y: 0, width: 0, height: 0 };
}

function expandedResourceCanvas(node: SceneLike) {
  const layout = validBounds(node.absoluteBoundingBox);
  const rendered = validBounds(node.absoluteRenderBounds);
  if (!layout || !rendered) return null;
  const tolerance = 1e-4;
  const containsLayout = rendered.x <= layout.x + tolerance
    && rendered.y <= layout.y + tolerance
    && rendered.x + rendered.width >= layout.x + layout.width - tolerance
    && rendered.y + rendered.height >= layout.y + layout.height - tolerance;
  const expanded = Math.abs(rendered.x - layout.x) > tolerance
    || Math.abs(rendered.y - layout.y) > tolerance
    || Math.abs(rendered.width - layout.width) > tolerance
    || Math.abs(rendered.height - layout.height) > tolerance;
  return containsLayout && expanded ? rendered : null;
}

function subtreeContainsText(node: SceneLike): boolean {
  const pending = [...(node.children ?? [])];
  while (pending.length > 0) {
    const current = pending.pop()!;
    if (current.type === "TEXT") return true;
    pending.push(...(current.children ?? []));
  }
  return false;
}

function sanitizeVisualValue(value: unknown, depth = 0, count = { value: 0 }): unknown {
  count.value += 1;
  if (count.value > MAX_VALUES || depth > MAX_DEPTH) throw new SelectionExportError("selection_too_large");
  if (typeof value === "string") {
    if (value.length > MAX_STRING) throw new SelectionExportError("selection_too_large");
    return value;
  }
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (Array.isArray(value)) {
    if (value.length > MAX_PROPERTIES) throw new SelectionExportError("selection_too_large");
    return value.map((nested) => sanitizeVisualValue(nested, depth + 1, count)).filter((nested) => nested !== undefined);
  }
  if (!value || typeof value !== "object") return undefined;
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > MAX_PROPERTIES) throw new SelectionExportError("selection_too_large");
  const safe: Record<string, unknown> = {};
  for (const [name, nested] of entries) {
    if (name.length > MAX_STRING) throw new SelectionExportError("selection_too_large");
    if (/(?:url|href|src|image(?:hash|ref)?|bytes?|base64|data|(?:node)?id|path|file)/i.test(name)) continue;
    const sanitized = sanitizeVisualValue(nested, depth + 1, count);
    if (sanitized !== undefined) safe[name] = sanitized;
  }
  return safe;
}

function addProperty(target: Record<string, unknown>, name: string, value: unknown): void {
  const sanitized = sanitizeVisualValue(value);
  if (sanitized !== undefined) target[name] = sanitized;
}

function propertyName(name: string): string { return name.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`); }

function channel(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.round(Math.max(0, Math.min(1, value)) * 255).toString(16).padStart(2, "0");
}

function solidRunColor(value: unknown): string | null {
  if (!Array.isArray(value) || value.length !== 1) return null;
  const paint = value[0];
  if (!paint || typeof paint !== "object") return null;
  const record = paint as Record<string, unknown>;
  if (record.type !== "SOLID" || record.visible === false || !record.color || typeof record.color !== "object") return null;
  const color = record.color as Record<string, unknown>;
  const red = channel(color.r);
  const green = channel(color.g);
  const blue = channel(color.b);
  const paintOpacity = typeof record.opacity === "number" ? record.opacity : 1;
  const colorAlpha = typeof color.a === "number" ? color.a : 1;
  const alpha = channel(paintOpacity * colorAlpha);
  return red && green && blue && alpha ? `#${red}${green}${blue}${alpha}` : null;
}

function hasNonDefaultTextFeature(value: unknown): boolean {
  if (value === null || value === undefined || value === false || value === 0 || value === "NONE") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.values(value as Record<string, unknown>).some(hasNonDefaultTextFeature);
  return true;
}

function hasDeclaredTextFeature(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  return Boolean(value) && typeof value === "object" && Object.keys(value as Record<string, unknown>).length > 0;
}

function textRuns(node: SceneLike): Array<Record<string, unknown>> | null {
  if (node.type !== "TEXT") return null;
  const getter = (node as unknown as { getStyledTextSegments?: (fields: readonly string[]) => unknown }).getStyledTextSegments;
  if (typeof getter !== "function") return null;
  let rawSegments: unknown;
  try {
    rawSegments = getter.call(node, [
      "fontName", "fontSize", "fills", "textDecoration", "textCase", "letterSpacing", "lineHeight",
      "listOptions", "listSpacing", "indentation", "paragraphIndent", "paragraphSpacing", "hyperlink",
      "boundVariables", "textStyleOverrides", "openTypeFeatures",
    ]);
  } catch {
    return [{ content: typeof (node as unknown as { characters?: unknown }).characters === "string" ? (node as unknown as { characters: string }).characters : "", style: {}, unsupportedFeatures: ["styled_text_segments_unavailable"] }];
  }
  if (!Array.isArray(rawSegments) || !rawSegments.length) return null;
  const runs = rawSegments.map((raw): Record<string, unknown> => {
    if (!raw || typeof raw !== "object") return { content: "", style: {}, unsupportedFeatures: ["styled_text_segment_invalid"] };
    const segment = raw as Record<string, unknown>;
    const unsupportedFeatures: string[] = [];
    const style: Record<string, unknown> = {};
    const fontName = segment.fontName;
    if (fontName && typeof fontName === "object") {
      const font = fontName as Record<string, unknown>;
      if (typeof font.family === "string" && typeof font.style === "string") style.font = { family: font.family, style: font.style };
      else unsupportedFeatures.push("font_name");
    } else unsupportedFeatures.push("font_name");
    if (typeof segment.fontSize === "number" && Number.isFinite(segment.fontSize) && segment.fontSize > 0) style.fontSize = segment.fontSize;
    else unsupportedFeatures.push("font_size");
    const color = solidRunColor(segment.fills);
    if (color) style.color = color;
    else unsupportedFeatures.push("text_run_fill");
    if (segment.textDecoration !== "NONE") unsupportedFeatures.push("text_decoration");
    if (segment.textCase !== "ORIGINAL") unsupportedFeatures.push("text_case");
    const letterSpacing = segment.letterSpacing;
    if (letterSpacing && typeof letterSpacing === "object" && (letterSpacing as { value?: unknown }).value !== 0) unsupportedFeatures.push("letter_spacing");
    // A uniform line height belongs to the text node's normal editable style.
    // It does not make a single styled segment into rich text.
    if (hasNonDefaultTextFeature(segment.listOptions)) unsupportedFeatures.push("list_options");
    if (hasNonDefaultTextFeature(segment.listSpacing)) unsupportedFeatures.push("list_spacing");
    if (hasNonDefaultTextFeature(segment.indentation)) unsupportedFeatures.push("indentation");
    if (hasNonDefaultTextFeature(segment.paragraphIndent)) unsupportedFeatures.push("paragraph_indent");
    if (hasNonDefaultTextFeature(segment.paragraphSpacing)) unsupportedFeatures.push("paragraph_spacing");
    if (hasNonDefaultTextFeature(segment.hyperlink)) unsupportedFeatures.push("hyperlink");
    if (hasNonDefaultTextFeature(segment.boundVariables)) unsupportedFeatures.push("bound_variables");
    if (hasNonDefaultTextFeature(segment.textStyleOverrides)) unsupportedFeatures.push("text_style_overrides");
    if (hasDeclaredTextFeature(segment.openTypeFeatures)) unsupportedFeatures.push("open_type_features");
    return {
      content: typeof segment.characters === "string" ? segment.characters : "",
      style,
      unsupportedFeatures,
    };
  });
  const content = typeof (node as unknown as { characters?: unknown }).characters === "string" ? (node as unknown as { characters: string }).characters : "";
  if (runs.map((run) => run.content).join("") !== content) return [{ content, style: {}, unsupportedFeatures: ["styled_text_segments_invalid"] }];
  return runs.length > 1 || runs.some((run) => (run.unsupportedFeatures as string[]).length) ? runs : null;
}

function nodeProperties(node: SceneLike): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  const layout = node as { layoutMode?: unknown; itemSpacing?: unknown; paddingTop?: unknown; paddingRight?: unknown; paddingBottom?: unknown; paddingLeft?: unknown; constraints?: unknown };
  if (typeof layout.layoutMode === "string" && layout.layoutMode !== "NONE") {
    properties.layout_mode = layout.layoutMode;
    for (const key of ["itemSpacing", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft"] as const) if (typeof layout[key] === "number") properties[propertyName(key)] = layout[key];
  }
  if (layout.constraints && typeof layout.constraints === "object") addProperty(properties, "constraints", layout.constraints);
  const source = node as Record<string, unknown>;
  for (const key of ["primaryAxisAlignItems", "counterAxisAlignItems", "primaryAxisSizingMode", "counterAxisSizingMode", "counterAxisSpacing", "layoutWrap", "minWidth", "maxWidth", "minHeight", "maxHeight", "clipsContent", "cornerRadius", "topLeftRadius", "topRightRadius", "bottomLeftRadius", "bottomRightRadius", "layoutAlign", "layoutGrow", "textAutoResize", "textAlignHorizontal", "textAlignVertical", "fontSize", "lineHeight", "letterSpacing", "strokeWeight", "variantProperties"] as const) {
    const value = source[key];
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean" || (value && typeof value === "object")) addProperty(properties, propertyName(key), value);
  }
  const reactionCount = Array.isArray(node.reactions) ? node.reactions.length : 0;
  if (node.prototypeStartNode || reactionCount) properties.interactions = { present: true, reaction_count: reactionCount, prototype_start: Boolean(node.prototypeStartNode) };
  if (node.locked) properties.locked = true;
  if (node.componentProperties) {
    const entries = Object.entries(node.componentProperties);
    if (entries.length > MAX_PROPERTIES) throw new SelectionExportError("selection_too_large");
    const componentProperties: Record<string, unknown> = {};
    for (const [name, value] of entries) {
      const sanitized = sanitizeVisualValue(value.value);
      if (sanitized !== undefined) componentProperties[name] = sanitized;
    }
    properties.component_properties = componentProperties;
  }
  return properties;
}

function nodeStyle(node: SceneLike, styleReferences: Record<string, string>): Record<string, unknown> {
  const source = node as Record<string, unknown>;
  const style: Record<string, unknown> = {};
  for (const key of ["fills", "strokes", "effects", "relativeTransform", "absoluteTransform"] as const) if (Array.isArray(source[key])) addProperty(style, propertyName(key), source[key]);
  const font = (node as { fontName?: { family?: unknown; style?: unknown } }).fontName;
  if (font && typeof font.family === "string" && typeof font.style === "string") addProperty(style, "font", { family: font.family, style: font.style });
  const color = solidRunColor(source.fills);
  if (color) style.color = color;
  const runs = textRuns(node);
  if (runs) style.runs = runs;
  if (Object.keys(styleReferences).length) style.style_references = styleReferences;
  return style;
}

function warning(code: string, message: string): SelectionWarning { return { code, message }; }

type ResourcePlan = { key: string; mime_type: SelectionResource["mime_type"]; node: FigmaSceneNode };
type ClipBounds = { x: number; y: number; width: number; height: number };
type NodePlan = { node: SceneLike; order: number; parent: NodePlan | null; resource?: ResourcePlan; styleReferences: Record<string, string>; capability: VisualCapability; nineSlice: NineSliceParseResult; clipFragment?: ClipBounds; clippedOut?: boolean };

function treeHasPrototypeBehavior(nodes: readonly SceneLike[]): boolean {
  const pending = [...nodes];
  while (pending.length) {
    const node = pending.pop()!;
    if (node.visible === false) continue;
    if (node.prototypeStartNode || (Array.isArray(node.reactions) && node.reactions.length)) return true;
    pending.push(...(node.children ?? []).map((child) => child as SceneLike));
  }
  return false;
}

// One deterministic DFS owns both declarations and lookup ordering.
function selectionPlan(nodes: readonly FigmaSceneNode[]): { nodes: NodePlan[]; resources: ResourcePlan[] } {
  const visibleRoots = nodes.filter((node) => node.visible !== false);
  if (!visibleRoots.length) throw new SelectionExportError("selection_empty");
  if (visibleRoots.length > MAX_TOP_LEVEL) throw new SelectionExportError("selection_too_large");
  const planned: NodePlan[] = [];
  const resources: ResourcePlan[] = [];
  const byReference = new Map<string, ResourcePlan>();
  const styleTokens = new Map<string, string>();
  const pending: Array<{ node: SceneLike; depth: number; parent: NodePlan | null }> = visibleRoots.slice().reverse().map((node) => ({ node: node as SceneLike, depth: 1, parent: null }));
  while (pending.length) {
    const { node, depth, parent } = pending.pop()!;
    // Invisible Figma nodes have no rendered contribution. Prune the complete
    // subtree before classification so neither FairyGUI objects nor resources
    // are declared for it.
    if (node.visible === false) continue;
    const order = planned.length + 1;
    if (order > MAX_NODES || depth > MAX_DEPTH || node.name.length > MAX_STRING || (typeof (node as unknown as { characters?: unknown }).characters === "string" && (node as unknown as { characters: string }).characters.length > MAX_STRING)) throw new SelectionExportError("selection_too_large");
    const styleReferences: Record<string, string> = {};
    for (const key of STYLE_REFERENCE_KEYS) {
      const raw = (node as Record<string, unknown>)[key];
      if (typeof raw !== "string" || raw.trim().length === 0) continue;
      let token = styleTokens.get(raw);
      if (!token) { token = `style-${styleTokens.size + 1}`; styleTokens.set(raw, token); }
      styleReferences[propertyName(key)] = token;
    }
    const classified = classifyVisualNode(node as VisualNode, { isRoot: parent === null, hasComplexTextRuns: textRuns(node) !== null, hasStyleReferences: Object.keys(styleReferences).length > 0 });
    // FairyGUI 6.1.4 has no verified nested Group-mask encoding inside one
    // component. Preserve the editable child tree instead of flattening the
    // whole group; only a selection-root mask is emitted as a native mask.
    const hasNestedMask = parent !== null && (node.children ?? []).some((child) => child.isMask === true);
    const visualOnlyNestedMask = hasNestedMask && !subtreeContainsText(node);
    const nestedMaskReasons = hasNestedMask && !visualOnlyNestedMask
      ? classified.reasons.filter((reason) => reason !== "mask_composite")
      : classified.reasons;
    const nestedMaskCapability: VisualCapability = visualOnlyNestedMask && classified.strategy === "native"
      ? { strategy: "composite_png", mimeType: "image/png", reasons: ["mask_composite"] }
      : nestedMaskReasons.length === classified.reasons.length
      ? classified
      : nestedMaskReasons.length > 0
        ? { strategy: "composite_png", mimeType: "image/png", reasons: nestedMaskReasons }
        : { strategy: "native", mimeType: null, reasons: [] };
    // A clipsContent frame remains an ordinary editable frame. Do not crop or
    // rasterize descendants from geometry alone: stacking siblings may be the
    // actual reason only part of a child is visible, and leaf clipping would
    // destroy editable text. The Writer handles only source-declared clip roles.
    const capability = nestedMaskCapability;
    const nineSlice = parseNineSliceAnnotation(node.name, bounds(node));
    const mime_type = capability.mimeType;
    const reference = capability.strategy === "skip" || capability.strategy === "native"
      ? null
      : capability.strategy === "image_asset"
        ? imageReference(node, order)
        : `${capability.strategy}:${order}`;
    let resource: ResourcePlan | undefined;
    if (reference && mime_type) {
      const identity = capability.strategy === "image_asset"
        ? `${mime_type}:${reference}`
        : `${mime_type}:${reference}:${order}`;
      resource = byReference.get(identity);
      if (!resource) {
        resource = { key: `asset-${resources.length + 1}`, mime_type, node };
        byReference.set(identity, resource);
        resources.push(resource);
      }
    }
    const current: NodePlan = { node, order, parent, resource, styleReferences, capability, nineSlice };
    planned.push(current);
    const children = capability.strategy === "composite_png" || capability.strategy === "vector_asset"
      ? []
      : (node.children ?? []).filter((child) => child.visible !== false);
    if (pending.length + children.length > MAX_NODES) throw new SelectionExportError("selection_too_large");
    for (let index = children.length - 1; index >= 0; index -= 1) pending.push({ node: children[index] as SceneLike, depth: depth + 1, parent: current });
  }
  if (!planned.length) throw new SelectionExportError("selection_empty");
  return { nodes: planned, resources };
}

export function serializeSelection(nodes: readonly FigmaSceneNode[]): SelectionManifest {
  const plan = selectionPlan(nodes);
  const roots: SerializedSelectionNode[] = [];
  const warnings: SelectionWarning[] = [];
  if (treeHasPrototypeBehavior(nodes as readonly SceneLike[])) warnings.push(warning("unsupported_prototype", "原型连线不会导出"));
  const serialized = new Map<NodePlan, SerializedSelectionNode>();
  const plannedChildren = new Map<NodePlan, NodePlan[]>();
  for (const item of plan.nodes) {
    if (item.parent) plannedChildren.set(item.parent, [...(plannedChildren.get(item.parent) ?? []), item]);
  }
  for (const item of plan.nodes) {
    const { node } = item;
    if (node.locked) warnings.push(warning("node_locked", "已保留锁定图层"));
    if (node.type === "VIDEO") warnings.push(warning("unsupported_video", "视频内容不会导出"));
    if (item.capability.strategy === "composite_png") warnings.push(warning("visual_rasterized", `已自动保真处理为图片：${item.capability.reasons.join(",")}`));
    if (item.nineSlice.diagnostic === "nine_slice_invalid") warnings.push(warning("nine_slice_invalid", "九宫格标记格式无效，已按普通图片处理"));
    if (item.nineSlice.diagnostic === "nine_slice_out_of_bounds") warnings.push(warning("nine_slice_out_of_bounds", "九宫格边距超过图层尺寸，已按普通图片处理"));
    const properties = nodeProperties(node);
    properties.export_strategy = item.capability.strategy;
    if (item.capability.reasons.length) properties.raster_reasons = item.capability.reasons;
    const resourceCanvas = item.resource?.mime_type === "image/png" ? expandedResourceCanvas(node) : null;
    if (resourceCanvas) properties.resource_canvas_bounds = resourceCanvas;
    if (item.nineSlice.insets) properties.nine_slice_insets = item.nineSlice.insets;
    if (item.clipFragment) properties.clip_fragment_bounds = item.clipFragment;
    const style = nodeStyle(node, item.styleReferences);
    const mask = item.parent === null ? nativeMaskDescriptor(node as VisualNode) : null;
    if (mask) {
      const childPlans = plannedChildren.get(item) ?? [];
      const maskPlan = childPlans[mask.maskIndex];
      const contentPlans = mask.contentIndexes.map((index) => childPlans[index]).filter((child): child is NodePlan => Boolean(child));
      if (maskPlan && contentPlans.length === mask.contentIndexes.length) {
        style.mask = {
          kind: mask.kind,
          maskNodeRef: `node-${maskPlan.order}`,
          contentNodeRefs: contentPlans.map((child) => `node-${child.order}`),
          effects: [],
          ...(mask.cornerRadii ? { cornerRadii: mask.cornerRadii } : {}),
        };
      }
    }
    const result: SerializedSelectionNode = {
      // Rasterization changes only the representation. It must never replace
      // the node's full layout box with absoluteRenderBounds: Figma may report
      // that render box after ancestor clipping or sibling occlusion, which
      // would bake an accidental crop into the FairyGUI document.
      id: `node-${item.order}`, name: node.name || "未命名图层", type: node.type, bounds: bounds(node), children: [],
      // Every emitted child already uses its axis-aligned canvas bounds. A
      // structural parent rotation would therefore rotate those flattened
      // positions a second time (notably for rotated component instances whose
      // descendants counter-rotate back into an upright card).
      rotation: item.resource?.mime_type === "image/png" || (node.children?.length ?? 0) > 0
        ? 0
        : typeof (node as unknown as { rotation?: unknown }).rotation === "number" ? (node as unknown as { rotation: number }).rotation : 0,
      // Figma visibility is inherited. FairyGUI exposes visibility per display
      // object, so carry an invisible ancestor down to every emitted child;
      // otherwise descendants reopen with an active eye in the Editor.
      visible: node.visible !== false && (item.parent ? serialized.get(item.parent)!.visible : true), opacity: typeof (node as unknown as { opacity?: unknown }).opacity === "number" ? (node as unknown as { opacity: number }).opacity : 1,
      source_order: item.order - 1, ...(typeof (node as unknown as { characters?: unknown }).characters === "string" ? { text: (node as unknown as { characters: string }).characters } : {}),
      properties, style, resource_keys: item.resource ? [item.resource.key] : [],
    };
    result.name = item.nineSlice.displayName || result.name;
    (item.parent ? serialized.get(item.parent)!.children : roots).push(result);
    serialized.set(item, result);
  }
  const uniqueWarnings = [...new Map(warnings.map((item) => [`${item.code}\0${item.message}`, item] as const)).values()];
  return { version: 1, display_name: roots[0]?.name ?? "当前选择", top_level_nodes: roots, resources: plan.resources.map(({ key, mime_type }) => ({ key, mime_type, size: 0 })), warnings: uniqueWarnings };
}

export function resourceLookup(nodes: readonly FigmaSceneNode[], manifest: SelectionManifest): ReadonlyMap<string, FigmaSceneNode> {
  const declarations = new Map(selectionPlan(nodes).resources.map((resource) => [resource.key, resource.node]));
  return new Map(manifest.resources.flatMap((resource) => {
    const node = declarations.get(resource.key);
    return node ? [[resource.key, node] as const] : [];
  }));
}

export function resourceClipFragments(manifest: SelectionManifest): ReadonlyMap<string, ClipBounds> {
  const fragments = new Map<string, ClipBounds>();
  const pending = [...manifest.top_level_nodes];
  while (pending.length) {
    const node = pending.pop()!;
    pending.push(...node.children);
    const raw = node.properties?.clip_fragment_bounds;
    if (!raw || typeof raw !== "object" || Array.isArray(raw) || node.resource_keys.length !== 1) continue;
    const candidate = raw as Record<string, unknown>;
    const values = [candidate.x, candidate.y, candidate.width, candidate.height];
    if (!values.every((value) => typeof value === "number" && Number.isFinite(value)) || (candidate.width as number) <= 0 || (candidate.height as number) <= 0) continue;
    fragments.set(node.resource_keys[0]!, candidate as ClipBounds);
  }
  return fragments;
}

export function resourceLayoutBounds(manifest: SelectionManifest): ReadonlyMap<string, ClipBounds> {
  const result = new Map<string, ClipBounds>();
  const pending = [...manifest.top_level_nodes];
  while (pending.length) {
    const node = pending.pop()!;
    pending.push(...node.children);
    const rawCanvas = node.properties?.resource_canvas_bounds;
    const canvas = rawCanvas && typeof rawCanvas === "object" && !Array.isArray(rawCanvas)
      ? validBounds(rawCanvas as ClipBounds)
      : null;
    const resolved = canvas ?? node.bounds;
    if (node.resource_keys.length === 1 && [resolved.x, resolved.y, resolved.width, resolved.height].every(Number.isFinite)
      && resolved.width > 0 && resolved.height > 0) result.set(node.resource_keys[0]!, resolved);
  }
  return result;
}

export function preflightSelection(nodes: readonly FigmaSceneNode[]): SelectionPreflight {
  try {
    const manifest = serializeSelection(nodes);
    const lookup = resourceLookup(nodes, manifest);
    const estimates = manifest.resources.map((resource) => {
      const node = lookup.get(resource.key);
      const area = node ? renderedBounds(node) : { width: 1, height: 1 };
      return Math.max(1, area.width * area.height * 4);
    });
    const estimatedBytes = estimates.reduce((total, size) => total + size, 0);
    if (estimates.some((size) => size > MAX_RESOURCE_BYTES) || estimatedBytes > MAX_SESSION_BYTES) throw new SelectionExportError("selection_too_large");
    return { manifest, nodeCount: selectionPlan(nodes).nodes.length, assetCount: manifest.resources.length, estimatedBytes, warnings: manifest.warnings, sendable: true };
  } catch (error) {
    const safe = error instanceof SelectionExportError ? error : new SelectionExportError("selection_export_failed");
    return { manifest: null, nodeCount: 0, assetCount: 0, estimatedBytes: 0, warnings: [warning(safe.code, safe.message)], sendable: false };
  }
}
