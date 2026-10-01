from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from psd_tools import PSDImage


class PsdIntakeError(ValueError):
    """A PSD cannot be admitted into the HIFI replacement workflow."""


def effective_layer_bounds(layer: Any, document_width: int, document_height: int) -> tuple[int, int, int, int]:
    """Recover document-space bounds when Photoshop serialized an empty shape bbox.

    Some polygon/star shape layers keep a valid normalized vector mask but
    write ``(0, -19, 0, -19)`` into the ordinary layer rectangle. Treating
    that record as empty drops visible pixels from ownership and mapping.
    """
    bounds = (
        int(getattr(layer, "left", 0)),
        int(getattr(layer, "top", 0)),
        int(getattr(layer, "right", 0)),
        int(getattr(layer, "bottom", 0)),
    )
    if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
        return bounds
    if getattr(layer, "kind", None) != "shape":
        return bounds
    mask = getattr(layer, "vector_mask", None)
    mask_bounds = getattr(mask, "bbox", None)
    if (
        mask is None
        or getattr(mask, "inverted", False)
        or not isinstance(mask_bounds, (tuple, list))
        or len(mask_bounds) != 4
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in mask_bounds)
    ):
        return bounds
    left, top, right, bottom = (float(value) for value in mask_bounds)
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        return bounds
    return (
        math.floor(left * document_width),
        math.floor(top * document_height),
        math.ceil(right * document_width),
        math.ceil(bottom * document_height),
    )


@dataclass(frozen=True)
class PsdInspection:
    source_name: str
    byte_size: int
    sha256: str
    width: int
    height: int
    depth: int
    color_mode: str
    layer_count: int
    kind_counts: dict[str, int]
    text_layer_count: int
    smart_object_count: int
    adjustment_layer_count: int
    effect_layer_count: int
    blocking_issues: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PsdTextRun:
    start: int
    length: int
    font_name: str | None
    font_size: float | None
    faux_bold: bool
    faux_italic: bool
    leading: float | None
    tracking: float | None
    fill_rgba: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class PsdTextStyle:
    runs: tuple[PsdTextRun, ...]
    transform: tuple[float, float, float, float, float, float]
    paragraph_justification: int | None
    anti_alias: int | None


@dataclass(frozen=True)
class PsdLayerEffect:
    kind: str
    enabled: bool
    blend_mode: str | None
    opacity: float | None
    color_rgba: tuple[float, float, float, float] | None
    size: float | None
    angle: float | None
    distance: float | None
    spread: float | None
    choke: float | None
    position: str | None


@dataclass(frozen=True)
class PsdLayer:
    id: str
    native_id: int | None
    parent_id: str | None
    document_index: int
    sibling_index: int
    name: str
    path: tuple[str, ...]
    kind: str
    bounds: tuple[int, int, int, int]
    visible: bool
    effective_visible: bool
    opacity: int
    blend_mode: str
    clipping: bool
    text: str | None
    has_pixel_mask: bool
    has_vector_mask: bool
    has_effects: bool
    text_style: PsdTextStyle | None = None
    effects: tuple[PsdLayerEffect, ...] = ()


@dataclass(frozen=True)
class PsdAnalysis:
    inspection: PsdInspection
    layers: tuple[PsdLayer, ...]


_ADJUSTMENT_KINDS = {
    "brightnesscontrast",
    "channelmixer",
    "colorbalance",
    "curves",
    "exposure",
    "gradientmap",
    "huesaturation",
    "levels",
    "photofilter",
    "posterize",
    "selectivecolor",
    "threshold",
    "vibrance",
}


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _call_boolean(layer: Any, method_name: str, fallback: bool = False) -> bool:
    method = getattr(layer, method_name, None)
    if not callable(method):
        return fallback
    try:
        return bool(method())
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise PsdIntakeError("invalid_psd") from error


