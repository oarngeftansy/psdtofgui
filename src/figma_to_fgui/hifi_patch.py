from __future__ import annotations

import base64
import copy
import hashlib
import math
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import cast

from lxml import etree

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.fixed_fonts import PROJECT_FIXED_FONTS
from figma_to_fgui.hifi_replacement_models import (
    HIFI_MAPPING_POLICY_REVISION,
    FguiComponentInventory,
    HifiDiffItem,
    HifiMappingDraft,
    HifiReplacementReview,
)
from figma_to_fgui.hifi_review import build_object_diffs
from figma_to_fgui.hifi_type_permissions import can_convert, can_convert_mapping, convert_graph
from figma_to_fgui.paths import safe_relative_path
from figma_to_fgui.service_contracts import ChangeBundle, ChangeFile, FileOperation


class HifiPatchError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
_COMMON_VISUAL_ATTRIBUTES = {
    "alpha",
    "colGap",
    "lineGap",
    "rotation",
    "size",
    "visible",
    "xy",
}
_TEXT_VISUAL_ATTRIBUTES = {
    "align",
    "autoSize",
    "bold",
    "color",
    "font",
    "fontSize",
    "faceDilate",
    "italic",
    "leading",
    "letterSpacing",
    "strokeColor",
    "strokeSize",
    "singleLine",
    "text",
    "vAlign",
}
_RASTER_PIXEL_ATTRIBUTES = {
    "align",
    "aspect",
    "autoSize",
    "color",
    "shrinkOnly",
    "vAlign",
}
_IMAGE_VISUAL_ATTRIBUTES = {"fileName", "pkg", "src", *_RASTER_PIXEL_ATTRIBUTES}
_LOADER_VISUAL_ATTRIBUTES = {"fill", "url", *_RASTER_PIXEL_ATTRIBUTES}
_GRAPH_VISUAL_ATTRIBUTES = {"type", "fillColor", "lineColor", "lineSize", "corner"}


def _flatten(manifest: SelectionManifest) -> dict[str, SelectionNode]:
    result: dict[str, SelectionNode] = {}
    pending = list(manifest.top_level_nodes)
    while pending:
        node = pending.pop()
        result[node.id] = node
        pending.extend(node.children)
    return result


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")


def _editor_int32(value: float) -> str:
    """Serialize geometry in the integer form required by FairyGUI Editor 6.1.4."""
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise HifiPatchError("hifi_geometry_invalid")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise HifiPatchError("hifi_geometry_invalid")
    rounded = int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))
    if not -(2**31) <= rounded < 2**31:
        raise HifiPatchError("hifi_geometry_invalid")
    return str(rounded)


def _project_font_uris(root: Path) -> dict[str, str]:
    """Return exact font SHA-256 to FairyGUI resource URI mappings."""
    result: dict[str, str] = {}
    for manifest_path in sorted(root.glob("assets/*/package.xml")):
        try:
            package = etree.parse(str(manifest_path), _PARSER).getroot()
        except (OSError, etree.XMLSyntaxError):
            continue
        package_id = str(package.attrib.get("id", ""))
        if not package_id:
            continue
        for resource in package.xpath("./resources/font[@id][@name]"):
            resource_id = str(resource.attrib["id"])
            resource_path = str(resource.attrib.get("path", "/")).strip("/")
            font_path = manifest_path.parent / resource_path / str(resource.attrib["name"])
            try:
                digest = hashlib.sha256(font_path.read_bytes()).hexdigest()
            except OSError:
                continue
            result.setdefault(digest, f"ui://{package_id}{resource_id}")
    return result


def _color(value: object, opacity: float = 1.0) -> str | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        red, green, blue, alpha = (max(0.0, min(1.0, float(item))) for item in value)
    except (TypeError, ValueError):
        return None
    channels = tuple(round(item * 255) for item in (red, green, blue))
    resolved_alpha = round(max(0.0, min(1.0, alpha * opacity)) * 255)
    if resolved_alpha < 255:
        return f"#{resolved_alpha:02x}{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"
    return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"


def _fixed_font_uri(font_name: object, font_uris: dict[str, str]) -> str | None:
    if not isinstance(font_name, str):
        return None
    normalized = font_name.casefold()
    spec = next(
        (
            item
            for item in PROJECT_FIXED_FONTS
            if normalized in {item.family.casefold(), item.postscript_name.casefold()}
        ),
        None,
    )
    return font_uris.get(spec.sha256) if spec is not None else None


