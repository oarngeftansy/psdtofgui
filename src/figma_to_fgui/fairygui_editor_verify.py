from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from PIL import Image, ImageChops, ImageStat

from figma_to_fgui.hifi_replacement_models import HifiEditorVerification, HifiTargetRef
from figma_to_fgui.paths import safe_relative_path


class FairyGuiEditorVerificationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def discover_fairygui_editor() -> Path | None:
    configured = os.environ.get("FAIRYGUI_EDITOR_PATH")
    candidates = [
        Path(configured) if configured else None,
        Path.home() / "Tools/FairyGUI-Editor-6.1.4/FairyGUI-Editor/FairyGUI-Editor.exe",
        Path.home() / "Downloads/FairyGUI-Editor_6.1.4/FairyGUI-Editor/FairyGUI-Editor.exe",
        Path("C:/Program Files/FairyGUI-Editor/FairyGUI-Editor.exe"),
    ]
    command = shutil.which("FairyGUI-Editor.exe")
    if command:
        candidates.append(Path(command))
    return next((path.resolve() for path in candidates if path and path.is_file()), None)


def _send_command(bridge: Path, action: str, params: dict[str, object], timeout: float) -> dict[str, Any]:
    command_id = "cmd_" + uuid.uuid4().hex[:8]
    command = bridge / "commands" / f"{command_id}.json"
    result = bridge / "results" / f"{command_id}.json"
    command.parent.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = command.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"id": command_id, "action": action, "params": params, "timeout": int(timeout * 1000)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, command)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result.is_file():
            try:
                payload = json.loads(result.read_text("utf-8"))
                result.unlink(missing_ok=True)
                if not isinstance(payload, dict) or payload.get("status") != "success":
                    error = FairyGuiEditorVerificationError("fgui_editor_command_failed")
                    if isinstance(payload, dict):
                        error.add_note(f"Editor action {action}: {payload.get('error', 'unknown error')}")
                    raise error
                return payload
            except json.JSONDecodeError:
                pass
        time.sleep(0.1)
    command.unlink(missing_ok=True)
    raise FairyGuiEditorVerificationError("fgui_editor_timeout")


def _install_bridge(project: Path) -> Path:
    source = Path(__file__).with_name("editor_bridge_assets") / "MCPBridge"
    if not source.is_dir():
        raise FairyGuiEditorVerificationError("fgui_editor_bridge_missing")
    destination = project / "plugins/MCPBridge"
    shutil.copytree(source, destination, dirs_exist_ok=True)
    bridge = destination / "bridge"
    for name in ("commands", "results", "screenshots"):
        (bridge / name).mkdir(parents=True, exist_ok=True)
    return bridge


def _has_package(payload: dict[str, Any], package_name: str) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    packages = data.get("packages")
    values = packages if isinstance(packages, list) else packages.values() if isinstance(packages, dict) else ()
    return any(isinstance(item, dict) and item.get("name") == package_name for item in values)


