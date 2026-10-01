"""Build a fresh FairyGUI project from one committed Figma selection."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar

from figma_to_fgui.component_mapping import ComponentMappingCatalog, load_mapping_catalog
from figma_to_fgui.fgui_asset_payloads import (
    MAX_ASSET_PAYLOAD_BYTES,
    MAX_TOTAL_ASSET_PAYLOAD_BYTES,
    read_bounded_stable_asset_file,
)
from figma_to_fgui.fgui_new_project_build import (
    BuiltNewProject,
    NewProjectBuildError,
    build_new_project,
)
from figma_to_fgui.fgui_new_project_models import (
    AssetPayload,
    AssetPayloadSet,
    NewProjectConfig,
)
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_models import FGUIPlanDocument, ResourcePlan
from figma_to_fgui.fgui_plan_validate import validate_fgui_plan
from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.models import Diagnostic, NormalizedNode, Severity
from figma_to_fgui.normalize import (
    SelectionAsset,
    normalize_document,
    selection_conversion_document,
)
from figma_to_fgui.service_contracts import (
    NewProjectAdjustment,
    NewProjectAdjustmentStrategy,
    NewProjectIssueKind,
)
from figma_to_fgui.uir_compile import compile_uir, uir_asset_id
from figma_to_fgui.uir_models import UIRDocument
from figma_to_fgui.uir_validate import validate_uir
from figma_to_fgui.validate import has_errors

DEFAULT_MAPPING_CATALOG_PATH = Path("rules/default/component-mapping-candidates.json")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_WorkflowResult = TypeVar("_WorkflowResult")

_PUBLIC_MESSAGES = {
    "fgui.component.definition_missing": (
        "A selected component needs a complete generated definition.",
        "Add the component definition or use an approved raster fallback.",
    ),
    "fgui.writer.workflow.mapping_conflict": (
        "The selected component mapping is ambiguous or conflicted.",
        "Resolve the component mapping and retry.",
    ),
    "fgui.writer.workflow.conversion_failed": (
        "The committed selection could not be converted into a new project.",
        "Review the selection and retry.",
    ),
    "fgui.writer.workflow.validation_failed": (
        "The converted selection did not pass the new-project validation gates.",
        "Repair the selection and retry.",
    ),
    "fgui.writer.workflow.resource_mismatch": (
        "The committed selection resources no longer match the conversion plan.",
        "Re-export the selection resources and retry.",
    ),
    "fgui.writer.workflow.output_failed": (
        "The new-project archive could not be published to the requested destination.",
        "Choose a valid output destination and retry.",
    ),
    "fgui.writer.workflow.build_failed": (
        "The new-project writer could not produce a validated archive.",
        "Repair the selection or destination and retry.",
    ),
}


class NewProjectWorkflowError(Exception):
    """A committed-selection workflow failure with fresh public diagnostics only."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        codes: set[str] = set()
        try:
            codes = {
                item.code
                for item in diagnostics
                if type(item) is Diagnostic and item.code in _PUBLIC_MESSAGES
            }
        except Exception:  # noqa: BLE001 - hostile diagnostic containers fail closed.
            codes = set()
        if not codes:
            codes = {"fgui.writer.workflow.conversion_failed"}
        self.diagnostics = tuple(_public_diagnostic(code) for code in sorted(codes))
        super().__init__("New FairyGUI project workflow failed.")


def _public_diagnostic(code: str) -> Diagnostic:
    message, suggested_action = _PUBLIC_MESSAGES[code]
    return Diagnostic(
        code=code,
        severity=Severity.ERROR,
        message=message,
        rule_id=code,
        rule_version=1,
        evidence=(f"workflow.code={code}",),
        suggested_action=suggested_action,
        blocks_binding=True,
    )


def _public_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> tuple[Diagnostic, ...]:
    """Copy only workflow allow-listed diagnostic categories into a public error."""
    codes = {
        {
            "fgui.component.definition_missing": "fgui.component.definition_missing",
            "fgui.writer.workflow.mapping_conflict": "fgui.writer.workflow.mapping_conflict",
            "uir.mapping_conflict": "fgui.writer.workflow.mapping_conflict",
        }[item.code]
        for item in diagnostics
        if type(item) is Diagnostic
        and item.code
        in {
            "fgui.component.definition_missing",
            "fgui.writer.workflow.mapping_conflict",
            "uir.mapping_conflict",
        }
    }
    if not codes:
        codes = {"fgui.writer.workflow.validation_failed"}
    return tuple(_public_diagnostic(code) for code in sorted(codes))


