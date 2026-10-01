"""Isolated Pillow probe entry point; invoked only as an absolute script under ``-I``."""

from __future__ import annotations

import json
import sys
import warnings
from io import BytesIO

from PIL import Image, ImageFile, UnidentifiedImageError

MAX_IMAGE_PIXELS = 89_478_485


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdecimal():
        return 2
    max_payload_bytes = int(sys.argv[1])
    if max_payload_bytes <= 0:
        return 2
    content = sys.stdin.buffer.read(max_payload_bytes + 1)
    if len(content) > max_payload_bytes:
        return 1
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                detected_format = image.format
                width, height = image.size
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ):
        return 1
    if detected_format is None:
        return 1
    sys.stdout.write(
        json.dumps({"format": detected_format, "height": height, "width": width}, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
