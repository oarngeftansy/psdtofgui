from __future__ import annotations

from figma_to_fgui.fgui_new_project_models import (
    ManifestComponent,
    ManifestObject,
    ManifestPackage,
    ManifestResource,
    NewProjectConfig,
    NewProjectManifest,
)
from figma_to_fgui.fgui_new_project_validate import (
    MAX_MANIFEST_COMPONENT_DEPTH,
    MAX_MANIFEST_DEPTH,
    validate_new_project_manifest,
)


def manifest_with_object_in_two_components() -> NewProjectManifest:
    shared = ManifestObject(
        id="1234abcd",
        sourceNodeRef="plan:shared",
        uirNodeRef="uir:shared",
        zIndex=0,
        type="container",
        transform={"bounds": {"x": 0, "y": 0, "width": 10, "height": 10}},
    )
    components = tuple(
        ManifestComponent(
            id=component_id,
            sourceComponentKind="root",
            sourceComponentRef=source_ref,
            name=name,
            relativePath=f"Panel/{name}-{component_id}.xml",
            size={"x": 0, "y": 0, "width": 10, "height": 10},
            objects=(shared,),
        )
        for component_id, source_ref, name in (
            ("1111aaaa", "plan:first", "First"),
            ("2222bbbb", "plan:second", "Second"),
        )
    )
    return NewProjectManifest(
        project=NewProjectConfig(
            projectName="Demo",
            packageName="Generated",
            fairyGuiVersion="6.1.4",
            publishTarget="unity",
        ),
        package=ManifestPackage(
            id="3333cccc",
            sourceDocumentRef="plan:ownership",
            name="Generated",
            relativePath="assets/Generated",
        ),
        components=components,
        resources=(),
    )


def test_manifest_rejects_shared_object_ownership() -> None:
    diagnostics = validate_new_project_manifest(manifest_with_object_in_two_components())

    assert "fgui.writer.manifest.object_owner_conflict" in {
        diagnostic.code for diagnostic in diagnostics
    }


def _config() -> NewProjectConfig:
    return NewProjectConfig(
        projectName="Demo",
        packageName="Generated",
        fairyGuiVersion="6.1.4",
        publishTarget="unity",
    )


def _manifest(components: tuple[ManifestComponent, ...]) -> NewProjectManifest:
    return NewProjectManifest(
        project=_config(),
        package=ManifestPackage(
            id="ffffffff",
            sourceDocumentRef="plan:validator",
            name="Generated",
            relativePath="assets/Generated",
        ),
        components=components,
        resources=(),
    )


def _object(
    index: int,
    *,
    parent: str | None = None,
    children: tuple[str, ...] = (),
    type_: str = "container",
    component_ref: str | None = None,
    resource_ref: str | None = None,
    consumed: tuple[str, ...] = (),
) -> ManifestObject:
    return ManifestObject(
        id=f"{0x20000000 + index:08x}",
        sourceNodeRef=f"plan:{index}",
        uirNodeRef=f"uir:{index}",
        parentObjectRef=parent,
        childObjectRefs=children,
        zIndex=0,
        type=type_,
        transform={"bounds": {"x": 0, "y": 0, "width": 1, "height": 1}},
        componentRef=component_ref,
        resourceRef=resource_ref,
        rasterConsumedNodeRefs=consumed,
    )


def _component(
    index: int,
    objects: tuple[ManifestObject, ...],
) -> ManifestComponent:
    component_id = f"{0x10000000 + index:08x}"
    return ManifestComponent(
        id=component_id,
        sourceComponentKind="definition",
        sourceComponentRef=f"definition:{index}",
        name=f"Component-{index}",
        relativePath=f"Component/Component-{index}-{component_id}.xml",
        size={"x": 0, "y": 0, "width": 1, "height": 1},
        objects=objects,
    )


