from __future__ import annotations

from figma_to_fgui.fgui_new_project_models import (
    ManifestComponent,
    ManifestObject,
    ManifestPackage,
    ManifestResource,
    NewProjectConfig,
    NewProjectManifest,
)
from figma_to_fgui.fgui_new_project_review import (
    build_new_project_designer_review,
)
from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.service_contracts import NewProjectAdjustmentStrategy


def _manifest() -> NewProjectManifest:
    resource_id = "2222bbbb"
    object_ = ManifestObject(
        id="3333cccc",
        sourceNodeRef="plan:image",
        uirNodeRef="uir:image",
        zIndex=0,
        type="image",
        transform={"bounds": {"x": 0, "y": 0, "width": 1, "height": 1}},
        resourceRef=resource_id,
    )
    return NewProjectManifest(
        project=NewProjectConfig(
            projectName="Review",
            packageName="Generated",
            fairyGuiVersion="6.1.4",
            publishTarget="unity",
        ),
        package=ManifestPackage(
            id="1111aaaa",
            sourceDocumentRef="plan:review",
            name="Generated",
            relativePath="assets/Generated",
        ),
        components=(
            ManifestComponent(
                id="4444dddd",
                sourceComponentKind="root",
                sourceComponentRef="root:review",
                name="Review",
                relativePath="Panel/Review-4444dddd.xml",
                size={"x": 0, "y": 0, "width": 1, "height": 1},
                objects=(object_,),
            ),
        ),
        resources=(
            ManifestResource(
                id=resource_id,
                sourceResourceRef="resource:review",
                name="Review.png",
                relativePath="Img/Review-2222bbbb.png",
                mimeType="image/png",
                contentSha256="0" * 64,
                exportParametersSha256="1" * 64,
                exportFormat="png",
                width=1,
                height=1,
                consumerObjectRefs=(object_.id,),
            ),
        ),
    )


def test_projects_manifest_into_image_component_package_and_checks() -> None:
    manifest = _manifest()
    review = build_new_project_designer_review(
        manifest,
        None,
        (
            Diagnostic(
                code="fgui.visual.raster_fallback",
                severity=Severity.WARNING,
                message="This wording must not select an adjustment strategy.",
                node_id="node-1",
                suggested_action="review_raster_fallback",
            ),
        ),
        build_id="a" * 32,
        generation=1,
        source_preview_urls_by_resource={
            "resource:review": "/v1/figma/selections/" + "b" * 32 + "/previews/0"
        },
        source_node_ids={"node-1": "figma-node-1", "uir:image": "figma-image-1"},
    )

    assert review.build_id == "a" * 32
    assert review.generation == 1
    assert review.package_review.components_added >= 1
    assert review.package_review.resource_closure_valid is True
    assert all(item.evidence_kind == "source-image" for item in review.image_reviews)
    assert review.image_reviews[0].source_node_id == "figma-image-1"
    assert all(
        item.evidence_kind == "structured-summary" and item.rendered_preview_url is None
        for item in review.component_reviews
    )
    assert review.warning_ids == tuple(check.id for check in review.checks)
    assert review.checks[0].issue_kind == "raster-fallback"
    assert review.checks[0].source_node_id == "figma-node-1"
    assert review.checks[0].allowed_strategies == (
        NewProjectAdjustmentStrategy.PRESERVE_EDITABLE,
    )


def test_shared_image_resource_uses_a_stable_source_node_for_review() -> None:
    manifest = _manifest()
    original = manifest.components[0].objects[0]
    copy = original.model_copy(
        update={
            "id": "5555eeee",
            "source_node_ref": "plan:image-copy",
            "uir_node_ref": "uir:image-copy",
        }
    )
    component = manifest.components[0].model_copy(
        update={"objects": (original, copy)}
    )
    resource = manifest.resources[0].model_copy(
        update={"consumer_object_refs": (original.id, copy.id)}
    )

    review = build_new_project_designer_review(
        manifest.model_copy(
            update={"components": (component,), "resources": (resource,)}
        ),
        None,
        build_id="a" * 32,
        generation=1,
        source_node_ids={
            "uir:image": "figma-image-b",
            "uir:image-copy": "figma-image-a",
        },
    )

    assert review.image_reviews[0].source_node_id == "figma-image-a"


def test_actionable_policy_is_code_authored_and_unknown_diagnostics_are_not_actionable() -> None:
    review = build_new_project_designer_review(
        _manifest(),
        None,
        (
            Diagnostic(
                code="writer.unknown",
                severity=Severity.WARNING,
                message="definition rasterize preserve editable",
                node_id="node-1",
                suggested_action="rasterize-subtree",
            ),
        ),
        build_id="a" * 32,
        generation=1,
        source_node_ids={"uir:image": "figma-image-1"},
    )

    assert review.checks[0].issue_kind is None
    assert review.checks[0].actionable is False
    assert review.checks[0].allowed_strategies == ()


def test_hierarchy_validation_stays_in_manifest_id_domain_for_roots_and_definitions() -> None:
    manifest = _manifest()
    parent = ManifestObject(
        id="aaaa1111", sourceNodeRef="plan:parent", uirNodeRef="uir:parent", zIndex=0,
        type="container", transform={"bounds": {"x": 0, "y": 0, "width": 1, "height": 1}},
        childObjectRefs=("bbbb2222",),
    )
    child = ManifestObject(
        id="bbbb2222", sourceNodeRef="plan:child", uirNodeRef="uir:child", zIndex=0,
        type="container", transform={"bounds": {"x": 0, "y": 0, "width": 1, "height": 1}},
        parentObjectRef=parent.id,
    )
    components = tuple(
        ManifestComponent(
            id=f"cccc333{index}", sourceComponentKind=kind,
            sourceComponentRef=f"{kind}:review", name=f"Review{index}",
            relativePath=(
                f"Panel/Review{index}.xml"
                if kind == "root"
                else f"Component/Review{index}.xml"
            ),
            size={"x": 0, "y": 0, "width": 1, "height": 1}, objects=(parent, child),
        )
        for index, kind in enumerate(("root", "definition"))
    )
    review = build_new_project_designer_review(
        manifest.model_copy(update={"components": components, "resources": ()}), None,
        build_id="a" * 32, generation=1,
    )

    assert [item.hierarchy_valid for item in review.component_reviews] == [True, True]


def test_error_check_blocks_approval_and_rendered_evidence_requires_real_bytes() -> None:
    manifest = _manifest()
    review = build_new_project_designer_review(
        manifest,
        None,
        (
            Diagnostic(
                code="writer.error",
                severity=Severity.ERROR,
                message="Blocked.",
                blocks_binding=True,
            ),
        ),
        build_id="a" * 32,
        generation=1,
        rendered_component_previews={manifest.components[0].id: b"not-an-image"},
        source_node_ids={"uir:image": "figma-image-1"},
    )

    assert review.approvable is False
    assert review.component_reviews[0].evidence_kind == "structured-summary"
    assert review.component_reviews[0].rendered_preview_url is None