def _apply_psd_text_style(
    element: etree._Element,
    node: SelectionNode,
    font_uris: dict[str, str],
) -> None:
    if element.tag not in {"text", "richtext"}:
        return
    style = node.style.get("psdTextStyle")
    if not isinstance(style, dict):
        return
    runs = style.get("runs")
    if not isinstance(runs, (tuple, list)) or len(runs) != 1 or not isinstance(runs[0], dict):
        return
    run = runs[0]
    # A mapped PSD text layer replaces the old text object's visual style.
    # Remove legacy-only decoration first so a disabled PSD stroke or faux
    # style cannot silently survive the replacement. The object, text value,
    # relations, gears and runtime identity remain untouched.
    for attribute in ("bold", "faceDilate", "italic", "strokeColor", "strokeSize"):
        element.attrib.pop(attribute, None)
    transform = style.get("transform")
    if not isinstance(transform, (tuple, list)) or len(transform) != 6:
        return
    try:
        scale_x = math.hypot(float(transform[0]), float(transform[1]))
        scale_y = math.hypot(float(transform[2]), float(transform[3]))
    except (TypeError, ValueError):
        return
    effective_font_size: float | None = None
    font_size = run.get("font_size")
    if isinstance(font_size, (int, float)) and not isinstance(font_size, bool):
        effective_font_size = float(font_size) * scale_y
        # Photoshop layer bounds are the visible glyph rectangle, not the em
        # box. Clamping the transformed em size to that rectangle shrinks the
        # same font a second time in FairyGUI (most visibly on digits and the
        # short sidebar labels). Preserve Photoshop's transformed em size and
        # compensate its line-box bearing when positioning the field below.
        element.attrib["fontSize"] = _number(effective_font_size)
        leading = run.get("leading")
        if isinstance(leading, (int, float)) and not isinstance(leading, bool):
            element.attrib["leading"] = _number((float(leading) - float(font_size)) * scale_y)
        tracking = run.get("tracking")
        if isinstance(tracking, (int, float)) and not isinstance(tracking, bool):
            letter_spacing = float(tracking) / 1000 * float(font_size) * scale_x
            element.attrib["letterSpacing"] = _number(letter_spacing)
    effects = node.properties.get("psdEffects", ())
    font_uri = _fixed_font_uri(run.get("font_name"), font_uris)
    if font_uri is not None:
        element.attrib["font"] = font_uri
    if run.get("faux_bold") is True:
        element.attrib["bold"] = "true"
    if run.get("faux_italic") is True:
        element.attrib["italic"] = "true"
    justification = style.get("paragraph_justification")
    alignment = {0: "left", 1: "right", 2: "center"}.get(justification or 0)
    if alignment is not None:
        element.attrib["align"] = alignment

    text_color = _color(run.get("fill_rgba"))
    if isinstance(effects, (tuple, list)):
        for effect in effects:
            if not isinstance(effect, dict) or effect.get("enabled") is not True:
                continue
            opacity_value = effect.get("opacity")
            opacity = (
                max(0.0, min(1.0, float(opacity_value) / 100))
                if isinstance(opacity_value, (int, float)) and not isinstance(opacity_value, bool)
                else 1.0
            )
            if effect.get("kind") == "ColorOverlay" and effect.get("blend_mode") == "normal":
                text_color = _color(effect.get("color_rgba"), opacity)
            elif (
                effect.get("kind") == "Stroke"
                and effect.get("blend_mode") == "normal"
                and effect.get("position") in {"outside", "center"}
            ):
                stroke_color = _color(effect.get("color_rgba"), opacity)
                if stroke_color is not None:
                    element.attrib["strokeColor"] = stroke_color
                size = effect.get("size")
                if isinstance(size, (int, float)) and not isinstance(size, bool):
                    # FairyGUI's smooth-font outline is visibly about twice
                    # Photoshop's outside-stroke width at the same numeric
                    # value. Convert the PSD pixel width to the Editor unit
                    # instead of carrying the old outline or applying both.
                    stroke_size = float(size) * 0.5
                    element.attrib["strokeSize"] = _number(stroke_size)
    if text_color is not None:
        element.attrib["color"] = text_color
    _update_current_text_gear_color(element)


def _selected_controller_page(element: etree._Element, controller_name: str) -> str | None:
    component = element.getparent()
    while component is not None and component.tag != "component":
        component = component.getparent()
    if component is None:
        return None
    controllers = component.xpath("./controller[@name=$name]", name=controller_name)
    if len(controllers) != 1:
        return None
    controller = controllers[0]
    raw_pages = controller.get("pages", "").split(",")
    page_ids = raw_pages[0::2]
    if not page_ids:
        return None
    selected = controller.get("selected", "0")
    try:
        selected_index = int(selected)
    except ValueError:
        return selected if selected in page_ids else None
    return page_ids[selected_index] if 0 <= selected_index < len(page_ids) else None


def _neutralize_current_gear_color(element: etree._Element) -> None:
    """Keep controller wiring and inactive colors, but do not tint the mapped PSD state."""
    for gear in element.findall("gearColor"):
        controller_name = gear.get("controller")
        if not controller_name:
            continue
        component = element.getparent()
        while component is not None and component.tag != "component":
            component = component.getparent()
        controllers = (
            component.xpath("./controller[@name=$name]", name=controller_name)
            if component is not None
            else ()
        )
        raw_pages = controllers[0].get("pages", "").split(",") if len(controllers) == 1 else []
        controller_pages = raw_pages[0::2]
        runtime_page = controller_pages[0] if controller_pages else None
        if runtime_page is None:
            continue
        values = gear.get("values", "").split(",")
        if not values or values == [""]:
            continue
        pages = gear.get("pages")
        if pages:
            page_ids = pages.split(",")
        else:
            page_ids = controller_pages
        if runtime_page in page_ids:
            runtime_index = page_ids.index(runtime_page)
            if runtime_index >= len(values):
                continue
            values[runtime_index] = "#ffffff"
            gear.set("values", ",".join(values))
        else:
            gear.set("default", "#ffffff")


def _update_current_text_gear_color(element: etree._Element) -> None:
    """Apply PSD colors only to the component's runtime-initial gear state.

    FairyGUI's ``controller.selected`` records the editor's design-time page.
    A component instantiated by its parent starts on the controller's first
    page instead.  If a gear has no explicit value for that page, ``default``
    is what the root-screen render uses.  Updating the selected page therefore
    left the old default fill/outline visible and changed an inactive state.
    """
    color = element.get("color")
    if color is None:
        return
    stroke_color = element.get("strokeColor") if element.get("strokeSize") else None
    current_value = f"{color},{stroke_color or color}"
    for gear in element.findall("gearColor"):
        controller_name = gear.get("controller")
        if not controller_name:
            continue
        component = element.getparent()
        while component is not None and component.tag != "component":
            component = component.getparent()
        controllers = (
            component.xpath("./controller[@name=$name]", name=controller_name)
            if component is not None
            else ()
        )
        raw_pages = controllers[0].get("pages", "").split(",") if len(controllers) == 1 else []
        runtime_page = raw_pages[0] if raw_pages else None
        page_ids = [page for page in gear.get("pages", "").split(",") if page]
        if runtime_page is None:
            continue
        if runtime_page in page_ids:
            values = gear.get("values", "").split("|")
            if len(values) != len(page_ids):
                continue
            values[page_ids.index(runtime_page)] = current_value
            gear.set("values", "|".join(values))
        else:
            gear.set("default", current_value)


def _prepare_psd_raster_target(element: etree._Element) -> None:
    """Remove legacy pixel transforms before an exact PSD raster is attached."""
    for attribute in _RASTER_PIXEL_ATTRIBUTES:
        element.attrib.pop(attribute, None)
    _neutralize_current_gear_color(element)
    if element.tag == "loader":
        element.set("fill", "scaleFree")


