var FigmaToFairyGUIPluginMain = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	function isUiToMainMessage(value) {
		if (!value || typeof value !== "object") return false;
		const message = value;
		const boundedIdentity = typeof message.nodeId === "string" && message.nodeId.length > 0 && message.nodeId.length <= 256 && typeof message.attempt === "string" && message.attempt.length > 0 && message.attempt.length <= 128;
		if (message.type === "create-review-area") return boundedIdentity && message.previewBytes instanceof Uint8Array && message.previewBytes.length >= 24 && message.previewBytes.length <= 2097152 && Number.isInteger(message.previewWidth) && Number(message.previewWidth) > 0 && Number(message.previewWidth) <= 4096 && Number.isInteger(message.previewHeight) && Number(message.previewHeight) > 0 && Number(message.previewHeight) <= 4096;
		return message.type === "selection-preflight" || (message.type === "selection-export" && (message.mode === void 0 || message.mode === "writer") || message.type === "semantic-screenshot-export") && typeof message.attempt === "string" && message.attempt.length > 0 || message.type === "locate-node" && boundedIdentity;
	}
	//#endregion
	//#region src/assets.ts
	var AssetExportError = class extends Error {
		constructor(label) {
			super(`无法导出图层：${label}`);
			this.name = "AssetExportError";
		}
	};
	async function* exportDeclaredAssets(manifest, lookup, exportOverride) {
		const results = new Array(manifest.resources.length);
		let cursor = 0;
		const worker = async () => {
			while (cursor < manifest.resources.length) {
				const index = cursor++;
				const resource = manifest.resources[index];
				const node = lookup.get(resource.key);
				if (!node) throw new AssetExportError("所选图层");
				try {
					const format = resource.mime_type === "image/svg+xml" ? "SVG" : "PNG";
					const bytes = exportOverride ? await exportOverride(node, resource.key, format) : await node.exportAsync({ format });
					results[index] = {
						key: resource.key,
						mime_type: resource.mime_type,
						bytes
					};
				} catch (error) {
					if (error && typeof error === "object" && "safeCode" in error && typeof error.safeCode === "string" && error.safeCode.startsWith("resource_")) throw error;
					throw new AssetExportError(node.name || "所选图层");
				}
			}
		};
		await Promise.all(Array.from({ length: Math.min(4, manifest.resources.length) }, worker));
		for (const resource of results) yield resource;
	}
	//#endregion
	//#region src/visual-capability.ts
	var VECTOR_TYPES = /* @__PURE__ */ new Set([
		"VECTOR",
		"BOOLEAN_OPERATION",
		"STAR",
		"LINE",
		"POLYGON",
		"ELLIPSE"
	]);
	var VISUAL_EFFECT_TYPES = /* @__PURE__ */ new Set([
		"DROP_SHADOW",
		"INNER_SHADOW",
		"LAYER_BLUR",
		"BACKGROUND_BLUR"
	]);
	function validColor(paint, allowAlpha) {
		const record = paint;
		const color = record.color;
		if (!color || typeof color !== "object") return false;
		const channels = color;
		if (![
			"r",
			"g",
			"b"
		].every((key) => typeof channels[key] === "number" && Number.isFinite(channels[key]))) return false;
		const colorAlpha = channels.a === void 0 ? 1 : channels.a;
		const paintOpacity = record.opacity === void 0 ? 1 : record.opacity;
		if (typeof colorAlpha !== "number" || !Number.isFinite(colorAlpha) || typeof paintOpacity !== "number" || !Number.isFinite(paintOpacity)) return false;
		return allowAlpha || colorAlpha === 1 && paintOpacity === 1;
	}
	function visibleRecords(value) {
		return Array.isArray(value) ? value.filter((entry) => {
			if (!entry || typeof entry !== "object" || entry.visible === false) return false;
			const record = entry;
			if (record.opacity === 0) return false;
			const color = record.color;
			return !(color && typeof color === "object" && color.a === 0);
		}) : [];
	}
	function positiveRadius(value) {
		return typeof value === "number" && Number.isFinite(value) && value > 0;
	}
	function radius(value, fallback) {
		if (value === void 0) return fallback;
		return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
	}
	/**
	* Return a project-neutral native mask recipe only for the deliberately small
	* subset whose Figma sibling semantics can be represented without flattening.
	*/
	function nativeMaskDescriptor(node) {
		if (![
			"GROUP",
			"FRAME",
			"COMPONENT"
		].includes(node.type)) return null;
		const children = node.children ?? [];
		const maskIndexes = children.flatMap((child, index) => child.isMask === true ? [index] : []);
		if (maskIndexes.length !== 1 || maskIndexes[0] !== 0 || children.length < 2) return null;
		const mask = children[0];
		if (mask.type !== "RECTANGLE" || (mask.children?.length ?? 0) > 0) return null;
		if (mask.maskType === "LUMINANCE" || typeof mask.opacity === "number" && mask.opacity !== 1) return null;
		if (visibleRecords(mask.effects).length || visibleRecords(mask.strokes).length) return null;
		if (typeof mask.blendMode === "string" && mask.blendMode !== "NORMAL" && mask.blendMode !== "PASS_THROUGH") return null;
		const fills = visibleRecords(mask.fills);
		if (fills.length !== 1) return null;
		const fillType = fills[0]?.type;
		if (typeof fillType === "string" && fillType.startsWith("GRADIENT_")) return null;
		if (fillType === "IMAGE") {
			if (mask.maskType === "VECTOR") return null;
			return {
				kind: "image",
				maskIndex: 0,
				contentIndexes: children.slice(1).map((_child, index) => index + 1)
			};
		}
		if (fillType !== "SOLID") return null;
		const fill = fills[0];
		const color = fill.color;
		if (typeof fill.opacity === "number" && fill.opacity !== 1 || color && typeof color === "object" && typeof color.a === "number" && color.a !== 1) return null;
		const general = typeof mask.cornerRadius === "number" && Number.isFinite(mask.cornerRadius) && mask.cornerRadius >= 0 ? mask.cornerRadius : 0;
		const cornerRadii = [
			radius(mask.topLeftRadius, general),
			radius(mask.topRightRadius, general),
			radius(mask.bottomRightRadius, general),
			radius(mask.bottomLeftRadius, general)
		];
		if (cornerRadii.some((value) => value === null)) return null;
		const resolvedRadii = cornerRadii;
		const rounded = resolvedRadii.some(positiveRadius);
		return {
			kind: rounded ? "roundedRectangle" : "rectangle",
			maskIndex: 0,
			contentIndexes: children.slice(1).map((_child, index) => index + 1),
			...rounded ? { cornerRadii: resolvedRadii } : {}
		};
	}
	function hasUnrepresentableTransform(node) {
		if (typeof node.rotation === "number" && Number.isFinite(node.rotation) && node.rotation !== 0) return true;
		const value = node.relativeTransform;
		if (!Array.isArray(value) || value.length !== 2) return false;
		const first = value[0];
		const second = value[1];
		if (!Array.isArray(first) || first.length !== 3 || !Array.isArray(second) || second.length !== 3) return true;
		const [a, c] = first;
		const [b, d] = second;
		return ![
			a,
			b,
			c,
			d
		].every((item) => typeof item === "number" && Number.isFinite(item)) || Math.abs(Number(a) - 1) > 1e-6 || Math.abs(Number(b)) > 1e-6 || Math.abs(Number(c)) > 1e-6 || Math.abs(Number(d) - 1) > 1e-6;
	}
	function isEditableGraph(node, fills, strokes, _isRoot) {
		const leafShape = ["RECTANGLE", "ELLIPSE"].includes(node.type) && (node.children?.length ?? 0) === 0;
		const rootContainerShape = ["FRAME", "COMPONENT"].includes(node.type) && (fills.length > 0 || strokes.length > 0);
		if (!leafShape && !rootContainerShape) return false;
		if (fills.length > 1 || strokes.length > 1 || [...fills, ...strokes].some((paint) => paint.type !== "SOLID")) return false;
		if (fills.some((paint) => !validColor(paint, true)) || strokes.some((paint) => !validColor(paint, false))) return false;
		if (strokes.length > 0 && (typeof node.strokeWeight !== "number" || !Number.isFinite(node.strokeWeight) || node.strokeWeight < 0)) return false;
		if (node.type === "ELLIPSE") return true;
		const general = typeof node.cornerRadius === "number" && Number.isFinite(node.cornerRadius) && node.cornerRadius >= 0 ? node.cornerRadius : 0;
		return [
			node.topLeftRadius,
			node.topRightRadius,
			node.bottomRightRadius,
			node.bottomLeftRadius
		].map((value) => value === void 0 ? general : value).every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0);
	}
	function classifyVisualNode(node, context) {
		if (node.type === "VIDEO") return {
			strategy: "skip",
			mimeType: null,
			reasons: []
		};
		if (VECTOR_TYPES.has(node.type)) return {
			strategy: "vector_asset",
			mimeType: "image/png",
			reasons: []
		};
		if (node.type === "INSTANCE" && (node.children?.length ?? 0) > 0) return {
			strategy: "native",
			mimeType: null,
			reasons: []
		};
		if ([
			"FRAME",
			"GROUP",
			"COMPONENT",
			"SECTION"
		].includes(node.type) && (node.children?.length ?? 0) > 0 && !(node.children ?? []).some((child) => child.isMask === true)) {
			if (visibleRecords(node.fills).some((paint) => paint.type === "IMAGE")) return {
				strategy: "image_asset",
				mimeType: "image/png",
				reasons: []
			};
			return {
				strategy: "native",
				mimeType: null,
				reasons: []
			};
		}
		const fills = visibleRecords(node.fills);
		const strokes = visibleRecords(node.strokes);
		const effects = visibleRecords(node.effects);
		const reasons = [];
		if (node.type === "INSTANCE" && (node.children?.length ?? 0) === 0) reasons.push("instance_composite");
		if ((node.children ?? []).some((child) => child.isMask === true) && !nativeMaskDescriptor(node)) reasons.push("mask_composite");
		if ([...fills, ...strokes].some((paint) => typeof paint.type === "string" && paint.type.startsWith("GRADIENT_"))) reasons.push("gradient_paint");
		if (effects.some((effect) => typeof effect.type === "string" && VISUAL_EFFECT_TYPES.has(effect.type))) reasons.push("visual_effect");
		if (typeof node.blendMode === "string" && node.blendMode !== "NORMAL" && node.blendMode !== "PASS_THROUGH") reasons.push("blend_mode");
		if (fills.length > 1 || strokes.length > 1) reasons.push("multiple_paints");
		if (reasons.length === 0 && node.type !== "TEXT" && !VECTOR_TYPES.has(node.type) && node.isMask !== true && !isEditableGraph(node, fills, strokes, context.isRoot) && (context.hasStyleReferences || fills.some((paint) => paint.type === "SOLID") || strokes.length > 0)) reasons.push("visual_style");
		if (hasUnrepresentableTransform(node)) reasons.push("unrepresentable_transform");
		if (node.type === "TEXT" && context.hasComplexTextRuns) reasons.push("rich_text_runs");
		if (reasons.length === 1 && reasons[0] === "rich_text_runs") return {
			strategy: "native",
			mimeType: null,
			reasons
		};
		if (reasons.length) return {
			strategy: "composite_png",
			mimeType: "image/png",
			reasons
		};
		if (fills.some((paint) => paint.type === "IMAGE")) return {
			strategy: "image_asset",
			mimeType: "image/png",
			reasons: []
		};
		return {
			strategy: "native",
			mimeType: null,
			reasons: []
		};
	}
	//#endregion
	//#region src/nine-slice.ts
	var MARKER = /@9s\(([^)]*)\)/g;
	var VALID_TRAILING = /\s*@9s\((\d+),(\d+),(\d+),(\d+)\)\s*$/;
	function parseNineSliceAnnotation(name, bounds) {
		const markers = [...name.matchAll(MARKER)];
		if (!name.includes("@9s")) return {
			displayName: name,
			insets: null,
			diagnostic: null
		};
		const match = VALID_TRAILING.exec(name);
		if (!match || markers.length !== 1) return {
			displayName: name,
			insets: null,
			diagnostic: "nine_slice_invalid"
		};
		const [left, top, right, bottom] = match.slice(1).map(Number);
		const displayName = name.slice(0, match.index).trimEnd();
		if (left + right >= bounds.width || top + bottom >= bounds.height) return {
			displayName,
			insets: null,
			diagnostic: "nine_slice_out_of_bounds"
		};
		return {
			displayName,
			insets: {
				left,
				top,
				right,
				bottom
			},
			diagnostic: null
		};
	}
	//#endregion
	//#region src/selection.ts
	var MAX_TOP_LEVEL = 20;
	var MAX_NODES = 5e3;
	var MAX_RESOURCE_BYTES = 25 * 1024 * 1024;
	var MAX_SESSION_BYTES = 200 * 1024 * 1024;
	var MAX_DEPTH = 32;
	var MAX_PROPERTIES = 128;
	var MAX_STRING = 64 * 1024;
	var MAX_VALUES = 1e5;
	var STYLE_REFERENCE_KEYS = [
		"fillStyleId",
		"strokeStyleId",
		"effectStyleId",
		"textStyleId"
	];
	var SelectionExportError = class extends Error {
		code;
		constructor(code) {
			super(code === "selection_too_large" ? "选择内容过大" : code === "selection_empty" ? "请选择要导出的图层" : "选择导出失败");
			this.code = code;
			this.name = "SelectionExportError";
		}
	};
	function imageReference(node, localOrder) {
		if (node.type === "VIDEO") return null;
		const fills = node.fills;
		if (!Array.isArray(fills)) return null;
		const image = fills.find((fill) => fill && typeof fill === "object" && fill.type === "IMAGE");
		if (!image) return null;
		return `${typeof image.imageHash === "string" ? `hash:${image.imageHash}` : typeof image.imageRef === "string" ? `ref:${image.imageRef}` : `local:${localOrder}`}:${JSON.stringify({
			fills,
			width: typeof node.width === "number" && Number.isFinite(node.width) ? node.width : null,
			height: typeof node.height === "number" && Number.isFinite(node.height) ? node.height : null,
			opacity: typeof node.opacity === "number" ? node.opacity : 1
		})}`;
	}
	function validBounds(value) {
		return value && [
			value.x,
			value.y,
			value.width,
			value.height
		].every(Number.isFinite) && value.width > 0 && value.height > 0 ? {
			x: value.x,
			y: value.y,
			width: value.width,
			height: value.height
		} : null;
	}
	function transformedLocalBounds(node) {
		const width = node.width;
		const height = node.height;
		const transform = node.absoluteTransform;
		if (typeof width !== "number" || !Number.isFinite(width) || width <= 0 || typeof height !== "number" || !Number.isFinite(height) || height <= 0 || !transform || transform.length !== 2 || transform.some((row) => row.length !== 3 || !row.every(Number.isFinite))) return null;
		const corners = [
			[0, 0],
			[width, 0],
			[0, height],
			[width, height]
		].map(([x, y]) => ({
			x: transform[0][0] * x + transform[0][1] * y + transform[0][2],
			y: transform[1][0] * x + transform[1][1] * y + transform[1][2]
		}));
		const xs = corners.map((point) => point.x);
		const ys = corners.map((point) => point.y);
		const left = Math.min(...xs);
		const top = Math.min(...ys);
		return {
			x: left,
			y: top,
			width: Math.max(...xs) - left,
			height: Math.max(...ys) - top
		};
	}
	function bounds(node) {
		return validBounds(node.absoluteBoundingBox) ?? validBounds(node.absoluteRenderBounds) ?? validBounds(transformedLocalBounds(node)) ?? {
			x: 0,
			y: 0,
			width: 0,
			height: 0
		};
	}
	function renderedBounds(node) {
		return validBounds(node.absoluteRenderBounds) ?? validBounds(node.absoluteBoundingBox) ?? validBounds(transformedLocalBounds(node)) ?? {
			x: 0,
			y: 0,
			width: 0,
			height: 0
		};
	}
	function expandedResourceCanvas(node) {
		const layout = validBounds(node.absoluteBoundingBox);
		const rendered = validBounds(node.absoluteRenderBounds);
		if (!layout || !rendered) return null;
		const tolerance = 1e-4;
		const containsLayout = rendered.x <= layout.x + tolerance && rendered.y <= layout.y + tolerance && rendered.x + rendered.width >= layout.x + layout.width - tolerance && rendered.y + rendered.height >= layout.y + layout.height - tolerance;
		const expanded = Math.abs(rendered.x - layout.x) > tolerance || Math.abs(rendered.y - layout.y) > tolerance || Math.abs(rendered.width - layout.width) > tolerance || Math.abs(rendered.height - layout.height) > tolerance;
		return containsLayout && expanded ? rendered : null;
	}
	function subtreeContainsText(node) {
		const pending = [...node.children ?? []];
		while (pending.length > 0) {
			const current = pending.pop();
			if (current.type === "TEXT") return true;
			pending.push(...current.children ?? []);
		}
		return false;
	}
	function sanitizeVisualValue(value, depth = 0, count = { value: 0 }) {
		count.value += 1;
		if (count.value > MAX_VALUES || depth > MAX_DEPTH) throw new SelectionExportError("selection_too_large");
		if (typeof value === "string") {
			if (value.length > MAX_STRING) throw new SelectionExportError("selection_too_large");
			return value;
		}
		if (value === null || typeof value === "boolean") return value;
		if (typeof value === "number") return Number.isFinite(value) ? value : void 0;
		if (Array.isArray(value)) {
			if (value.length > MAX_PROPERTIES) throw new SelectionExportError("selection_too_large");
			return value.map((nested) => sanitizeVisualValue(nested, depth + 1, count)).filter((nested) => nested !== void 0);
		}
		if (!value || typeof value !== "object") return void 0;
		const entries = Object.entries(value);
		if (entries.length > MAX_PROPERTIES) throw new SelectionExportError("selection_too_large");
		const safe = {};
		for (const [name, nested] of entries) {
			if (name.length > MAX_STRING) throw new SelectionExportError("selection_too_large");
			if (/(?:url|href|src|image(?:hash|ref)?|bytes?|base64|data|(?:node)?id|path|file)/i.test(name)) continue;
			const sanitized = sanitizeVisualValue(nested, depth + 1, count);
			if (sanitized !== void 0) safe[name] = sanitized;
		}
		return safe;
	}
	function addProperty(target, name, value) {
		const sanitized = sanitizeVisualValue(value);
		if (sanitized !== void 0) target[name] = sanitized;
	}
	function propertyName(name) {
		return name.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
	}
	function channel(value) {
		if (typeof value !== "number" || !Number.isFinite(value)) return null;
		return Math.round(Math.max(0, Math.min(1, value)) * 255).toString(16).padStart(2, "0");
	}
	function solidRunColor(value) {
		if (!Array.isArray(value) || value.length !== 1) return null;
		const paint = value[0];
		if (!paint || typeof paint !== "object") return null;
		const record = paint;
		if (record.type !== "SOLID" || record.visible === false || !record.color || typeof record.color !== "object") return null;
		const color = record.color;
		const red = channel(color.r);
		const green = channel(color.g);
		const blue = channel(color.b);
		const alpha = channel((typeof record.opacity === "number" ? record.opacity : 1) * (typeof color.a === "number" ? color.a : 1));
		return red && green && blue && alpha ? `#${red}${green}${blue}${alpha}` : null;
	}
	function hasNonDefaultTextFeature(value) {
		if (value === null || value === void 0 || value === false || value === 0 || value === "NONE") return false;
		if (Array.isArray(value)) return value.length > 0;
		if (typeof value === "object") return Object.values(value).some(hasNonDefaultTextFeature);
		return true;
	}
	function hasDeclaredTextFeature(value) {
		if (Array.isArray(value)) return value.length > 0;
		return Boolean(value) && typeof value === "object" && Object.keys(value).length > 0;
	}
	function textRuns(node) {
		if (node.type !== "TEXT") return null;
		const getter = node.getStyledTextSegments;
		if (typeof getter !== "function") return null;
		let rawSegments;
		try {
			rawSegments = getter.call(node, [
				"fontName",
				"fontSize",
				"fills",
				"textDecoration",
				"textCase",
				"letterSpacing",
				"lineHeight",
				"listOptions",
				"listSpacing",
				"indentation",
				"paragraphIndent",
				"paragraphSpacing",
				"hyperlink",
				"boundVariables",
				"textStyleOverrides",
				"openTypeFeatures"
			]);
		} catch {
			return [{
				content: typeof node.characters === "string" ? node.characters : "",
				style: {},
				unsupportedFeatures: ["styled_text_segments_unavailable"]
			}];
		}
		if (!Array.isArray(rawSegments) || !rawSegments.length) return null;
		const runs = rawSegments.map((raw) => {
			if (!raw || typeof raw !== "object") return {
				content: "",
				style: {},
				unsupportedFeatures: ["styled_text_segment_invalid"]
			};
			const segment = raw;
			const unsupportedFeatures = [];
			const style = {};
			const fontName = segment.fontName;
			if (fontName && typeof fontName === "object") {
				const font = fontName;
				if (typeof font.family === "string" && typeof font.style === "string") style.font = {
					family: font.family,
					style: font.style
				};
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
			if (letterSpacing && typeof letterSpacing === "object" && letterSpacing.value !== 0) unsupportedFeatures.push("letter_spacing");
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
				unsupportedFeatures
			};
		});
		const content = typeof node.characters === "string" ? node.characters : "";
		if (runs.map((run) => run.content).join("") !== content) return [{
			content,
			style: {},
			unsupportedFeatures: ["styled_text_segments_invalid"]
		}];
		return runs.length > 1 || runs.some((run) => run.unsupportedFeatures.length) ? runs : null;
	}
	function nodeProperties(node) {
		const properties = {};
		const layout = node;
		if (typeof layout.layoutMode === "string" && layout.layoutMode !== "NONE") {
			properties.layout_mode = layout.layoutMode;
			for (const key of [
				"itemSpacing",
				"paddingTop",
				"paddingRight",
				"paddingBottom",
				"paddingLeft"
			]) if (typeof layout[key] === "number") properties[propertyName(key)] = layout[key];
		}
		if (layout.constraints && typeof layout.constraints === "object") addProperty(properties, "constraints", layout.constraints);
		const source = node;
		for (const key of [
			"primaryAxisAlignItems",
			"counterAxisAlignItems",
			"primaryAxisSizingMode",
			"counterAxisSizingMode",
			"counterAxisSpacing",
			"layoutWrap",
			"minWidth",
			"maxWidth",
			"minHeight",
			"maxHeight",
			"clipsContent",
			"cornerRadius",
			"topLeftRadius",
			"topRightRadius",
			"bottomLeftRadius",
			"bottomRightRadius",
			"layoutAlign",
			"layoutGrow",
			"textAutoResize",
			"textAlignHorizontal",
			"textAlignVertical",
			"fontSize",
			"lineHeight",
			"letterSpacing",
			"strokeWeight",
			"variantProperties"
		]) {
			const value = source[key];
			if (typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value && typeof value === "object") addProperty(properties, propertyName(key), value);
		}
		const reactionCount = Array.isArray(node.reactions) ? node.reactions.length : 0;
		if (node.prototypeStartNode || reactionCount) properties.interactions = {
			present: true,
			reaction_count: reactionCount,
			prototype_start: Boolean(node.prototypeStartNode)
		};
		if (node.locked) properties.locked = true;
		if (node.componentProperties) {
			const entries = Object.entries(node.componentProperties);
			if (entries.length > MAX_PROPERTIES) throw new SelectionExportError("selection_too_large");
			const componentProperties = {};
			for (const [name, value] of entries) {
				const sanitized = sanitizeVisualValue(value.value);
				if (sanitized !== void 0) componentProperties[name] = sanitized;
			}
			properties.component_properties = componentProperties;
		}
		return properties;
	}
	function nodeStyle(node, styleReferences) {
		const source = node;
		const style = {};
		for (const key of [
			"fills",
			"strokes",
			"effects",
			"relativeTransform",
			"absoluteTransform"
		]) if (Array.isArray(source[key])) addProperty(style, propertyName(key), source[key]);
		const font = node.fontName;
		if (font && typeof font.family === "string" && typeof font.style === "string") addProperty(style, "font", {
			family: font.family,
			style: font.style
		});
		const color = solidRunColor(source.fills);
		if (color) style.color = color;
		const runs = textRuns(node);
		if (runs) style.runs = runs;
		if (Object.keys(styleReferences).length) style.style_references = styleReferences;
		return style;
	}
	function warning(code, message) {
		return {
			code,
			message
		};
	}
	function treeHasPrototypeBehavior(nodes) {
		const pending = [...nodes];
		while (pending.length) {
			const node = pending.pop();
			if (node.visible === false) continue;
			if (node.prototypeStartNode || Array.isArray(node.reactions) && node.reactions.length) return true;
			pending.push(...(node.children ?? []).map((child) => child));
		}
		return false;
	}
	function selectionPlan(nodes) {
		const visibleRoots = nodes.filter((node) => node.visible !== false);
		if (!visibleRoots.length) throw new SelectionExportError("selection_empty");
		if (visibleRoots.length > MAX_TOP_LEVEL) throw new SelectionExportError("selection_too_large");
		const planned = [];
		const resources = [];
		const byReference = /* @__PURE__ */ new Map();
		const styleTokens = /* @__PURE__ */ new Map();
		const pending = visibleRoots.slice().reverse().map((node) => ({
			node,
			depth: 1,
			parent: null
		}));
		while (pending.length) {
			const { node, depth, parent } = pending.pop();
			if (node.visible === false) continue;
			const order = planned.length + 1;
			if (order > MAX_NODES || depth > MAX_DEPTH || node.name.length > MAX_STRING || typeof node.characters === "string" && node.characters.length > MAX_STRING) throw new SelectionExportError("selection_too_large");
			const styleReferences = {};
			for (const key of STYLE_REFERENCE_KEYS) {
				const raw = node[key];
				if (typeof raw !== "string" || raw.trim().length === 0) continue;
				let token = styleTokens.get(raw);
				if (!token) {
					token = `style-${styleTokens.size + 1}`;
					styleTokens.set(raw, token);
				}
				styleReferences[propertyName(key)] = token;
			}
			const classified = classifyVisualNode(node, {
				isRoot: parent === null,
				hasComplexTextRuns: textRuns(node) !== null,
				hasStyleReferences: Object.keys(styleReferences).length > 0
			});
			const hasNestedMask = parent !== null && (node.children ?? []).some((child) => child.isMask === true);
			const visualOnlyNestedMask = hasNestedMask && !subtreeContainsText(node);
			const nestedMaskReasons = hasNestedMask && !visualOnlyNestedMask ? classified.reasons.filter((reason) => reason !== "mask_composite") : classified.reasons;
			const capability = visualOnlyNestedMask && classified.strategy === "native" ? {
				strategy: "composite_png",
				mimeType: "image/png",
				reasons: ["mask_composite"]
			} : nestedMaskReasons.length === classified.reasons.length ? classified : nestedMaskReasons.length > 0 ? {
				strategy: "composite_png",
				mimeType: "image/png",
				reasons: nestedMaskReasons
			} : {
				strategy: "native",
				mimeType: null,
				reasons: []
			};
			const nineSlice = parseNineSliceAnnotation(node.name, bounds(node));
			const mime_type = capability.mimeType;
			const reference = capability.strategy === "skip" || capability.strategy === "native" ? null : capability.strategy === "image_asset" ? imageReference(node, order) : `${capability.strategy}:${order}`;
			let resource;
			if (reference && mime_type) {
				const identity = capability.strategy === "image_asset" ? `${mime_type}:${reference}` : `${mime_type}:${reference}:${order}`;
				resource = byReference.get(identity);
				if (!resource) {
					resource = {
						key: `asset-${resources.length + 1}`,
						mime_type,
						node
					};
					byReference.set(identity, resource);
					resources.push(resource);
				}
			}
			const current = {
				node,
				order,
				parent,
				resource,
				styleReferences,
				capability,
				nineSlice
			};
			planned.push(current);
			const children = capability.strategy === "composite_png" || capability.strategy === "vector_asset" ? [] : (node.children ?? []).filter((child) => child.visible !== false);
			if (pending.length + children.length > MAX_NODES) throw new SelectionExportError("selection_too_large");
			for (let index = children.length - 1; index >= 0; index -= 1) pending.push({
				node: children[index],
				depth: depth + 1,
				parent: current
			});
		}
		if (!planned.length) throw new SelectionExportError("selection_empty");
		return {
			nodes: planned,
			resources
		};
	}
	function serializeSelection(nodes) {
		const plan = selectionPlan(nodes);
		const roots = [];
		const warnings = [];
		if (treeHasPrototypeBehavior(nodes)) warnings.push(warning("unsupported_prototype", "原型连线不会导出"));
		const serialized = /* @__PURE__ */ new Map();
		const plannedChildren = /* @__PURE__ */ new Map();
		for (const item of plan.nodes) if (item.parent) plannedChildren.set(item.parent, [...plannedChildren.get(item.parent) ?? [], item]);
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
			const mask = item.parent === null ? nativeMaskDescriptor(node) : null;
			if (mask) {
				const childPlans = plannedChildren.get(item) ?? [];
				const maskPlan = childPlans[mask.maskIndex];
				const contentPlans = mask.contentIndexes.map((index) => childPlans[index]).filter((child) => Boolean(child));
				if (maskPlan && contentPlans.length === mask.contentIndexes.length) style.mask = {
					kind: mask.kind,
					maskNodeRef: `node-${maskPlan.order}`,
					contentNodeRefs: contentPlans.map((child) => `node-${child.order}`),
					effects: [],
					...mask.cornerRadii ? { cornerRadii: mask.cornerRadii } : {}
				};
			}
			const result = {
				id: `node-${item.order}`,
				name: node.name || "未命名图层",
				type: node.type,
				bounds: bounds(node),
				children: [],
				rotation: item.resource?.mime_type === "image/png" || (node.children?.length ?? 0) > 0 ? 0 : typeof node.rotation === "number" ? node.rotation : 0,
				visible: node.visible !== false && (item.parent ? serialized.get(item.parent).visible : true),
				opacity: typeof node.opacity === "number" ? node.opacity : 1,
				source_order: item.order - 1,
				...typeof node.characters === "string" ? { text: node.characters } : {},
				properties,
				style,
				resource_keys: item.resource ? [item.resource.key] : []
			};
			result.name = item.nineSlice.displayName || result.name;
			(item.parent ? serialized.get(item.parent).children : roots).push(result);
			serialized.set(item, result);
		}
		const uniqueWarnings = [...new Map(warnings.map((item) => [`${item.code}\0${item.message}`, item])).values()];
		return {
			version: 1,
			display_name: roots[0]?.name ?? "当前选择",
			top_level_nodes: roots,
			resources: plan.resources.map(({ key, mime_type }) => ({
				key,
				mime_type,
				size: 0
			})),
			warnings: uniqueWarnings
		};
	}
	function resourceLookup(nodes, manifest) {
		const declarations = new Map(selectionPlan(nodes).resources.map((resource) => [resource.key, resource.node]));
		return new Map(manifest.resources.flatMap((resource) => {
			const node = declarations.get(resource.key);
			return node ? [[resource.key, node]] : [];
		}));
	}
	function resourceClipFragments(manifest) {
		const fragments = /* @__PURE__ */ new Map();
		const pending = [...manifest.top_level_nodes];
		while (pending.length) {
			const node = pending.pop();
			pending.push(...node.children);
			const raw = node.properties?.clip_fragment_bounds;
			if (!raw || typeof raw !== "object" || Array.isArray(raw) || node.resource_keys.length !== 1) continue;
			const candidate = raw;
			if (![
				candidate.x,
				candidate.y,
				candidate.width,
				candidate.height
			].every((value) => typeof value === "number" && Number.isFinite(value)) || candidate.width <= 0 || candidate.height <= 0) continue;
			fragments.set(node.resource_keys[0], candidate);
		}
		return fragments;
	}
	function resourceLayoutBounds(manifest) {
		const result = /* @__PURE__ */ new Map();
		const pending = [...manifest.top_level_nodes];
		while (pending.length) {
			const node = pending.pop();
			pending.push(...node.children);
			const rawCanvas = node.properties?.resource_canvas_bounds;
			const resolved = (rawCanvas && typeof rawCanvas === "object" && !Array.isArray(rawCanvas) ? validBounds(rawCanvas) : null) ?? node.bounds;
			if (node.resource_keys.length === 1 && [
				resolved.x,
				resolved.y,
				resolved.width,
				resolved.height
			].every(Number.isFinite) && resolved.width > 0 && resolved.height > 0) result.set(node.resource_keys[0], resolved);
		}
		return result;
	}
	function preflightSelection(nodes) {
		try {
			const manifest = serializeSelection(nodes);
			const lookup = resourceLookup(nodes, manifest);
			const estimates = manifest.resources.map((resource) => {
				const node = lookup.get(resource.key);
				const area = node ? renderedBounds(node) : {
					width: 1,
					height: 1
				};
				return Math.max(1, area.width * area.height * 4);
			});
			const estimatedBytes = estimates.reduce((total, size) => total + size, 0);
			if (estimates.some((size) => size > MAX_RESOURCE_BYTES) || estimatedBytes > MAX_SESSION_BYTES) throw new SelectionExportError("selection_too_large");
			return {
				manifest,
				nodeCount: selectionPlan(nodes).nodes.length,
				assetCount: manifest.resources.length,
				estimatedBytes,
				warnings: manifest.warnings,
				sendable: true
			};
		} catch (error) {
			const safe = error instanceof SelectionExportError ? error : new SelectionExportError("selection_export_failed");
			return {
				manifest: null,
				nodeCount: 0,
				assetCount: 0,
				estimatedBytes: 0,
				warnings: [warning(safe.code, safe.message)],
				sendable: false
			};
		}
	}
	//#endregion
	//#region src/code.ts
	var ResourceCanvasError = class extends Error {
		safeCode;
		constructor(safeCode) {
			super(safeCode);
			this.safeCode = safeCode;
		}
	};
	var MAX_SCREENSHOT_DIMENSION = 4096;
	var MAX_SCREENSHOT_PIXELS = 16e6;
	var PNG_SIGNATURE = [
		137,
		80,
		78,
		71,
		13,
		10,
		26,
		10
	];
	function sameSelection(runtime, expected) {
		const current = runtime.currentPage?.selection ?? [];
		if (current.length !== expected.length) return false;
		const expectedMembers = new Set(expected);
		const currentMembers = new Set(current);
		if (expectedMembers.size !== expected.length || currentMembers.size !== current.length) return false;
		return [...expectedMembers].every((node) => currentMembers.has(node));
	}
	function nodeBounds(node) {
		const bounds = node.absoluteRenderBounds ?? node.absoluteBoundingBox;
		return bounds && [
			bounds.x,
			bounds.y,
			bounds.width,
			bounds.height
		].every(Number.isFinite) && bounds.width > 0 && bounds.height > 0 ? bounds : null;
	}
	function layoutBounds(node) {
		const value = node.absoluteBoundingBox;
		if (value && [
			value.x,
			value.y,
			value.width,
			value.height
		].every(Number.isFinite) && value.width > 0 && value.height > 0) return value;
		return nodeBounds(node);
	}
	function sameBounds(left, right) {
		return Boolean(left && [
			"x",
			"y",
			"width",
			"height"
		].every((key) => Math.abs(left[key] - right[key]) <= 1e-4));
	}
	function validTransform(value) {
		return Boolean(value && value.length === 2 && value[0].length === 3 && value[1].length === 3 && [...value[0], ...value[1]].every(Number.isFinite));
	}
	function rigidTransform(value) {
		if (!validTransform(value)) return false;
		const [a, c] = value[0];
		const [b, d] = value[1];
		const tolerance = 1e-4;
		const close = (left, right) => Math.abs(left - right) <= tolerance;
		return close(Math.hypot(a, b), 1) && close(Math.hypot(c, d), 1) && close(a * c + b * d, 0) && close(Math.abs(a * d - b * c), 1);
	}
	function selectedBounds(nodes) {
		let left = Infinity;
		let top = Infinity;
		let right = -Infinity;
		let bottom = -Infinity;
		for (const node of nodes) {
			const bounds = nodeBounds(node);
			if (!bounds) return null;
			left = Math.min(left, bounds.x);
			top = Math.min(top, bounds.y);
			right = Math.max(right, bounds.x + bounds.width);
			bottom = Math.max(bottom, bounds.y + bounds.height);
		}
		const result = {
			x: left,
			y: top,
			width: right - left,
			height: bottom - top
		};
		return nodes.length && Number.isFinite(result.width) && Number.isFinite(result.height) ? result : null;
	}
	function rootsOverlap(nodes) {
		const bounds = nodes.map(nodeBounds);
		for (let leftIndex = 0; leftIndex < bounds.length; leftIndex += 1) {
			const left = bounds[leftIndex];
			if (!left) return true;
			for (let rightIndex = leftIndex + 1; rightIndex < bounds.length; rightIndex += 1) {
				const right = bounds[rightIndex];
				if (!right) return true;
				const horizontal = Math.min(left.x + left.width, right.x + right.width) - Math.max(left.x, right.x);
				const vertical = Math.min(left.y + left.height, right.y + right.height) - Math.max(left.y, right.y);
				if (horizontal > 0 && vertical > 0) return true;
			}
		}
		return false;
	}
	function screenshotBoundsAllowed(bounds) {
		return bounds.width > 0 && bounds.height > 0 && bounds.width <= MAX_SCREENSHOT_DIMENSION && bounds.height <= MAX_SCREENSHOT_DIMENSION && bounds.width * bounds.height <= MAX_SCREENSHOT_PIXELS;
	}
	function resourceCanvasScale(bounds) {
		if (![
			bounds.x,
			bounds.y,
			bounds.width,
			bounds.height
		].every(Number.isFinite) || bounds.width <= 0 || bounds.height <= 0) return null;
		const dimensionScale = Math.min(1, MAX_SCREENSHOT_DIMENSION / bounds.width, MAX_SCREENSHOT_DIMENSION / bounds.height);
		const pixelScale = Math.min(1, Math.sqrt(MAX_SCREENSHOT_PIXELS / (bounds.width * bounds.height)));
		const scale = Math.min(dimensionScale, pixelScale);
		return Number.isFinite(scale) && scale > 0 ? scale : null;
	}
	function pngError(bytes) {
		if (bytes.length < 24 || PNG_SIGNATURE.some((value, index) => bytes[index] !== value)) return "selection_export_failed";
		const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
		if (view.getUint32(8) !== 13 || bytes[12] !== 73 || bytes[13] !== 72 || bytes[14] !== 68 || bytes[15] !== 82) return "selection_export_failed";
		const width = view.getUint32(16);
		const height = view.getUint32(20);
		if (!width || !height) return "selection_export_failed";
		if (width > MAX_SCREENSHOT_DIMENSION || height > MAX_SCREENSHOT_DIMENSION || width * height > MAX_SCREENSHOT_PIXELS) return "selection_too_large";
		return null;
	}
	function pngDimensions(bytes) {
		if (pngError(bytes)) return null;
		const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
		return {
			width: view.getUint32(16),
			height: view.getUint32(20)
		};
	}
	function repairMissingResourceBounds(nodes, resources, lookup) {
		return nodes.map((node) => {
			const children = repairMissingResourceBounds(node.children, resources, lookup);
			if (node.bounds.width > 0 && node.bounds.height > 0 || node.resource_keys.length !== 1) return {
				...node,
				children
			};
			const key = node.resource_keys[0];
			const dimensions = pngDimensions(resources.get(key) ?? /* @__PURE__ */ new Uint8Array());
			if (!dimensions) return {
				...node,
				children
			};
			const transform = lookup.get(key)?.absoluteTransform;
			const x = transform && Number.isFinite(transform[0][2]) ? transform[0][2] : 0;
			const y = transform && Number.isFinite(transform[1][2]) ? transform[1][2] : 0;
			return {
				...node,
				bounds: {
					x,
					y,
					...dimensions
				},
				children
			};
		});
	}
	async function exportClippedFragment(runtime, source, clip) {
		const cloneSource = source;
		const exportScale = resourceCanvasScale(clip);
		if (exportScale === null) throw new ResourceCanvasError("resource_canvas_invalid");
		if (!validTransform(source.absoluteTransform)) throw new ResourceCanvasError("resource_transform_unavailable");
		if (typeof cloneSource.clone !== "function") throw new ResourceCanvasError("resource_clone_unavailable");
		let frame;
		try {
			frame = runtime.createFrame();
		} catch {
			throw new ResourceCanvasError("resource_canvas_create_failed");
		}
		let clone = null;
		let attached = false;
		let bytes = null;
		let cleanupFailed = false;
		try {
			frame.name = "Temporary clipped export";
			frame.fills = [];
			frame.layoutMode = "NONE";
			frame.clipsContent = true;
			frame.x = clip.x;
			frame.y = clip.y;
			frame.resize(clip.width, clip.height);
			try {
				clone = cloneSource.clone();
			} catch {
				throw new ResourceCanvasError("resource_clone_failed");
			}
			if (!clone || typeof clone.remove !== "function" || !validTransform(clone.relativeTransform)) throw new ResourceCanvasError("resource_clone_invalid");
			clone.visible = true;
			try {
				frame.appendChild(clone);
			} catch {
				throw new ResourceCanvasError("resource_clone_attach_failed");
			}
			attached = true;
			const transform = source.absoluteTransform;
			try {
				clone.relativeTransform = [[
					transform[0][0],
					transform[0][1],
					transform[0][2] - clip.x
				], [
					transform[1][0],
					transform[1][1],
					transform[1][2] - clip.y
				]];
			} catch {
				throw new ResourceCanvasError("resource_clone_position_failed");
			}
			try {
				bytes = await frame.exportAsync({
					format: "PNG",
					constraint: {
						type: "SCALE",
						value: exportScale
					}
				});
			} catch {
				throw new ResourceCanvasError("resource_canvas_export_failed");
			}
			const error = pngError(bytes);
			if (error) throw new ResourceCanvasError(error === "selection_too_large" ? "resource_png_too_large" : "resource_png_invalid");
		} finally {
			let frameRemoved = false;
			try {
				frame.remove();
				frameRemoved = true;
			} catch {
				cleanupFailed = true;
			}
			if (clone && (!attached || !frameRemoved)) try {
				clone.remove();
			} catch {
				cleanupFailed = true;
			}
		}
		if (cleanupFailed || !bytes) throw new ResourceCanvasError("resource_canvas_cleanup_failed");
		return bytes;
	}
	function startPlugin(runtime) {
		runtime.showUI(__html__, {
			width: 640,
			height: 800
		});
		let prepared = null;
		const attempts = /* @__PURE__ */ new Map();
		let blockedCode = "selection_export_failed";
		let locatedSelection = null;
		const refresh = (type) => {
			const snapshot = runtime.currentPage ? [...runtime.currentPage.selection] : [];
			const preflight = preflightSelection(snapshot);
			prepared = preflight.manifest ? {
				manifest: preflight.manifest,
				lookup: resourceLookup(snapshot, preflight.manifest),
				roots: snapshot
			} : null;
			blockedCode = preflight.warnings[0]?.code ?? "selection_export_failed";
			const locateAttempt = type === "selection-changed" && locatedSelection && snapshot.length === 1 && snapshot[0]?.id === locatedSelection.nodeId ? locatedSelection.attempt : void 0;
			if (type === "selection-changed") locatedSelection = null;
			runtime.ui.postMessage({
				type,
				preflight,
				...locateAttempt ? { locateAttempt } : {}
			}, { origin: "*" });
		};
		runtime.ui.onmessage = (message, _props) => {
			if (!isUiToMainMessage(message)) return;
			if (message.type === "selection-preflight") {
				refresh("selection-preflight");
				return;
			}
			if (message.type === "locate-node") {
				runtime.getNodeByIdAsync?.(message.nodeId).then((node) => {
					if (!node || !runtime.currentPage) return;
					locatedSelection = {
						attempt: message.attempt,
						nodeId: message.nodeId
					};
					runtime.currentPage.selection = [node];
					runtime.viewport?.scrollAndZoomIntoView([node]);
				});
				return;
			}
			if (message.type === "create-review-area") {
				(async () => {
					const fail = () => runtime.ui.postMessage({
						type: "selection-error",
						attempt: message.attempt,
						code: "review_area_failed"
					}, { origin: "*" });
					if (!runtime.currentPage || !runtime.getNodeByIdAsync || !runtime.createRectangle || !runtime.createImage) {
						fail();
						return;
					}
					const liveBounds = selectedBounds(runtime.currentPage.selection);
					const source = await runtime.getNodeByIdAsync(message.nodeId);
					const sourceBounds = source ? nodeBounds(source) : null;
					if (!liveBounds || !source || !sourceBounds || typeof source.clone !== "function") {
						fail();
						return;
					}
					if (message.previewBytes.length > 2097152 || pngError(message.previewBytes) || new DataView(message.previewBytes.buffer, message.previewBytes.byteOffset, message.previewBytes.byteLength).getUint32(16) !== message.previewWidth || new DataView(message.previewBytes.buffer, message.previewBytes.byteOffset, message.previewBytes.byteLength).getUint32(20) !== message.previewHeight) {
						fail();
						return;
					}
					const existing = runtime.currentPage.children?.find((node) => {
						const candidate = node;
						return candidate.name === "FairyGUI 待审核" && candidate.type === "FRAME" && candidate.getPluginData?.("figma-to-fgui.review-area") === "v1";
					});
					let frame = null;
					try {
						frame = runtime.createFrame();
						frame.name = "FairyGUI 待审核（更新中）";
						const ownedFrame = frame;
						if (typeof ownedFrame.setPluginData !== "function") throw new Error("review ownership unavailable");
						ownedFrame.setPluginData("figma-to-fgui.review-area", "v1");
						frame.fills = [];
						frame.layoutMode = "NONE";
						frame.clipsContent = false;
						const padding = 24;
						const gap = 32;
						const targetWidth = sourceBounds.width;
						const targetHeight = sourceBounds.height;
						frame.x = liveBounds.x + liveBounds.width + 160;
						frame.y = liveBounds.y;
						frame.resize(padding * 2 + targetWidth * 2 + gap, padding * 2 + targetHeight);
						const clone = source.clone();
						if (!clone || typeof clone.remove !== "function" || typeof clone.x !== "number" || typeof clone.y !== "number") throw new Error("unsupported review clone");
						frame.appendChild(clone);
						clone.x = padding;
						clone.y = padding;
						const imageHash = runtime.createImage(message.previewBytes).hash;
						const generated = runtime.createRectangle();
						generated.resize(targetWidth, targetHeight);
						generated.x = padding + targetWidth + gap;
						generated.y = padding;
						generated.fills = [{
							type: "IMAGE",
							imageHash,
							scaleMode: "FIT"
						}];
						frame.appendChild(generated);
						existing?.remove?.();
						frame.name = "FairyGUI 待审核";
						runtime.viewport?.scrollAndZoomIntoView([frame]);
						runtime.ui.postMessage({
							type: "review-area-created",
							attempt: message.attempt
						}, { origin: "*" });
					} catch {
						try {
							frame?.remove();
						} catch {}
						fail();
					}
				})();
				return;
			}
			if (message.type === "selection-export") {
				const snapshot = prepared;
				if (snapshot) {
					attempts.set(message.attempt, snapshot);
					if (attempts.size > 8) attempts.delete(attempts.keys().next().value);
				}
				(async () => {
					if (!snapshot) {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: blockedCode
						}, { origin: "*" });
						return;
					}
					try {
						const resources = [];
						const fragments = resourceClipFragments(snapshot.manifest);
						const layouts = resourceLayoutBounds(snapshot.manifest);
						for await (const resource of exportDeclaredAssets(snapshot.manifest, snapshot.lookup, async (node, key, format) => {
							const clip = fragments.get(key);
							if (clip) {
								if (format !== "PNG") throw new Error("clipped fragments require PNG");
								return exportClippedFragment(runtime, node, clip);
							}
							const fullBounds = layouts.get(key) ?? layoutBounds(node);
							if (format === "PNG" && fullBounds && (node.visible === false || !sameBounds(nodeBounds(node), fullBounds))) return exportClippedFragment(runtime, node, fullBounds);
							try {
								return await node.exportAsync({ format });
							} catch {
								throw new ResourceCanvasError("resource_direct_export_failed");
							}
						})) resources.push(resource);
						const mimeTypes = new Map(resources.map((resource) => [resource.key, resource.mime_type]));
						const resourceBytes = new Map(resources.map((resource) => [resource.key, resource.bytes]));
						const manifest = {
							...snapshot.manifest,
							top_level_nodes: repairMissingResourceBounds(snapshot.manifest.top_level_nodes, resourceBytes, snapshot.lookup),
							resources: snapshot.manifest.resources.map((resource) => ({
								...resource,
								mime_type: mimeTypes.get(resource.key) ?? resource.mime_type
							}))
						};
						runtime.ui.postMessage({
							type: "selection-export",
							attempt: message.attempt,
							manifest,
							resources
						}, { origin: "*" });
					} catch (error) {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: error instanceof ResourceCanvasError ? error.safeCode : "selection_export_failed"
						}, { origin: "*" });
					}
				})();
				return;
			}
			if (message.type === "semantic-screenshot-export") {
				const snapshot = attempts.get(message.attempt);
				(async () => {
					if (!snapshot) {
						const code = (runtime.currentPage?.selection.length ?? 0) === 0 ? "selection_empty" : "selection_changed";
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code
						}, { origin: "*" });
						return;
					}
					if (!sameSelection(runtime, snapshot.roots)) {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: "selection_changed"
						}, { origin: "*" });
						return;
					}
					const bounds = selectedBounds(snapshot.roots);
					if (!bounds) {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: "selection_export_failed"
						}, { origin: "*" });
						return;
					}
					if (!screenshotBoundsAllowed(bounds)) {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: "selection_too_large"
						}, { origin: "*" });
						return;
					}
					if (snapshot.roots.length > 1 && rootsOverlap(snapshot.roots)) {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: "selection_export_failed"
						}, { origin: "*" });
						return;
					}
					const directNode = snapshot.roots.length === 1 ? snapshot.roots[0] : null;
					if (directNode && typeof directNode.exportAsync !== "function") {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: "selection_export_failed"
						}, { origin: "*" });
						return;
					}
					const isolatedRoots = directNode ? null : snapshot.roots.map((root) => {
						const source = root;
						return nodeBounds(root) && rigidTransform(root.absoluteTransform) && typeof source.clone === "function" ? {
							source,
							transform: root.absoluteTransform
						} : null;
					});
					if (isolatedRoots?.some((root) => !root)) {
						runtime.ui.postMessage({
							type: "selection-error",
							attempt: message.attempt,
							code: "selection_export_failed"
						}, { origin: "*" });
						return;
					}
					let frame = null;
					const clones = [];
					const attachedClones = /* @__PURE__ */ new Set();
					let screenshotBytes = null;
					let failureCode = null;
					try {
						if (!directNode) {
							frame = runtime.createFrame();
							frame.name = "Temporary isolated screenshot";
							frame.fills = [];
							frame.layoutMode = "NONE";
							frame.clipsContent = true;
							frame.x = bounds.x;
							frame.y = bounds.y;
							frame.resize(bounds.width, bounds.height);
							for (const isolated of isolatedRoots) {
								const { source, transform } = isolated;
								const clone = source.clone();
								if (!clone || typeof clone.remove !== "function") throw new Error("unsupported screenshot clone");
								clones.push(clone);
								if (typeof clone.x !== "number" || typeof clone.y !== "number" || !validTransform(clone.relativeTransform)) throw new Error("unsupported screenshot clone");
								frame.appendChild(clone);
								attachedClones.add(clone);
								clone.relativeTransform = [[
									transform[0][0],
									transform[0][1],
									transform[0][2] - bounds.x
								], [
									transform[1][0],
									transform[1][1],
									transform[1][2] - bounds.y
								]];
							}
						}
						if (!sameSelection(runtime, snapshot.roots)) failureCode = "selection_changed";
						else {
							const bytes = await (directNode ?? frame).exportAsync({
								format: "PNG",
								constraint: {
									type: "SCALE",
									value: 1
								}
							});
							if (!sameSelection(runtime, snapshot.roots)) failureCode = "selection_changed";
							else if (!bytes.length || bytes.length > 1024e3) failureCode = bytes.length ? "selection_too_large" : "selection_export_failed";
							else {
								failureCode = pngError(bytes);
								if (!failureCode) screenshotBytes = bytes;
							}
						}
					} catch {
						failureCode = "selection_export_failed";
					}
					let frameRemoved = false;
					let cleanupFailed = false;
					if (frame) try {
						frame.remove();
						frameRemoved = true;
					} catch {
						cleanupFailed = true;
					}
					for (let index = clones.length - 1; index >= 0; index -= 1) {
						const clone = clones[index];
						if (frameRemoved && attachedClones.has(clone)) continue;
						try {
							clone.remove();
						} catch {
							cleanupFailed = true;
						}
					}
					if (cleanupFailed) failureCode = "selection_export_failed";
					if (failureCode || !screenshotBytes) runtime.ui.postMessage({
						type: "selection-error",
						attempt: message.attempt,
						code: failureCode ?? "selection_export_failed"
					}, { origin: "*" });
					else runtime.ui.postMessage({
						type: "semantic-screenshot-export",
						attempt: message.attempt,
						mimeType: "image/png",
						bytes: screenshotBytes
					}, { origin: "*" });
				})();
			}
		};
		runtime.on("selectionchange", () => refresh("selection-changed"));
		refresh("selection-preflight");
	}
	if (typeof figma !== "undefined") startPlugin(figma);
	//#endregion
	exports.startPlugin = startPlugin;
	return exports;
})({});
