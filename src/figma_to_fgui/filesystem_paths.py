from __future__ import annotations

import os
from pathlib import Path


def io_path(path: Path) -> Path:
    """Return a Windows extended-length path for filesystem operations."""
    if os.name != "nt":
        return path
    absolute = Path(os.path.abspath(path))
    value = str(absolute)
    if value.startswith("\\\\?\\"):
        return absolute
    if value.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{value[2:]}")
    return Path(f"\\\\?\\{value}")
