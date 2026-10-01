import hashlib
import json
import shutil
from pathlib import Path

import pytest
from lxml import etree

from figma_to_fgui.models import ClassificationDecision, DecisionSource, Diagnostic, Severity
from figma_to_fgui.pipeline import convert_document
from figma_to_fgui.semantic_models import SemanticAnalysisOutcome, SemanticType


def _raw_document() -> dict[str, object]:
    return json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project)
    return project


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(path for path in root.rglob("*") if path.is_file())
    }


def test_pipeline_classifies_rules_before_semantic_analysis_and_merges_afterward(
    tmp_path: Path,
) -> None:
    class InspectingAnalyzer:
        calls = 0

        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            self.calls += 1
            assert roots
            assert [(item.node_id, item.output_type, item.evidence, item.confidence) for item in rule_candidates] == [
                ("1:1", "PANEL", ("type=FRAME", "width>=540", "children>=1"), 0.95),
                ("1:2", "TEXT", ("type=TEXT",), 1.0),
            ]
            return SemanticAnalysisOutcome()

    analyzer = InspectingAnalyzer()
    result = convert_document(
        _raw_document(),
        _project(tmp_path),
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
        semantic_analyzer=analyzer,
    )

    assert analyzer.calls == 1
    assert result.files


def test_unsupported_custom_override_falls_back_without_erasing_generated_nodes(
    tmp_path: Path,
) -> None:
    class FakeAnalyzer:
        calls = 0

        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            self.calls += 1
            assert len(roots) == 1
            assert rule_candidates
            assert screenshot == b"png"
            return SemanticAnalysisOutcome(
                overrides=(
                    ClassificationDecision(
                        node_id="1:1",
                        output_type="COMPONENT",
                        rule_id="ai.semantic.v1",
                        rule_version=1,
                        evidence=("validated structured AI decision",),
                        confidence=0.9,
                        source=DecisionSource.AI,
                    ),
                )
            )

    analyzer = FakeAnalyzer()
    result = convert_document(
        _raw_document(),
        _project(tmp_path),
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
        semantic_analyzer=analyzer,
        screenshot=b"png",
    )

    assert analyzer.calls == 1
    assert "semantic.unsupported_type" in {item.code for item in result.diagnostics}
    assert "semantic.ai_applied" not in {item.code for item in result.diagnostics}
    assert result.files
    assert any(item.relative_path.endswith("Panel_Sample_Main.xml") for item in result.files)


def test_rich_ai_button_keeps_the_existing_safe_generator_slot(tmp_path: Path) -> None:
    class FakeAnalyzer:
        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            panel = next(item for item in rule_candidates if item.node_id == "1:1")
            return SemanticAnalysisOutcome(
                overrides=(
                    panel.model_copy(
                        update={
                            "rule_id": "ai.semantic.v1",
                            "rule_version": 1,
                            "evidence": ("validated structured AI decision",),
                            "confidence": 0.93,
                            "source": DecisionSource.AI,
                            "semantic_name": "CheckoutButton",
                            "semantic_type": SemanticType.BUTTON,
                        }
                    ),
                )
            )

    result = convert_document(
        _raw_document(),
        _project(tmp_path),
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
        semantic_analyzer=FakeAnalyzer(),
    )

    diagnostic_codes = {item.code for item in result.diagnostics}
    assert "semantic.ai_applied" in diagnostic_codes
    assert "semantic.unsupported_type" not in diagnostic_codes
    assert result.files
    component = tmp_path / "staging" / "Sample" / "Panel" / "Panel_Sample_CheckoutButton.xml"
    assert component.read_bytes()


def test_ai_failure_generates_same_files_as_rules_only(tmp_path: Path) -> None:
    class FailingAnalyzer:
        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            raise RuntimeError("remote failure with sensitive details")

    baseline_root = tmp_path / "baseline"
    degraded_root = tmp_path / "degraded"
    baseline = convert_document(
        _raw_document(),
        _project(tmp_path / "baseline-project"),
        "Sample",
        baseline_root,
        Path("rules/default/classification.yaml"),
    )
    degraded = convert_document(
        _raw_document(),
        _project(tmp_path / "degraded-project"),
        "Sample",
        degraded_root,
        Path("rules/default/classification.yaml"),
        semantic_analyzer=FailingAnalyzer(),
    )

    assert _file_hashes(degraded_root) == _file_hashes(baseline_root)
    assert any(item.code == "semantic.fallback" for item in degraded.diagnostics)
    assert all(item.severity is not Severity.ERROR for item in degraded.diagnostics)
    assert "sensitive details" not in degraded.model_dump_json()
    assert baseline.files == degraded.files


def test_merges_analyzer_failure_diagnostics_with_pipeline_fallback(tmp_path: Path) -> None:
    class DegradedAnalyzer:
        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            return SemanticAnalysisOutcome(
                diagnostics=(
                    Diagnostic(
                        code="ai.transport",
                        severity=Severity.WARNING,
                        message="Safe analyzer failure.",
                    ),
                ),
                used_fallback=True,
            )

    result = convert_document(
        _raw_document(),
        _project(tmp_path),
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
        semantic_analyzer=DegradedAnalyzer(),
    )

    assert [item.code for item in result.diagnostics if item.code.startswith(("ai.", "semantic."))] == [
        "ai.transport",
        "semantic.fallback",
    ]


