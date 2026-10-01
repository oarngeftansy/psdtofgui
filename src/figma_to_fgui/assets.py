import hashlib

from figma_to_fgui.models import ClassificationDecision, NormalizedNode, ResourcePlan
from figma_to_fgui.project_index import ProjectIndex


def make_resource_id(seed: str, occupied: frozenset[str]) -> str:
    attempt = 0
    while True:
        digest = hashlib.sha256(f"{seed}|{attempt}".encode()).hexdigest()[:8]
        if digest not in occupied:
            return digest
        attempt += 1


def plan_resources(
    roots: tuple[NormalizedNode, ...],
    decisions: tuple[ClassificationDecision, ...],
    index: ProjectIndex,
    package_name: str,
) -> tuple[ResourcePlan, ...]:
    nodes: dict[str, NormalizedNode] = {}

    def collect(node: NormalizedNode) -> None:
        nodes[node.id] = node
        for child in node.children:
            collect(child)

    for root in roots:
        collect(root)
    occupied = set(index.ids_by_package.get(package_name, frozenset()))
    plans: list[ResourcePlan] = []
    for decision in decisions:
        if decision.output_type not in {"IMAGE", "PANEL"}:
            continue
        node = nodes[decision.node_id]
        name = f"Bg_{node.name.replace(' ', '_')}.png"
        resource_id = make_resource_id(f"{node.id}|{name}", frozenset(occupied))
        occupied.add(resource_id)
        plans.append(
            ResourcePlan(
                node_id=node.id,
                action="GENERATE",
                resource_name=name,
                resource_id=resource_id,
                relative_path=f"{package_name}/Img/{name}",
            )
        )
    return tuple(plans)
