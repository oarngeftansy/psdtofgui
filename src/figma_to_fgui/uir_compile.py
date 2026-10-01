import hashlib
import json
import math
from collections.abc import Iterable, Mapping

from figma_to_fgui.component_mapping import ComponentMapping, ComponentMappingCatalog
from figma_to_fgui.data_policy import freeze_json_value, private_data_violations
from figma_to_fgui.models import Bounds, Diagnostic, NormalizedNode, Severity
from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    SemanticStatus,
    UIFontPolicy,
    UIRAsset,
    UIRComponentInstance,
    UIRConversion,
    UIRDocument,
    UIRGeometry,
    UIRMappingDecision,
    UIRNineSlice,
    UIRNode,
    UIRNodeSource,
    UIRSemantic,
    UIRSource,
    UIRText,
    UIRTextRun,
    UIRTextStyle,
)

_LAYOUT_FACTS = {
    "bottom_left_radius": "bottomLeftRadius",
    "bottom_right_radius": "bottomRightRadius",
    "constraints": "constraints",
    "counter_axis_align_items": "counterAxisAlignItems",
    "counter_axis_spacing": "counterAxisSpacing",
    "counter_axis_sizing_mode": "counterAxisSizingMode",
    "item_spacing": "itemSpacing",
    "layout_align": "layoutAlign",
    "layout_grow": "layoutGrow",
    "layout_mode": "layoutMode",
    "layout_wrap": "layoutWrap",
    "max_height": "maxHeight",
    "max_width": "maxWidth",
    "min_height": "minHeight",
    "min_width": "minWidth",
    "padding_bottom": "paddingBottom",
    "padding_left": "paddingLeft",
    "padding_right": "paddingRight",
    "padding_top": "paddingTop",
    "primary_axis_align_items": "primaryAxisAlignItems",
    "primary_axis_sizing_mode": "primaryAxisSizingMode",
    "top_left_radius": "topLeftRadius",
    "top_right_radius": "topRightRadius",
}
_VISUAL_STYLE_FACTS = {
    "absoluteTransform",
    "absolute_transform",
    "blendMode",
    "blend_mode",
    "effects",
    "fills",
    "mask",
    "relativeTransform",
    "relative_transform",
    "strokes",
    "styleReferences",
    "style_references",
}
MAX_NORMALIZED_TREE_DEPTH = 256


def _guard_normalized_tree_depth(roots: tuple[NormalizedNode, ...]) -> None:
    pending = [(root, 1) for root in roots]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_NORMALIZED_TREE_DEPTH:
            raise ValueError("normalized tree depth exceeds the supported limit")
        pending.extend((child, depth + 1) for child in node.children)


