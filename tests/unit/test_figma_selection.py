from __future__ import annotations

import pytest
from pydantic import ValidationError

from figma_to_fgui.figma_selection import (
    SelectionError,
    SelectionLimits,
    SelectionManifest,
    SelectionNode,
    SelectionWarning,
    validate_selection_manifest,
)
from figma_to_fgui.models import Bounds


def selection_manifest(
    *, top_level: int = 1, style: dict[str, object] | None = None
) -> SelectionManifest:
    return SelectionManifest(
        display_name="Checkout",
        top_level_nodes=tuple(
            SelectionNode(
                id=f"node-{index}",
                name=f"Layer {index}",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=32, height=16),
                style=style or {},
            )
            for index in range(top_level)
        ),
    )


def test_manifest_rejects_more_than_twenty_top_level_nodes() -> None:
    with pytest.raises(SelectionError) as caught:
        validate_selection_manifest(selection_manifest(top_level=21), SelectionLimits())

    assert caught.value.code == "selection_too_large"


def test_manifest_rejects_external_urls_and_raw_font_bytes() -> None:
    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        validate_selection_manifest(
            selection_manifest(style={"imageUrl": "https://example.invalid/a.png"}), SelectionLimits()
        )

    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        validate_selection_manifest(
            selection_manifest(style={"fontBytes": "AAEAAA"}), SelectionLimits()
        )


def test_manifest_models_are_immutable_and_forbid_unknown_fields() -> None:
    manifest = selection_manifest()

    with pytest.raises(ValidationError):
        SelectionManifest(display_name="Checkout", top_level_nodes=(), extra="forbidden")
    with pytest.raises(ValidationError):
        manifest.display_name = "Changed"  # type: ignore[misc]


def test_manifest_rejects_duplicate_or_blank_node_ids() -> None:
    manifest = selection_manifest()
    root = manifest.top_level_nodes[0]
    duplicate = root.model_copy(update={"children": (root,)})
    manifest = manifest.model_copy(update={"top_level_nodes": (duplicate,)})

    with pytest.raises(SelectionError, match="selection_node_duplicate"):
        validate_selection_manifest(manifest, SelectionLimits())
    with pytest.raises(ValidationError):
        SelectionNode(
            id=" ",
            name="Blank",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=1, height=1),
        )


def test_manifest_rejects_deep_or_excessive_content() -> None:
    nested: dict[str, object] = {"value": "ok"}
    for _ in range(33):
        nested = {"next": nested}

    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        validate_selection_manifest(selection_manifest(style=nested), SelectionLimits())


def test_manifest_bounds_warning_count_and_serialized_session_size() -> None:
    manifest = selection_manifest().model_copy(
        update={"warnings": tuple(SelectionWarning(code="w", message="m") for _ in range(5001))}
    )
    with pytest.raises(SelectionError, match="selection_too_large"):
        validate_selection_manifest(manifest, SelectionLimits())


def test_manifest_accepts_legacy_per_node_warnings_within_the_node_limit() -> None:
    manifest = selection_manifest().model_copy(
        update={"warnings": tuple(SelectionWarning(code="node_hidden", message="hidden") for _ in range(117))}
    )

    assert validate_selection_manifest(manifest, SelectionLimits()) is manifest
