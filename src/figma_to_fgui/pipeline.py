import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from figma_to_fgui.changeset import build_changeset
from figma_to_fgui.classify import classify_tree, merge_classification_decisions
from figma_to_fgui.generate import generate_staging
from figma_to_fgui.models import (
    ChangeSet,
    ClassificationDecision,
    Diagnostic,
    NormalizedNode,
    Severity,
)
from figma_to_fgui.normalize import SelectionAsset, SelectionDocument, normalize_document
from figma_to_fgui.project_index import index_project
from figma_to_fgui.rules import load_rules
from figma_to_fgui.semantic_models import SemanticAnalysisOutcome
from figma_to_fgui.semantic_names import validate_semantic_overrides
from figma_to_fgui.validate import validate_staging


class ConversionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    figma_json: Path
    project_root: Path
    package_name: str
    staging_root: Path
    classification_rules: Path


MAX_SELECTION_CONVERSION_BYTES = 8 * 1024 * 1024


class ConversionLimitError(ValueError):
    pass


class SemanticAnalyzer(Protocol):
    """Boundary for optional semantic analysis of normalized selections."""

    def analyze(
        self,
        roots: tuple[NormalizedNode, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome: ...


def _semantic_fallback_diagnostic() -> Diagnostic:
    return Diagnostic(
        code="semantic.fallback",
        severity=Severity.WARNING,
        message="Semantic analysis was unavailable; rule classification remains active.",
    )


def _analyze(
    roots: tuple[NormalizedNode, ...],
    rule_candidates: tuple[ClassificationDecision, ...],
    semantic_analyzer: SemanticAnalyzer | None,
    screenshot: bytes | None,
) -> SemanticAnalysisOutcome:
    if semantic_analyzer is None:
        return SemanticAnalysisOutcome()
    try:
        outcome = semantic_analyzer.analyze(
            roots,
            rule_candidates=rule_candidates,
            screenshot=screenshot,
        )
    except Exception:  # noqa: BLE001 - optional third-party analyzer failures must degrade safely.
        return SemanticAnalysisOutcome(
            diagnostics=(_semantic_fallback_diagnostic(),), used_fallback=True
        )
    if not isinstance(outcome, SemanticAnalysisOutcome):
        return SemanticAnalysisOutcome(
            diagnostics=(_semantic_fallback_diagnostic(),), used_fallback=True
        )
    if outcome.used_fallback:
        diagnostics = outcome.diagnostics
        if not any(item.code == "semantic.fallback" for item in diagnostics):
            diagnostics += (_semantic_fallback_diagnostic(),)
        return outcome.model_copy(update={"overrides": (), "diagnostics": diagnostics})
    return outcome


def convert(request: ConversionRequest) -> ChangeSet:
    raw = json.loads(request.figma_json.read_text("utf-8"))
    return convert_document(
        raw,
        request.project_root,
        request.package_name,
        request.staging_root,
        request.classification_rules,
    )


def convert_document(
    raw: dict[str, object],
    project_root: Path,
    package_name: str,
    staging_root: Path,
    classification_rules: Path,
    selection_assets: tuple[SelectionAsset, ...] = (),
    semantic_analyzer: SemanticAnalyzer | None = None,
    screenshot: bytes | None = None,
) -> ChangeSet:
    if isinstance(raw, SelectionDocument):
        if selection_assets and selection_assets != raw._conversion_assets:
            raise ValueError("selection asset context conflict")
        selection_assets = raw._conversion_assets
    if sum(asset.size for asset in selection_assets) > MAX_SELECTION_CONVERSION_BYTES:
        raise ConversionLimitError("selection conversion is too large")
    roots, normalization_diagnostics = normalize_document(raw)
    rules = load_rules(classification_rules)
    rule_candidates = classify_tree(roots, rules)
    semantic = _analyze(roots, rule_candidates, semantic_analyzer, screenshot)
    index = index_project(project_root)
    if package_name not in index.packages:
        raise ValueError("project package is unavailable")
    safe_overrides, override_diagnostics = validate_semantic_overrides(
        roots,
        semantic.overrides,
        rule_candidates=rule_candidates,
        project_index=index,
        package_name=package_name,
    )
    semantic = semantic.model_copy(
        update={
            "overrides": safe_overrides,
            "diagnostics": semantic.diagnostics + override_diagnostics,
        }
    )
    decisions = merge_classification_decisions(roots, rule_candidates, semantic.overrides)
    _, generation_diagnostics = generate_staging(
        roots, decisions, package_name, staging_root, project_root, index, selection_assets
    )
    validation_diagnostics = validate_staging(staging_root, index)
    semantic_diagnostics = semantic.diagnostics
    if semantic.overrides:
        semantic_diagnostics += (
            Diagnostic(
                code="semantic.ai_applied",
                severity=Severity.INFO,
                message="Validated semantic analysis overrides were applied.",
            ),
        )
    diagnostics = (
        normalization_diagnostics
        + semantic_diagnostics
        + generation_diagnostics
        + validation_diagnostics
    )
    return build_changeset(project_root, staging_root, diagnostics)
