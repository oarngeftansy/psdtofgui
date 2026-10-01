"""Derive new controller-state artwork from PSD color evidence.

Only recognized, fully controller-scoped badge components are eligible. The
generated nodes keep each existing FGUI object and state page; unsupported
states remain unresolved instead of reusing legacy artwork.
"""

from __future__ import annotations

import colorsys
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from lxml import etree
from PIL import Image, ImageDraw

from figma_to_fgui.figma_selection import SelectionNode, SelectionResource
from figma_to_fgui.hifi_replacement_models import FguiComponentInventory
from figma_to_fgui.models import Bounds

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)
_SEMANTIC_PAGES = {"normal", "num", "unlock", "gift", "up", "uphigher", "new"}


def _palette(composite: Path, style_images: tuple[Path, ...] = ()) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    samples = []
    for path in style_images or (composite,):
        with Image.open(path) as opened:
            sample = opened.convert("RGBA")
            sample.thumbnail((256, 256))
            pixels = np.asarray(sample, dtype=np.uint8).reshape(-1, 4)
            samples.append(pixels[pixels[:, 3] >= 128, :3])
    pixels = np.concatenate(samples)
    candidates: list[tuple[float, tuple[int, ...]]] = []
    light: list[tuple[int, ...]] = []
    for color in pixels[::2]:
        rgb = tuple(int(v) for v in color)
        _, saturation, value = colorsys.rgb_to_hsv(*(v / 255 for v in rgb))
        if saturation > .38 and value > .72:
            candidates.append((value, rgb))
        if saturation < .22 and value > .82:
            light.append(rgb)
    if not candidates or not light:
        raise ValueError("hifi_state_palette_unavailable")
    bright_cutoff = max(.86, float(np.quantile([v for v, _ in candidates], .75)))
    bright = [color for value, color in candidates if value >= bright_cutoff]
    accent = tuple(int(np.median([v[channel] for v in bright])) for channel in range(3))
    fill = tuple(int(np.median([v[channel] for v in light])) for channel in range(3))
    return accent, fill  # type: ignore[return-value]