def _feature_value_present(value: object) -> bool:
    if value is None or value is False or value == 0:
        return False
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "false", "none", "no", "off"}
    if isinstance(value, Mapping):
        return any(_feature_value_present(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_feature_value_present(item) for item in value)
    return True


def unsupported_behavior_reason(node: NormalizedNode) -> str | None:
    selection_warning_codes = node.properties.get("selection_warning_codes", ())
    if isinstance(selection_warning_codes, (list, tuple)):
        if "unsupported_prototype" in selection_warning_codes:
            return "interaction_semantics_out_of_scope"
        if "unsupported_video" in selection_warning_codes:
            return "video_content_out_of_scope"
    policies = (
        (
            frozenset({"interaction", "interactions", "trigger", "triggers"}),
            "interaction_semantics_out_of_scope",
        ),
        (
            frozenset({"list_data", "list_items", "listData", "listItems"}),
            "list_semantics_out_of_scope",
        ),
        (
            frozenset(
                {"controller", "controller_data", "controllers", "controllerData"}
            ),
            "controller_semantics_out_of_scope",
        ),
        (
            frozenset(
                {
                    "gear_display",
                    "gear_frame",
                    "gear_text",
                    "gear",
                    "gears",
                    "gearDisplay",
                    "gearFrame",
                    "gearText",
                }
            ),
            "gear_semantics_out_of_scope",
        ),
        (frozenset({"explicit_mask"}), "mask_semantics_out_of_scope"),
    )
    for keys, reason in policies:
        if any(
            key in facts and _feature_value_present(facts[key])
            for facts in (node.properties, node.raw_style)
            for key in keys
        ):
            return reason
    return None


def _safe_public_fact(value: object) -> object | None:
    try:
        frozen = freeze_json_value(value)
    except (TypeError, ValueError):
        return None
    return None if private_data_violations(frozen) else frozen


def _has_rejected_public_fact(node: NormalizedNode) -> bool:
    property_keys = {
        *_LAYOUT_FACTS,
        "clips_content",
        "corner_radius",
        "interactions",
    }
    return any(
        value is not None and _safe_public_fact(value) is None
        for facts, keys in (
            (node.properties, property_keys),
            (node.raw_style, _VISUAL_STYLE_FACTS),
        )
        for key in keys
        if key in facts
        for value in (facts[key],)
    )


def _layout_facts(node: NormalizedNode) -> dict[str, object]:
    facts: dict[str, object] = {
        "sourceOrder": node.source_order,
        "visible": node.visible,
    }
    for source_key, target_key in _LAYOUT_FACTS.items():
        if source_key not in node.properties:
            continue
        value = _safe_public_fact(node.properties[source_key])
        if value is not None:
            facts[target_key] = value
    return facts


def _visual_facts(
    node: NormalizedNode,
    node_ref_map: Mapping[str, str] | None = None,
) -> dict[str, object]:
    facts: dict[str, object] = {}
    for key in sorted(_VISUAL_STYLE_FACTS):
        if key not in node.raw_style:
            continue
        value = _safe_public_fact(node.raw_style[key])
        if value is not None:
            facts[key] = value
    for source_key, target_key in {
        "clips_content": "clipsContent",
        "corner_radius": "cornerRadius",
        "top_left_radius": "topLeftRadius",
        "top_right_radius": "topRightRadius",
        "bottom_left_radius": "bottomLeftRadius",
        "bottom_right_radius": "bottomRightRadius",
        "stroke_weight": "strokeWeight",
    }.items():
        if source_key in node.properties:
            value = _safe_public_fact(node.properties[source_key])
            if value is not None:
                facts[target_key] = value
    mask = facts.get("mask")
    if isinstance(mask, Mapping) and node_ref_map:
        translated = dict(mask)
        mask_ref = translated.get("maskNodeRef")
        if isinstance(mask_ref, str) and mask_ref in node_ref_map:
            translated["maskNodeRef"] = node_ref_map[mask_ref]
        content_refs = translated.get("contentNodeRefs")
        if isinstance(content_refs, (list, tuple)):
            translated["contentNodeRefs"] = tuple(
                node_ref_map.get(ref, ref) if isinstance(ref, str) else ref
                for ref in content_refs
            )
        safe_root_ref = translated.get("safeRasterRootRef")
        if isinstance(safe_root_ref, str) and safe_root_ref in node_ref_map:
            translated["safeRasterRootRef"] = node_ref_map[safe_root_ref]
        facts["mask"] = translated
    return facts


def _local_transform(
    node: NormalizedNode,
) -> tuple[float, float, float, float, float, float] | None:
    raw = node.raw_style.get(
        "relativeTransform", node.raw_style.get("relative_transform")
    )
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    rows = tuple(raw)
    if not all(
        isinstance(row, (list, tuple)) and len(row) == 3 for row in rows
    ):
        return None
    values = tuple(value for row in rows for value in row)
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in values
    ):
        return None
    return (
        float(values[0]),
        float(values[3]),
        float(values[1]),
        float(values[4]),
        float(values[2]),
        float(values[5]),
    )


def _interaction_facts(node: NormalizedNode) -> tuple[dict[str, object], ...]:
    raw = node.properties.get("interactions")
    candidates = raw if isinstance(raw, (list, tuple)) else (raw,)
    facts: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        safe = _safe_public_fact(candidate)
        if isinstance(safe, Mapping):
            facts.append(dict(safe))
    return tuple(facts)