def _blend_mode(layer: Any) -> str:
    value = getattr(layer, "blend_mode", None)
    name = getattr(value, "name", None)
    return str(name).lower() if name else str(value or "normal").lower()


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _effect_token(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("latin1", errors="replace")
    token = str(value).strip()
    return {
        "Nrml": "normal",
        "normal": "normal",
        "OutF": "outside",
        "InsF": "inside",
        "CtrF": "center",
    }.get(token, token or None)


def _effect_color(value: Any) -> tuple[float, float, float, float] | None:
    if not hasattr(value, "get"):
        return None
    red = _optional_float(value.get(b"Rd  "))
    green = _optional_float(value.get(b"Grn "))
    blue = _optional_float(value.get(b"Bl  "))
    if red is None or green is None or blue is None:
        return None
    return (red / 255, green / 255, blue / 255, 1.0)


def _layer_effects(layer: Any) -> tuple[PsdLayerEffect, ...]:
    try:
        source_effects = tuple(getattr(layer, "effects", ()))
    except (AttributeError, KeyError, TypeError, ValueError):
        return ()
    return tuple(
        PsdLayerEffect(
            kind=type(effect).__name__,
            enabled=bool(getattr(effect, "enabled", False)),
            blend_mode=_effect_token(getattr(effect, "blend_mode", None)),
            opacity=_optional_float(getattr(effect, "opacity", None)),
            color_rgba=_effect_color(getattr(effect, "color", None)),
            size=_optional_float(getattr(effect, "size", None)),
            angle=_optional_float(getattr(effect, "angle", None)),
            distance=_optional_float(getattr(effect, "distance", None)),
            spread=_optional_float(getattr(effect, "spread", None)),
            choke=_optional_float(getattr(effect, "choke", None)),
            position=_effect_token(getattr(effect, "position", None)),
        )
        for effect in source_effects
    )


def _text_style(layer: Any) -> PsdTextStyle | None:
    if str(getattr(layer, "kind", "")).casefold() != "type":
        return None
    engine = getattr(layer, "engine_dict", None)
    if not hasattr(engine, "get"):
        return None
    fonts = tuple(str(value) for value in getattr(layer, "font_names", ()))
    style_run = (engine or {}).get("StyleRun") or {}
    run_array = style_run.get("RunArray", ()) if hasattr(style_run, "get") else ()
    run_lengths = style_run.get("RunLengthArray", ()) if hasattr(style_run, "get") else ()
    runs: list[PsdTextRun] = []
    start = 0
    for index, run in enumerate(run_array):
        if not hasattr(run, "get"):
            continue
        sheet = run.get("StyleSheet", {})
        data = sheet.get("StyleSheetData", {}) if hasattr(sheet, "get") else {}
        if not hasattr(data, "get"):
            continue
        raw_length = run_lengths[index] if index < len(run_lengths) else 0
        length = max(0, int(raw_length))
        font_index = _optional_int(data.get("Font"))
        font_index = font_index if font_index is not None else -1
        font_name = fonts[font_index] if 0 <= font_index < len(fonts) else None
        fill = data.get("FillColor", {})
        values = fill.get("Values", ()) if hasattr(fill, "get") else ()
        fill_rgba = (
            (float(values[1]), float(values[2]), float(values[3]), float(values[0]))  # type: ignore[misc]
            if len(values) >= 4
            else None
        )
        runs.append(
            PsdTextRun(
                start=start,
                length=length,
                font_name=font_name,
                font_size=_optional_float(data.get("FontSize")),
                faux_bold=bool(data.get("FauxBold", False)),
                faux_italic=bool(data.get("FauxItalic", False)),
                leading=_optional_float(data.get("Leading")),
                tracking=_optional_float(data.get("Tracking")),
                fill_rgba=fill_rgba,
            )
        )
        start += length
    raw_transform = tuple(getattr(layer, "transform", ()))
    transform = (
        (
            float(raw_transform[0]),
            float(raw_transform[1]),
            float(raw_transform[2]),
            float(raw_transform[3]),
            float(raw_transform[4]),
            float(raw_transform[5]),
        )
        if len(raw_transform) == 6
        else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    )
    paragraph = (engine or {}).get("ParagraphRun") or {}
    paragraph_runs = paragraph.get("RunArray", ()) if hasattr(paragraph, "get") else ()
    justification: int | None = None
    if paragraph_runs and hasattr(paragraph_runs[0], "get"):
        sheet = paragraph_runs[0].get("ParagraphSheet", {})
        properties = sheet.get("Properties", {}) if hasattr(sheet, "get") else {}
        value = properties.get("Justification") if hasattr(properties, "get") else None
        justification = _optional_int(value)
    anti_alias_value = (engine or {}).get("AntiAlias") or ""
    anti_alias = _optional_int(anti_alias_value)
    return PsdTextStyle(
        runs=tuple(runs),
        transform=transform,
        paragraph_justification=justification,
        anti_alias=anti_alias,
    )


def analyze_psd(path: Path, *, source_name: str) -> PsdAnalysis:
    try:
        with path.open("rb") as source:
            if source.read(4) != b"8BPS":
                raise PsdIntakeError("invalid_psd")
        document = PSDImage.open(path)
    except PsdIntakeError:
        raise
    except Exception as error:
        raise PsdIntakeError("invalid_psd") from error

    source_hash = _digest(path)
    source_layers = list(document.descendants())
    kinds = Counter(str(layer.kind) for layer in source_layers)
    adjustment_count = sum(count for kind, count in kinds.items() if kind in _ADJUSTMENT_KINDS)
    effect_count = sum(1 for layer in source_layers if _call_boolean(layer, "has_effects"))
    visible_layers = [
        layer
        for layer in source_layers
        if _call_boolean(layer, "is_visible", bool(getattr(layer, "visible", True)))
    ]
    visible_kinds = Counter(str(layer.kind) for layer in visible_layers)
    visible_adjustment_count = sum(
        count for kind, count in visible_kinds.items() if kind in _ADJUSTMENT_KINDS
    )
    visible_effect_count = sum(
        1 for layer in visible_layers if _call_boolean(layer, "has_effects")
    )

    blockers: list[str] = []
    if document.color_mode.name != "RGB":
        blockers.append("color_mode_not_rgb")
    if document.depth not in {8, 16}:
        blockers.append("unsupported_bit_depth")
    if visible_kinds.get("smartobject", 0):
        blockers.append("smart_objects_require_equivalence_check")
    if visible_adjustment_count:
        blockers.append("adjustment_layers_require_equivalence_check")
    if visible_effect_count:
        blockers.append("layer_effects_require_equivalence_check")
    if visible_kinds.get("pixel", 0):
        blockers.append("pixel_layers_require_equivalence_check")
    if visible_kinds.get("shape", 0):
        blockers.append("shape_styles_require_equivalence_check")
    if visible_kinds.get("type", 0):
        blockers.append("text_styles_require_equivalence_check")

    warnings: list[str] = []
    if document.depth == 16:
        warnings.append("16_bit_pixels_must_not_be_downconverted")

    inspection = PsdInspection(
        source_name=source_name,
        byte_size=path.stat().st_size,
        sha256=source_hash,
        width=document.width,
        height=document.height,
        depth=document.depth,
        color_mode=document.color_mode.name,
        layer_count=len(source_layers),
        kind_counts=dict(sorted(kinds.items())),
        text_layer_count=kinds.get("type", 0),
        smart_object_count=kinds.get("smartobject", 0),
        adjustment_layer_count=adjustment_count,
        effect_layer_count=effect_count,
        blocking_issues=tuple(blockers),
        warnings=tuple(warnings),
    )

    ids_by_object: dict[int, str] = {}
    used_ids: set[str] = set()
    for index, layer in enumerate(source_layers):
        raw_native_id = getattr(layer, "layer_id", None)
        native_id = raw_native_id if isinstance(raw_native_id, int) else None
        identity = str(native_id) if native_id is not None else f"index-{index}"
        layer_id = f"psd-layer:{source_hash}:{identity}"
        if layer_id in used_ids:
            layer_id = f"{layer_id}:{index}"
        used_ids.add(layer_id)
        ids_by_object[id(layer)] = layer_id

    paths_by_id: dict[str, tuple[str, ...]] = {}
    sibling_counts: dict[int | None, int] = {}
    ir_layers: list[PsdLayer] = []
    for index, layer in enumerate(source_layers):
        layer_id = ids_by_object[id(layer)]
        parent = getattr(layer, "parent", None)
        parent_id = ids_by_object.get(id(parent))
        parent_key = id(parent) if parent_id is not None else None
        sibling_index = sibling_counts.get(parent_key, 0)
        sibling_counts[parent_key] = sibling_index + 1
        name = str(getattr(layer, "name", "") or f"Layer {index + 1}")
        path_parts = (*paths_by_id.get(parent_id or "", ()), name)
        paths_by_id[layer_id] = path_parts
        raw_native_id = getattr(layer, "layer_id", None)
        native_id = raw_native_id if isinstance(raw_native_id, int) else None
        raw_text = getattr(layer, "text", None)
        text = str(raw_text) if raw_text is not None else None
        visible = bool(getattr(layer, "visible", True))
        ir_layers.append(
            PsdLayer(
                id=layer_id,
                native_id=native_id,
                parent_id=parent_id,
                document_index=index,
                sibling_index=sibling_index,
                name=name,
                path=path_parts,
                kind=str(getattr(layer, "kind", "unknown")),
                bounds=effective_layer_bounds(layer, document.width, document.height),
                visible=visible,
                effective_visible=_call_boolean(layer, "is_visible", visible),
                opacity=int(getattr(layer, "opacity", 255)),
                blend_mode=_blend_mode(layer),
                clipping=bool(getattr(layer, "clipping", False)),
                text=text,
                has_pixel_mask=_call_boolean(layer, "has_mask"),
                has_vector_mask=_call_boolean(layer, "has_vector_mask"),
                has_effects=_call_boolean(layer, "has_effects"),
                text_style=_text_style(layer),
                effects=_layer_effects(layer),
            )
        )
    return PsdAnalysis(inspection=inspection, layers=tuple(ir_layers))


def inspect_psd(path: Path, *, source_name: str) -> PsdInspection:
    return analyze_psd(path, source_name=source_name).inspection
