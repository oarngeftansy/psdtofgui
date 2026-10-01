"""Shared guardrails for the village mapping regression and acceptance runner."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

PRODUCTION_ROOTS = (
    Path("src/figma_to_fgui"),
    Path("apps/figma-plugin/src"),
    Path("apps/web-console/src/figma"),
    Path("rules/default"),
)
PRODUCTION_SUFFIXES = frozenset(
    {".css", ".html", ".json", ".py", ".toml", ".ts", ".tsx", ".yaml", ".yml"}
)
SAMPLE_ONLY_MARKERS = (
    "村庄升阶",
    "village-root",
    "selection_village_ascend",
    "village_background",
    "rank-before",
)
# Plain `title` and `background` are intentionally not forbidden: they are generic
# presentation names, unlike the fixture-unique IDs above.
GENERIC_MAPPING_TERMS = ("common_primary_button", "通用一级按钮", "23:55")
GENERIC_MAPPING_ALLOWLIST = frozenset(
    {Path("rules/default/component-mapping-candidates.json")}
)


def production_special_case_violations(
    overrides: Mapping[Path, str] | None = None,
    *,
    workspace: Path = Path("."),
) -> tuple[tuple[Path, str], ...]:
    """Return only fixture-specific terms found in production implementation roots."""
    root = workspace.resolve(strict=True)
    replacements = overrides or {}
    files = sorted(
        path
        for production_root in PRODUCTION_ROOTS
        for path in (root / production_root).rglob("*")
        if path.is_file() and path.suffix.casefold() in PRODUCTION_SUFFIXES
    )
    violations: list[tuple[Path, str]] = []
    for path in files:
        relative = path.relative_to(root)
        content = replacements.get(relative, path.read_text("utf-8"))
        violations.extend(
            (relative, marker) for marker in SAMPLE_ONLY_MARKERS if marker in content
        )
        for term in GENERIC_MAPPING_TERMS:
            occurrences = content.count(term)
            allowed_once = relative in GENERIC_MAPPING_ALLOWLIST and occurrences == 1
            if occurrences and not allowed_once:
                violations.append((relative, term))
    return tuple(violations)