def test_object_depth_is_measured_from_the_root_independent_of_id_order() -> None:
    count = MAX_MANIFEST_DEPTH + 1
    ids = [f"{0x20000000 + count - index:08x}" for index in range(count)]
    objects = tuple(
        ManifestObject(
            id=ids[index],
            sourceNodeRef=f"plan:deep:{index}",
            uirNodeRef=f"uir:deep:{index}",
            parentObjectRef=None if index == 0 else ids[index - 1],
            childObjectRefs=() if index + 1 == count else (ids[index + 1],),
            zIndex=0,
            type="container",
            transform={"bounds": {"x": 0, "y": 0, "width": 1, "height": 1}},
        )
        for index in range(count)
    )

    diagnostics = validate_new_project_manifest(_manifest((_component(0, objects),)))

    assert "fgui.writer.manifest.object_depth_exceeded" in {
        diagnostic.code for diagnostic in diagnostics
    }


def test_component_cycle_and_depth_are_iterative() -> None:
    first_id = "10000000"
    second_id = "10000001"
    first = _component(
        0,
        (_object(0, type_="componentReference", component_ref=second_id),),
    )
    second = _component(
        1,
        (_object(1, type_="componentReference", component_ref=first_id),),
    )
    cycle_codes = {item.code for item in validate_new_project_manifest(_manifest((first, second)))}
    assert "fgui.writer.manifest.component_cycle" in cycle_codes

    count = MAX_MANIFEST_COMPONENT_DEPTH + 1
    components: list[ManifestComponent] = []
    for index in reversed(range(count)):
        target = None if index + 1 == count else f"{0x10000000 + index + 1:08x}"
        object_ = _object(
            index,
            type_="container" if target is None else "componentReference",
            component_ref=target,
        )
        components.append(_component(index, (object_,)))
    depth_codes = {
        item.code for item in validate_new_project_manifest(_manifest(tuple(components)))
    }
    assert "fgui.writer.manifest.component_depth_exceeded" in depth_codes


def test_component_depth_limit_does_not_cap_independent_component_count() -> None:
    components = tuple(_component(index, (_object(index),)) for index in range(257))

    diagnostics = validate_new_project_manifest(_manifest(components))

    assert "fgui.writer.manifest.component_depth_exceeded" not in {
        diagnostic.code for diagnostic in diagnostics
    }


def test_paths_consumers_and_raster_descendants_fail_closed() -> None:
    first = _object(
        0,
        type_="rasterSubtree",
        resource_ref="30000000",
        consumed=("uir:shared-descendant",),
    )
    second = _object(
        1,
        type_="rasterSubtree",
        resource_ref="30000001",
        consumed=("uir:shared-descendant",),
    )
    component_a = _component(0, (first,))
    component_b = _component(1, (second,)).model_copy(
        update={"relative_path": component_a.relative_path.swapcase()}
    )
    manifest = _manifest((component_a, component_b)).model_copy(
        update={
            "resources": (
                ManifestResource(
                    id="30000000",
                    sourceResourceRef="resource:first",
                    name="First",
                    relativePath="Img/First-30000000.png",
                    mimeType="image/png",
                    contentSha256="a" * 64,
                    exportParametersSha256="c" * 64,
                    exportFormat="png",
                    consumerObjectRefs=(first.id,),
                ),
                ManifestResource(
                    id="30000001",
                    sourceResourceRef="resource:second",
                    name="Second",
                    relativePath="Img/Second-30000001.png",
                    mimeType="image/png",
                    contentSha256="b" * 64,
                    exportParametersSha256="d" * 64,
                    exportFormat="png",
                    consumerObjectRefs=("deadbeef",),
                ),
            )
        }
    )

    codes = {item.code for item in validate_new_project_manifest(manifest)}

    assert "fgui.writer.manifest.target_path_invalid" in codes
    assert "fgui.writer.manifest.resource_consumer_missing" in codes
    assert "fgui.writer.manifest.resource_consumer_mismatch" in codes
    assert "fgui.writer.manifest.raster_descendant_duplicate" in codes


def test_diagnostics_have_one_public_canonical_order() -> None:
    diagnostics = validate_new_project_manifest(manifest_with_object_in_two_components())
    keys = [
        (
            item.code,
            item.evidence,
            item.path or "",
            item.node_id or "",
            item.rule_id or "",
            item.rule_version or 0,
            item.message,
        )
        for item in diagnostics
    ]

    assert keys == sorted(keys)
    assert all(item.rule_id and item.rule_version == 1 for item in diagnostics)
    assert all(item.evidence and item.suggested_action for item in diagnostics)
    assert all(item.blocks_binding for item in diagnostics)
