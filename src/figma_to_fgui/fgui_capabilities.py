"""Classify UIR nodes by their generic FairyGUI conversion capability."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from figma_to_fgui.fgui_graph import graph_plan_for_node
from figma_to_fgui.fgui_plan_models import (
    CapabilityDecision,
    CapabilityStatus,
    MaskKind,
    MaskMode,
)
from figma_to_fgui.fgui_plan_policy import (
    NATIVE_CLIP_SOURCE_RULE_ID,
    NATIVE_COMPONENT_REFERENCE_RULE_ID,
    NATIVE_CONTAINER_RULE_ID,
    NATIVE_GRAPH_RULE_ID,
    NATIVE_IMAGE_RULE_ID,
    NATIVE_RICH_TEXT_RULE_ID,
    NATIVE_TEXT_RULE_ID,
    RASTER_SUBTREE_RULE_ID,
)
from figma_to_fgui.uir_models import ConversionMode, UIRDocument, UIRNode

NATIVE_CLIP_KINDS = frozenset({"rectangle", "roundedRectangle"})
NATIVE_MASK_KINDS = frozenset({"image"})
RASTER_MASK_KINDS = frozenset({"boolean", "gradient", "blur", "blend"})
_MAX_TEXT_RUN_COUNT_DIGITS = 10

_NON_RASTERIZABLE_RULE_IDS = frozenset(
    {
        "fgui.unsupported.interaction",
        "fgui.unsupported.list",
        "fgui.unsupported.controller",
        "fgui.unsupported.gear",
        "fgui.unsupported.complex_auto_layout",
        "fgui.text.runs_content_mismatch",
        "fgui.text.runs_unsupported",
        "fgui.text.style_unsupported",
        "fgui.text.font_unresolved",
    }
)
_NON_RASTERIZABLE_REASONS = frozenset(
    {
        "component_mapping_ambiguous",
        "component_mapping_conflict",
        "font_policy_requires_resolution",
        "text_run_feature_unsupported",
        "video_content_out_of_scope",
    }
)


@dataclass(frozen=True)
class TextRunCapability:
    """Classify whether text runs have verified editable FairyGUI support."""

    kind: Literal["plain-text", "native-rich-text", "editable-risk", "blocked"]
    run_count: int
    preserved_properties: tuple[str, ...]
    unsupported_properties: tuple[str, ...]
    has_base_style_risk: bool = False


def is_reviewable_text_decision(decision: CapabilityDecision) -> bool:
    """Return whether a decision is the registered editable plain-text fallback."""
    if (
        decision.status != CapabilityStatus.UNSUPPORTED
        or decision.rule_id
        not in {"fgui.text.runs_unsupported", "fgui.text.style_unsupported"}
        or decision.blocking
        or decision.reasons
        not in {("rich_text_runs",), ("text_style_properties",)}
        or len(decision.evidence) != 3
    ):
        return False
    count = decision.evidence[0].removeprefix("text.runs.count=")
    unsupported = decision.evidence[2].removeprefix("text.runs.unsupported=")
    return (
        decision.evidence[0] == f"text.runs.count={count}"
        and count.isascii()
        and count.isdecimal()
        and len(count) <= _MAX_TEXT_RUN_COUNT_DIGITS
        and int(count) > 0
        and decision.evidence[1] == "text.runs.preserved=content"
        and decision.evidence[2] == f"text.runs.unsupported={unsupported}"
        and bool(unsupported)
        and all(item for item in unsupported.split(","))
    )


def analyze_text_runs(node: UIRNode) -> TextRunCapability:
    """Return the editable capability of a text node's complete set of runs."""
    text = node.text
    if text is None or not text.runs:
        return TextRunCapability("plain-text", 0, ("content",), ())
    if "".join(run.content for run in text.runs) != text.content:
        return TextRunCapability("blocked", len(text.runs), (), ("contentClosure",))

    unsupported: set[str] = set()
    for run in text.runs:
        unsupported.update(run.unsupported_features)
        if run.style.font_candidates not in {(), text.style.font_candidates}:
            unsupported.add("fontCandidates")
        if run.style.font_size not in {None, text.style.font_size}:
            unsupported.add("fontSize")
        if run.style.stroke_color not in {None, text.style.stroke_color}:
            unsupported.add("strokeColor")
        if run.style.stroke_size not in {None, text.style.stroke_size}:
            unsupported.add("strokeSize")
        if (
            run.style.horizontal_align not in {None, text.style.horizontal_align}
            or run.style.vertical_align not in {None, text.style.vertical_align}
        ):
            unsupported.add("paragraphAlignment")

    color_markup_required = any(
        run.style.color not in {None, text.style.color} for run in text.runs
    )
    has_ubb_delimiter = any(
        "[" in run.content or "]" in run.content for run in text.runs
    )
    if color_markup_required and has_ubb_delimiter:
        unsupported.add("ubbEncoding")
    if unsupported:
        return TextRunCapability(
            "editable-risk",
            len(text.runs),
            ("content",),
            tuple(sorted(unsupported)),
            bool(text.base_unsupported_features),
        )
    return TextRunCapability("native-rich-text", len(text.runs), ("content", "color"), ())