def _selection_box(
    root: SelectionNode,
    node: SelectionNode,
    inventory: FguiComponentInventory,
) -> tuple[float, float, float, float]:
    if root.bounds.width <= 0 or root.bounds.height <= 0:
        raise HifiPatchError("hifi_selection_root_invalid")
    if root.id.startswith("psd-root:"):
        return (
            node.bounds.x - root.bounds.x,
            node.bounds.y - root.bounds.y,
            node.bounds.width,
            node.bounds.height,
        )
    scale_x = inventory.width / root.bounds.width
    scale_y = inventory.height / root.bounds.height
    return (
        (node.bounds.x - root.bounds.x) * scale_x,
        (node.bounds.y - root.bounds.y) * scale_y,
        node.bounds.width * scale_x,
        node.bounds.height * scale_y,
    )


def _set_visual(
    element: etree._Element,
    node: SelectionNode,
    root: SelectionNode,
    inventory: FguiComponentInventory,
    font_uris: dict[str, str],
) -> None:
    x, y, width, height = _selection_box(root, node, inventory)
    # A component's XML size is a logical/layout and interaction contract, not
    # merely its painted bounds.  PSD groups describe target pixels. Preserve
    # the old logical size by default and let mapped children carry the larger
    # or smaller visual geometry (including intentional overflow).
    if (
        root.id.startswith("psd-root:")
        and element.tag == "component"
        and node.properties.get("hifiResizeLogicalBounds") is not True
    ):
        element_id = element.get("id")
        old_object = next(
            (
                value
                for value in inventory.objects
                if element_id is not None
                and (value.object_id == element_id or value.local_object_id == element_id)
            ),
            None,
        )
        if old_object is not None and old_object.width > 0 and old_object.height > 0:
            width, height = old_object.width, old_object.height
        elif element.get("size"):
            try:
                width, height = (float(value) for value in element.get("size", "").split(",", 1))
            except (TypeError, ValueError):
                pass
    _apply_psd_text_style(element, node, font_uris)
    if (
        root.id.startswith("psd-root:")
        and element.tag in {"text", "richtext"}
        and node.text is not None
    ):
        mapped_text = node.properties.get("hifiMappedText")
        layout_text = mapped_text if isinstance(mapped_text, str) else node.text
        try:
            font_size = float(element.get("fontSize", "0"))
        except ValueError:
            font_size = 0.0
        psd_text_origin = node.properties.get("hifiPsdTextOrigin")
        if (
            isinstance(psd_text_origin, (tuple, list))
            and len(psd_text_origin) == 2
            and font_size > 0
        ):
            try:
                origin_x = float(psd_text_origin[0])
                origin_y = float(psd_text_origin[1])
            except (TypeError, ValueError):
                pass
            else:
                visible_right = x + width
                right_padding = 0.0
                undersized_middle_line = (
                    height < font_size and element.get("vAlign") == "middle"
                )
                # FairyGUI's synthetic bold can advance the final glyph past
                # Photoshop's visible layer bound.  Reserve font-relative
                # line-box room for that advance; this is derived from the em
                # size and replaces the old fixed-pixel inset.
                terminal_padding = font_size * (0.5 if element.get("bold") == "true" else 0.25)
                right_padding = 0.0
                group_id = element.get("group")
                parent = element.getparent()
                if group_id is None or parent is None:
                    right_padding = max(right_padding, terminal_padding)
                else:
                    group = next(
                        (
                            sibling
                            for sibling in parent
                            if sibling.tag == "group" and sibling.get("id") == group_id
                        ),
                        None,
                    )
                    members = [
                        sibling for sibling in parent if sibling.get("group") == group_id
                    ]
                    if (
                        group is None
                        or group.get("layout") not in {"hz", "vt"}
                        or members and members[-1] is element
                    ):
                        right_padding = max(right_padding, terminal_padding)
                    if group is not None and group.get("layout") == "hz" and members[:1] == [element]:
                        # A horizontal FairyGUI group starts its layout at the
                        # text field's line-box origin. Photoshop records the
                        # first painted glyph instead. The smooth-font resource
                        # has a left side-bearing of about 0.06 em at the sizes
                        # used by the PSD, so move the existing group origin by
                        # that font metric. Child IDs/order and group layout stay
                        # untouched; later members (icon, value) follow normally.
                        group_x, group_y = (
                            float(value) for value in group.get("xy", "0,0").split(",")
                        )
                        group.set(
                            "xy",
                            f"{_editor_int32(group_x - font_size * 0.06)},{_editor_int32(group_y)}",
                        )
                # Photoshop stores a baseline origin while its layer bounds
                # start at the first painted glyph. FairyGUI stores the text
                # line box. Align the old text object's line box to that PSD
                # origin and leave enough right-side room so the last glyph is
                # not clipped by the visible-bounds rectangle.
                x = origin_x
                y = origin_y - font_size
                if element.get("anchor") == "true":
                    # Anchored button titles keep an extra smooth-font gutter
                    # after FairyGUI resolves their pivot.  Compensate it as
                    # a font metric; unanchored labels are already aligned by
                    # their PSD baseline and must not inherit this correction.
                    x -= font_size * 0.07
                    y -= font_size * 0.05
                elif element.get("strokeSize") is not None:
                    # FGUI adds a small smooth-font outline gutter outside the
                    # text origin while Photoshop reports the stroked ink
                    # bound itself.  The Editor measurement is proportional
                    # to the em size for both sidebar labels and the primary
                    # action title.
                    x -= font_size * 0.03
                    y -= font_size * 0.03
                elif element.get("bold") == "true" and element.get("group"):
                    # In a compact icon+label row, FairyGUI's synthetic bold
                    # leaves a left side-bearing that Photoshop's ink bounds
                    # do not include. Correct the em bearing on the existing
                    # text object; the owning hz group will derive its gap
                    # from this final line-box geometry.
                    x -= font_size * 0.09
                if undersized_middle_line and "hifiOwnerWidth" not in node.properties:
                    y -= font_size * 0.11
                width = max(width, visible_right - x + right_padding)
                if undersized_middle_line:
                    # A PSD layer box is only the painted ink.  Reusing that
                    # short box for a vertically-centred FGUI text field clips
                    # the font's ascender/descender (and, in Editor 6.1.4, can
                    # drop the terminal glyph).  Restore an em-based line box
                    # while keeping the original text object and its group.
                    height = max(height, font_size * 1.15)
                    element.attrib["vAlign"] = "top"
        element_id = element.get("id")
        relation_dependent = False
        parent = element.getparent()
        if element_id and parent is not None:
            relation_dependent = any(
                relation.get("target") == element_id
                for sibling in parent
                for relation in sibling.findall("relation")
            )
        needs_fixed_line = (
            isinstance(mapped_text, str)
            or element.get("autoSize") == "shrink"
            or element.get("singleLine") != "true" and " " in layout_text.strip()
        )
        if needs_fixed_line and font_size > 0:
            # PSD reports visible glyph bounds, while FairyGUI's text field
            # needs extra line-metric room. Reusing the PSD box verbatim can
            # make a runtime Button title wrap and then clip its second line,
            # or make `autoSize=shrink` reduce labels that already have the
            # correct PSD font size.
            if isinstance(mapped_text, str) and not relation_dependent:
                owner_width = node.properties.get("hifiOwnerWidth", inventory.width)
                available_width = (
                    float(owner_width) - x
                    if isinstance(owner_width, (int, float))
                    and not isinstance(owner_width, bool)
                    else inventory.width - x
                )
                width = max(width, available_width)
            if element.get("autoSize") == "shrink":
                height = max(height, font_size * 1.15)
                element.attrib["vAlign"] = "top"
            element.attrib["autoSize"] = "none"
            element.attrib["singleLine"] = "true"
        if (
            isinstance(node.style.get("psdTextStyle"), dict)
            and font_size > 0
            and not (
                isinstance(psd_text_origin, (tuple, list))
                and len(psd_text_origin) == 2
            )
        ):
            # PSD bounds start at the visible glyph, whereas FairyGUI `xy`
            # starts at the font line box. Compensate the font's top bearing
            # so the rendered glyph returns to the PSD coordinate.
            y -= font_size * 0.3
    if element.get("anchor") == "true":
        pivot = element.get("pivot", "0,0").split(",")
        x += float(pivot[0]) * width
        y += float(pivot[1]) * height
    if (
        root.id.startswith("psd-root:")
        and element.tag == "component"
        and element.get("group") is None
        and any(
            relation.get("target", "") == ""
            and "middle-middle" in relation.get("sidePair", "").split(",")
            for relation in element.findall("relation")
        )
        and round(height) % 2 == 1
    ):
        # Editor 6.1.4 resolves a directly root-centred component with an odd
        # pixel height on the upper half-pixel.  The XML y value is therefore
        # painted one pixel above the requested PSD raster bound.  Advanced
        # FGUI groups perform their own relation pass and do not exhibit this
        # shift, so keep the correction limited to ungrouped component
        # instances whose existing middle relation is preserved.
        y += 1
    # FairyGUI 6.1.4 parses xy/size as Int32 pairs. Decimal geometry makes the
    # whole component open as an empty canvas, even when every referenced file
    # exists, so round at the XML boundary just like the new-project writer.
    element.attrib["xy"] = f"{_editor_int32(x)},{_editor_int32(y)}"
    element.attrib["size"] = f"{_editor_int32(width)},{_editor_int32(height)}"
    if element.tag == "text" and node.text is not None:
        element.attrib["text"] = node.text
    if "opacity" in node.model_fields_set:
        element.attrib["alpha"] = _number(node.opacity)
    if "rotation" in node.model_fields_set:
        element.attrib["rotation"] = _number(node.rotation)
    if element.tag == "graph" and (root.id.startswith("psd-root:") or "fguiGraph" in node.properties):
        from figma_to_fgui.fgui_plan_models import GraphPlan
        payload = node.properties.get("fguiGraph")
        if payload is None or node.resource_keys:
            raise HifiPatchError(
                f"hifi_in_place_raster_unsupported:{element.get('id')}:{node.id}"
            )
        try:
            graph = GraphPlan.model_validate(payload)
        except ValueError as error:
            raise HifiPatchError("hifi_native_graph_invalid") from error
        for attribute in _GRAPH_VISUAL_ATTRIBUTES:
            element.attrib.pop(attribute, None)
        element.set("type", graph.shape)
        element.set("lineSize", _number(float(graph.line_size)))
        element.set("fillColor", graph.fill_color or "#00000000")
        if graph.line_color is not None:
            element.set("lineColor", graph.line_color)
        if graph.corner_radius is not None:
            element.set("corner", _number(float(graph.corner_radius)))
        elif graph.corner_radii is not None:
            element.set("corner", ",".join(_number(float(v)) for v in graph.corner_radii))


