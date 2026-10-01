from pathlib import Path

from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_models import FGUIPlanDocument
from figma_to_fgui.fgui_plan_validate import canonical_plan_bytes, validate_fgui_plan
from figma_to_fgui.uir_models import UIRDocument

FIXTURE = Path("tests/fixtures/fgui-plan/generic-primitives.uir.json")
EXPECTED = Path("tests/golden/expected/generic-primitives.fgui-plan.json")


def generic_document(*, name: str, width: float) -> UIRDocument:
    """Return the generic fixture with presentation facts changed only."""
    document = UIRDocument.model_validate_json(FIXTURE.read_text("utf-8"))
    root = document.nodes["node:root"]
    renamed_root = root.model_copy(
        update={
            "source": root.source.model_copy(update={"name": name}),
            "geometry": root.geometry.model_copy(
                update={
                    "resolved_bounds": root.geometry.resolved_bounds.model_copy(
                        update={"width": width}
                    )
                }
            ),
        }
    )
    return document.model_copy(
        update={"nodes": {**document.nodes, renamed_root.id: renamed_root}}
    )


def decision_rules(plan: FGUIPlanDocument) -> tuple[tuple[str, str, str, int], ...]:
    return tuple(
        sorted(
            (
                decision.node_ref,
                str(decision.status),
                decision.rule_id,
                decision.rule_version,
            )
            for decision in plan.decisions.values()
        )
    )


def test_generic_primitives_match_reviewed_plan_golden() -> None:
    document = UIRDocument.model_validate_json(FIXTURE.read_text("utf-8"))

    plan = compile_fgui_plan(document)

    assert validate_fgui_plan(plan) == ()
    assert canonical_plan_bytes(plan) == EXPECTED.read_bytes()


def test_plan_implementation_contains_no_village_specific_branch() -> None:
    implementation = "\n".join(
        Path(path).read_text("utf-8")
        for path in (
            "src/figma_to_fgui/fgui_capabilities.py",
            "src/figma_to_fgui/fgui_plan_compile.py",
        )
    )

    for forbidden in ("村庄升阶", "village-root", "selection_village_ascend"):
        assert forbidden not in implementation


def test_name_and_size_mutation_preserves_rule_selection() -> None:
    original = compile_fgui_plan(generic_document(name="Inventory", width=750))
    mutated = compile_fgui_plan(generic_document(name="Event Shop", width=1440))

    assert decision_rules(original) == decision_rules(mutated)