def _solid_paint_color(value: object) -> str | None:
    visible = (
        tuple(
            paint
            for paint in value
            if isinstance(paint, Mapping) and paint.get("visible", True) is not False
        )
        if isinstance(value, (list, tuple))
        else ()
    )
    if len(visible) != 1 or visible[0].get("type") != "SOLID":
        return None
    paint = visible[0]
    color = paint.get("color")
    if not isinstance(color, Mapping):
        return None
    channels = tuple(
        color.get(key, default)
        for key, default in (("r", 0), ("g", 0), ("b", 0), ("a", 1))
    )
    paint_opacity = paint.get("opacity", 1)
    if not all(
        isinstance(channel, (int, float)) and math.isfinite(channel)
        for channel in (*channels, paint_opacity)
    ):
        return None
    red, green, blue = (
        max(0, min(255, round(float(channel) * 255)))
        for channel in channels[:3]
    )
    alpha = max(
        0,
        min(255, round(float(channels[3]) * float(paint_opacity) * 255)),
    )
    return f"#{red:02x}{green:02x}{blue:02x}{alpha:02x}"


def _text_style(raw: Mapping[str, object], properties: Mapping[str, object]) -> UIRTextStyle:
    raw_font_candidates = raw.get("fontCandidates", ())
    candidates: tuple[str, ...] = ()
    if isinstance(raw_font_candidates, (list, tuple)) and all(
        isinstance(item, str) for item in raw_font_candidates
    ):
        candidates = tuple(dict.fromkeys(raw_font_candidates))
    rest_family = raw.get("fontFamily")
    rest_postscript = raw.get("fontPostScriptName")
    rest_weight = raw.get("fontWeight")
    if isinstance(rest_family, str) and rest_family:
        derived_rest: tuple[str, ...] = (
            (rest_postscript, rest_family)
            if isinstance(rest_postscript, str) and rest_postscript
            else (
                (f"{rest_family} {round(rest_weight)}", rest_family)
                if isinstance(rest_weight, (int, float)) and rest_weight != 400
                else (rest_family,)
            )
        )
        candidates = tuple(dict.fromkeys((*candidates, *derived_rest)))
    font = raw.get("font")
    if isinstance(font, Mapping):
        family = font.get("family")
        style = font.get("style")
        if isinstance(family, str) and family:
            derived = (
                (f"{family} {style}", family)
                if isinstance(style, str) and style
                else (family,)
            )
            candidates = tuple(dict.fromkeys((*candidates, *derived)))

    def fact(*names: str) -> object | None:
        for name in names:
            if name in raw:
                return raw[name]
            if name in properties:
                return properties[name]
        return None

    font_size = fact("fontSize", "font_size")
    raw_line_height = fact("lineHeight", "line_height", "lineHeightPx")
    line_height: float | None = None
    if isinstance(raw_line_height, Mapping):
        if str(raw_line_height.get("unit", "")).upper() == "PIXELS":
            value = raw_line_height.get("value")
            if isinstance(value, (int, float)) and value > 0:
                line_height = float(value)
    elif isinstance(raw_line_height, (int, float)) and raw_line_height > 0:
        line_height = float(raw_line_height)
    color = fact("color")
    if not isinstance(color, str):
        color = _solid_paint_color(raw.get("fills"))
    stroke_color = _solid_paint_color(raw.get("strokes"))
    stroke_size = fact("strokeWeight", "stroke_weight")
    horizontal = fact("textAlignHorizontal", "text_align_horizontal")
    vertical = fact("textAlignVertical", "text_align_vertical")
    auto_resize = fact("textAutoResize", "text_auto_resize")
    return UIRTextStyle(
        fontCandidates=candidates,
        fontSize=font_size if isinstance(font_size, (int, float)) else None,
        lineHeight=line_height,
        color=color if isinstance(color, str) else None,
        strokeColor=stroke_color,
        strokeSize=(
            stroke_size
            if isinstance(stroke_size, (int, float)) and stroke_size > 0
            else None
        ),
        textAlignHorizontal=horizontal if isinstance(horizontal, str) else None,
        textAlignVertical=vertical if isinstance(vertical, str) else None,
        textAutoResize=auto_resize.upper() if isinstance(auto_resize, str) else None,
    )


