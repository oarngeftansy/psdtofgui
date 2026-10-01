import re
from pathlib import PurePosixPath

_PROJECT_NAME = re.compile(r"^[\w\-\u4e00-\u9fff]{1,64}$")


def safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not path.parts or path.is_absolute() or ":" in path.parts[0] or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {value}")
    return path.as_posix()


def validate_project_name(value: str) -> str:
    if _PROJECT_NAME.fullmatch(value) is None:
        raise ValueError("invalid project name")
    return value
