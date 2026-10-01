from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class FixedFontSpec:
    family: str
    postscript_name: str
    source_filename: str
    sha256: str


@dataclass(frozen=True)
class FixedFontStatus:
    family: str
    postscript_name: str
    source_filename: str
    sha256: str
    installed: bool
    matched_filename: str | None


PROJECT_FIXED_FONTS = (
    FixedFontSpec(
        family="HYZhengYuan-75S",
        postscript_name="HYZhengYuan-GES",
        source_filename="HYZhengYuan-75S.ttf",
        sha256="00968481e6e8548f0d0302d3f70584fd84d5577e119176292fc7fa122b03a8e8",
    ),
    FixedFontSpec(
        family="CoreSansESW01-55Medium",
        postscript_name="CoreSansESW01-55Medium",
        source_filename="core sans es w01_55 medium.ttf",
        sha256="aa9ad3987bd173e2954273b768ce5f5e6696b054dd57ace9931aa4cb57e06b56",
    ),
)


def windows_font_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    windows = os.environ.get("WINDIR")
    if local_app_data:
        roots.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
    if windows:
        roots.append(Path(windows) / "Fonts")
    return tuple(roots)


def _file_sha256(path: Path) -> str | None:
    digest = sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def check_fixed_fonts(
    specs: tuple[FixedFontSpec, ...] = PROJECT_FIXED_FONTS,
    search_roots: tuple[Path, ...] | None = None,
) -> tuple[FixedFontStatus, ...]:
    expected = {item.sha256: item for item in specs}
    matches: dict[str, str] = {}
    for root in search_roots if search_roots is not None else windows_font_roots():
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_file() or path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            digest = _file_sha256(path)
            if digest in expected and digest not in matches:
                matches[digest] = path.name
            if len(matches) == len(expected):
                break
        if len(matches) == len(expected):
            break
    return tuple(
        FixedFontStatus(
            family=spec.family,
            postscript_name=spec.postscript_name,
            source_filename=spec.source_filename,
            sha256=spec.sha256,
            installed=spec.sha256 in matches,
            matched_filename=matches.get(spec.sha256),
        )
        for spec in specs
    )