def unsupported_base_text_features(
    raw_style: Mapping[str, object], properties: Mapping[str, object] | None = None
) -> tuple[str, ...]:
    """Return base text semantics that the generic plan cannot express yet."""
    unsupported: list[str] = []
    properties = properties or {}

    def fact(camel: str, snake: str) -> object:
        return raw_style.get(camel, properties.get(snake))

    decoration = fact("textDecoration", "text_decoration")
    if isinstance(decoration, str) and decoration.upper() != "NONE":
        unsupported.append("text_decoration")
    text_case = fact("textCase", "text_case")
    if isinstance(text_case, str) and text_case.upper() != "ORIGINAL":
        unsupported.append("text_case")
    letter_spacing = fact("letterSpacing", "letter_spacing")
    if isinstance(letter_spacing, Mapping):
        letter_spacing = letter_spacing.get("value")
    if isinstance(letter_spacing, (int, float)) and letter_spacing != 0:
        unsupported.append("letter_spacing")
    line_height = fact("lineHeight", "line_height")
    font_size = fact("fontSize", "font_size")
    if isinstance(line_height, Mapping):
        unit = str(line_height.get("unit", "")).upper()
        value = line_height.get("value")
        if unit not in {"", "AUTO", "PIXELS"} or (
            unit == "PIXELS"
            and (
                not isinstance(value, (int, float))
                or not isinstance(font_size, (int, float))
            )
        ):
            unsupported.append("line_height")
    if "lineHeightPx" in raw_style and not isinstance(font_size, (int, float)):
        unsupported.append("line_height")
    if any(key in raw_style for key in ("lineHeightPercent", "lineHeightPercentFontSize")):
        unsupported.append("line_height")
    text_auto_resize = fact("textAutoResize", "text_auto_resize")
    if isinstance(text_auto_resize, str) and text_auto_resize.upper() not in {
        "NONE",
        "HEIGHT",
        "WIDTH_AND_HEIGHT",
        "TRUNCATE",
    }:
        unsupported.append("text_auto_resize")
    for key, feature in (
        ("paragraphIndent", "paragraph_indent"),
        ("paragraphSpacing", "paragraph_spacing"),
        ("listSpacing", "list_spacing"),
    ):
        value = raw_style.get(key)
        if isinstance(value, (int, float)) and value != 0:
            unsupported.append(feature)
    return tuple(dict.fromkeys(unsupported))


def unsupported_text_visual_features(
    raw_style: Mapping[str, object], properties: Mapping[str, object]
) -> tuple[str, ...]:
    """Return text paint/effect facts not represented by TextPlan v1."""
    unsupported: list[str] = []
    effects = raw_style.get("effects")
    if isinstance(effects, (list, tuple)) and any(
        isinstance(effect, Mapping) and effect.get("visible") is not False
        for effect in effects
    ):
        unsupported.append("text_effect")
    blend = raw_style.get("blendMode", raw_style.get("blend_mode"))
    if isinstance(blend, str) and blend.upper() not in {"NORMAL", "PASS_THROUGH"}:
        unsupported.append("text_blend_mode")
    fills = raw_style.get("fills")
    if isinstance(fills, (list, tuple)) and fills and _solid_paint_color(fills) is None:
        unsupported.append("text_fill")
    strokes = raw_style.get("strokes")
    if isinstance(strokes, (list, tuple)) and strokes:
        stroke_weight = properties.get("stroke_weight", raw_style.get("strokeWeight"))
        if _solid_paint_color(strokes) is None or not (
            isinstance(stroke_weight, (int, float)) and stroke_weight > 0
        ):
            unsupported.append("text_stroke")
    return tuple(unsupported)


_EXTRACTABLE_RUN_STYLE_KEYS = frozenset(
    {
        "color",
        "fills",
        "font",
        "fontCandidates",
        "fontFamily",
        "fontPostScriptName",
        "fontSize",
        "fontWeight",
        "font_weight",
        "strokeWeight",
        "stroke_weight",
        "strokes",
        "textAlignHorizontal",
        "textAlignVertical",
        "text_align_horizontal",
        "text_align_vertical",
    }
)