def _workflow_error(code: str) -> NewProjectWorkflowError:
    return NewProjectWorkflowError((_public_diagnostic(code),))


def _run_conversion_gate(
    operation: Callable[[], _WorkflowResult], *, code: str = "fgui.writer.workflow.conversion_failed"
) -> _WorkflowResult:
    """Convert operational failures without allowing their exception chain to escape."""
    failure: NewProjectWorkflowError | None = None
    try:
        return operation()
    except Exception:  # noqa: BLE001 - conversion internals are not public evidence.
        failure = _workflow_error(code)
    if failure is not None:
        raise failure from None
    raise AssertionError("unreachable workflow gate state")


def _selection_mapping_diagnostics(
    catalog: ComponentMappingCatalog, manifest: SelectionManifest
) -> tuple[Diagnostic, ...]:
    """Apply the catalog's exact source ID/name matching before UIR conversion."""
    pending = list(manifest.top_level_nodes)
    codes: set[str] = set()
    while pending:
        node: SelectionNode = pending.pop()
        if node.type == "INSTANCE":
            if not node.visible:
                pending.extend(node.children)
                continue
            if (
                node.properties.get("export_strategy") == "composite_png"
                and len(node.resource_keys) == 1
            ):
                pending.extend(node.children)
                continue
            matches = tuple(
                item
                for item in catalog.components
                if node.id in item.figma.node_ids or node.name in item.figma.names
            )
            if len(matches) > 1:
                codes.add("fgui.writer.workflow.mapping_conflict")
            elif (
                not node.children
                and (not matches or matches[0].status == "candidate")
            ):
                # A readable instance can be compiled from its committed child tree.
                # Only opaque instances require a separately provable definition.
                codes.add("fgui.component.definition_missing")
        pending.extend(node.children)
    return tuple(_public_diagnostic(code) for code in sorted(codes))


def _apply_adjustments(
    manifest: SelectionManifest, adjustments: tuple[NewProjectAdjustment, ...]
) -> SelectionManifest:
    """Apply a validated, closed adjustment set to source nodes before normalization."""
    by_source: dict[str, NewProjectAdjustment] = {}
    for adjustment in adjustments:
        if adjustment.source_node_id in by_source:
            raise ValueError("duplicate adjustment target")
        if (
            adjustment.issue_kind is not NewProjectIssueKind.RASTER_FALLBACK
            or adjustment.strategy is not NewProjectAdjustmentStrategy.PRESERVE_EDITABLE
        ):
            # Selection v1 has no typed contained-definition tree. Never infer one.
            raise ValueError("adjustment is not provable from the committed selection")
        by_source[adjustment.source_node_id] = adjustment

    found: set[str] = set()

    def visit(node: SelectionNode) -> SelectionNode:
        children = tuple(visit(child) for child in node.children)
        adjustment = by_source.get(node.id)
        if adjustment is None:
            return node.model_copy(update={"children": children})
        found.add(node.id)
        properties = dict(node.properties)
        properties.pop("export_strategy", None)
        properties.pop("raster_reasons", None)
        return node.model_copy(update={"children": children, "properties": properties})

    roots = tuple(visit(node) for node in manifest.top_level_nodes)
    if found != set(by_source):
        raise ValueError("adjustment target is outside the committed selection")
    return manifest.model_copy(update={"top_level_nodes": roots})


def _compiler_mapping_catalog(
    catalog: ComponentMappingCatalog,
    manifest: SelectionManifest,
    roots: tuple[NormalizedNode, ...],
) -> ComponentMappingCatalog:
    """Bridge source Figma IDs to normalized IDs without changing catalog matching."""
    instances: list[tuple[SelectionNode, NormalizedNode]] = []
    pending = list(zip(manifest.top_level_nodes, roots, strict=True))
    while pending:
        source_node, normalized_node = pending.pop()
        if source_node.type != normalized_node.type or len(source_node.children) != len(
            normalized_node.children
        ):
            raise ValueError("selection normalization no longer preserves tree identity")
        if source_node.type == "INSTANCE":
            instances.append((source_node, normalized_node))
        pending.extend(zip(source_node.children, normalized_node.children, strict=True))

    components = []
    for item in catalog.components:
        if item.status == "candidate":
            continue
        opaque_visible_matches = tuple(
            normalized_node.id
            for source_node, normalized_node in instances
            if source_node.visible
            and not source_node.children
            and (
                source_node.id in item.figma.node_ids
                or source_node.name in item.figma.names
            )
        )
        figma = item.figma.model_copy(
            update={
                # New-project Writer has no external component definitions. A
                # readable instance is therefore always compiled from its
                # committed child tree. Keep mapping only for visible opaque
                # instances so they continue to fail closed or use an explicit
                # raster fallback; hidden opaque instances need no definition.
                "node_ids": opaque_visible_matches,
                "names": (),
            }
        )
        components.append(item.model_copy(update={"figma": figma}))
    return catalog.model_copy(update={"components": tuple(components)})