def _new_object_id(node_id: str) -> str:
    return "hifi_" + hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:10]


def _new_visual(
    node: SelectionNode,
    root: SelectionNode,
    inventory: FguiComponentInventory,
    resource_id: str | None = None,
    file_name: str | None = None,
    font_uris: dict[str, str] | None = None,
) -> etree._Element:
    node_type = node.type.upper()
    if resource_id is not None:
        element = etree.Element("image")
        element.attrib["src"] = resource_id
        if file_name is not None:
            element.attrib["fileName"] = file_name
    elif node_type == "TEXT":
        element = etree.Element("text")
        if node.text is not None:
            element.attrib["text"] = node.text
    elif node_type in {"RECTANGLE", "ELLIPSE", "VECTOR", "LINE", "POLYGON", "STAR"}:
        element = etree.Element("graph")
        element.attrib["type"] = "ellipse" if node_type == "ELLIPSE" else "rect"
    else:
        raise HifiPatchError("unsupported_added_visual")
    element.attrib["id"] = _new_object_id(node.id)
    element.attrib["name"] = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", node.name)[:128] or element.attrib["id"]
    # Only explicitly authorized additions use this helper. Mapped legacy
    # objects must be updated in place and must never receive a sibling skin.
    element.attrib["touchable"] = "false"
    _set_visual(element, node, root, inventory, font_uris or {})
    return element


def _package_manifest(root: Path, inventory: FguiComponentInventory) -> tuple[Path, str]:
    component_path = Path(inventory.target.component_relative_path)
    parts = component_path.parts
    if len(parts) < 3:
        raise HifiPatchError("invalid_target_package")
    package_root = Path(*parts[:2]) if parts[0] == "assets" else Path(parts[0])
    manifest = root / package_root / "package.xml"
    if not manifest.is_file():
        raise HifiPatchError("invalid_target_package")
    return manifest, package_root.as_posix()