def _run_unsupported_style_features(
    raw_style: Mapping[str, object], base_style: Mapping[str, object]
) -> tuple[str, ...]:
    """Return stable unrepresented facts from a parsed text run style."""
    unsupported: list[str] = []
    has_run_weight = "fontWeight" in raw_style or "font_weight" in raw_style
    run_weight = raw_style.get("fontWeight", raw_style.get("font_weight"))
    base_weight = base_style.get("fontWeight", base_style.get("font_weight"))
    if has_run_weight and run_weight != base_weight:
        unsupported.append("fontWeight")
    if set(raw_style) - _EXTRACTABLE_RUN_STYLE_KEYS:
        unsupported.append("unrecognized_run_style")
    return tuple(unsupported)


def _text_payload(node: NormalizedNode) -> UIRText | None:
    if node.type != "TEXT":
        return None
    content = node.text or ""
    style = _text_style(node.raw_style, node.properties)
    unsupported_base = unsupported_base_text_features(node.raw_style, node.properties)
    runs: tuple[UIRTextRun, ...] = ()
    raw_runs = node.raw_style.get("runs")
    if raw_runs is not None:
        parsed: list[UIRTextRun] = []
        valid = isinstance(raw_runs, (list, tuple))
        if valid:
            for raw_run in raw_runs:
                if not isinstance(raw_run, Mapping) or not isinstance(
                    raw_run.get("content"), str
                ):
                    valid = False
                    break
                raw_run_style = raw_run.get("style", {})
                if not isinstance(raw_run_style, Mapping):
                    valid = False
                    break
                unsupported = tuple(
                    str(item)
                    for item in raw_run.get("unsupportedFeatures", ())
                    if isinstance(item, str)
                )
                unsupported = tuple(
                    dict.fromkeys(
                        (*unsupported, *_run_unsupported_style_features(raw_run_style, node.raw_style))
                    )
                )
                parsed.append(
                    UIRTextRun(
                        content=raw_run["content"],
                        style=_text_style(raw_run_style, {}),
                        unsupportedFeatures=unsupported,
                    )
                )
        if valid and "".join(run.content for run in parsed) == content:
            if unsupported_base and parsed:
                first = parsed[0]
                parsed[0] = first.model_copy(
                    update={
                        "unsupported_features": tuple(
                            dict.fromkeys((*first.unsupported_features, *unsupported_base))
                        )
                    }
                )
            runs = tuple(parsed)
        else:
            runs = (
                UIRTextRun(
                    content=content,
                    style=style,
                    unsupportedFeatures=("normalized_text_runs_invalid",),
                ),
            )
    else:
        if unsupported_base:
            runs = (
                UIRTextRun(
                    content=content,
                    style=style,
                    unsupportedFeatures=unsupported_base,
                ),
            )
    raw_policy = node.raw_style.get("fontPolicy", {})
    if isinstance(raw_policy, Mapping):
        allow_fallback = raw_policy.get("allowFallback", True)
        resolved_font = raw_policy.get("resolvedFont")
        policy = UIFontPolicy(
            allowFallback=(
                allow_fallback if isinstance(allow_fallback, bool) else False
            ),
            resolvedFont=resolved_font if isinstance(resolved_font, str) else None,
        )
    else:
        policy = UIFontPolicy(allowFallback=False)
    return UIRText(
        content=content,
        style=style,
        runs=runs,
        baseUnsupportedFeatures=unsupported_base,
        fontPolicy=policy,
    )


def _component_variants(node: NormalizedNode) -> dict[str, str]:
    for key in ("variant_properties", "component_properties", "componentProperties"):
        value = node.properties.get(key)
        if isinstance(value, Mapping):
            return {
                str(name): str(item)
                for name, item in value.items()
                if isinstance(item, (str, int, float, bool))
            }
    return {}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}:{_digest(value)[:24]}"


def uir_asset_id(
    *,
    logical_id: str,
    mime_type: str,
    sha256: str | None,
    width: int | None,
    height: int | None,
    nine_slice: tuple[int, int, int, int] | None,
    export_format: str,
) -> str:
    """Return the canonical UIR identity for one exported source asset."""
    return _stable_id(
        "asset",
        {
            "exportFormat": export_format,
            "height": height,
            "logicalId": logical_id,
            "mimeType": mime_type,
            "nineSlice": (
                None
                if nine_slice is None
                else {
                    "x": nine_slice[0],
                    "y": nine_slice[1],
                    "width": nine_slice[2],
                    "height": nine_slice[3],
                }
            ),
            "sha256": sha256,
            "width": width,
        },
    )


