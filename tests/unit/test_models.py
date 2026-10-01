import pytest
from pydantic import ValidationError

from figma_to_fgui.models import Bounds, Diagnostic, Severity


def test_contracts_are_immutable_and_validate_geometry() -> None:
    bounds = Bounds(x=1.5, y=2.5, width=100, height=50)
    with pytest.raises(ValidationError):
        bounds.width = 101
    with pytest.raises(ValidationError):
        Bounds(x=0, y=0, width=-1, height=10)


def test_diagnostic_has_stable_code_and_context() -> None:
    diagnostic = Diagnostic(
        code="geometry.rounded",
        severity=Severity.INFO,
        message="Rounded width from 10.4 to 10",
        node_id="12:34",
        rule_id="geometry.integer-output",
        rule_version=1,
    )
    assert diagnostic.code == "geometry.rounded"