def _extract_candidate(artifact: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    with ZipFile(artifact) as archive:
        for member in archive.infolist():
            relative = safe_relative_path(member.filename.rstrip("/")) if member.filename.rstrip("/") else None
            if relative is None:
                continue
            target = destination / relative
            target.resolve().relative_to(destination.resolve())
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    projects = list(destination.glob("*.fairy"))
    if len(projects) != 1:
        raise FairyGuiEditorVerificationError("fgui_editor_project_invalid")
    return projects[0]


def _image_evidence(
    screenshot: Path,
    reference: Path,
    expected_width: int,
    expected_height: int,
) -> tuple[int, int, bool, float | None, bool]:
    with Image.open(screenshot) as rendered:
        rendered.load()
        width, height = rendered.size
        rgb = rendered.convert("RGB")
        extrema = ImageStat.Stat(rgb).extrema
        # A failed test-view capture can be the correct size while containing
        # only the preview's gray/black shell. Require real image complexity,
        # not merely two different solid colors.
        has_pixels = any(high > low for low, high in extrema) and rgb.convert("L").entropy() >= 2
        dimension_match = (width, height) == (expected_width, expected_height)
        if not dimension_match or not has_pixels:
            return width, height, dimension_match, None, has_pixels
        with Image.open(reference) as source:
            if source.size != (expected_width, expected_height):
                raise FairyGuiEditorVerificationError("fgui_reference_dimensions_invalid")
            expected = source.convert("RGB")
        difference = ImageChops.difference(rgb, expected)
        mean = sum(ImageStat.Stat(difference).mean) / (3 * 255)
        return width, height, True, mean, True


def verify_in_fairygui_editor(
    *,
    data_dir: Path,
    session_id: str,
    candidate_sha256: str,
    artifact: Path,
    target: HifiTargetRef,
    reference: Path,
    expected_width: int,
    expected_height: int,
) -> HifiEditorVerification:
    executable = discover_fairygui_editor()
    if executable is None:
        return HifiEditorVerification(
            version=1,
            session_id=session_id,
            candidate_sha256=candidate_sha256,
            editor_found=False,
            project_opened=False,
            component_opened=False,
            render_captured=False,
            expected_width=expected_width,
            expected_height=expected_height,
            full_frame=False,
            approvable=False,
            warnings=("未找到 FairyGUI Editor 6.1.4。",),
        )

    configured_run_root = os.environ.get("FAIRYGUI_EDITOR_RUN_ROOT")
    # FairyGUI's Lua plugin loader is unreliable when the project path contains
    # non-ASCII characters, so keep disposable verification projects in the
    # user's ASCII home path even when the application workspace is localized.
    run_root = (
        Path(configured_run_root)
        if configured_run_root
        else Path.home() / "HifiEditorRuns/figma-to-fgui"
    ) / session_id
    run_root.mkdir(parents=True, exist_ok=True)
    project_root = run_root / f"{candidate_sha256[:12]}-{uuid.uuid4().hex[:8]}"
    project_file = _extract_candidate(artifact, project_root)
    bridge = _install_bridge(project_root)
    # Editor silently opens an empty shell for relative project paths.
    process = subprocess.Popen([str(executable), str(project_file.resolve())])
    try:
        deadline = time.monotonic() + 60
        opened = False
        while time.monotonic() < deadline:
            try:
                packages = _send_command(bridge, "list_packages", {}, 2)
                if _has_package(packages, target.package_name):
                    opened = True
                    break
            except FairyGuiEditorVerificationError:
                if process.poll() not in {None, 0}:
                    break
            time.sleep(0.25)
        if not opened:
            raise FairyGuiEditorVerificationError("fgui_editor_start_failed")
        _send_command(
            bridge,
            "start_test",
            {"package_name": target.package_name, "component_name": target.component_name},
            20,
        )
        time.sleep(2)
        prime = _send_command(
            bridge,
            "capture_preview",
            {"save_name": candidate_sha256 + "-prime", "scale": 1, "offset_y": 0},
            20,
        )
        Path(str(prime.get("data", {}).get("path", ""))).unlink(missing_ok=True)
        capture = _send_command(
            bridge,
            "capture_preview",
            {
                "save_name": candidate_sha256,
                "scale": 1,
                # TestView clips tall components to the visible editor window.
                # Moving the capture object off the viewport for this one
                # render makes GetScreenShot render its complete local bounds;
                # the bridge restores the position immediately afterward.
                "offset_y": min(600, max(0, expected_height - 1)),
            },
            20,
        )
        screenshot = Path(str(capture.get("data", {}).get("path", "")))
        if not screenshot.is_file():
            raise FairyGuiEditorVerificationError("fgui_editor_capture_failed")
        evidence_dir = data_dir / "hifi-replacements/editor-evidence" / session_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence = evidence_dir / f"{candidate_sha256}.png"
        shutil.copyfile(screenshot, evidence)
        width, height, dimensions_match, difference, has_pixels = _image_evidence(
            evidence, reference, expected_width, expected_height
        )
        full_frame = dimensions_match and has_pixels
        warnings: list[str] = []
        if not has_pixels:
            warnings.append("Editor 截图为空，无法进行视觉比对。")
        if not dimensions_match:
            warnings.append(
                f"Editor 截图为 {width}×{height}，目标应为 {expected_width}×{expected_height}；尚未获得完整画面。"
            )
        if difference is not None and difference > 0:
            warnings.append(
                f"与 PSD 原图的平均像素差为 {difference:.4f}。写入内容为 PSD 栅格原件；"
                "差异通常来自按决策保留的旧对象与渲染舍入，请结合截图在 Editor 检查中核对。"
            )
        # Approval is deliberately exact.  A small average can conceal a
        # visibly wrong icon, glyph, or shifted edge in an otherwise large
        # canvas; only a byte-for-byte RGB match is acceptable here.
        visual_match = difference == 0.0
        if full_frame and not visual_match:
            warnings.append(
                "Editor 完整截图与 PSD 参考图存在任何像素差异；"
                "候选已阻断，必须修复映射或渲染差异后重新验证。"
            )
        return HifiEditorVerification(
            version=1,
            session_id=session_id,
            candidate_sha256=candidate_sha256,
            editor_found=True,
            editor_version="6.1.4",
            project_opened=True,
            component_opened=True,
            render_captured=True,
            screenshot_url=f"/v1/hifi-replacements/{session_id}/editor-screenshot",
            screenshot_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
            screenshot_width=width,
            screenshot_height=height,
            expected_width=expected_width,
            expected_height=expected_height,
            full_frame=full_frame,
            mean_pixel_difference=difference,
            approvable=full_frame and visual_match,
            warnings=tuple(warnings),
        )
    except FairyGuiEditorVerificationError:
        raise
    except OSError as error:
        raise FairyGuiEditorVerificationError("fgui_editor_start_failed") from error
    finally:
        # This Editor instance belongs exclusively to automated verification.
        # Leaving it open also leaves MCPBridge polling its command directory.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def screenshot_path(data_dir: Path, session_id: str, candidate_sha256: str) -> Path:
    return (
        data_dir
        / "hifi-replacements/editor-evidence"
        / session_id
        / f"{candidate_sha256}.png"
    )