def _resource_canvas_bounds(node: NormalizedNode) -> Bounds | None:
    raw = node.properties.get("resource_canvas_bounds")
    if not isinstance(raw, Mapping):
        return None
    values = tuple(raw.get(key) for key in ("x", "y", "width", "height"))
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        return None
    if any(v is None for v in values):
        return None
    if any(v is None for v in values):
        return None
    x, y, width, height = (float(v) for v in values)  # type: ignore[arg-type]
    if width <= 0 or height <= 0:
        return None
    return Bounds(x=x, y=y, width=width, height=height)


def _local_bounds(node: NormalizedNode, parent: NormalizedNode | None) -> Bounds:
    resolved = _resource_canvas_bounds(node) or node.bounds
    if parent is None:
        return Bounds(x=0, y=0, width=resolved.width, height=resolved.height)
    return Bounds(
        x=resolved.x - parent.bounds.x,
        y=resolved.y - parent.bounds.y,
        width=resolved.width,
        height=resolved.height,
    )


def _source_fingerprint(node: NormalizedNode) -> str:
    text = _text_payload(node)
    return _digest(
        {
            "id": node.id,
            "name": node.name,
            "type": node.type,
            "bounds": node.bounds.model_dump(mode="json"),
            "text": node.text,
            "rotation": node.rotation,
            "opacity": node.opacity,
            "visible": node.visible,
            "sourceOrder": node.source_order,
            "layout": _layout_facts(node),
            "visual": _visual_facts(node),
            "interactions": _interaction_facts(node),
            "textFacts": (
                None
                if text is None
                else text.model_dump(mode="json", by_alias=True, exclude_none=True)
            ),
            "componentVariants": _component_variants(node),
            "conversionFacts": {
                "exportStrategy": _safe_public_fact(
                    node.properties.get("export_strategy")
                ),
                "rasterReasons": _safe_public_fact(
                    node.properties.get("raster_reasons")
                ),
                **(
                    {"unsupportedBehavior": unsupported_behavior}
                    if (unsupported_behavior := unsupported_behavior_reason(node))
                    is not None
                    else {}
                ),
            },
            "resourceRefs": [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in node.resource_refs
            ],
            "children": [child.id for child in node.children],
        }
    )


def _derived_asset(node: NormalizedNode) -> UIRAsset | None:
    if len(node.resource_refs) != 1:
        return None
    for reference in node.resource_refs:
        logical_id = reference.asset
        mime_type = reference.mime_type
        asset_id = uir_asset_id(
            logical_id=logical_id,
            mime_type=mime_type,
            sha256=reference.sha256,
            width=reference.width,
            height=reference.height,
            nine_slice=(
                None
                if reference.nine_slice is None
                else (
                    reference.nine_slice.x,
                    reference.nine_slice.y,
                    reference.nine_slice.width,
                    reference.nine_slice.height,
                )
            ),
            export_format=reference.export_format,
        )
        return UIRAsset(
            id=asset_id,
            logicalId=logical_id,
            mimeType=mime_type,
            sha256=reference.sha256,
            width=reference.width,
            height=reference.height,
            nineSlice=(
                None
                if reference.nine_slice is None
                else UIRNineSlice(
                    x=reference.nine_slice.x,
                    y=reference.nine_slice.y,
                    width=reference.nine_slice.width,
                    height=reference.nine_slice.height,
                )
            ),
            exportFormat=reference.export_format,
        )
    return None


def _conversion(node: NormalizedNode, asset_ref: str | None = None) -> UIRConversion:
    if _has_rejected_public_fact(node):
        return UIRConversion(
            mode=ConversionMode.UNSUPPORTED,
            reasons=("private_or_invalid_fact",),
        )
    unsupported_behavior = unsupported_behavior_reason(node)
    if unsupported_behavior is not None:
        return UIRConversion(
            mode=ConversionMode.UNSUPPORTED,
            reasons=(unsupported_behavior,),
        )
    if len(node.resource_refs) > 1:
        return UIRConversion(
            mode=ConversionMode.UNSUPPORTED,
            reasons=("multiple_resource_references",),
        )
    if node.properties.get("export_strategy") == "composite_png":
        return UIRConversion(
            mode=ConversionMode.RASTER_FALLBACK,
            reasons=tuple(node.properties.get("raster_reasons", ()))
            or ("composite_visual",),
            assetRef=asset_ref,
        )
    return UIRConversion(mode=ConversionMode.NATIVE, assetRef=asset_ref)


