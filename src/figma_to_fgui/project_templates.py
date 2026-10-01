from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import ValidationError

from figma_to_fgui.paths import validate_project_name
from figma_to_fgui.service_contracts import TemplateOption


class TemplateNotFound(ValueError):
    pass


class TemplateCatalog:
    def __init__(self, root: Path | None) -> None:
        self.root = root

    @staticmethod
    def _option(path: Path) -> TemplateOption | None:
        metadata = path / "template.json"
        if (
            not path.is_dir()
            or path.is_symlink()
            or not metadata.is_file()
            or metadata.is_symlink()
        ):
            return None
        try:
            return TemplateOption.model_validate_json(metadata.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError):
            return None

    def _template(self, template_id: str) -> tuple[Path, TemplateOption]:
        if self.root is not None and self.root.is_dir():
            for path in sorted(self.root.iterdir(), key=lambda item: item.name):
                option = self._option(path)
                if option is not None and option.template_id == template_id:
                    return path, option
        raise TemplateNotFound(template_id)

    def list_options(self) -> tuple[TemplateOption, ...]:
        if self.root is None or not self.root.is_dir():
            return ()
        return tuple(
            option
            for path in sorted(self.root.iterdir(), key=lambda item: item.name)
            if (option := self._option(path)) is not None
        )

    @staticmethod
    def _reject_symlinks(root: Path) -> None:
        if any(path.is_symlink() for path in root.rglob("*")):
            raise ValueError("template contains a symlink")

    def create(self, template_id: str, project_name: str, destination: Path) -> Path:
        validate_project_name(project_name)
        source, _ = self._template(template_id)
        self._reject_symlinks(source)
        packages = sorted(source.glob("*/package.xml"), key=lambda path: path.parent.name)
        if len(packages) != 1:
            raise ValueError("template must contain exactly one package")
        shutil.copytree(source, destination)
        (destination / "template.json").unlink()
        (destination / packages[0].parent.name).rename(destination / project_name)
        return destination
