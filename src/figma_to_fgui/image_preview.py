from __future__ import annotations

from io import BytesIO
from pathlib import Path
from warnings import catch_warnings, simplefilter

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PREVIEW_DIMENSION = 512
MAX_SOURCE_PIXELS = 16_777_216


def _encode(opened: Image.Image) -> tuple[int, int, bytes]:
    width, height = opened.size
    if width * height > MAX_SOURCE_PIXELS:
        raise ValueError("image too large")
    image = ImageOps.exif_transpose(opened)
    mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
    preview = image.convert(mode)
    preview.thumbnail((MAX_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION))
    output = BytesIO()
    preview.save(output, "WEBP")
    return width, height, output.getvalue()


def _preview(source: BytesIO | Path) -> tuple[int, int, bytes] | None:
    try:
        with catch_warnings():
            simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as opened:
                return _encode(opened)
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ):
        return None


def encode_webp_preview(content: bytes) -> tuple[int, int, bytes] | None:
    return _preview(BytesIO(content))


def encode_webp_preview_path(path: Path) -> tuple[int, int, bytes] | None:
    return _preview(path)