def _mappings_for(
    node: NormalizedNode, catalog: ComponentMappingCatalog
) -> tuple[ComponentMapping, ...]:
    return tuple(
        item
        for item in catalog.components
        if node.id in item.figma.node_ids or node.name in item.figma.names
    )


def _mapping_decision(
    node: NormalizedNode,
    node_ref: str,
    mapping: ComponentMapping,
    selection_id: str,
    asset_ref: str | None,
) -> tuple[UIRMappingDecision, UIRSemantic, UIRConversion]:
    if mapping.status == "candidate":
        raise ValueError("component mapping catalog must be validated")
    decision_id = _stable_id(
        "decision",
        {
            "candidateKey": mapping.key,
            "selectionId": selection_id,
            "sourceNodeId": node.id,
        },
    )
    evidence = (
        "exact_node_id" if node.id in mapping.figma.node_ids else "exact_name",
        *((mapping.reason,) if mapping.reason else ()),
    )
    confidence = 1.0 if mapping.status == "verified" else 0.0
    decision = UIRMappingDecision(
        id=decision_id,
        nodeRef=node_ref,
        candidateKey=mapping.key,
        status=MappingStatus(mapping.status),
        evidence=evidence,
        confidence=confidence,
        ruleSource="component-mapping:" + ",".join(mapping.source),
    )
    if mapping.status == "verified":
        return (
            decision,
            UIRSemantic(
                name=mapping.key,
                role="component",
                status=SemanticStatus.CONFIRMED,
                decisionRef=decision_id,
            ),
            UIRConversion(mode=ConversionMode.COMPONENT_REFERENCE),
        )
    if mapping.status == "missing":
        return (
            decision,
            UIRSemantic(
                name=mapping.key,
                role="component",
                status=SemanticStatus.FALLBACK,
                decisionRef=decision_id,
            ),
            UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("component_mapping_missing",),
                assetRef=asset_ref,
            ),
        )
    return (
        decision,
        UIRSemantic(
            name=mapping.key,
            role="component",
            status=SemanticStatus.CANDIDATE,
            decisionRef=decision_id,
        ),
        UIRConversion(
            mode=ConversionMode.UNSUPPORTED,
            reasons=("component_mapping_conflict",),
        ),
    )


