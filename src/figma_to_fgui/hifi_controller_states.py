"""Real Editor evidence for declared controller states, separate from approval."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from lxml import etree
from PIL import Image

from figma_to_fgui.fairygui_editor_verify import (
    _install_bridge,
    _send_command,
    discover_fairygui_editor,
)


def controller_matrix(xml: bytes, *, limit: int = 64) -> list[dict[str, int]]:
    document = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    names, sizes = [], []
    for controller in document.findall("controller"):
        name = controller.get("name")
        pages = controller.get("pages", "").split(",")
        if not name or name in names or len(pages) % 2:
            raise ValueError("hifi_controller_contract_invalid")
        names.append(name)
        sizes.append(len(pages) // 2)
    if math.prod(sizes) > limit:
        raise ValueError("hifi_controller_state_budget_exceeded")
    return [dict(zip(names, values)) for values in itertools.product(*(range(n) for n in sizes))]


def compare_state_responses(before: list[Path], after: list[Path]) -> bool:
    if not before or len(before) != len(after):
        return False

    def pixels(path: Path) -> np.ndarray:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGBA"), dtype=np.int16)

    old_base, new_base = pixels(before[0]), pixels(after[0])
    if old_base.shape != new_base.shape:
        return False
    for old_path, new_path in zip(before, after):
        old, new = pixels(old_path), pixels(new_path)
        if old.shape != old_base.shape or new.shape != old_base.shape:
            return False
        if not np.array_equal(old - old_base, new - new_base):
            return False
    return True


def capture_runtime_state(bridge: Path, state: dict[str, int], index: int) -> Path:
    for name, page in state.items():
        changed = _send_command(
            bridge,
            "switch_controller",
            {"controller_name": name, "page_index": page, "target": "runtime"},
            20,
        )
        data = changed.get("data", {})
        if data.get("newIndex") != page or data.get("target") != "runtime":
            raise ValueError("hifi_runtime_controller_switch_failed")
    for label in ("prime", f"state-{index}"):
        captured = _send_command(
            bridge, "capture_preview", {"save_name": label, "scale": 1, "offset_y": 0}, 20
        )
        if captured.get("data", {}).get("capture_source") != "testView_content":
            raise ValueError("hifi_runtime_capture_unverified")
    return Path(captured["data"]["path"])


def capture_controller_evidence(before_root: Path, after_root: Path, target: Any, output: Path) -> dict[str, Any]:
    xml = (before_root / target.component_relative_path).read_bytes()
    states = controller_matrix(xml)
    if states != controller_matrix((after_root / target.component_relative_path).read_bytes()):
        raise ValueError("hifi_controller_contract_changed")
    executable = discover_fairygui_editor()
    if executable is None:
        raise ValueError("fgui_editor_not_found")
    output.mkdir(parents=True, exist_ok=True)
    captures: dict[str, list[Path]] = {}
    for label, project in (("before", before_root), ("after", after_root)):
        run = Path.home() / "HifiEditorRuns" / f"controller-{label}-{uuid.uuid4().hex[:8]}"
        shutil.copytree(project, run)
        bridge = _install_bridge(run)
        process = subprocess.Popen([str(executable), str(next(run.glob("*.fairy")).resolve())])
        try:
            for attempt in range(25):
                try:
                    _send_command(bridge, "list_packages", {}, 2)
                    break
                except ValueError:
                    if attempt == 24:
                        raise
            _send_command(
                bridge,
                "start_test",
                {"package_name": target.package_name, "component_name": target.component_name},
                20,
            )
            time.sleep(2)  # Editor initializes the F5 instance asynchronously.
            captures[label] = []
            for index, state in enumerate(states):
                captured = capture_runtime_state(bridge, state, index)
                path = output / f"{label}-{index}.png"
                shutil.copyfile(captured, path)
                captures[label].append(path)
                print(f"Editor controller evidence: {label} {index + 1}/{len(states)}", flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    response_preserved = compare_state_responses(captures["before"], captures["after"])
    report = {
        "component": target.component_relative_path,
        "capture_mode": "F5_runtime",
        "states": states,
        "declared_controller_states_complete": True,
        "state_pixel_response_preserved": response_preserved,
        "before_component_sha256": hashlib.sha256(xml).hexdigest(),
        "after_component_sha256": hashlib.sha256(
            (after_root / target.component_relative_path).read_bytes()
        ).hexdigest(),
        "captures": {
            label: [
                {"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in paths
            ]
            for label, paths in captures.items()
        },
        "interactions_transitions_and_game_bindings_verified": False,
        "approvable": False,
    }
    (output / "controller-evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