class MaskFacts(BaseModel):
    """Strictly parsed mask facts stored in a UIR node's opaque visual map."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, allow_inf_nan=False
    )

    kind: MaskKind
    mask_node_ref: str = Field(alias="maskNodeRef", min_length=1)
    content_node_refs: tuple[str, ...] = Field(alias="contentNodeRefs", min_length=1)
    safe_raster_root_ref: str | None = Field(default=None, alias="safeRasterRootRef")
    effects: tuple[str, ...] = ()
    corner_radii: tuple[float, float, float, float] | None = Field(
        default=None, alias="cornerRadii"
    )

    @model_validator(mode="after")
    def validate_corner_radii(self) -> MaskFacts:
        if self.corner_radii is not None and any(
            value < 0 for value in self.corner_radii
        ):
            raise ValueError("mask corner radii must be nonnegative")
        if self.kind == MaskKind.ROUNDED_RECTANGLE and self.corner_radii is None:
            raise ValueError("rounded masks require corner radii")
        return self


@dataclass(frozen=True)
class MaskCapability:
    """Validated, project-neutral result used by the plan compiler."""

    container_ref: str
    facts: MaskFacts | None
    mode: MaskMode | None
    resource_ref: str | None = None
    consumed_node_refs: tuple[str, ...] = ()
    diagnostic_code: str | None = None


def _decision_id(node_id: str, rule_id: str, rule_version: int) -> str:
    payload = json.dumps(
        {
            "nodeId": node_id,
            "ruleId": rule_id,
            "ruleVersion": rule_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"decision:{hashlib.sha256(payload).hexdigest()[:24]}"


def _decision(
    node: UIRNode,
    status: CapabilityStatus,
    rule_id: str,
    rule_version: int,
    reasons: tuple[str, ...] = (),
    blocking: bool = False,
    evidence: tuple[str, ...] = (),
) -> CapabilityDecision:
    stable_evidence = evidence or (
        f"conversion.mode={node.conversion.mode.value}",
        f"source.type={node.source.type}",
    )
    return CapabilityDecision(
        id=_decision_id(node.id, rule_id, rule_version),
        nodeRef=node.id,
        status=status,
        ruleId=rule_id,
        ruleVersion=rule_version,
        evidence=stable_evidence,
        reasons=reasons,
        blocking=blocking,
    )


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _fact_map_contains_key(value: object, expected: frozenset[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            _normalized_key(str(key)) in expected
            or _fact_map_contains_key(nested, expected)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_fact_map_contains_key(item, expected) for item in value)
    return False


def _complex_auto_layout(node: UIRNode) -> bool:
    normalized = {_normalized_key(str(key)): value for key, value in node.layout.items()}
    layout_mode = normalized.get("layoutmode")
    if not isinstance(layout_mode, str) or layout_mode.upper() not in {
        "HORIZONTAL",
        "VERTICAL",
    }:
        return False
    sizing_values = (
        normalized.get("primaryaxissizingmode"),
        normalized.get("counteraxissizingmode"),
    )
    if any(isinstance(value, str) and value.upper() == "AUTO" for value in sizing_values):
        return True
    layout_wrap = normalized.get("layoutwrap")
    if isinstance(layout_wrap, str) and layout_wrap.upper() not in {
        "NONE",
        "NO_WRAP",
    }:
        return True
    if any(
        key in normalized
        for key in ("maxheight", "maxwidth", "minheight", "minwidth")
    ):
        return True
    primary_alignment = normalized.get("primaryaxisalignitems")
    return (
        isinstance(primary_alignment, str)
        and primary_alignment.upper() == "SPACE_BETWEEN"
    )


def _plan_transform_is_representable(node: UIRNode) -> bool:
    transform = node.geometry.local_transform
    if node.geometry.rotation != 0:
        return False
    has_transform_fact = any(
        key in node.visual
        for key in (
            "relativeTransform",
            "relative_transform",
            "absoluteTransform",
            "absolute_transform",
        )
    )
    if transform is None:
        return not has_transform_fact
    a, b, c, d, _tx, _ty = transform
    epsilon = 1e-6
    rigid = (
        abs((a * a + b * b) - 1) <= epsilon
        and abs((c * c + d * d) - 1) <= epsilon
        and abs(a * c + b * d) <= epsilon
        and abs((a * d - b * c) - 1) <= epsilon
    )
    if not rigid:
        return False
    return not (
        abs(a - 1) > epsilon
        or abs(b) > epsilon
        or abs(c) > epsilon
        or abs(d - 1) > epsilon
        or node.geometry.rotation != 0
    )


def _text_visual_is_represented(node: UIRNode) -> bool:
    if bool(node.visual.get("effects")):
        return False
    blend = node.visual.get("blendMode", node.visual.get("blend_mode"))
    if isinstance(blend, str) and blend.upper() not in {"NORMAL", "PASS_THROUGH"}:
        return False
    fills = node.visual.get("fills")
    if (
        isinstance(fills, (list, tuple))
        and fills
        and (node.text is None or node.text.style.color is None)
    ):
        return False
    strokes = node.visual.get("strokes")
    return not (
        isinstance(strokes, (list, tuple))
        and strokes
        and (
            node.text is None
            or node.text.style.stroke_color is None
            or node.text.style.stroke_size is None
        )
    )


def _has_visible_items(value: object) -> bool:
    return isinstance(value, (list, tuple)) and any(
        isinstance(item, Mapping) and item.get("visible") is not False
        for item in value
    )


def _has_unrepresented_visual_style(node: UIRNode) -> bool:
    visual = node.visual
    blend = visual.get("blendMode", visual.get("blend_mode"))
    return (
        _has_visible_items(visual.get("effects"))
        or _has_visible_items(visual.get("fills"))
        or _has_visible_items(visual.get("strokes"))
        or bool(visual.get("styleReferences", visual.get("style_references")))
        or (
            isinstance(blend, str)
            and blend.upper() not in {"NORMAL", "PASS_THROUGH"}
        )
    )


def _unsupported_feature(
    node: UIRNode,
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    readable_container = bool(node.children) and node.source.type in {
        "FRAME",
        "GROUP",
        "COMPONENT",
        "SECTION",
        "TRANSFORM_GROUP",
        "INSTANCE",
    }
    if node.interactions:
        return (
            "fgui.unsupported.interaction",
            ("interaction_semantics_out_of_scope",),
            (f"interactions.count={len(node.interactions)}",),
        )
    if not readable_container and not _plan_transform_is_representable(node):
        return (
            "fgui.unsupported.transform",
            ("transform_semantics_out_of_scope",),
            ("geometry.localTransform.unrepresentable=true",),
        )
    if node.source.type == "TEXT" and not _text_visual_is_represented(node):
        return (
            "fgui.unsupported.visual_style",
            ("text_visual_style_out_of_scope",),
            ("text.visual.unrepresented=true",),
        )
    semantic_role = (node.semantic.role or "").casefold()
    source_type = node.source.type.upper()
    if (
        node.source.type != "TEXT"
        and not readable_container
        and node.conversion.asset_ref is None
        and graph_plan_for_node(node) is None
        and _has_unrepresented_visual_style(node)
    ):
        return (
            "fgui.unsupported.visual_style",
            ("visual_style_out_of_scope",),
            ("visual.unrepresented=true",),
        )
    if semantic_role == "list" or source_type == "LIST" or _fact_map_contains_key(
        node.layout, frozenset({"list", "listitems", "listdata"})
    ):
        return (
            "fgui.unsupported.list",
            ("list_semantics_out_of_scope",),
            ("feature=list",),
        )
    if (
        semantic_role == "controller"
        or source_type in {"COMPONENT_SET", "CONTROLLER"}
        or _fact_map_contains_key(
            node.visual, frozenset({"controller", "controllers"})
        )
    ):
        return (
            "fgui.unsupported.controller",
            ("controller_semantics_out_of_scope",),
            ("feature=controller",),
        )
    if (
        semantic_role == "gear"
        or source_type == "GEAR"
        or _fact_map_contains_key(node.visual, frozenset({"gear", "gears"}))
    ):
        return (
            "fgui.unsupported.gear",
            ("gear_semantics_out_of_scope",),
            ("feature=gear",),
        )
    return None


def is_non_rasterizable_decision(decision: CapabilityDecision) -> bool:
    """Return whether rasterization would hide required behavior or review state."""
    if is_reviewable_text_decision(decision):
        return False
    return decision.status == CapabilityStatus.UNSUPPORTED and (
        decision.rule_id in _NON_RASTERIZABLE_RULE_IDS
        or any(
            reason in _NON_RASTERIZABLE_REASONS or reason.endswith("_out_of_scope")
            for reason in decision.reasons
        )
    )


def native_clip_source_visual_is_safe(node: UIRNode) -> bool:
    """A fully opaque solid rectangle paint does not change clip geometry."""
    if node.geometry.opacity != 1 or any(
        bool(node.visual.get(key)) for key in ("effects", "strokes")
    ):
        return False
    blend = node.visual.get("blendMode", node.visual.get("blend_mode"))
    if isinstance(blend, str) and blend.upper() not in {"NORMAL", "PASS_THROUGH"}:
        return False
    fills = node.visual.get("fills", ())
    if not isinstance(fills, (list, tuple)):
        return False
    visible = tuple(
        fill
        for fill in fills
        if isinstance(fill, Mapping) and fill.get("visible") is not False
    )
    if len(visible) != 1 or visible[0].get("type") != "SOLID":
        return False
    opacity = visible[0].get("opacity", 1)
    color = visible[0].get("color")
    alpha = color.get("a", 1) if isinstance(color, Mapping) else 1
    return opacity == 1 and alpha == 1


def can_promote_native_clip_source(
    node: UIRNode,
    analysis: MaskCapability,
    current: CapabilityDecision,
) -> bool:
    """Return whether a validated clip role may replace the base node rule."""
    return node.conversion.mode != ConversionMode.UNSUPPORTED and (
        not is_non_rasterizable_decision(current)
        or (
            current.rule_id == "fgui.unsupported.visual_style"
            and analysis.facts is not None
            and analysis.facts.mask_node_ref != analysis.container_ref
            and native_clip_source_visual_is_safe(node)
        )
    )


def base_decision_for_node(
    node: UIRNode,
    document: UIRDocument,
    rule_version: int = 1,
    *,
    text_run_capability: TextRunCapability | None = None,
) -> CapabilityDecision:
    """Return the canonical non-mask capability decision for one UIR node."""
    run_capability = (
        text_run_capability
        if node.source.type == "TEXT" and text_run_capability is not None
        else analyze_text_runs(node)
        if node.source.type == "TEXT"
        else None
    )
    if run_capability is not None and run_capability.kind == "blocked":
        return _decision(
            node,
            CapabilityStatus.UNSUPPORTED,
            "fgui.text.runs_content_mismatch",
            rule_version,
            ("text_run_content_mismatch",),
            True,
            (
                f"text.runs.count={run_capability.run_count}",
                "text.runs.unsupported=contentClosure",
            ),
        )
    unsupported_feature = _unsupported_feature(node)
    raster_absorbs_feature = (
        node.conversion.mode == ConversionMode.RASTER_FALLBACK
        and unsupported_feature is not None
        and unsupported_feature[0]
        in {
            "fgui.unsupported.transform",
            "fgui.unsupported.visual_style",
            "fgui.unsupported.complex_auto_layout",
        }
    )
    asset_absorbs_feature = (
        node.conversion.asset_ref in document.assets
        and not node.children
        and unsupported_feature is not None
        and unsupported_feature[0]
        in {
            "fgui.unsupported.transform",
            "fgui.unsupported.visual_style",
        }
    )
    if (
        unsupported_feature is not None
        and not raster_absorbs_feature
        and not asset_absorbs_feature
    ):
        rule_id, reasons, evidence = unsupported_feature
        return _decision(
            node,
            CapabilityStatus.UNSUPPORTED,
            rule_id,
            rule_version,
            reasons,
            True,
            evidence,
        )
    if node.source.type == "TEXT":
        text = node.text
        if (
            text is not None
            and not text.font_policy.allow_fallback
            and (
                text.font_policy.resolved_font is None
                or not text.font_policy.resolved_font.strip()
            )
        ):
            return _decision(
                node,
                CapabilityStatus.UNSUPPORTED,
                "fgui.text.font_unresolved",
                rule_version,
                ("font_policy_requires_resolution",),
                True,
                ("text.fontPolicy.allowFallback=false", "text.font.resolved=false"),
            )
    if run_capability is not None and run_capability.kind == "editable-risk":
        base_style_risk = run_capability.has_base_style_risk
        return _decision(
            node,
            CapabilityStatus.UNSUPPORTED,
            "fgui.text.style_unsupported" if base_style_risk else "fgui.text.runs_unsupported",
            rule_version,
            ("text_style_properties",) if base_style_risk else ("rich_text_runs",),
            evidence=(
                f"text.runs.count={run_capability.run_count}",
                "text.runs.preserved=" + ",".join(run_capability.preserved_properties),
                "text.runs.unsupported="
                + ",".join(run_capability.unsupported_properties),
            ),
        )
    if node.conversion.mode == ConversionMode.RASTER_FALLBACK:
        if node.conversion.asset_ref not in document.assets:
            return _decision(
                node,
                CapabilityStatus.UNSUPPORTED,
                "fgui.unsupported.raster_asset",
                rule_version,
                ("raster_asset_missing",),
                True,
            )
        return _decision(
            node,
            CapabilityStatus.RASTER_FALLBACK,
            RASTER_SUBTREE_RULE_ID,
            rule_version,
            node.conversion.reasons,
        )
    if node.conversion.mode == ConversionMode.UNSUPPORTED:
        return _decision(
            node,
            CapabilityStatus.UNSUPPORTED,
            "fgui.unsupported.source",
            rule_version,
            node.conversion.reasons,
            True,
        )
    if node.conversion.mode == ConversionMode.COMPONENT_REFERENCE:
        return _decision(
            node,
            CapabilityStatus.NATIVE,
            NATIVE_COMPONENT_REFERENCE_RULE_ID,
            rule_version,
        )
    if node.conversion.asset_ref in document.assets:
        if node.children:
            return _decision(
                node,
                CapabilityStatus.UNSUPPORTED,
                "fgui.unsupported.asset_backed_subtree",
                rule_version,
                ("native_asset_children_unsupported",),
                True,
            )
        return _decision(
            node,
            CapabilityStatus.NATIVE,
            NATIVE_IMAGE_RULE_ID,
            rule_version,
        )
    if node.children and node.source.type in {
        "FRAME",
        "COMPONENT",
        "TRANSFORM_GROUP",
    }:
        return _decision(
            node,
            CapabilityStatus.NATIVE,
            NATIVE_CONTAINER_RULE_ID,
            rule_version,
        )
    if graph_plan_for_node(node) is not None:
        return _decision(
            node,
            CapabilityStatus.NATIVE,
            NATIVE_GRAPH_RULE_ID,
            rule_version,
        )
    if node.source.type in {
        "FRAME",
        "GROUP",
        "COMPONENT",
        "SECTION",
        "TRANSFORM_GROUP",
    } or (
        node.source.type == "INSTANCE" and bool(node.children)
    ):
        return _decision(
            node,
            CapabilityStatus.NATIVE,
            NATIVE_CONTAINER_RULE_ID,
            rule_version,
        )
    if node.source.type == "TEXT":
        if run_capability is not None and run_capability.kind == "native-rich-text":
            return _decision(
                node,
                CapabilityStatus.NATIVE,
                NATIVE_RICH_TEXT_RULE_ID,
                rule_version,
                evidence=(f"text.runs.count={run_capability.run_count}",),
            )
        return _decision(
            node,
            CapabilityStatus.NATIVE,
            NATIVE_TEXT_RULE_ID,
            rule_version,
        )
    return _decision(
        node,
        CapabilityStatus.UNSUPPORTED,
        "fgui.unsupported.node_type",
        rule_version,
        ("node_type_unsupported",),
        True,
    )


def _is_ancestor(document: UIRDocument, ancestor_id: str, node_id: str) -> bool:
    visited: set[str] = set()
    current_id: str | None = node_id
    while current_id is not None and current_id not in visited:
        if current_id == ancestor_id:
            return True
        visited.add(current_id)
        current = document.nodes.get(current_id)
        current_id = None if current is None else current.parent_id
    return False


def _subtree_ids(document: UIRDocument, root_id: str) -> tuple[str, ...]:
    result: list[str] = []
    pending = [root_id]
    visited: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        result.append(node_id)
        node = document.nodes.get(node_id)
        if node is not None:
            pending.extend(reversed(node.children))
    return tuple(result)


def _invalid_mask(container_ref: str, code: str) -> MaskCapability:
    return MaskCapability(
        container_ref=container_ref,
        facts=None,
        mode=None,
        diagnostic_code=code,
    )


def _usable_mask_bounds(node: UIRNode) -> bool:
    bounds = node.geometry.resolved_bounds
    return (
        all(math.isfinite(value) for value in (bounds.x, bounds.y, bounds.width, bounds.height))
        and bounds.width > 0
        and bounds.height > 0
    )


def _resolved_corner_radii(node: UIRNode) -> tuple[float, float, float, float] | None:
    general = node.visual.get("cornerRadius", 0)
    if not isinstance(general, (int, float)):
        return None
    values = tuple(
        node.visual.get(key, general)
        for key in (
            "topLeftRadius",
            "topRightRadius",
            "bottomRightRadius",
            "bottomLeftRadius",
        )
    )
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
        for value in values
    ):
        return None
    radii = tuple(float(value) for value in values)
    return (radii[0], radii[1], radii[2], radii[3])


def analyze_mask_capabilities(document: UIRDocument) -> dict[str, MaskCapability]:
    """Parse and validate all declared mask facts in stable container order."""
    results: dict[str, MaskCapability] = {}
    for container_id in sorted(document.nodes):
        container = document.nodes[container_id]
        if "mask" in container.visual and container.visual.get("clipsContent") is True:
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.clip_composition_unsupported"
            )
            continue
        if "mask" not in container.visual:
            if container.visual.get("clipsContent") is not True or not container.children:
                continue
            if container.source.type not in {
                "RECTANGLE",
                "FRAME",
                "COMPONENT",
                "INSTANCE",
            }:
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.source_role_invalid"
                )
                continue
            if not _usable_mask_bounds(container):
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.bounds_invalid"
                )
                continue
            radii = _resolved_corner_radii(container)
            if radii is None or any(
                value > min(
                    container.geometry.resolved_bounds.width,
                    container.geometry.resolved_bounds.height,
                )
                / 2
                for value in radii
            ):
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.corner_radius_invalid"
                )
                continue
            kind = (
                MaskKind.ROUNDED_RECTANGLE
                if any(value > 0 for value in radii)
                else MaskKind.RECTANGLE
            )
            results[container_id] = MaskCapability(
                container_ref=container_id,
                facts=MaskFacts(
                    kind=kind,
                    maskNodeRef=container_id,
                    contentNodeRefs=container.children,
                    cornerRadii=(
                        radii if kind == MaskKind.ROUNDED_RECTANGLE else None
                    ),
                ),
                mode=MaskMode.NATIVE_CLIP,
            )
            continue
        try:
            facts = MaskFacts.model_validate(container.visual["mask"])
        except ValidationError:
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.facts_invalid"
            )
            continue

        referenced_ids = (facts.mask_node_ref, *facts.content_node_refs)
        required_ids = referenced_ids + (
            () if facts.safe_raster_root_ref is None else (facts.safe_raster_root_ref,)
        )
        if any(node_id not in document.nodes for node_id in required_ids):
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.source_missing"
            )
            continue

        child_positions = {node_id: index for index, node_id in enumerate(container.children)}
        owned_nodes = (document.nodes[node_id] for node_id in referenced_ids)
        content_positions = [child_positions.get(node_id) for node_id in facts.content_node_refs]
        mask_position = child_positions.get(facts.mask_node_ref)
        hierarchy_invalid = (
            len(set(referenced_ids)) != len(referenced_ids)
            or any(node.parent_id != container_id for node in owned_nodes)
            or any(node_id not in child_positions for node_id in referenced_ids)
            or any(position is None for position in content_positions)
            or mask_position is None
        )
        if not hierarchy_invalid:
            assert mask_position is not None
            numeric_positions = [position for position in content_positions if position is not None]
            hierarchy_invalid = numeric_positions != list(
                range(mask_position + 1, mask_position + 1 + len(numeric_positions))
            )
        if hierarchy_invalid:
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.invalid_hierarchy"
            )
            continue

        mask_source = document.nodes[facts.mask_node_ref]
        if facts.corner_radii is not None and any(
            value
            > min(
                mask_source.geometry.resolved_bounds.width,
                mask_source.geometry.resolved_bounds.height,
            )
            / 2
            for value in facts.corner_radii
        ):
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.corner_radius_invalid"
            )
            continue
        if facts.kind in NATIVE_CLIP_KINDS and not facts.effects:
            if mask_source.source.type not in {"RECTANGLE", "FRAME", "COMPONENT"}:
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.source_role_invalid"
                )
                continue
            if not _usable_mask_bounds(mask_source):
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.bounds_invalid"
                )
                continue
            results[container_id] = MaskCapability(
                container_ref=container_id,
                facts=facts,
                mode=MaskMode.NATIVE_CLIP,
            )
            continue
        if facts.kind in NATIVE_MASK_KINDS and not facts.effects:
            if mask_source.source.type not in {"RECTANGLE", "ELLIPSE", "VECTOR", "IMAGE"}:
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.source_role_invalid"
                )
                continue
            if not _usable_mask_bounds(mask_source):
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.bounds_invalid"
                )
                continue
            image_asset_ref = mask_source.conversion.asset_ref
            image_asset = (
                None
                if image_asset_ref is None
                else document.assets.get(image_asset_ref)
            )
            if image_asset is None or not image_asset.mime_type.startswith("image/"):
                results[container_id] = _invalid_mask(
                    container_id, "fgui.mask.image_resource_missing"
                )
                continue
            results[container_id] = MaskCapability(
                container_ref=container_id,
                facts=facts,
                mode=MaskMode.NATIVE_MASK,
            )
            continue

        if facts.kind not in RASTER_MASK_KINDS and not facts.effects:
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.facts_invalid"
            )
            continue

        safe_root_id = facts.safe_raster_root_ref
        if safe_root_id is None:
            results[container_id] = _invalid_mask(
                container_id, "fgui.visual.effect_unsupported"
            )
            continue
        if any(
            not _is_ancestor(document, safe_root_id, node_id)
            for node_id in referenced_ids
        ):
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.invalid_hierarchy"
            )
            continue
        safe_root = document.nodes[safe_root_id]
        if not _usable_mask_bounds(safe_root):
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.bounds_invalid"
            )
            continue
        asset_ref = safe_root.conversion.asset_ref
        if asset_ref is None or asset_ref not in document.assets:
            results[container_id] = _invalid_mask(
                container_id, "fgui.visual.effect_unsupported"
            )
            continue
        consumed_node_refs = _subtree_ids(document, safe_root_id)
        if any(
            not _is_ancestor(document, safe_root_id, node_id)
            for node_id in consumed_node_refs
        ):
            results[container_id] = _invalid_mask(
                container_id, "fgui.mask.invalid_hierarchy"
            )
            continue
        results[container_id] = MaskCapability(
            container_ref=container_id,
            facts=facts,
            mode=MaskMode.RASTER_SUBTREE,
            resource_ref=asset_ref,
            consumed_node_refs=consumed_node_refs,
        )

    raster_items = [
        (container_id, analysis)
        for container_id, analysis in results.items()
        if analysis.mode == MaskMode.RASTER_SUBTREE
    ]
    overlapping_containers: set[str] = set()
    for index, (container_id, analysis) in enumerate(raster_items):
        claimed_nodes = set(analysis.consumed_node_refs)
        for other_container_id, other_analysis in raster_items[index + 1 :]:
            if claimed_nodes.intersection(other_analysis.consumed_node_refs):
                overlapping_containers.update((container_id, other_container_id))
    for container_id in overlapping_containers:
        results[container_id] = _invalid_mask(
            container_id, "fgui.mask.invalid_hierarchy"
        )
    return results


def _mask_decision(
    node: UIRNode,
    analysis: MaskCapability,
    rule_version: int,
) -> CapabilityDecision:
    if analysis.diagnostic_code is not None:
        return _decision(
            node,
            CapabilityStatus.UNSUPPORTED,
            analysis.diagnostic_code,
            rule_version,
            ("mask_invalid",),
            True,
        )
    return _decision(
        node,
        CapabilityStatus.RASTER_FALLBACK,
        RASTER_SUBTREE_RULE_ID,
        rule_version,
        ("mask_raster_fallback",),
    )


def decision_for_node(
    node: UIRNode, document: UIRDocument, rule_version: int = 1
) -> CapabilityDecision:
    """Return the first applicable generic capability or mask rule for ``node``."""
    decisions = analyze_capabilities(document, rule_version=rule_version)
    return decisions.get(node.id, base_decision_for_node(node, document, rule_version))


def analyze_capabilities(
    document: UIRDocument,
    *,
    rule_version: int = 1,
    mask_capabilities: Mapping[str, MaskCapability] | None = None,
    text_run_capabilities: Mapping[str, TextRunCapability] | None = None,
) -> dict[str, CapabilityDecision]:
    """Classify every UIR node in stable document-key order."""
    decisions = {
        node_id: base_decision_for_node(
            document.nodes[node_id],
            document,
            rule_version,
            text_run_capability=(
                None
                if text_run_capabilities is None
                else text_run_capabilities.get(node_id)
            ),
        )
        for node_id in sorted(document.nodes)
    }
    resolved_masks = (
        analyze_mask_capabilities(document)
        if mask_capabilities is None
        else mask_capabilities
    )
    for analysis in resolved_masks.values():
        if analysis.diagnostic_code is not None:
            node = document.nodes[analysis.container_ref]
            if not is_non_rasterizable_decision(decisions[node.id]):
                decisions[node.id] = _mask_decision(node, analysis, rule_version)
        elif analysis.mode == MaskMode.RASTER_SUBTREE and analysis.facts is not None:
            safe_root_id = analysis.facts.safe_raster_root_ref
            hides_blocking_behavior = any(
                is_non_rasterizable_decision(decisions[node_id])
                for node_id in analysis.consumed_node_refs
            )
            if safe_root_id is not None and not hides_blocking_behavior:
                node = document.nodes[safe_root_id]
                decisions[node.id] = _mask_decision(node, analysis, rule_version)
        elif analysis.mode == MaskMode.NATIVE_CLIP and analysis.facts is not None:
            node = document.nodes[analysis.facts.mask_node_ref]
            current = decisions[node.id]
            if can_promote_native_clip_source(node, analysis, current):
                decisions[node.id] = _decision(
                    node,
                    CapabilityStatus.NATIVE,
                    NATIVE_CLIP_SOURCE_RULE_ID,
                    rule_version,
                )
        elif analysis.mode == MaskMode.NATIVE_MASK and analysis.facts is not None:
            node = document.nodes[analysis.facts.mask_node_ref]
            current = decisions[node.id]
            if (
                node.conversion.asset_ref in document.assets
                and not is_non_rasterizable_decision(current)
            ):
                decisions[node.id] = _decision(
                    node,
                    CapabilityStatus.NATIVE,
                    NATIVE_IMAGE_RULE_ID,
                    rule_version,
                )
    return decisions
