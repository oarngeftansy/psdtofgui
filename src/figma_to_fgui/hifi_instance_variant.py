"""Create an isolated visual variant of one existing component instance.

The caller works on a disposable project copy. This primitive preserves the
instance and child identities; candidate validation remains a separate gate.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from lxml import etree
from PIL import Image, UnidentifiedImageError

from figma_to_fgui.hifi_project_inspector import _package_resource_index

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


@dataclass(frozen=True)
class InstanceVariant:
    original_component: str
    variant_component: str
    component_resource_id: str
    image_resource_id: str
    image_path: str


@dataclass(frozen=True)
class DefinitionVariant:
    original_component: str
    variant_component: str
    component_resource_id: str


def clone_instance_definition(
    project_root: Path, *, parent_component_path: str, instance_id: str,
    identity: str,
) -> DefinitionVariant:
    """Retarget one existing instance to an untouched private definition.

    The clone is a program component, not a display-list overlay. Its visual
    children are changed later by the ordinary in-place patch writer.
    """
    root = project_root.resolve()
    parent_path = (root / parent_component_path).resolve()
    if not parent_path.is_relative_to(root) or not parent_path.is_file():
        raise ValueError("hifi_variant_parent_unavailable")
    parent = etree.parse(str(parent_path), _PARSER)
    matches = parent.xpath("./displayList/component[@id=$id]", id=instance_id)
    if len(matches) != 1:
        raise ValueError("hifi_variant_instance_missing")
    instance = matches[0]
    resources, package_ids = _package_resource_index(root)
    parent_package = next((p for p in parent_path.parents if p.resolve() in package_ids), None)
    if parent_package is None:
        raise ValueError("hifi_variant_package_missing")
    package_id = instance.get("pkg") or package_ids[parent_package.resolve()]
    original_path = resources.get((package_id, instance.get("src", "")))
    if original_path is None or not original_path.is_file():
        raise ValueError("hifi_variant_reference_missing")
    package_root = next((p for p in original_path.parents if p.resolve() in package_ids), None)
    if package_root is None:
        raise ValueError("hifi_variant_package_missing")
    digest = hashlib.sha256(
        (parent_component_path + "\0" + instance_id + "\0" + identity).encode()
    ).hexdigest()[:12]
    component_id = "h" + digest[:8]
    variant_path = original_path.with_name(f"{original_path.stem}__hifi_{digest}.xml")
    manifest_path = package_root / "package.xml"
    manifest = etree.parse(str(manifest_path), _PARSER)
    resources_element = manifest.find("./resources")
    if resources_element is None:
        raise ValueError("hifi_variant_package_invalid")
    if variant_path.exists() or manifest.xpath("./resources/*[@id=$id]", id=component_id):
        raise ValueError("hifi_variant_collision")
    resources_element.append(etree.Element(
        "component", id=component_id, name=variant_path.name,
        path="/" + variant_path.parent.relative_to(package_root).as_posix() + "/",
        exported="true",
    ))
    # Preserve every instance attribute except its resource reference.
    instance.set("src", component_id)
    instance.set("fileName", variant_path.relative_to(package_root).as_posix())
    shutil.copyfile(original_path, variant_path)
    manifest.write(str(manifest_path), encoding="utf-8", xml_declaration=True)
    parent.write(str(parent_path), encoding="utf-8", xml_declaration=True)
    return DefinitionVariant(
        original_component=original_path.relative_to(root).as_posix(),
        variant_component=variant_path.relative_to(root).as_posix(),
        component_resource_id=component_id,
    )


def create_instance_visual_variant(
    project_root: Path,
    *,
    parent_component_path: str,
    instance_id: str,
    visual_child_id: str,
    replacement_png: Path,
) -> InstanceVariant:
    """Clone one referenced definition and replace an existing image child.

    No new display-list object is created. Unsupported references and ID/path
    collisions fail before writing anything to the project copy.
    """
    root = project_root.resolve()
    parent_path = (root / parent_component_path).resolve()
    if not parent_path.is_relative_to(root) or not parent_path.is_file():
        raise ValueError("hifi_variant_parent_unavailable")
    parent = etree.parse(str(parent_path), _PARSER)
    matches = parent.xpath("./displayList/component[@id=$id]", id=instance_id)
    if len(matches) != 1:
        raise ValueError("hifi_variant_instance_missing")
    instance = matches[0]
    resources, package_ids = _package_resource_index(root)
    parent_package = next((package for package in parent_path.parents if package.resolve() in package_ids), None)
    if parent_package is None:
        raise ValueError("hifi_variant_package_missing")
    package_id = instance.get("pkg") or package_ids[parent_package.resolve()]
    original_path = resources.get((package_id, instance.get("src", "")))
    if original_path is None or not original_path.is_file():
        raise ValueError("hifi_variant_reference_missing")
    if "__hifi_" in original_path.stem:
        raise ValueError("hifi_variant_collision")
    package_root = next((package for package in original_path.parents if package.resolve() in package_ids), None)
    if package_root is None:
        raise ValueError("hifi_variant_package_missing")
    original = etree.parse(str(original_path), _PARSER)
    children = original.xpath("./displayList/image[@id=$id]", id=visual_child_id)
    if len(children) != 1:
        raise ValueError("hifi_variant_image_missing")
    image_bytes = replacement_png.read_bytes()
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("hifi_variant_image_invalid")
    try:
        with Image.open(replacement_png) as decoded:
            if decoded.format != "PNG" or decoded.width < 1 or decoded.height < 1:
                raise ValueError("hifi_variant_image_invalid")
            decoded.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("hifi_variant_image_invalid") from exc
    digest = hashlib.sha256(
        (parent_component_path + "\0" + instance_id + "\0" + visual_child_id).encode()
        + image_bytes
    ).hexdigest()[:12]
    component_id = "h" + digest[:8]
    image_id = "i" + digest[:8]
    variant_name = f"{original_path.stem}__hifi_{digest}.xml"
    variant_path = original_path.with_name(variant_name)
    image_path = package_root / "Img/HIFI" / original_path.stem / f"{digest}.png"
    manifest_path = package_root / "package.xml"
    manifest = etree.parse(str(manifest_path), _PARSER)
    listed = manifest.xpath("./resources/*")
    if variant_path.exists() or image_path.exists() or any(
        item.get("id") in {component_id, image_id} or item.get("name") == variant_name
        for item in listed
    ):
        raise ValueError("hifi_variant_collision")
    image = children[0]
    image.set("src", image_id)
    image.set("fileName", image_path.name)
    resource_list = manifest.find("./resources")
    if resource_list is None:
        raise ValueError("hifi_variant_package_invalid")
    resource_list.append(etree.Element(
        "component", id=component_id, name=variant_name,
        path="/" + variant_path.parent.relative_to(package_root).as_posix() + "/",
        exported="true",
    ))
    resource_list.append(etree.Element(
        "image", id=image_id, name=image_path.name,
        path="/" + image_path.parent.relative_to(package_root).as_posix() + "/",
        exported="true", atlas="0",
    ))
    instance.set("src", component_id)
    instance.set("fileName", variant_path.relative_to(package_root).as_posix())
    image_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(replacement_png, image_path)
    original.write(str(variant_path), encoding="utf-8", xml_declaration=True)
    manifest.write(str(manifest_path), encoding="utf-8", xml_declaration=True)
    parent.write(str(parent_path), encoding="utf-8", xml_declaration=True)
    return InstanceVariant(
        original_component=original_path.relative_to(root).as_posix(),
        variant_component=variant_path.relative_to(root).as_posix(),
        component_resource_id=component_id,
        image_resource_id=image_id,
        image_path=image_path.relative_to(root).as_posix(),
    )