def _source_node_ids(
    manifest: SelectionManifest, roots: tuple[NormalizedNode, ...]
) -> dict[str, str]:
    result: dict[str, str] = {}
    pending = list(zip(manifest.top_level_nodes, roots, strict=True))
    while pending:
        source, normalized = pending.pop()
        result[normalized.id] = source.id
        pending.extend(zip(source.children, normalized.children, strict=True))
    return result


def _payloads_from_selection_assets(
    resources: Mapping[str, ResourcePlan], assets: tuple[SelectionAsset, ...]
) -> AssetPayloadSet:
    """Read exactly the selected resource bytes declared by the compiled Plan."""
    assets_by_logical_id: dict[str, SelectionAsset] = {}
    for asset in assets:
        if asset.asset in assets_by_logical_id:
            raise ValueError("duplicate selected resource")
        assets_by_logical_id[asset.asset] = asset
    if set(assets_by_logical_id) != {
        resource.logical_asset_id for resource in resources.values()
    }:
        raise ValueError("selection resources do not close over plan resources")

    payloads: list[AssetPayload] = []
    total_bytes = 0
    for resource_id in sorted(resources):
        resource = resources[resource_id]
        # ``source_asset_ref`` is the UIR-derived ID.  The Plan retains the
        # committed selection asset identity in ``logical_asset_id``.
        selected_asset = assets_by_logical_id.get(resource.logical_asset_id)
        source_asset_ref = (
            None
            if selected_asset is None
            else uir_asset_id(
                logical_id=selected_asset.asset,
                mime_type=selected_asset.mime_type,
                sha256=selected_asset.sha256,
                width=resource.width,
                height=resource.height,
                nine_slice=(
                    None
                    if resource.nine_slice is None
                    else (
                        resource.nine_slice.x,
                        resource.nine_slice.y,
                        resource.nine_slice.width,
                        resource.nine_slice.height,
                    )
                ),
                export_format=resource.export_format,
            )
        )
        if (
            resource.id != resource_id
            or selected_asset is None
            or resource.source_asset_ref != source_asset_ref
            or resource.content_sha256 is None
            or resource.mime_type != selected_asset.mime_type
            or resource.content_sha256 != selected_asset.sha256
        ):
            raise ValueError("selection resource does not match plan")
        remaining_bytes = MAX_TOTAL_ASSET_PAYLOAD_BYTES - total_bytes
        if selected_asset.size > min(MAX_ASSET_PAYLOAD_BYTES, remaining_bytes):
            raise ValueError("selection resource exceeds payload limits")
        content = read_bounded_stable_asset_file(
            selected_asset.source_path,
            max_bytes=min(MAX_ASSET_PAYLOAD_BYTES, remaining_bytes),
        )
        if (
            len(content) != selected_asset.size
            or hashlib.sha256(content).hexdigest() != selected_asset.sha256
        ):
            raise ValueError("selection resource bytes changed")
        total_bytes += len(content)
        payloads.append(
            AssetPayload(
                resourceId=resource_id,
                declaredMimeType=selected_asset.mime_type,
                content=content,
            )
        )
    return AssetPayloadSet.from_items(payloads)


def _build_error_code(error: NewProjectBuildError) -> str:
    """Map only one exact known build boundary to a fresh workflow diagnostic."""
    try:
        diagnostics = error.diagnostics
        if (
            type(diagnostics) is tuple
            and len(diagnostics) == 1
            and type(diagnostics[0]) is Diagnostic
            and diagnostics[0].code == "fgui.writer.build.output_invalid_failed"
        ):
            return "fgui.writer.workflow.output_failed"
    except Exception:  # noqa: BLE001 - hostile error objects fail closed.
        return "fgui.writer.workflow.build_failed"
    return "fgui.writer.workflow.build_failed"