def _resource_extension(mime_type: str) -> str:
    return {"image/png": ".png", "image/webp": ".webp", "image/svg+xml": ".svg"}[mime_type]


def build_hifi_change_bundle(
    root: Path,
    inventory: FguiComponentInventory,
    selection: SelectionManifest,
    mapping: HifiMappingDraft,
    *,
    job_id: str = "hifi-replacement",
    selection_root: Path | None = None,
    parity_reference: Path | None = None,
) -> ChangeBundle:
    from figma_to_fgui.hifi_mapping import is_psd_visual_empty
    if any(item.visual_echo for item in mapping.items):
        raise HifiPatchError("hifi_visual_echo_forbidden")
    source_owners: dict[str, str] = {}
    for item in mapping.items:
        if item.action not in {"accept", "retarget"}:
            continue
        owned_ids = set(item.owned_source_ids) | set(item.composite_source_ids)
        if not owned_ids and item.figma_node_id:
            owned_ids.add(item.figma_node_id)
        for source_id in owned_ids:
            previous = source_owners.setdefault(source_id, item.item_id)
            if previous != item.item_id:
                raise HifiPatchError("duplicate_psd_pixel_ownership")
    source_nodes = _flatten(selection)
    for item in mapping.items:
        if item.action != "preserve_structure":
            continue
        if item.out_of_scope or item.occluded:
            # Scope and occlusion are proven decisions backed by geometry or
            # covering evidence; they are not structural waivers and the
            # candidate must not write these objects either way.
            continue
        if item.old_object_id:
            original = next((o for o in inventory.objects if o.object_id == item.old_object_id), None)
            valid = original is not None and original.structural_only and item.figma_node_id is None
        else:
            node = source_nodes.get(item.figma_node_id or "")
            valid = node is not None and is_psd_visual_empty(node)
        if not valid:
            raise HifiPatchError("hifi_structural_resolution_invalid")
    if inventory.expanded_instances:
        from figma_to_fgui.hifi_nested import build_nested_bundle
        return build_nested_bundle(root, inventory, selection, mapping, job_id=job_id,
                                   selection_root=selection_root)
    if mapping.unresolved_count:
        raise HifiPatchError("hifi_mapping_incomplete")
    if len(selection.top_level_nodes) != 1:
        raise HifiPatchError("hifi_selection_requires_single_root")
    selection_root_node = selection.top_level_nodes[0]
    if (
        selection_root_node.id.startswith("psd-root:")
        and not any(item.action in {"accept", "retarget"} for item in mapping.items)
    ):
        raise HifiPatchError("hifi_mapping_requires_replacements")
    if selection_root_node.id.startswith("psd-root:") and any(
        item.action == "add_visual" for item in mapping.items
    ):
        raise HifiPatchError("hifi_psd_visual_requires_existing_object")
    relative_path = safe_relative_path(inventory.target.component_relative_path)
    source = root / relative_path
    before = source.read_bytes()
    document = etree.parse(str(source), _PARSER)
    component = document.getroot()
    display_list = component.find("displayList")
    if display_list is None:
        raise HifiPatchError("component_display_list_missing")
    by_old_id = {
        str(element.attrib["id"]): element
        for element in display_list
        if element.attrib.get("id")
    }
    nodes = _flatten(selection)
    declared_resources = {resource.key: resource for resource in selection.resources}
    generated_resources: dict[str, tuple[str, str, str, bytes]] = {}
    font_uris = _project_font_uris(root)

    def material(node: SelectionNode) -> tuple[str | None, str | None]:
        if not node.resource_keys:
            return None, None
        if selection_root is None:
            raise HifiPatchError("selection_resource_unavailable")
        key = node.resource_keys[0]
        declared = declared_resources.get(key)
        if declared is None:
            raise HifiPatchError("selection_resource_unavailable")
        source_resource = selection_root / "resources" / key
        try:
            content = source_resource.read_bytes()
        except OSError as error:
            raise HifiPatchError("selection_resource_unavailable") from error
        if len(content) != declared.size:
            raise HifiPatchError("selection_resource_unavailable")
        token = hashlib.sha256((inventory.target.component_id + "\0" + key).encode("utf-8")).hexdigest()
        resource_id = "h" + token[:8]
        extension = _resource_extension(declared.mime_type)
        file_name = re.sub(r"[^A-Za-z0-9_\-]", "_", node.name)[:80] + "-" + token[:8] + extension
        virtual_path = f"/Img/HIFI/{inventory.target.component_name}/"
        relative_path = safe_relative_path(
            f"{Path(inventory.target.component_relative_path).parent.parent.as_posix()}/{virtual_path.strip('/')}/{file_name}"
        )
        generated_resources[key] = (resource_id, file_name, relative_path, content)
        return resource_id, file_name

    claimed: set[str] = set()
    for item in mapping.items:
        if item.action in {"accept", "retarget"}:
            if item.old_object_id is None or item.figma_node_id is None:
                raise HifiPatchError("invalid_mapping")
            if item.figma_node_id in claimed:
                raise HifiPatchError("duplicate_figma_mapping")
            claimed.add(item.figma_node_id)
            try:
                element = by_old_id[item.old_object_id]
                node = nodes[item.figma_node_id]
            except KeyError as error:
                raise HifiPatchError("mapping_target_missing") from error
            if node.properties.get("hifiViewportEmpty") is True:
                continue
            if (
                selection_root_node.id.startswith("psd-root:")
                and element.tag == "component"
                and node.children
            ):
                # Moving an instance does not replace its referenced visual
                # children. Each child and affected state needs its own map.
                raise HifiPatchError("hifi_nested_visual_mapping_required")
            if (
                selection_root_node.id.startswith("psd-root:")
                and element.tag == "graph"
                and node.resource_keys
                and min(node.bounds.width, node.bounds.height) <= 1
            ):
                # Cropping a PSD overflow/letterbox layer to the component
                # viewport can leave a one-pixel transparent boundary. It has
                # no useful skin to apply and must not force a GGraph into a
                # GImage merely to satisfy source bookkeeping.
                continue
            if element.tag == "graph" and node.resource_keys:
                if selection_root_node.id.startswith("psd-root:"):
                    raise HifiPatchError(
                        f"hifi_display_type_change_forbidden:{item.old_object_id}"
                    )
                if not can_convert_mapping(root, inventory, item, node):
                    raise HifiPatchError("hifi_type_conversion_not_authorized")
                convert_graph(element)
            if element.tag == "component" and item.logical_bounds_policy == "resize":
                node = node.model_copy(update={
                    "properties": {**node.properties, "hifiResizeLogicalBounds": True}
                })
            _set_visual(element, node, selection_root_node, inventory, font_uris)
            old = next((value for value in inventory.objects if value.object_id == item.old_object_id), None)
            resource_id, file_name = material(node)
            if resource_id is not None and old is not None:
                _prepare_psd_raster_target(element)
                if element.tag == "image":
                    element.attrib["src"] = resource_id
                    element.attrib.pop("pkg", None)
                    if file_name is not None:
                        element.attrib["fileName"] = file_name
                elif element.tag == "loader":
                    manifest_path, _ = _package_manifest(root, inventory)
                    package_id = etree.parse(str(manifest_path), _PARSER).getroot().get("id")
                    if not package_id:
                        raise HifiPatchError("invalid_target_package")
                    element.attrib["url"] = f"ui://{package_id}{resource_id}"
                else:
                    # GGraph/GComponent cannot become a GImage without
                    # changing the runtime object contract. Never disguise
                    # an unsupported replacement with a new display object.
                    raise HifiPatchError("hifi_in_place_raster_unsupported")
        elif item.action == "exception" and item.status == "fgui_only":
            old = next(
                (v for v in inventory.objects if v.object_id == item.old_object_id),
                None,
            )
            element = by_old_id.get(item.old_object_id or "")
            if old is not None and old.default_visible and element is not None:
                # The design dropped this object. Keep the object and every
                # program attribute; only its default visibility turns off.
                element.set("visible", "false")
        elif item.action == "keep_old" and item.visual_disposition == "retire":
            element = by_old_id.get(item.old_object_id or "")
            if element is None:
                raise HifiPatchError("mapping_target_missing")
            # Retire only the default target-state contribution. Object ID,
            # type, gears, relations, transitions and instance parameters stay
            # untouched and can still be addressed by runtime code/controllers.
            element.set("visible", "false")
        elif item.action == "add_visual":
            if item.figma_node_id is None or item.figma_node_id in claimed:
                continue
            try:
                node = nodes[item.figma_node_id]
            except KeyError as error:
                raise HifiPatchError("mapping_target_missing") from error
            resource_id, file_name = material(node)
            element = _new_visual(
                node,
                selection_root_node,
                inventory,
                resource_id,
                file_name,
                font_uris,
            )
            display_list.append(element)
            claimed.add(item.figma_node_id)
        elif item.action not in {"keep_old", "exception", "preserve_structure"}:
            raise HifiPatchError("invalid_mapping_action")
    after = etree.tostring(
        document,
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=False,
    )
    changes: list[ChangeFile] = [
        ChangeFile(
            operation=FileOperation.REPLACE,
            relative_path=relative_path,
            before_sha256=hashlib.sha256(before).hexdigest(),
            after_sha256=hashlib.sha256(after).hexdigest(),
            content_b64=base64.b64encode(after).decode("ascii"),
        )
    ]
    if generated_resources:
        manifest_path, package_root = _package_manifest(root, inventory)
        manifest_relative = manifest_path.relative_to(root).as_posix()
        manifest_before = manifest_path.read_bytes()
        manifest_document = etree.parse(str(manifest_path), _PARSER)
        resources_element = manifest_document.getroot().find("resources")
        if resources_element is None:
            raise HifiPatchError("invalid_target_package")
        existing_ids = {str(element.attrib.get("id", "")) for element in resources_element}
        existing_names = {str(element.attrib.get("name", "")) for element in resources_element}
        for resource_id, file_name, resource_relative, content in sorted(generated_resources.values()):
            if resource_id in existing_ids or file_name in existing_names:
                raise HifiPatchError("hifi_resource_conflict")
            virtual_parent = "/" + Path(resource_relative).parent.relative_to(Path(package_root)).as_posix().strip("/") + "/"
            etree.SubElement(
                resources_element,
                "image",
                id=resource_id,
                name=file_name,
                path=virtual_parent,
                exported="true",
                # PSD fragments are already exact raster evidence. Keeping them
                # outside an atlas avoids editor repacking/cropping (notably for
                # layers taller than the default 2048 texture limit).
                atlas="0",
            )
            changes.append(
                ChangeFile(
                    operation=FileOperation.CREATE,
                    relative_path=resource_relative,
                    after_sha256=hashlib.sha256(content).hexdigest(),
                    content_b64=base64.b64encode(content).decode("ascii"),
                )
            )
        manifest_after = etree.tostring(
            manifest_document,
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=True,
        )
        changes.append(
            ChangeFile(
                operation=FileOperation.REPLACE,
                relative_path=manifest_relative,
                before_sha256=hashlib.sha256(manifest_before).hexdigest(),
                after_sha256=hashlib.sha256(manifest_after).hexdigest(),
                content_b64=base64.b64encode(manifest_after).decode("ascii"),
            )
        )
    return ChangeBundle(
        version=1,
        job_id=job_id,
        project_id=inventory.target.project_id,
        files=tuple(changes),
    )