def test_failed_analyzer_outcome_cannot_apply_returned_overrides(tmp_path: Path) -> None:
    class FailedAnalyzer:
        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            return SemanticAnalysisOutcome(
                overrides=(
                    ClassificationDecision(
                        node_id="1:1",
                        output_type="COMPONENT",
                        rule_id="ai.semantic.v1",
                        rule_version=1,
                        evidence=("must not be applied",),
                        confidence=0.9,
                        source=DecisionSource.AI,
                    ),
                ),
                diagnostics=(
                    Diagnostic(
                        code="custom.provider_failure",
                        severity=Severity.WARNING,
                        message="Safe custom failure.",
                    ),
                ),
                used_fallback=True,
            )

    baseline_root = tmp_path / "baseline"
    degraded_root = tmp_path / "degraded"
    baseline = convert_document(
        _raw_document(),
        _project(tmp_path / "baseline-project"),
        "Sample",
        baseline_root,
        Path("rules/default/classification.yaml"),
    )
    degraded = convert_document(
        _raw_document(),
        _project(tmp_path / "degraded-project"),
        "Sample",
        degraded_root,
        Path("rules/default/classification.yaml"),
        semantic_analyzer=FailedAnalyzer(),
    )

    assert _file_hashes(degraded_root) == _file_hashes(baseline_root)
    assert baseline.files == degraded.files
    assert "custom.provider_failure" in {item.code for item in degraded.diagnostics}
    assert "semantic.fallback" in {item.code for item in degraded.diagnostics}
    assert "semantic.ai_applied" not in {item.code for item in degraded.diagnostics}


def test_custom_analyzer_cannot_bypass_semantic_name_validation(tmp_path: Path) -> None:
    class UnsafeAnalyzer:
        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            return SemanticAnalysisOutcome(
                overrides=(
                    ClassificationDecision(
                        node_id="1:1",
                        output_type="PANEL",
                        rule_id="custom.semantic",
                        rule_version=1,
                        evidence=("untrusted custom analyzer",),
                        confidence=1,
                        source=DecisionSource.AI,
                        semantic_name="../escape",
                    ),
                    ClassificationDecision(
                        node_id="1:2",
                        output_type="TEXT",
                        rule_id="custom.semantic",
                        rule_version=1,
                        evidence=("untrusted custom analyzer",),
                        confidence=1,
                        source=DecisionSource.AI,
                        semantic_name="main",
                    ),
                )
            )

    baseline_root = tmp_path / "baseline"
    guarded_root = tmp_path / "guarded"
    baseline = convert_document(
        _raw_document(),
        _project(tmp_path / "baseline-project"),
        "Sample",
        baseline_root,
        Path("rules/default/classification.yaml"),
    )
    guarded = convert_document(
        _raw_document(),
        _project(tmp_path / "guarded-project"),
        "Sample",
        guarded_root,
        Path("rules/default/classification.yaml"),
        semantic_analyzer=UnsafeAnalyzer(),
    )

    assert _file_hashes(guarded_root) == _file_hashes(baseline_root)
    assert guarded.files == baseline.files
    assert "semantic.invalid_name" in {item.code for item in guarded.diagnostics}
    assert "semantic.name_conflict" in {item.code for item in guarded.diagnostics}
    assert "semantic.ai_applied" not in {item.code for item in guarded.diagnostics}


@pytest.mark.parametrize(
    "registered_name",
    ["Panel_Sample_Login.xml", "panel_sample_login.XML"],
)
def test_ai_name_cannot_replace_an_existing_component_owned_by_another_source(
    tmp_path: Path, registered_name: str,
) -> None:
    class RenamingAnalyzer:
        def analyze(
            self,
            roots: tuple[object, ...],
            *,
            rule_candidates: tuple[ClassificationDecision, ...],
            screenshot: bytes | None = None,
        ) -> SemanticAnalysisOutcome:
            assert roots and rule_candidates
            return SemanticAnalysisOutcome(
                overrides=(
                    ClassificationDecision(
                        node_id="1:1",
                        output_type="PANEL",
                        rule_id="ai.semantic.v1",
                        rule_version=1,
                        evidence=("validated structured AI decision",),
                        confidence=0.9,
                        source=DecisionSource.AI,
                        semantic_name="Login",
                    ),
                )
            )

    raw = _raw_document()
    raw["name"] = "Checkout"
    project = _project(tmp_path)
    package_path = project / "Sample/package.xml"
    package = etree.parse(str(package_path))
    resources = package.getroot().find("resources")
    assert resources is not None
    etree.SubElement(
        resources,
        "component",
        id="existing-login",
        name=registered_name,
        path="/Panel/",
        exported="true",
    )
    package.write(str(package_path), encoding="utf-8", xml_declaration=True)
    existing_login = project / "Sample/Panel/Panel_Sample_Login.xml"
    existing_login.parent.mkdir(exist_ok=True)
    existing_login.write_bytes(b"existing-login-component")

    result = convert_document(
        raw,
        project,
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
        semantic_analyzer=RenamingAnalyzer(),
    )

    paths = {item.relative_path for item in result.files}
    assert "Sample/Panel/Panel_Sample_Checkout.xml" in paths
    assert "Sample/Panel/Panel_Sample_Login.xml" not in paths
    assert "semantic.project_name_conflict" in {item.code for item in result.diagnostics}
    assert existing_login.read_bytes() == b"existing-login-component"