def _workflow_validation(
    uir: UIRDocument,
    plan: FGUIPlanDocument,
    normalize_diagnostics: tuple[Diagnostic, ...],
    mapping_diagnostics: tuple[Diagnostic, ...],
) -> tuple[tuple[Diagnostic, ...], bool]:
    diagnostics = (
        *normalize_diagnostics,
        *mapping_diagnostics,
        *validate_uir(uir),
        *plan.diagnostics,
        *validate_fgui_plan(plan),
    )
    return diagnostics, not plan.bindable or has_errors(diagnostics)


def build_selection_new_project(
    *,
    manifest: SelectionManifest,
    resources_root: Path,
    selection_fingerprint: str,
    project_name: str,
    output_directory: Path,
    mapping_catalog_path: Path = DEFAULT_MAPPING_CATALOG_PATH,
    adjustments: tuple[NewProjectAdjustment, ...] = (),
    on_stage: Callable[[str, int], None] | None = None,
) -> BuiltNewProject:
    """Build a validated new FairyGUI project from one committed selection."""
    if _FINGERPRINT.fullmatch(selection_fingerprint) is None:
        raise _workflow_error("fgui.writer.workflow.conversion_failed")
    report_stage = on_stage or (lambda _stage, _progress: None)

    adjusted_manifest = _run_conversion_gate(lambda: _apply_adjustments(manifest, adjustments))
    conversion = _run_conversion_gate(
        lambda: selection_conversion_document(
            adjusted_manifest, resources_root, selection_fingerprint
        )
    )
    roots, normalize_diagnostics = _run_conversion_gate(
        lambda: normalize_document(conversion.raw)
    )
    source_node_ids = _run_conversion_gate(
        lambda: _source_node_ids(adjusted_manifest, roots)
    )
    catalog = _run_conversion_gate(lambda: load_mapping_catalog(mapping_catalog_path))
    mapping_diagnostics = _run_conversion_gate(
        lambda: _selection_mapping_diagnostics(catalog, adjusted_manifest)
    )
    compiler_catalog = _run_conversion_gate(
        lambda: _compiler_mapping_catalog(catalog, adjusted_manifest, roots)
    )
    uir = _run_conversion_gate(
        lambda: compile_uir(
            roots,
            source_revision=selection_fingerprint,
            selection_id=selection_fingerprint[:32],
            mapping_catalog=compiler_catalog,
        )
    )
    plan = _run_conversion_gate(
        lambda: compile_fgui_plan(uir, profile_version="fgui-6.1.4-v1", rule_version=1)
    )
    report_stage("checking", 55)
    diagnostics, failed_validation = _run_conversion_gate(
        lambda: _workflow_validation(
            uir, plan, normalize_diagnostics, mapping_diagnostics
        )
    )
    if failed_validation:
        raise NewProjectWorkflowError(_public_diagnostics(diagnostics))

    payloads = _run_conversion_gate(
        lambda: _payloads_from_selection_assets(plan.resources, conversion.assets),
        code="fgui.writer.workflow.resource_mismatch",
    )
    config = _run_conversion_gate(
        lambda: NewProjectConfig(
            projectName=project_name,
            packageName="Generated",
            fairyGuiVersion="6.1.4",
            publishTarget="unity",
        )
    )
    report_stage("packaging", 80)

    failure: NewProjectWorkflowError | None = None
    try:
        built = build_new_project(
            plan,
            config,
            payloads,
            output_directory,
            source_names={node.id: node.source.name for node in uir.nodes.values()},
        )
        return built.model_copy(
            update={
                "diagnostics": diagnostics,
                "plan": plan,
                "source_resource_keys": {
                    resource.id: selected.key
                    for resource in plan.resources.values()
                    for selected, asset in zip(adjusted_manifest.resources, conversion.assets, strict=True)
                    if resource.logical_asset_id == asset.asset
                },
                "source_node_ids": {
                    node.id: source_node_ids[node.source.node_id]
                    for node in uir.nodes.values()
                },
            }
        )
    except NewProjectBuildError as error:
        failure = _workflow_error(_build_error_code(error))
    except Exception:  # noqa: BLE001 - build internals are never public workflow evidence.
        failure = _workflow_error("fgui.writer.workflow.build_failed")
    if failure is not None:
        raise failure from None
    raise AssertionError("unreachable new-project build state")
