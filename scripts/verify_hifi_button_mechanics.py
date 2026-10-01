"""Real-material mechanical regression; never a full-page acceptance or mapping."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from lxml import etree

from figma_to_fgui.apply import apply_bundle
from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode, SelectionResource
from figma_to_fgui.hifi_controller_states import capture_controller_evidence
from figma_to_fgui.hifi_mapping import build_mapping
from figma_to_fgui.hifi_nested import component_target, inspect_component_tree
from figma_to_fgui.hifi_patch import build_hifi_change_bundle, validate_hifi_candidate
from figma_to_fgui.hifi_replacement_models import FguiComponentInventory
from figma_to_fgui.models import Bounds
from figma_to_fgui.psd_source_store import PsdRasterResource, PsdSourceStore

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--evidence-dir",
    type=Path,
    required=True,
    help="Folder with real-import report.json and inventory.json; never approves a session",
)
parser.add_argument(
    "--layer-id", type=int, default=4265, help="Actual PSD leaf to exercise; diagnostic only"
)
parser.add_argument(
    "--owned-body",
    type=int,
    choices=(4273, 4847),
    help="Diagnostic background only; does not approve shared-instance equivalence",
)
args = parser.parse_args()
base = args.evidence_dir
label = "graph-image-mechanics" if args.layer_id == 4265 else f"graph-image-leaf-{args.layer_id}"
if args.owned_body:
    label = f"graph-image-owned-body-{args.owned_body}"
root = Path(json.loads((base / "report.json").read_text("utf-8"))["source_root"])
target = FguiComponentInventory.model_validate_json(
    (base / "inventory.json").read_text("utf-8")
).target
inventory = inspect_component_tree(root, target)
store = PsdSourceStore(base)
source_id = "e6762c29014eef93aa5fd0e7a41e38e2b63e081566982854a744e128d71e4961"
source = store.get(source_id)
layer = next(l for l in source.layers if l.id.endswith(f":{args.layer_id}"))
material = store.raster_resource(source_id, layer.id)
ownership = None
if args.owned_body:
    from psd_tools import PSDImage

    from figma_to_fgui.psd_effect_render import render_owned_visual

    leaves, text_id = (
        ((4265, 4267, 4269), 4271) if args.owned_body == 4273 else ((4840, 4842, 4844), 4846)
    )
    key = lambda v: f"psd-layer:{source_id}:{v}"
    document = PSDImage.open(store.artifact_path(source_id) / "source.psd")
    rendered, bounds = render_owned_visual(
        document,
        source,
        key(args.owned_body),
        frozenset(map(key, leaves)),
        frozenset({key(text_id)}),
    )
    resource_key = "owned-diagnostic-" + hashlib.sha256(rendered.tobytes()).hexdigest()[:32]
    resource = store.artifact_path(source_id) / "resources" / resource_key
    rendered.save(resource, format="PNG")
    material = PsdRasterResource(
        layer_id=key(args.owned_body),
        key=resource_key,
        mime_type="image/png",
        size=resource.stat().st_size,
        bounds=bounds,
    )
    ownership = {
        "group": args.owned_body,
        "owned_leaves": leaves,
        "retained_text": text_id,
        "render_bounds": bounds,
        "same_body_used_for_both_instances_for_mechanics_only": True,
    }
ids = ("n30_gm65:n30_gm65", "n6:n30_gm65")
objects = {o.object_id: o for o in inventory.objects}
nodes = tuple(
    SelectionNode(
        id="diagnostic-" + str(index),
        name="PSD leaf mechanics only",
        type="IMAGE",
        bounds=Bounds(
            x=objects[key].x, y=objects[key].y, width=objects[key].width, height=objects[key].height
        ),
        resource_keys=(material.key,),
    )
    for index, key in enumerate(ids)
)
selection = SelectionManifest(
    display_name="Actual PSD leaf; diagnostic only",
    top_level_nodes=(
        SelectionNode(
            id="psd-root:" + source_id,
            name="PSD",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=1080, height=2340),
            children=nodes,
        ),
    ),
    resources=(
        SelectionResource(key=material.key, mime_type=material.mime_type, size=material.size),
    ),
)
mapping = build_mapping(inventory, selection)
lookup = dict(zip(ids, [n.id for n in nodes]))
mapping = mapping.model_copy(
    update={
        "items": tuple(
            i.model_copy(update={"action": "retarget", "figma_node_id": lookup[i.old_object_id]})
            if i.old_object_id in lookup
            else i.model_copy(update={"action": "keep_old" if i.old_object_id else "exception"})
            for i in mapping.items
        ),
        "unresolved_count": 0,
    }
)
candidate = base / (label + "-" + str(int(time.time())))
shutil.copytree(root, candidate)
bundle = build_hifi_change_bundle(
    root, inventory, selection, mapping, selection_root=store.artifact_path(source_id)
)
apply_bundle(candidate, bundle)
review = validate_hifi_candidate(
    root, candidate, inventory, mapping, session_id="0" * 32, lossless_blockers=("diagnostic_only",)
)
child_path = "assets/Tower/Component/Btn_Tower_Mian_Enter.xml"
before, after = [etree.parse(str(p / child_path)) for p in (root, candidate)]
assert before.xpath("./displayList/*/@id") == after.xpath("./displayList/*/@id")
assert len(after.xpath("./displayList/*")) == 4
assert after.find("./displayList")[0].tag == "image"
assert (candidate / target.component_relative_path).read_bytes() == (
    root / target.component_relative_path
).read_bytes()
print("Actual shared graph converted in place; protected XML preserved", flush=True)
child_target = component_target(root, target, child_path)
evidence = capture_controller_evidence(
    root, candidate, child_target, candidate.parent / (label + "-controller-evidence")
)
report = {
    "diagnostic_only": True,
    "full_page_mapping_complete": False,
    "approvable": False,
    "candidate": str(candidate.resolve()),
    "source_layer_id": material.layer_id,
    "visual_ownership": ownership,
    "instance_ids": ids,
    "root_object_count": len(
        etree.parse(str(root / target.component_relative_path)).xpath("./displayList/*")
    ),
    "button_object_count": 4,
    "identity_order_and_behavior_preserved": review.protected_checks_passed,
    "changed_files": [f.relative_path for f in bundle.files],
    "controller_evidence": evidence,
    "scope": "Shared graph/image mechanics with actual PSD leaf; full decoration fidelity, per-instance art, input interactions, transitions and game bindings are not accepted.",
}
(base / (label + "-report.json")).write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False), flush=True)