def _render(role: str, width: int, height: int, accent: tuple[int, int, int],
            fill: tuple[int, int, int]) -> Image.Image:
    scale = 4
    w, h = width * scale, height * scale
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    stroke = max(2, round(min(w, h) * .065))
    inset = stroke + scale
    box = (inset, inset, w - inset, h - inset)
    shadow = (max(0, inset - scale), inset + scale, w - inset + scale, h - inset + scale)
    if role in {"normal", "num", "new"}:
        radius = min(w, h) // 2 if role != "new" else h // 3
        draw.rounded_rectangle(shadow, radius=radius, fill=(35, 25, 20, 80))
        draw.rounded_rectangle(box, radius=radius, fill=(*fill, 255), outline=(*accent, 255), width=stroke)
        if role == "normal":
            inner = max(stroke * 2, min(w, h) // 5)
            draw.ellipse((inner, inner, w - inner, h - inner), fill=(*accent, 240))
        if role == "num":
            glint = max(stroke, min(w, h) // 8)
            draw.arc((glint, glint, w - glint, h - glint), 205, 325, fill=(255, 255, 255, 220), width=scale)
    elif role == "unlock":
        draw.rounded_rectangle((w * .2, h * .43, w * .8, h * .82), radius=h * .07,
                               fill=(*fill, 255), outline=(*accent, 255), width=stroke)
        draw.arc((w * .3, h * .12, w * .7, h * .67), 190, 345, fill=(*accent, 255), width=stroke)
        draw.ellipse((w * .46, h * .57, w * .54, h * .66), fill=(*accent, 255))
    elif role == "gift":
        draw.rounded_rectangle((w * .17, h * .4, w * .83, h * .82), radius=h * .04,
                               fill=(*fill, 255), outline=(*accent, 255), width=stroke)
        draw.rectangle((w * .12, h * .34, w * .88, h * .49), fill=(*accent, 255))
        draw.rectangle((w * .46, h * .34, w * .54, h * .82), fill=(*accent, 255))
        draw.arc((w * .25, h * .12, w * .52, h * .43), 80, 340, fill=(*accent, 255), width=stroke)
        draw.arc((w * .48, h * .12, w * .75, h * .43), 200, 460, fill=(*accent, 255), width=stroke)
    elif role == "up":
        points = [(w * .5, h * .1), (w * .9, h * .48), (w * .68, h * .48),
                  (w * .68, h * .86), (w * .32, h * .86), (w * .32, h * .48), (w * .1, h * .48)]
        draw.polygon(points, fill=(*accent, 255))
        draw.line([points[0], points[1], points[2]], fill=(*fill, 255), width=scale * 2, joint="curve")
    else:
        raise ValueError("hifi_state_role_unsupported")
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _state_roles(document: etree._Element) -> dict[str, str] | None:
    visual = document.xpath("./displayList/image[@id] | ./displayList/text[@id]")
    if not visual:
        return None
    controllers = document.findall("controller")
    for controller in controllers:
        pages = controller.get("pages", "").split(",")
        if len(pages) % 2:
            continue
        by_id = {pages[index]: pages[index + 1].casefold().replace("_", "")
                 for index in range(0, len(pages), 2)}
        if not ({"normal", "num", "new"} <= set(by_id.values())
                and set(by_id.values()) <= _SEMANTIC_PAGES):
            continue
        groups = {group.get("id"): group for group in document.xpath("./displayList/group[@id]")}
        roles = {}
        for element in visual:
            gear = element.find("gearDisplay")
            if gear is None and element.get("group") in groups:
                gear = groups[element.get("group")].find("gearDisplay")
            if gear is None or gear.get("controller") != controller.get("name"):
                break
            states = {by_id.get(page) for page in gear.get("pages", "").split(",")}
            if None in states or not states:
                break
            if element.tag == "text":
                role = "text"
            elif states <= {"up", "uphigher"}:
                role = "up"
            elif len(states) == 1:
                role = next(iter(states))  # type: ignore[assignment]
            else:
                break
            roles[element.get("id")] = role
        if len(roles) == len(visual):
            return roles
    return None


def derive_state_nodes(
    root: Path, inventory: FguiComponentInventory, composite: Path,
    resource_dir: Path, *, viewport_offset: tuple[float, float] = (0, 0),
    style_images: tuple[Path, ...] = (),
) -> tuple[tuple[SelectionNode, ...], tuple[SelectionResource, ...]]:
    """Return new visual nodes for eligible existing state objects only."""
    by_path: dict[str, list[Any]] = {}
    for item in inventory.objects:
        if item.component_relative_path and item.object_type in {"image", "text"}:
            by_path.setdefault(item.component_relative_path, []).append(item)
    accent, fill = _palette(composite, style_images)
    image_paths = {}
    for manifest in (*root.glob("*/package.xml"), *root.glob("assets/*/package.xml")):
        package = etree.parse(str(manifest), _PARSER).getroot()
        package_id = package.get("id")
        for resource in package.xpath("./resources/image[@id][@name]"):
            image_paths[(package_id, resource.get("id"))] = (
                manifest.parent / resource.get("path", "/").strip("/") / resource.get("name")
            )
    nodes = []
    resources = {}
    for path, objects in by_path.items():
        source = (root / path).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            continue
        document = etree.parse(str(source), _PARSER)
        roles = _state_roles(document.getroot())
        if roles is None:
            continue
        elements = {element.get("id"): element for element in document.xpath(
            "./displayList/image[@id] | ./displayList/text[@id]"
        )}
        package_root = next((parent for parent in source.parents if (parent / "package.xml").is_file()), None)
        package_id = etree.parse(str(package_root / "package.xml"), _PARSER).getroot().get("id") if package_root else None
        for item in objects:
            role = roles.get(item.local_object_id)
            if role is None:
                continue
            element = elements[item.local_object_id]
            width, height = item.width, item.height
            if role != "text" and (width <= 0 or height <= 0):
                image_path = image_paths.get((element.get("pkg") or package_id, element.get("src")))
                if image_path is None or not image_path.is_file():
                    continue
                with Image.open(image_path) as old_image:
                    width, height = old_image.size
            if width <= 0 or height <= 0:
                continue
            node_id = "derived-state:" + hashlib.sha256(item.object_id.encode()).hexdigest()[:24]
            bounds = Bounds(x=item.x + viewport_offset[0], y=item.y + viewport_offset[1],
                            width=width, height=height)
            keys = ()
            if role != "text":
                width, height = round(width), round(height)
                if width < 1 or height < 1 or width * height > 1_000_000:
                    continue
                key = "state-" + hashlib.sha256(
                    (path + "\0" + item.local_object_id + "\0" + role + "\0"
                     + str(accent) + str(fill)).encode()
                ).hexdigest()[:32]
                destination = resource_dir / key
                if not destination.is_file():
                    resource_dir.mkdir(parents=True, exist_ok=True)
                    _render(role, width, height, accent, fill).save(destination, format="PNG")
                resources[key] = SelectionResource(
                    key=key, mime_type="image/png", size=destination.stat().st_size,
                )
                keys = (key,)  # type: ignore[assignment]
            style = {}
            if role == "text":
                font_size = float(element.get("fontSize", "20"))
                dark = tuple(max(0, round(channel * .45)) / 255 for channel in accent)
                style = {"psdTextStyle": {
                    "runs": [{"font_size": font_size, "fill_rgba": (*dark, 1.0)}],
                    "transform": (1, 0, 0, 1, 0, 0),
                }}
            nodes.append(SelectionNode(
                id=node_id, name=f"推导状态视觉 · {item.name}",
                type="TEXT" if role == "text" else "IMAGE", bounds=bounds,
                style=style,
                resource_keys=keys,
                properties={"generatedStateOwner": item.object_id, "generatedStateRole": role},
            ))
    return tuple(nodes), tuple(resources.values())
