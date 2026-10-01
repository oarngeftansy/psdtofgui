from __future__ import annotations

from pathlib import Path

from scripts.run_hifi_replacement_acceptance import run_acceptance


def test_hifi_replacement_delivery_preserves_scope_and_candidate_hash(tmp_path: Path) -> None:
    workspace = Path(__file__).parents[2]
    result = run_acceptance(workspace, tmp_path / "candidate.zip")
    assert result["automatedStatus"] == "PASS"
    assert result["scopeValid"] is True
    assert result["protectedIdentityUnchanged"] is True
    assert result["sharedReferenceUnchanged"] is True
    assert result["lowfiReferenceReplaced"] is True
    assert result["hifiResourceRegistered"] is True
    assert result["hifiResourceBytesMatch"] is True
    assert result["approvalBlocked"] is True
    assert result["deliveryBlocked"] is True
    assert result["candidateSha256"] == result["reviewSha256"]
    assert result["archiveContainsTarget"] is True
    assert result["decisionCounts"] == {
        "accept": 4,
        "add_visual": 1,
        # The tentative badge already reserves its PSD node; it is not also
        # emitted as an unmatched addition requiring a second exception.
        "exception": 1,
        "keep_old": 2,
        "retarget": 1,
    }
    assert result["objectDiffCounts"] == {
        "added": 1,
        "changed": 5,
        "exception": 1,
        "kept": 2,
    }
