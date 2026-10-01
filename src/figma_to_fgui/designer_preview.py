from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field

from figma_to_fgui.models import Diagnostic, FrozenModel, Severity
from figma_to_fgui.service_contracts import ChangeBundle, ChangeFile, FileOperation


class DesignerChange(FrozenModel):
    action: Literal["新增", "更新"]
    label: str
    thumbnail_available: bool = False
    before_image_url: str | None = None
    after_image_url: str | None = None


class DesignerCheck(FrozenModel):
    status: Literal["success", "warning", "error"]
    message: str


class DesignerPreview(FrozenModel):
    summary: str
    changes: tuple[DesignerChange, ...]
    checks: tuple[DesignerCheck, ...] = Field(default_factory=tuple)


def _thumbnail_exists(before_root: Path, relative_path: str) -> bool:
    name = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16] + ".webp"
    return (before_root / ".figma-to-fgui-preview" / name).is_file()


def is_designer_image(relative_path: str) -> bool:
    return Path(relative_path).suffix.lower() in {
        ".bmp",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }


def _change_label(change: ChangeFile, before_root: Path) -> tuple[str, bool]:
    path = Path(change.relative_path)
    if is_designer_image(change.relative_path):
        return f"图片：{path.stem}", _thumbnail_exists(before_root, change.relative_path)
    if path.suffix.lower() == ".xml":
        prefix = "界面" if path.parent.name == "Panel" else "组件"
        return f"{prefix}：{path.stem}", False
    return f"资源：{path.stem}", False


def _check(diagnostic: Diagnostic) -> DesignerCheck:
    status: Literal["success", "warning", "error"]
    if diagnostic.severity is Severity.ERROR:
        status = "error"
        message = "发现需要处理的问题"
    elif diagnostic.severity is Severity.WARNING:
        status = "warning"
        message = "存在需要确认的调整"
    else:
        status = "success"
        message = "工程检查通过"
    return DesignerCheck(status=status, message=message)


def build_designer_preview(
    before_root: Path,
    bundle: ChangeBundle,
    diagnostics: tuple[Diagnostic, ...],
    image_url: Callable[[int, Literal["before", "after"]], str | None] | None = None,
) -> DesignerPreview:
    changes: list[DesignerChange] = []
    for index, change in enumerate(bundle.files):
        label, thumbnail_available = _change_label(change, before_root)
        changes.append(
            DesignerChange(
                action="新增" if change.operation is FileOperation.CREATE else "更新",
                label=label,
                thumbnail_available=thumbnail_available,
                before_image_url=image_url(index, "before") if image_url else None,
                after_image_url=image_url(index, "after") if image_url else None,
            )
        )
    created = sum(change.action == "新增" for change in changes)
    updated = len(changes) - created
    checks = tuple(_check(diagnostic) for diagnostic in diagnostics) or (
        DesignerCheck(status="success", message="工程检查通过"),
    )
    return DesignerPreview(
        summary=f"{created} 项新增 · {updated} 项更新", changes=tuple(changes), checks=checks
    )
