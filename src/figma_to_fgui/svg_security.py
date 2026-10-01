"""Fail-closed validation for self-contained SVG image resources."""

from __future__ import annotations

import math

from lxml import etree

MAX_SVG_BYTES = 2 * 1024 * 1024
_FORBIDDEN_TAGS = {
    "script",
    "foreignobject",
    "animate",
    "animatetransform",
    "animatemotion",
    "set",
    "iframe",
    "object",
    "embed",
    "audio",
    "video",
    "style",
}


def _safe_svg_root(content: bytes) -> etree._Element:
    if (
        len(content) > MAX_SVG_BYTES
        or b"<!DOCTYPE" in content.upper()
        or b"<!ENTITY" in content.upper()
    ):
        raise ValueError("invalid svg")
    try:
        root = etree.fromstring(
            content,
            etree.XMLParser(
                resolve_entities=False,
                no_network=True,
                load_dtd=False,
                huge_tree=False,
            ),
        )
    except (etree.XMLSyntaxError, ValueError) as error:
        raise ValueError("invalid svg") from error
    if etree.QName(root).localname.lower() != "svg":
        raise ValueError("invalid svg")
    for element in root.iter():
        if (
            not isinstance(element.tag, str)
            or etree.QName(element).localname.lower() in _FORBIDDEN_TAGS
        ):
            raise ValueError("invalid svg")
        for name, value in element.attrib.items():
            local_name = etree.QName(name).localname.lower()
            normalized = value.strip().lower()
            if (
                local_name.startswith("on")
                or local_name == "style"
                or (local_name in {"href", "src"} and not normalized.startswith("#"))
                or ("url(" in normalized and not normalized.startswith("url(#"))
            ):
                raise ValueError("invalid svg")
    return root


def validate_safe_svg(content: bytes) -> None:
    """Accept only bounded, inert SVG with internal fragment references."""
    _safe_svg_root(content)


def safe_svg_dimensions(content: bytes) -> tuple[int, int] | None:
    """Return positive integral intrinsic dimensions from one safe SVG."""
    root = _safe_svg_root(content)
    values: list[int] = []
    for name in ("width", "height"):
        raw = root.get(name)
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        if not math.isfinite(value) or value <= 0 or not value.is_integer():
            return None
        values.append(int(value))
    return values[0], values[1]
