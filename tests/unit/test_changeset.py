from pathlib import Path

from figma_to_fgui.changeset import build_changeset, hash_tree
from figma_to_fgui.models import Diagnostic, Severity


def test_tree_hash_is_stable_and_changes_when_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    file = source / "package.xml"
    file.write_text("<package/>", "utf-8")
    first = hash_tree(source)
    assert hash_tree(source) == first
    file.write_text("<package id='changed'/>", "utf-8")
    assert hash_tree(source) != first


def test_error_diagnostic_marks_changeset_not_applicable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    source.mkdir()
    staging.mkdir()
    (staging / "file.txt").write_text("new", "utf-8")
    error = Diagnostic(code="x", severity=Severity.ERROR, message="blocked")
    changeset = build_changeset(source, staging, (error,))
    assert not changeset.applicable
    assert changeset.files[0].relative_path == "file.txt"