def _protected_object(element: etree._Element) -> bytes:
    copy = etree.fromstring(etree.tostring(element))
    mutable = set(_COMMON_VISUAL_ATTRIBUTES)
    if copy.tag in {"text", "richtext"}:
        mutable.update(_TEXT_VISUAL_ATTRIBUTES)
    elif copy.tag == "image":
        mutable.update(_IMAGE_VISUAL_ATTRIBUTES)
    elif copy.tag == "loader":
        mutable.update(_LOADER_VISUAL_ATTRIBUTES)
    elif copy.tag == "graph":
        mutable.update(_GRAPH_VISUAL_ATTRIBUTES)

    for attribute in tuple(copy.attrib):
        if attribute in mutable:
            del copy.attrib[attribute]
    for gear in copy.findall("gearColor"):
        # Color payload is visual state. Controller/page wiring remains in the
        # protected representation and is separately required to stay intact.
        gear.attrib.pop("values", None)
        gear.attrib.pop("default", None)
    if copy.tag == "component":
        # Button/Label instance parameters are the public visual defaults for
        # mapped nested title/icon objects. Exported-controller selections are
        # also instance visual defaults; controller definitions/actions,
        # relations, properties and every other child remain protected.
        copy.attrib.pop("controller", None)
        for parameter in (*copy.findall("Button"), *copy.findall("Label")):
            parameter.attrib.pop("title", None)
            parameter.attrib.pop("icon", None)
    for gear in copy.findall("gearIcon"):
        gear.attrib.pop("default", None)
        gear.attrib.pop("values", None)
    return cast(bytes, etree.tostring(copy, method="c14n", with_comments=True))


