from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from figma_to_fgui.fairygui_editor_verify import (
    FairyGuiEditorVerificationError,
    _image_evidence,
    discover_fairygui_editor,
    verify_in_fairygui_editor,
)
from figma_to_fgui.hifi_replacement_models import HifiTargetRef


def test_discovers_configured_editor(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "FairyGUI-Editor.exe"
    executable.write_bytes(b"editor")
    monkeypatch.setenv("FAIRYGUI_EDITOR_PATH", str(executable))

    assert discover_fairygui_editor() == executable.resolve()


def test_image_evidence_requires_a_nonempty_full_size_capture(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    exact = tmp_path / "exact.png"
    cropped = tmp_path / "cropped.png"
    blank = tmp_path / "blank.png"
    preview_shell = tmp_path / "preview-shell.png"
    source = Image.new("RGB", (20, 10), (20, 30, 40))
    for y in range(10):
        for x in range(20):
            source.putpixel((x, y), (x * 12, y * 24, (x + y) * 8))
    source.save(reference)
    source.save(exact)
    source.crop((0, 0, 18, 10)).save(cropped)
    Image.new("RGB", (20, 10), (0, 0, 0)).save(blank)
    shell = Image.new("RGB", (20, 10), (0, 0, 0))
    for y in range(5):
        for x in range(20):
            shell.putpixel((x, y), (160, 160, 160))
    shell.save(preview_shell)

    assert _image_evidence(exact, reference, 20, 10)[:3] == (20, 10, True)
    assert _image_evidence(exact, reference, 20, 10)[4] is True
    assert _image_evidence(cropped, reference, 20, 10) == (18, 10, False, None, True)
    assert _image_evidence(blank, reference, 20, 10) == (20, 10, True, None, False)
    assert _image_evidence(preview_shell, reference, 20, 10) == (
        20,
        10,
        True,
        None,
        False,
    )


def test_image_evidence_never_resizes_the_psd_reference(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.png"
    wrong_size_reference = tmp_path / "wrong-size-reference.png"
    image = Image.new("RGB", (20, 10), (20, 30, 40))
    for y in range(10):
        for x in range(20):
            image.putpixel((x, y), (x * 12, y * 24, (x + y) * 8))
    image.save(rendered)
    Image.new("RGB", (10, 5), (20, 30, 40)).save(wrong_size_reference)

    with pytest.raises(
        FairyGuiEditorVerificationError,
        match="fgui_reference_dimensions_invalid",
    ):
        _image_evidence(rendered, wrong_size_reference, 20, 10)


def test_editor_verification_rejects_a_full_frame_with_visible_pixel_difference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "FairyGUI-Editor.exe"
    executable.write_bytes(b"editor")
    artifact = tmp_path / "candidate.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr(
            "Candidate.fairy",
            '<projectDescription id="1234567890abcdef1234567890abcdef" '
            'type="Unity" version="5.0"/>',
        )
    reference = tmp_path / "reference.png"
    rendered = tmp_path / "rendered.png"
    source = Image.new("RGB", (20, 10))
    different = Image.new("RGB", (20, 10))
    for y in range(10):
        for x in range(20):
            source.putpixel((x, y), (x * 12, y * 24, (x + y) * 8))
            different.putpixel((x, y), (255 - x * 12, 255 - y * 24, 255 - (x + y) * 8))
    source.save(reference)
    different.save(rendered)
    prime = tmp_path / "prime.png"
    source.save(prime)

    class FinishedEditor:
        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float) -> int:
            return 0

    captures = iter((prime, rendered))

    def send_command(_bridge: Path, action: str, _params: dict[str, object], _timeout: float):
        if action == "list_packages":
            return {"data": {"packages": [{"name": "Tower"}]}}
        if action == "capture_preview":
            return {"data": {"path": str(next(captures))}}
        return {"data": {}}

    monkeypatch.setattr(
        "figma_to_fgui.fairygui_editor_verify.discover_fairygui_editor",
        lambda: executable,
    )
    monkeypatch.setattr("figma_to_fgui.fairygui_editor_verify._send_command", send_command)
    monkeypatch.setattr(
        "figma_to_fgui.fairygui_editor_verify.subprocess.Popen",
        lambda _args: FinishedEditor(),
    )
    monkeypatch.setattr("figma_to_fgui.fairygui_editor_verify.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("FAIRYGUI_EDITOR_RUN_ROOT", str(tmp_path / "runs"))
    target = HifiTargetRef(
        project_id="a" * 32,
        project_fingerprint="b" * 64,
        package_id="tower123",
        package_name="Tower",
        directory="Panel",
        component_id="panel123",
        component_name="Panel_Tower_Main",
        component_relative_path="assets/Tower/Panel/Panel_Tower_Main.xml",
    )

    verification = verify_in_fairygui_editor(
        data_dir=tmp_path / "data",
        session_id="c" * 32,
        candidate_sha256="d" * 64,
        artifact=artifact,
        target=target,
        reference=reference,
        expected_width=20,
        expected_height=10,
    )

    assert verification.full_frame is True
    assert verification.mean_pixel_difference is not None
    assert verification.mean_pixel_difference > 0.01
    assert verification.approvable is False


def test_editor_verification_rejects_even_one_changed_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "FairyGUI-Editor.exe"
    executable.write_bytes(b"editor")
    artifact = tmp_path / "candidate.zip"
    with ZipFile(artifact, "w") as archive:
        archive.writestr(
            "Candidate.fairy",
            '<projectDescription id="1234567890abcdef1234567890abcdef" '
            'type="Unity" version="5.0"/>',
        )
    reference = tmp_path / "reference.png"
    rendered = tmp_path / "rendered.png"
    source = Image.new("RGB", (20, 10), (20, 30, 40))
    for y in range(10):
        for x in range(20):
            source.putpixel((x, y), (x * 12, y * 24, (x + y) * 8))
    changed = source.copy()
    red, green, blue = changed.getpixel((10, 5))
    changed.putpixel((10, 5), (red + 1, green, blue))
    source.save(reference)
    changed.save(rendered)
    prime = tmp_path / "prime.png"
    source.save(prime)

    class FinishedEditor:
        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float) -> int:
            return 0

    captures = iter((prime, rendered))

    def send_command(_bridge: Path, action: str, _params: dict[str, object], _timeout: float):
        if action == "list_packages":
            return {"data": {"packages": [{"name": "Tower"}]}}
        if action == "capture_preview":
            return {"data": {"path": str(next(captures))}}
        return {"data": {}}

    monkeypatch.setattr(
        "figma_to_fgui.fairygui_editor_verify.discover_fairygui_editor",
        lambda: executable,
    )
    monkeypatch.setattr("figma_to_fgui.fairygui_editor_verify._send_command", send_command)
    monkeypatch.setattr(
        "figma_to_fgui.fairygui_editor_verify.subprocess.Popen",
        lambda _args: FinishedEditor(),
    )
    monkeypatch.setattr("figma_to_fgui.fairygui_editor_verify.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("FAIRYGUI_EDITOR_RUN_ROOT", str(tmp_path / "runs"))
    target = HifiTargetRef(
        project_id="a" * 32,
        project_fingerprint="b" * 64,
        package_id="tower123",
        package_name="Tower",
        directory="Panel",
        component_id="panel123",
        component_name="Panel_Tower_Main",
        component_relative_path="assets/Tower/Panel/Panel_Tower_Main.xml",
    )

    verification = verify_in_fairygui_editor(
        data_dir=tmp_path / "data",
        session_id="e" * 32,
        candidate_sha256="f" * 64,
        artifact=artifact,
        target=target,
        reference=reference,
        expected_width=20,
        expected_height=10,
    )

    assert verification.mean_pixel_difference is not None
    assert 0 < verification.mean_pixel_difference < 0.01
    assert verification.approvable is False
