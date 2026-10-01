"""Prove two PSD button bodies can replace distinct instances without overlays.

This is an isolated Editor diagnostic, not a full-page candidate or approval.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from lxml import etree

from figma_to_fgui.fairygui_editor_verify import (
    _install_bridge,
    _send_command,
    discover_fairygui_editor,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    base = args.evidence_dir
    source = max(base.glob("graph-image-owned-body-4273-[0-9]*"))
    run = Path.home() / "HifiEditorRuns" / f"variant-{uuid.uuid4().hex[:8]}"
    shutil.copytree(source, run)
    package = run / "assets/Tower"
    original = package / "Component/Btn_Tower_Mian_Enter.xml"
    variant = package / "Component/Btn_Tower_Mian_Enter_Reward.xml"
    original_doc = etree.parse(str(original))
    variant_doc = etree.parse(str(original))
    original_children = original_doc.xpath("./displayList/*/@id")
    variant_image = variant_doc.xpath("./displayList/*[@id='n30_gm65']")[0]
    image_id = "hmerit001"
    image_name = "MeritRewardsBody.png"
    variant_image.set("src", image_id)
    variant_image.set("fileName", image_name)
    variant_doc.write(str(variant), encoding="utf-8", xml_declaration=True)
    image_rel = Path("Img/HIFI/Btn_Tower_Mian_Enter") / image_name
    shutil.copyfile(base / "owned-body-shadow-4847.png", package / image_rel)

    variant_id = "hfie0001"
    manifest_path = package / "package.xml"
    manifest = etree.parse(str(manifest_path))
    resources = manifest.find("./resources")
    assert resources is not None
    assert not resources.xpath("./*[@id='hfie0001' or @id='hmerit001']")
    resources.append(etree.Element("component", id=variant_id, name=variant.name, path="/Component/", exported="true"))
    resources.append(etree.Element("image", id=image_id, name=image_name,
                                   path="/Img/HIFI/Btn_Tower_Mian_Enter/", exported="true", atlas="0"))
    manifest.write(str(manifest_path), encoding="utf-8", xml_declaration=True)

    root_path = package / "Panel/Panel_Tower_Main.xml"
    root = etree.parse(str(root_path))
    before_ids = root.xpath("./displayList/*/@id")
    reward = root.xpath("./displayList/*[@id='n6']")[0]
    assert reward.get("src") == "gm65e21"
    reward.set("src", variant_id)
    reward.set("fileName", f"Component/{variant.name}")
    root.write(str(root_path), encoding="utf-8", xml_declaration=True)
    assert root.xpath("./displayList/*/@id") == before_ids
    assert variant_doc.xpath("./displayList/*/@id") == original_children
    assert len(root.xpath("./displayList/*")) == 21
    assert len(variant_doc.xpath("./displayList/*")) == 4
    assert [c.get("name") for c in original_doc.findall("controller")] == [
        c.get("name") for c in variant_doc.findall("controller")
    ]

    editor = discover_fairygui_editor()
    if editor is None:
        raise RuntimeError("fgui_editor_not_found")
    bridge = _install_bridge(run)
    project = next(run.glob("*.fairy"))
    process = subprocess.Popen([str(editor), str(project.resolve())])
    try:
        for attempt in range(40):
            try:
                _send_command(bridge, "list_packages", {}, 2)
                break
            except ValueError:
                if attempt == 39:
                    raise
                time.sleep(0.5)
        _send_command(bridge, "start_test", {"package_name": "Tower", "component_name": "Panel_Tower_Main"}, 20)
        time.sleep(2)
        _send_command(bridge, "capture_preview", {"save_name": "prime", "scale": 1, "offset_y": 600}, 20)
        capture = _send_command(bridge, "capture_preview", {"save_name": "two-bodies", "scale": 1, "offset_y": 600}, 20)
        evidence = Path(capture["data"]["path"])
        screenshot = base / "variant-two-bodies-editor.png"
        shutil.copyfile(evidence, screenshot)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    report = {
        "diagnostic_only": True,
        "approvable": False,
        "project": str(run),
        "screenshot": str(screenshot),
        "root_ids_preserved": True,
        "button_child_ids_preserved": True,
        "delegate_resource_id": "gm65e21",
        "reward_resource_id": variant_id,
        "distinct_materials": [hashlib.sha256((package / "Img/HIFI/Btn_Tower_Mian_Enter/PSD_leaf_mechanics_only-8e5f443c.png").read_bytes()).hexdigest(),
                               hashlib.sha256((package / image_rel).read_bytes()).hexdigest()],
        "game_resource_id_dependency_unverified": True,
    }
    (base / "variant-two-bodies-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
