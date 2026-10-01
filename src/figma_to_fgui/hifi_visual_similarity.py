"""Conservative pixel proof for replacing a static FGUI image from a PSD layer."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from lxml import etree
from PIL import Image
from skimage.metrics import structural_similarity

from figma_to_fgui.hifi_replacement_models import FguiObjectRef

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def static_image_path(root: Path, old: FguiObjectRef) -> Path | None:
    """Resolve the old image bytes only when its declared source is unambiguous."""
    if old.object_type != "image" or not old.component_relative_path or not old.local_object_id:
        return None
    source = (root / old.component_relative_path).resolve()
    if not source.is_relative_to(root.resolve()) or not source.is_file():
        return None
    element = etree.parse(str(source), _PARSER).xpath("./displayList/image[@id=$id]", id=old.local_object_id)
    if len(element) != 1 or element[0].get("src") != old.resource_id:
        return None
    filename = element[0].get("fileName")
    if not filename:
        return None
    package = next((parent for parent in source.parents if (parent / "package.xml").is_file()), None)
    if package is None:
        return None
    image = (package / filename).resolve()
    return image if image.is_relative_to(package) and image.is_file() else None


def exact_visual_similarity(old_image: Path, new_image: Path) -> float:
    """Require structural and color agreement after geometry normalization."""
    with Image.open(old_image) as old, Image.open(new_image) as new:
        old_rgba = np.asarray(old.convert("RGBA").resize((96, 216), Image.Resampling.LANCZOS))
        new_rgba = np.asarray(new.convert("RGBA").resize((96, 216), Image.Resampling.LANCZOS))
    alpha_difference = float(np.mean(np.abs(old_rgba[:, :, 3].astype(np.float32)
                                            - new_rgba[:, :, 3].astype(np.float32)))) / 255
    if alpha_difference > .02:
        return 0.0
    # Composite over a neutral field so transparent RGB garbage cannot create
    # a false match with an opaque source image.
    old_alpha = old_rgba[:, :, 3:4].astype(np.float32) / 255
    new_alpha = new_rgba[:, :, 3:4].astype(np.float32) / 255
    old_pixels = np.rint(old_rgba[:, :, :3] * old_alpha + 127 * (1 - old_alpha)).astype(np.uint8)
    new_pixels = np.rint(new_rgba[:, :, :3] * new_alpha + 127 * (1 - new_alpha)).astype(np.uint8)
    difference = float(np.mean(np.abs(old_pixels.astype(np.float32) - new_pixels.astype(np.float32)))) / 255
    if difference > .02:
        return 0.0
    similarity = float(structural_similarity(old_pixels, new_pixels, channel_axis=2, data_range=255))  # type: ignore[no-untyped-call]
    return similarity if similarity >= .98 else 0.0