def _compile_node(
    node: NormalizedNode,
    *,
    parent: NormalizedNode | None,
    parent_id: str | None,
    path: tuple[int, ...],
    selection_id: str,
    nodes: dict[str, UIRNode],
    decisions: dict[str, UIRMappingDecision],
    assets: dict[str, UIRAsset],
    mapping_catalog: ComponentMappingCatalog | None,
) -> str:
    node_id = _stable_id(
        "node", {"selectionId": selection_id, "sourceNodeId": node.id, "path": path}
    )
    child_ids = tuple(
        _compile_node(
            child,
            parent=node,
            parent_id=node_id,
            path=(*path, index),
            selection_id=selection_id,
            nodes=nodes,
            decisions=decisions,
            assets=assets,
            mapping_catalog=mapping_catalog,
        )
        for index, child in enumerate(node.children)
    )
    text = _text_payload(node)
    component = None
    if node.type == "INSTANCE":
        component = UIRComponentInstance(variantProperties=_component_variants(node))
    semantic = UIRSemantic(status=SemanticStatus.CANDIDATE)
    derived_asset = _derived_asset(node)
    if derived_asset is not None:
        assets[derived_asset.id] = derived_asset
    conversion = _conversion(
        node, None if derived_asset is None else derived_asset.id
    )
    if (
        node.type == "INSTANCE"
        and mapping_catalog is not None
        and conversion.mode != ConversionMode.UNSUPPORTED
    ):
        mappings = _mappings_for(node, mapping_catalog)
        if len(mappings) == 1:
            decision, semantic, conversion = _mapping_decision(
                node,
                node_id,
                mappings[0],
                selection_id,
                None if derived_asset is None else derived_asset.id,
            )
            decisions[decision.id] = decision
        elif len(mappings) > 1:
            semantic = UIRSemantic(
                role="component",
                status=SemanticStatus.CANDIDATE,
            )
            conversion = UIRConversion(
                mode=ConversionMode.UNSUPPORTED,
                reasons=("component_mapping_ambiguous",),
            )
    nodes[node_id] = UIRNode(
        id=node_id,
        source=UIRNodeSource(
            nodeId=node.id,
            type=node.type,
            name=node.name,
            fingerprint=_source_fingerprint(node),
        ),
        semantic=semantic,
        parentId=parent_id,
        children=child_ids,
        zIndex=path[-1] if path else 0,
        geometry=UIRGeometry(
            resolvedBounds=_local_bounds(node, parent),
            localTransform=_local_transform(node),
            rotation=node.rotation,
            opacity=node.opacity,
        ),
        layout=_layout_facts(node),
        visual=_visual_facts(
            node,
            {
                node.id: node_id,
                **{
                    child.id: child_id
                    for child, child_id in zip(
                        node.children, child_ids, strict=True
                    )
                },
            },
        ),
        interactions=_interaction_facts(node),
        text=text,
        component=component,
        conversion=conversion,
    )
    return node_id


def compile_uir(
    roots: tuple[NormalizedNode, ...],
    *,
    source_revision: str,
    selection_id: str,
    compiler_version: str = "uir-v1",
    assets: Iterable[UIRAsset] = (),
    mapping_catalog: ComponentMappingCatalog | None = None,
) -> UIRDocument:
    _guard_normalized_tree_depth(roots)
    if mapping_catalog is not None and any(
        item.status == "candidate" for item in mapping_catalog.components
    ):
        raise ValueError("component mapping catalog must be validated")
    nodes: dict[str, UIRNode] = {}
    decisions: dict[str, UIRMappingDecision] = {}
    asset_items = tuple(assets)
    compiled_assets = {item.id: item for item in asset_items}
    root_ids = tuple(
        _compile_node(
            root,
            parent=None,
            parent_id=None,
            path=(index,),
            selection_id=selection_id,
            nodes=nodes,
            decisions=decisions,
            assets=compiled_assets,
            mapping_catalog=mapping_catalog,
        )
        for index, root in enumerate(roots)
    )
    document_id = _stable_id(
        "uir",
        {
            "compilerVersion": compiler_version,
            "selectionId": selection_id,
            "sourceRevision": source_revision,
        },
    )
    selection_diagnostics = tuple(
        Diagnostic(
            code=f"uir.selection.{code}",
            severity=Severity.WARNING,
            message="Figma selection export reported a fidelity warning.",
            node_id=root_id,
            rule_id=f"uir.selection.{code}",
            rule_version=1,
            evidence=(f"selection.warning={code}",),
            suggested_action="review_selection_warning",
            blocks_binding=False,
        )
        for root, root_id in zip(roots, root_ids, strict=True)
        for code in root.properties.get("selection_warning_codes", ())
        if isinstance(code, str)
    )
    privacy_diagnostics = tuple(
        Diagnostic(
            code="uir.private_data_forbidden",
            severity=Severity.ERROR,
            message="Figma metadata contains private or invalid opaque data.",
            node_id=node.id,
            rule_id="uir.private_data_forbidden",
            rule_version=1,
            evidence=("source.public_metadata=false",),
            suggested_action="remove_private_source_metadata",
            blocks_binding=True,
        )
        for node in nodes.values()
        if "private_or_invalid_fact" in node.conversion.reasons
    )
    return UIRDocument(
        documentId=document_id,
        compilerVersion=compiler_version,
        source=UIRSource(revision=source_revision, selectionId=selection_id),
        roots=root_ids,
        nodes=nodes,
        assets=compiled_assets,
        mappingDecisions=decisions,
        diagnostics=(*selection_diagnostics, *privacy_diagnostics),
    )