def _protected_component_structure(document: etree._ElementTree) -> bytes:
    copy = etree.fromstring(etree.tostring(document.getroot()))
    copy.attrib.pop("size", None)
    copy.attrib.pop("restrictSize", None)
    for controller in copy.findall("controller"):
        controller.attrib.pop("selected", None)
    display_list = copy.find("displayList")
    if display_list is not None:
        for child in tuple(display_list):
            display_list.remove(child)
    return cast(bytes, etree.tostring(copy, method="c14n", with_comments=True))


def _xml_bounds(element: etree._Element) -> tuple[float, float, float, float]:
    def pair(value: str | None) -> tuple[float, float]:
        if value is None:
            return 0.0, 0.0
        try:
            first, second = value.split(",", 1)
            return float(first), float(second)
        except (TypeError, ValueError):
            return 0.0, 0.0

    x, y = pair(element.attrib.get("xy"))
    width, height = pair(element.attrib.get("size"))
    return x, y, max(0.0, width), max(0.0, height)


def _overlaps(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    if min(first_width, first_height, second_width, second_height) <= 0:
        return False
    return (
        first_x < second_x + second_width
        and second_x < first_x + first_width
        and first_y < second_y + second_height
        and second_y < first_y + first_height
    )


def _behavior_occlusions(
    document: etree._ElementTree,
    inventory: FguiComponentInventory,
) -> tuple[str, ...]:
    display_list = document.getroot().find("displayList")
    if display_list is None:
        return ()
    ordered = tuple(display_list)
    indexes = {str(element.attrib.get("id", "")): index for index, element in enumerate(ordered)}
    added = tuple(
        (index, element)
        for index, element in enumerate(ordered)
        if str(element.attrib.get("id", "")).startswith("hifi_")
        and element.attrib.get("visible", "true") != "false"
        and element.attrib.get("alpha", "1") != "0"
    )
    blocked: list[str] = []
    for item in inventory.objects:
        if not item.behavior_protected:
            continue
        old_index = indexes.get(item.object_id)
        if old_index is None:
            continue
        old_bounds = (item.x, item.y, item.width, item.height)
        if any(index > old_index and _overlaps(old_bounds, _xml_bounds(element)) for index, element in added):
            blocked.append(item.object_id)
    return tuple(blocked)


def validate_hifi_candidate(
    before_root: Path,
    after_root: Path,
    inventory: FguiComponentInventory,
    mapping: HifiMappingDraft,
    *,
    session_id: str,
    lossless_blockers: tuple[str, ...] = (),
    _scope_paths: frozenset[str] = frozenset(),
    _scope_prefixes: tuple[str, ...] = (),
    _after_component_doc: etree._ElementTree | None = None,
) -> HifiReplacementReview:
    if inventory.expanded_instances:
        from figma_to_fgui.hifi_nested import validate_nested_candidate
        return validate_nested_candidate(before_root, after_root, inventory, mapping,
                                         session_id=session_id, lossless_blockers=lossless_blockers)
    relative = safe_relative_path(inventory.target.component_relative_path)
    before_files = {
        path.relative_to(before_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in before_root.rglob("*")
        if path.is_file() and ".figma-to-fgui" not in path.parts
    }
    after_files = {
        path.relative_to(after_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in after_root.rglob("*")
        if path.is_file() and ".figma-to-fgui" not in path.parts
    }
    changed = sorted(path for path in set(before_files) | set(after_files) if before_files.get(path) != after_files.get(path))
    manifest_path, package_root = _package_manifest(before_root, inventory)
    manifest_relative = manifest_path.relative_to(before_root).as_posix()
    allowed_prefix = f"{package_root}/Img/HIFI/{inventory.target.component_name}/"
    if any(
        path not in {relative, manifest_relative} and path not in _scope_paths
        and not path.startswith((allowed_prefix, *_scope_prefixes))
        for path in changed
    ):
        raise HifiPatchError("hifi_scope_violation")
    before_doc = etree.parse(str(before_root / relative), _PARSER)
    after_doc = _after_component_doc or etree.parse(str(after_root / relative), _PARSER)
    before_ids = before_doc.xpath("./displayList/*/@id")
    after_ids = after_doc.xpath("./displayList/*/@id")
    before_types = {
        str(element.attrib["id"]): element.tag
        for element in before_doc.xpath("./displayList/*[@id]")
    }
    after_types = {
        str(element.attrib["id"]): element.tag
        for element in after_doc.xpath("./displayList/*[@id]")
    }
    expected_added = {
        _new_object_id(item.figma_node_id)
        for item in mapping.items
        if item.action == "add_visual" and item.figma_node_id is not None
    }
    if (
        len(set(after_ids)) != len(after_ids)
        or len(after_ids) != len(after_doc.xpath("./displayList/*"))
        or [object_id for object_id in after_ids if object_id in before_ids] != before_ids
        or set(after_ids) - set(before_ids) != expected_added
        or any(after_types.get(object_id) != tag for object_id, tag in before_types.items())
    ):
        raise HifiPatchError("hifi_display_list_changed")
    if _protected_component_structure(before_doc) != _protected_component_structure(after_doc):
        raise HifiPatchError("hifi_protected_structure_changed")
    before_by_id = {
        str(element.attrib["id"]): element
        for element in before_doc.xpath("./displayList/*[@id]")
    }
    after_by_id = {
        str(element.attrib["id"]): element
        for element in after_doc.xpath("./displayList/*[@id]")
    }
    protected_ok = True
    for object_id, before_element in before_by_id.items():
        after_element = after_by_id.get(object_id)
        structural = any(
            i.old_object_id == object_id and i.action == "preserve_structure"
            and not i.out_of_scope and not i.occluded
            for i in mapping.items
        )
        if structural:
            original = next((o for o in inventory.objects if o.object_id == object_id), None)
            if original is None or not original.structural_only or after_element is None:
                raise HifiPatchError("hifi_structural_resolution_invalid")
            if (etree.tostring(before_element, method="c14n")
                    != etree.tostring(after_element, method="c14n")):
                # Variant isolation may retarget a preserved instance to its
                # own private clone (__hifi_ file). The clone is a byte copy
                # plus deeper isolation, so the reference switch preserves
                # the object contract; every other attribute must stay
                # identical.
                normalized_after = copy.deepcopy(after_element)
                mapped_ids = {
                    item.old_object_id for item in mapping.items
                    if item.action in {"accept", "retarget"}
                }
                variant_reference = "__hifi_" in (normalized_after.get("fileName") or "")
                derived_group = original.object_type == "group" and any(
                    child.parent_id == object_id and child.object_id in mapped_ids
                    for child in inventory.objects
                )
                if not variant_reference and not derived_group:
                    raise HifiPatchError("hifi_structural_resolution_invalid")
                if variant_reference:
                    normalized_after.set("src", before_element.get("src"))
                    if before_element.get("fileName") is None:
                        normalized_after.attrib.pop("fileName", None)
                    else:
                        normalized_after.set("fileName", before_element.get("fileName"))
                if derived_group:
                    # Group geometry is derived from its moved/resized
                    # members. Membership, order, relations and every other
                    # contract attribute remain byte-identical.
                    for attribute in ("xy", "size", "colGap", "lineGap"):
                        if before_element.get(attribute) is None:
                            normalized_after.attrib.pop(attribute, None)
                        else:
                            normalized_after.set(attribute, before_element.get(attribute))
                if (etree.tostring(before_element, method="c14n")
                        != etree.tostring(normalized_after, method="c14n")):
                    raise HifiPatchError("hifi_structural_resolution_invalid")
        comparable_before = before_element
        graph_decision = next((i for i in mapping.items if i.old_object_id == object_id), None)
        if (before_element.tag == "graph" and after_element is not None
                and after_element.tag == "image" and
                (can_convert(before_root, inventory, object_id)
                 or graph_decision is not None and graph_decision.graph_conversion_proven
                 and graph_decision.action in {"accept", "retarget"}
                 and after_element.get("src"))):
            comparable_before = etree.fromstring(etree.tostring(before_element))
            convert_graph(comparable_before)
        if after_element is None:
            protected_ok = False
            break
        if (before_element.tag == "component"
                and ("__hifi_" in (before_element.get("fileName") or "")
                     or "__hifi_" in (after_element.get("fileName") or ""))):
            # Variant isolation retargets an instance to its private clone;
            # the clone is a byte copy of the referenced definition plus
            # deeper isolation, so the reference switch is not a structural
            # change. Normalise both sides before the protected comparison.
            normalized_before = copy.deepcopy(before_element)
            normalized_before.attrib.pop("src", None)
            normalized_before.attrib.pop("fileName", None)
            normalized_after = copy.deepcopy(after_element)
            normalized_after.attrib.pop("src", None)
            normalized_after.attrib.pop("fileName", None)
            if _protected_object(normalized_before) != _protected_object(normalized_after):
                protected_ok = False
                break
        elif _protected_object(comparable_before) != _protected_object(after_element):
            protected_ok = False
            break
        old = next((item for item in inventory.objects if item.object_id == object_id), None)
        if (old is not None and old.shared_resource
                and before_element.attrib.get("src") != after_element.attrib.get("src")
                and "__hifi_" not in (after_element.get("fileName") or "")):
            protected_ok = False
            break
    if not protected_ok:
        raise HifiPatchError("hifi_protected_structure_changed")
    package = etree.parse(str(after_root / manifest_relative), _PARSER).getroot()
    resource_ids = {str(item.attrib.get("id", "")) for item in package.xpath("./resources/*")}
    for element in after_doc.xpath("./displayList/*[@src]"):
        source_package = element.attrib.get("pkg")
        if source_package is None and str(element.attrib["src"]) not in resource_ids:
            raise HifiPatchError("hifi_resource_closure_invalid")
    diff_items = tuple(
        HifiDiffItem(
            version=1,
            relative_path=path,
            operation="replace" if path in before_files else "create",
            before_sha256=before_files.get(path),
            after_sha256=after_files[path],
            summary="更新已确认的视觉属性并保留原对象结构",
        )
        for path in changed
    )
    warnings = tuple(f"PSD 无损证据待 Editor 验证：{code}" for code in lossless_blockers)
    unverified_state_objects = tuple(
        item.object_id for item in inventory.objects
        if item.behavior_protected and item.object_id in before_by_id
        and etree.tostring(before_by_id[item.object_id], method="c14n")
        != etree.tostring(after_by_id[item.object_id], method="c14n")
    )
    if unverified_state_objects:
        warnings += ("受保护对象的视觉发生变化，尚缺少各状态与交互验证："
                     + ", ".join(unverified_state_objects),)
    behavior_occlusions = _behavior_occlusions(after_doc, inventory)
    if behavior_occlusions:
        warnings += (
            "新增 HIFI 图层遮挡受状态、实例参数、关系或动画控制的旧对象："
            + ", ".join(behavior_occlusions),
        )
    if not inventory.parse_complete:
        warnings += ("目标组件含未知标签或属性；候选保留其原始字节结构，仍需 Editor 检查。",)
    return HifiReplacementReview(
        version=1,
        policy_revision=HIFI_MAPPING_POLICY_REVISION,
        session_id=session_id,
        mapping_revision=mapping.mapping_revision,
        target=inventory.target,
        changed_files=diff_items,
        object_diffs=build_object_diffs(before_doc, after_doc, mapping),
        protected_checks_passed=True,
        parse_coverage_complete=inventory.parse_complete,
        # Lossless equivalence, state and occlusion findings stay as
        # warnings: they are discharged by the FairyGUI Editor verification
        # and the human editor checks, not by build-time inspection.
        approvable=inventory.parse_complete,
        warnings=warnings,
        editor_check_required=True,
    )
