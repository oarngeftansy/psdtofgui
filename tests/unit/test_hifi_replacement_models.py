from __future__ import annotations

import pytest
from pydantic import ValidationError

from figma_to_fgui.hifi_replacement_models import HifiMappingDecision


def test_mapping_decision_rejects_delete_and_stale_shape() -> None:
    with pytest.raises(ValidationError):
        HifiMappingDecision(
            version=1,
            mapping_revision=2,
            item_id="m1",
            action="delete",
        )

    decision = HifiMappingDecision(
        version=1,
        mapping_revision=2,
        item_id="m1",
        action="keep_old",
    )
    assert decision.action == "keep_old"


def test_retarget_requires_exactly_one_figma_node() -> None:
    with pytest.raises(ValidationError, match="requires figma_node_id"):
        HifiMappingDecision(
            version=1,
            mapping_revision=1,
            item_id="m1",
            action="retarget",
        )
    with pytest.raises(ValidationError, match="only valid for retarget"):
        HifiMappingDecision(
            version=1,
            mapping_revision=1,
            item_id="m1",
            action="accept",
            figma_node_id="12:4",
        )


def test_contracts_are_strict_and_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        HifiMappingDecision.model_validate(
            {
                "version": "1",
                "mapping_revision": 1,
                "item_id": "m1",
                "action": "keep_old",
            }
        )
    with pytest.raises(ValidationError):
        HifiMappingDecision(
            version=1,
            mapping_revision=1,
            item_id="m1",
            action="keep_old",
            unsafe=True,
        )
