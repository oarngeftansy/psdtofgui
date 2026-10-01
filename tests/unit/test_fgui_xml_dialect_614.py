from __future__ import annotations

import base64
import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import figma_to_fgui.fgui_xml_dialect_614 as dialect
from figma_to_fgui.fgui_asset_payloads import ValidatedAssetPayload
from figma_to_fgui.fgui_new_project_ids import (
    TargetIdAllocator,
    component_logical_key,
    component_path,
    object_logical_key,
    package_logical_key,
    resource_logical_key,
    resource_path,
)
from figma_to_fgui.fgui_new_project_models import (
    AssetPayload,
    ManifestComponent,
    ManifestObject,
    ManifestPackage,
    ManifestResource,
    NewProjectConfig,
    NewProjectManifest,
)
from figma_to_fgui.fgui_new_project_validate import (
    NewProjectManifestError,
    validate_new_project_manifest,
    validate_xml_files,
)
from figma_to_fgui.fgui_plan_models import ResourcePlan, TextPlan
from figma_to_fgui.fgui_xml_dialect_614 import parse_editor_fixture

FIXTURES = Path(__file__).parents[1] / "fixtures"
VALID_FIXTURE = FIXTURES / "fgui-editor-6.1.4/minimal"


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    shutil.copytree(VALID_FIXTURE, root)
    return root


def _marker(root: Path) -> Path:
    return root / "Minimal.fairy"


def _package(root: Path) -> Path:
    return root / "assets/Generated/package.xml"


def _component(root: Path) -> Path:
    return root / "assets/Generated/Panel/Root.xml"


def test_minimal_editor_fixture_is_recognized() -> None:
    fixture = parse_editor_fixture(VALID_FIXTURE)
    assert fixture.project_name == "Minimal"
    assert fixture.package_name == "Generated"
    assert fixture.component_names == ("Root",)
    assert fixture.component_sizes == ((320, 180),)


def test_single_leading_slash_resource_path_uses_package_virtual_root() -> None:
    fixture = parse_editor_fixture(VALID_FIXTURE)

    assert fixture.component_names == ("Root",)


def test_optional_component_xml_name_does_not_define_identity(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _component(root).write_text(
        "<component name='DisplayAlias' size='320,180'><displayList/></component>", "utf-8"
    )

    fixture = parse_editor_fixture(root)

    assert fixture.component_names == ("Root",)


def test_malformed_project_marker_is_rejected(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _marker(root).write_text("<projectDescription", "utf-8")

    with pytest.raises(ValueError, match="project marker"):
        parse_editor_fixture(root)


@pytest.mark.parametrize(
    "marker",
    [
        "<wrong id='88496b97754345efb9173265c58ee99b' type='Unity' version='5.0'/>",
        "<projectDescription id='' type='Unity' version='5.0'/>",
        "<projectDescription id='../secret' type='Unity' version='5.0'/>",
        "<projectDescription id='88496b97754345efb9173265c58ee99b' type='Web' version='5.0'/>",
        "<projectDescription id='88496b97754345efb9173265c58ee99b' type='Unity' version='6.1.4'/>",
    ],
    ids=["root", "empty-id", "invalid-id", "type", "version"],
)
def test_invalid_project_marker_is_rejected(tmp_path: Path, marker: str) -> None:
    root = _fixture(tmp_path)
    _marker(root).write_text(marker, "utf-8")

    with pytest.raises(ValueError, match="project marker"):
        parse_editor_fixture(root)


@pytest.mark.parametrize(
    "package",
    [
        "<packageDescription",
        "<wrong id='mrz8gz9s'><resources/><publish/></wrong>",
        "<packageDescription id=''><resources/><publish/></packageDescription>",
        "<packageDescription id='bad/id'><resources/><publish/></packageDescription>",
        "<packageDescription id='mrz8gz9s'><publish/></packageDescription>",
        "<packageDescription id='mrz8gz9s'><resources/></packageDescription>",
        (
            "<packageDescription id='mrz8gz9s'><resources>"
            "<component id='frrzw' name='Root.xml' path='Panel/'/>"
            "<image id='frrzw' name='image.png'/>"
            "</resources><publish/></packageDescription>"
        ),
        (
            "<packageDescription id='mrz8gz9s'><resources>"
            "<component id='' name='Root.xml' path='components/'/>"
            "</resources><publish/></packageDescription>"
        ),
        (
            "<packageDescription id='mrz8gz9s'><resources>"
            "<component id='bad/id' name='Root.xml' path='components/'/>"
            "</resources><publish/></packageDescription>"
        ),
    ],
    ids=[
        "malformed",
        "root",
        "empty-id",
        "invalid-id",
        "resources",
        "publish",
        "duplicate-resource-id",
        "empty-resource-id",
        "invalid-resource-id",
    ],
)
def test_invalid_package_structure_is_rejected(tmp_path: Path, package: str) -> None:
    root = _fixture(tmp_path)
    _package(root).write_text(package, "utf-8")

    with pytest.raises(ValueError, match="package"):
        parse_editor_fixture(root)


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("../../../", "outside.xml"),
        ("//components/", "Root.xml"),
        ("/../", "Root.xml"),
        ("/components//nested/", "Root.xml"),
        ("C:/components/", "Root.xml"),
        ("/C:/components/", "Root.xml"),
        ("\\\\server\\share\\", "Root.xml"),
        ("components/", "nested/Root.xml"),
        ("components/", r"nested\Root.xml"),
        ("components/", "Root\u007f.xml"),
        ("components/Root\u007f/", "Root.xml"),
        ("components/", ""),
    ],
    ids=[
        "traversal",
        "double-leading-slash",
        "virtual-root-traversal",
        "embedded-empty-segment",
        "drive",
        "virtual-root-drive",
        "unc",
        "name-posix-dir",
        "name-win-dir",
        "name-control",
        "path-control",
        "empty-name",
    ],
)
def test_unsafe_resource_path_or_name_is_rejected(tmp_path: Path, path: str, name: str) -> None:
    root = _fixture(tmp_path)
    outside = tmp_path / "outside.xml"
    outside.write_text("<component name='Outside' size='1,1'><displayList/></component>", "utf-8")
    _package(root).write_text(
        "<packageDescription id='mrz8gz9s'><resources>"
        f"<component id='frrzw' name='{name}' path='{path}'/>"
        "</resources><publish/></packageDescription>",
        "utf-8",
    )

    with pytest.raises(ValueError, match="resource"):
        parse_editor_fixture(root)

    assert outside.read_text("utf-8").startswith("<component name='Outside'")


def test_path_escape_is_rejected_before_fixture_external_xml_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture(tmp_path)
    outside = tmp_path / "outside.xml"
    outside.write_text("<component name='Outside' size='1,1'><displayList/></component>", "utf-8")
    _package(root).write_text(
        "<packageDescription id='mrz8gz9s'><resources>"
        "<component id='frrzw' name='outside.xml' path='../../../'/>"
        "</resources><publish/></packageDescription>",
        "utf-8",
    )
    parse_xml = dialect.etree.parse

    def guarded_parse(path: str, parser: object) -> object:
        if Path(path) == outside:
            pytest.fail("parser read XML outside the fixture")
        return parse_xml(path, parser)

    monkeypatch.setattr(dialect.etree, "parse", guarded_parse)

    with pytest.raises(ValueError, match="unsafe resource path"):
        parse_editor_fixture(root)


@pytest.mark.parametrize(
    "component",
    [
        "<component",
        "<wrong name='Root' size='320,180'><displayList/></wrong>",
        "<component name='' size='320,180'><displayList/></component>",
        "<component name='../Root' size='320,180'><displayList/></component>",
        "<component name='Root&#x7f;' size='320,180'><displayList/></component>",
        "<component name='Root' size='320'><displayList/></component>",
        "<component name='Root' size='0,180'><displayList/></component>",
        "<component name='Root' size='320,-1'><displayList/></component>",
        "<component name='Root' size='320,180'/>",
    ],
    ids=[
        "malformed",
        "root",
        "empty-name",
        "unsafe-name",
        "control-name",
        "size-arity",
        "zero-size",
        "negative-size",
        "display-list",
    ],
)
def test_invalid_component_structure_is_rejected(tmp_path: Path, component: str) -> None:
    root = _fixture(tmp_path)
    _component(root).write_text(component, "utf-8")

    with pytest.raises(ValueError, match="component"):
        parse_editor_fixture(root)


@pytest.mark.parametrize(
    "publish",
    [
        "<publish/>",
        '<publish name="" path="../Assets/Art/ui/generated" packageCount="2"/>',
    ],
    ids=["empty", "observed-attributes"],
)
def test_observed_empty_publish_shapes_are_accepted(tmp_path: Path, publish: str) -> None:
    root = _fixture(tmp_path)
    _package(root).write_text(
        "<packageDescription id='mrz8gz9s'><resources>"
            "<component id='frrzw' name='Root.xml' path='/Panel/'/>"
        f"</resources>{publish}</packageDescription>",
        "utf-8",
    )

    assert parse_editor_fixture(root).component_names == ("Root",)


@pytest.mark.parametrize(
    "publish",
    ["<publish><unexpected/></publish>", "<publish>unexpected text</publish>"],
    ids=["child", "text"],
)
def test_publish_rejects_unobserved_nonempty_structure(tmp_path: Path, publish: str) -> None:
    root = _fixture(tmp_path)
    _package(root).write_text(
        "<packageDescription id='mrz8gz9s'><resources>"
        "<component id='frrzw' name='Root.xml' path='/components/'/>"
        f"</resources>{publish}</packageDescription>",
        "utf-8",
    )

    with pytest.raises(ValueError, match="package publish"):
        parse_editor_fixture(root)


def test_component_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    outside = tmp_path / "outside.xml"
    outside.write_text("<component name='Root' size='320,180'><displayList/></component>", "utf-8")
    target = _component(root)
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Windows user cannot create file symlinks")

    with pytest.raises(ValueError, match="symlink or reparse"):
        parse_editor_fixture(root)


def test_component_reparse_point_is_rejected_without_symlink_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture(tmp_path)
    target = _component(root)
    original_lstat = Path.lstat
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: (
            SimpleNamespace(st_file_attributes=0x400) if path == target else original_lstat(path)
        ),
    )

    with pytest.raises(ValueError, match="symlink or reparse"):
        parse_editor_fixture(root)


def test_writer_entry_points_are_exposed() -> None:
    assert callable(dialect.serialize_project_marker)
    assert callable(dialect.serialize_package_xml)
    assert callable(dialect.serialize_component_xml)
    assert callable(dialect.serialize_project_files)


GOLDEN_ROOT = Path(__file__).parents[1] / "golden/expected/minimal-new-project"
ASSET_BYTES = (FIXTURES / "fgui-new-project/resources/one-pixel.png").read_bytes()
ASSET_SHA256 = hashlib.sha256(ASSET_BYTES).hexdigest()
EXPORT_SHA256 = "e" * 64
SERIALIZATION_FIXTURES = (
    "container",
    "text",
    "rich-text",
    "image",
    "loader",
    "component-reference",
    "raster-subtree",
    "nine-slice",
    "rectangle-clip",
    "rounded-clip",
    "image-mask",
)


def _target_id(kind: str, logical_key: str) -> str:
    return TargetIdAllocator().allocate(kind, logical_key)


def _object_id(source: tuple[str, str], source_node_ref: str) -> str:
    return _target_id("object", object_logical_key(source, source_node_ref))


def _component_id(source: tuple[str, str]) -> str:
    return _target_id("component", component_logical_key(source))


def _resource_details(
    fixture: str, consumer_id: str, *, nine_slice: bool = False
) -> tuple[ManifestResource, ValidatedAssetPayload]:
    source_resource_ref = f"resource:{fixture}"
    resource_id = _target_id(
        "resource",
        resource_logical_key(source_resource_ref, ASSET_SHA256, EXPORT_SHA256),
    )
    name = f"asset-{fixture}"
    nine_slice_value = {"x": 0, "y": 0, "width": 1, "height": 1} if nine_slice else None
    manifest_resource = ManifestResource(
        id=resource_id,
        sourceResourceRef=source_resource_ref,
        name=name,
        relativePath=resource_path(name, resource_id).as_posix(),
        mimeType="image/png",
        contentSha256=ASSET_SHA256,
        exportParametersSha256=EXPORT_SHA256,
        exportFormat="png",
        width=1,
        height=1,
        nineSlice=nine_slice_value,
        consumerObjectRefs=(consumer_id,),
    )
    plan_resource = ResourcePlan(
        id=source_resource_ref,
        sourceAssetRef=f"asset:{fixture}",
        logicalAssetId=f"logical:{fixture}",
        contentSha256=ASSET_SHA256,
        exportParametersSha256=EXPORT_SHA256,
        mimeType="image/png",
        exportFormat="png",
        width=1,
        height=1,
        nineSlice=nine_slice_value,
        consumers=(f"plan:{fixture}",),
    )
    payload = ValidatedAssetPayload(
        resource=plan_resource,
        payload=AssetPayload(
            resourceId=source_resource_ref,
            declaredMimeType="image/png",
            content=ASSET_BYTES,
        ),
    )
    return manifest_resource, payload


def _text_plan(*, rich: bool = False) -> TextPlan:
    if rich:
        return TextPlan(
            content="Hello & <world>\n",
            fontSize=14.5,
            color="#112233",
            horizontalAlign="center",
            verticalAlign="middle",
            runs=(
                {"content": "Hello & ", "color": "#112233"},
                {"content": "<world>\n", "color": "#445566"},
            ),
        )
    return TextPlan(
        content='A & <B> "quoted"\n',
        fontSize=12.5,
        color="#112233",
        strokeColor="#445566",
        strokeSize=1.25,
        horizontalAlign="center",
        verticalAlign="middle",
    )


def _manifest_fixture(
    fixture: str,
) -> tuple[NewProjectManifest, tuple[ValidatedAssetPayload, ...]]:
    document_ref = f"plan:golden:{fixture}"
    root_source = ("root", f"plan:root:{fixture}")
    package_id = _target_id("package", package_logical_key(document_ref, "Generated"))
    root_component_id = _component_id(root_source)
    root_id = _object_id(root_source, f"plan:root:{fixture}")
    child_source_ref = f"plan:{fixture}"
    child_id = _object_id(root_source, child_source_ref)
    children: list[ManifestObject] = []
    resources: list[ManifestResource] = []
    payloads: list[ValidatedAssetPayload] = []
    definitions: list[ManifestComponent] = []

    root_update: dict[str, object] = {}
    if fixture == "container":
        nested_id = child_id
        leaf_id = _object_id(root_source, "plan:container-leaf")
        children.extend(
            (
                ManifestObject(
                    id=nested_id,
                    sourceNodeRef=child_source_ref,
                    uirNodeRef="uir:container",
                    parentObjectRef=root_id,
                    childObjectRefs=(leaf_id,),
                    zIndex=0,
                    type="container",
                    transform={"bounds": {"x": 7.5, "y": 9, "width": 20, "height": 30}},
                ),
                ManifestObject(
                    id=leaf_id,
                    sourceNodeRef="plan:container-leaf",
                    uirNodeRef="uir:container-leaf",
                    parentObjectRef=nested_id,
                    zIndex=0,
                    type="text",
                    transform={"bounds": {"x": 1, "y": 2, "width": 18, "height": 12}},
                    text=_text_plan(),
                ),
            )
        )
    elif fixture in {"text", "rich-text"}:
        children.append(
            ManifestObject(
                id=child_id,
                sourceNodeRef=child_source_ref,
                uirNodeRef=f"uir:{fixture}",
                parentObjectRef=root_id,
                zIndex=0,
                type="richText" if fixture == "rich-text" else "text",
                transform={
                    "bounds": {"x": 7.5, "y": -0.0, "width": 20, "height": 30},
                    "rotation": 12.5,
                    "opacity": 0.5,
                    "visible": False,
                },
                text=_text_plan(rich=fixture == "rich-text"),
            )
        )
    elif fixture == "component-reference":
        definition_source = ("definition", "definition:card")
        definition_id = _component_id(definition_source)
        definition_root_id = _object_id(definition_source, "plan:definition-root")
        definitions.append(
            ManifestComponent(
                id=definition_id,
                sourceComponentKind="definition",
                sourceComponentRef="definition:card",
                name="Card",
                relativePath=component_path("Card", definition_id, "definition").as_posix(),
                size={"x": 0, "y": 0, "width": 64, "height": 32},
                objects=(
                    ManifestObject(
                        id=definition_root_id,
                        sourceNodeRef="plan:definition-root",
                        uirNodeRef="uir:definition-root",
                        zIndex=0,
                        type="container",
                        transform={"bounds": {"x": 0, "y": 0, "width": 64, "height": 32}},
                    ),
                ),
            )
        )
        children.append(
            ManifestObject(
                id=child_id,
                sourceNodeRef=child_source_ref,
                uirNodeRef="uir:component-reference",
                parentObjectRef=root_id,
                zIndex=0,
                type="componentReference",
                transform={"bounds": {"x": 7.5, "y": 9, "width": 64, "height": 32}},
                componentRef=definition_id,
            )
        )
    elif fixture in {"rectangle-clip", "rounded-clip"}:
        content_id = child_id
        if fixture == "rectangle-clip":
            root_update = {
                "childObjectRefs": (content_id,),
                "maskMode": "nativeClip",
                "maskKind": "rectangle",
                "maskObjectRef": root_id,
                "maskContentObjectRefs": (content_id,),
            }
            content_z_index = 0
        else:
            mask_id = child_id
            content_id = _object_id(root_source, "plan:rounded-content")
            root_update = {
                "childObjectRefs": (mask_id, content_id),
                "maskMode": "nativeClip",
                "maskKind": "roundedRectangle",
                "maskObjectRef": mask_id,
                "maskContentObjectRefs": (content_id,),
                "maskCornerRadii": (2.5, 3.5, 4.5, 5.5),
            }
            children.append(
                ManifestObject(
                    id=mask_id,
                    sourceNodeRef=child_source_ref,
                    uirNodeRef="uir:rounded-mask",
                    parentObjectRef=root_id,
                    zIndex=0,
                    type="container",
                    transform={"bounds": {"x": 0, "y": 0, "width": 100, "height": 80}},
                )
            )
            content_z_index = 1
        children.append(
            ManifestObject(
                id=content_id,
                sourceNodeRef=(
                    child_source_ref if fixture == "rectangle-clip" else "plan:rounded-content"
                ),
                uirNodeRef=f"uir:{fixture}-content",
                parentObjectRef=root_id,
                zIndex=content_z_index,
                type="text",
                transform={"bounds": {"x": 4, "y": 5, "width": 20, "height": 12}},
                text=_text_plan(),
            )
        )
    elif fixture == "image-mask":
        mask_id = child_id
        content_id = _object_id(root_source, "plan:image-mask-content")
        resource, payload = _resource_details(fixture, mask_id)
        resources.append(resource)
        payloads.append(payload)
        root_update = {
            "childObjectRefs": (mask_id, content_id),
            "maskMode": "nativeMask",
            "maskKind": "image",
            "maskObjectRef": mask_id,
            "maskContentObjectRefs": (content_id,),
        }
        children.extend(
            (
                ManifestObject(
                    id=mask_id,
                    sourceNodeRef=child_source_ref,
                    uirNodeRef="uir:image-mask-source",
                    parentObjectRef=root_id,
                    zIndex=0,
                    type="image",
                    transform={"bounds": {"x": 0, "y": 0, "width": 100, "height": 80}},
                    resourceRef=resource.id,
                ),
                ManifestObject(
                    id=content_id,
                    sourceNodeRef="plan:image-mask-content",
                    uirNodeRef="uir:image-mask-content",
                    parentObjectRef=root_id,
                    zIndex=1,
                    type="text",
                    transform={"bounds": {"x": 4, "y": 5, "width": 20, "height": 12}},
                    text=_text_plan(),
                ),
            )
        )
    else:
        resource, payload = _resource_details(fixture, child_id, nine_slice=fixture == "nine-slice")
        resources.append(resource)
        payloads.append(payload)
        object_type = {
            "image": "image",
            "loader": "loader",
            "raster-subtree": "rasterSubtree",
            "nine-slice": "image",
        }[fixture]
        extra: dict[str, object] = {}
        if fixture == "raster-subtree":
            extra = {
                "maskMode": "rasterSubtree",
                "maskKind": "boolean",
                "rasterConsumedNodeRefs": ("uir:consumed-a", "uir:consumed-b"),
            }
        children.append(
            ManifestObject(
                id=child_id,
                sourceNodeRef=child_source_ref,
                uirNodeRef=f"uir:{fixture}",
                parentObjectRef=root_id,
                zIndex=0,
                type=object_type,
                transform={"bounds": {"x": 7.5, "y": 9, "width": 20, "height": 30}},
                resourceRef=resource.id,
                **extra,
            )
        )

    root_fields: dict[str, object] = {
        "childObjectRefs": tuple(item.id for item in children if item.parent_object_ref == root_id),
        **root_update,
    }
    root = ManifestObject(
        id=root_id,
        sourceNodeRef=f"plan:root:{fixture}",
        uirNodeRef=f"uir:root:{fixture}",
        zIndex=0,
        type="container",
        transform={"bounds": {"x": 0, "y": 0, "width": 320, "height": 180}},
        **root_fields,
    )
    component = ManifestComponent(
        id=root_component_id,
        sourceComponentKind="root",
        sourceComponentRef=f"plan:root:{fixture}",
        name="Root",
        relativePath=component_path("Root", root_component_id, "root").as_posix(),
        size={"x": 0, "y": 0, "width": 320, "height": 180},
        objects=(root, *children),
    )
    manifest = NewProjectManifest(
        project=NewProjectConfig(
            projectName=f"Golden-{fixture}",
            packageName="Generated",
            fairyGuiVersion="6.1.4",
            publishTarget="unity",
        ),
        package=ManifestPackage(
            id=package_id,
            sourceDocumentRef=document_ref,
            name="Generated",
            relativePath="assets/Generated",
        ),
        components=(*definitions, component),
        resources=tuple(resources),
    )
    return manifest, tuple(payloads)


def _load_golden_files(fixture: str) -> dict[str, bytes]:
    root = GOLDEN_ROOT / fixture
    files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    encoded_asset = (GOLDEN_ROOT / "_asset.b64").read_text("ascii").strip()
    manifest, _payloads = _manifest_fixture(fixture)
    for resource in manifest.resources:
        files[f"{manifest.package.relative_path}/{resource.relative_path}"] = base64.b64decode(
            encoded_asset, validate=True
        )
    return files


@pytest.mark.parametrize("fixture", SERIALIZATION_FIXTURES)
def test_dialect_serialization_matches_approved_golden(fixture: str) -> None:
    manifest, payloads = _manifest_fixture(fixture)

    files = dialect.serialize_project_files(manifest, payloads)

    assert files == _load_golden_files(fixture)
    assert validate_xml_files(files) == ()


def test_standard_package_paths_are_registered_and_referenced_consistently() -> None:
    component_manifest, component_payloads = _manifest_fixture("component-reference")
    image_manifest, image_payloads = _manifest_fixture("image")

    package = dialect.etree.fromstring(dialect.serialize_package_xml(component_manifest))
    assert package.xpath("./resources/component[@path='/Panel/']")
    assert package.xpath("./resources/component[@path='/Component/']")

    image_package = dialect.etree.fromstring(dialect.serialize_package_xml(image_manifest))
    assert image_package.xpath("./resources/image[@path='/Img/']")

    component_files = dialect.serialize_project_files(component_manifest, component_payloads)
    root_component = next(
        content for path, content in component_files.items() if "/Panel/" in path
    )
    assert b'fileName="Component/' in root_component
    assert b'fileName="components/' not in root_component

    image_files = dialect.serialize_project_files(image_manifest, image_payloads)
    image_component = next(content for path, content in image_files.items() if "/Panel/" in path)
    assert b'fileName="Img/' in image_component
    assert b'fileName="resources/' not in image_component


def test_writer_rejects_unknown_node_types_instead_of_omitting_them() -> None:
    manifest, payloads = _manifest_fixture("text")
    component = manifest.components[-1]
    bad_object = component.objects[-1].model_copy(update={"type": "futureNode"})
    bad_component = component.model_copy(update={"objects": (component.objects[0], bad_object)})
    bad_manifest = manifest.model_copy(update={"components": (bad_component,)})

    with pytest.raises(dialect.UnsupportedDialectFeature, match="futureNode"):
        dialect.serialize_project_files(bad_manifest, payloads)


def test_writer_rejects_manually_constructed_invalid_rich_text_plans() -> None:
    manifest, payloads = _manifest_fixture("rich-text")
    component = manifest.components[-1]
    rich_object = component.objects[-1]
    assert rich_object.text is not None
    text = rich_object.text
    first, second = text.runs
    invalid_texts = (
        text.model_copy(update={"content": "does not match runs"}),
        text.model_copy(
            update={"runs": (first, second.model_copy(update={"font_size": 18}))}
        ),
        text.model_copy(
            update={
                "runs": (
                    first,
                    second.model_copy(update={"font_candidates": ("Arial",)}),
                )
            }
        ),
        text.model_copy(
            update={
                "runs": (
                    first,
                    second.model_copy(update={"stroke_color": "#abcdef"}),
                )
            }
        ),
        text.model_copy(
            update={"runs": (first, second.model_copy(update={"color": "red"}))}
        ),
        text.model_copy(
            update={
                "content": "[world]",
                "runs": (
                    first.model_copy(update={"content": "["}),
                    second.model_copy(update={"content": "world]"}),
                ),
            }
        ),
    )

    for invalid_text in invalid_texts:
        bad_object = rich_object.model_copy(update={"text": invalid_text})
        bad_component = component.model_copy(
            update={"objects": (component.objects[0], bad_object)}
        )
        bad_manifest = manifest.model_copy(update={"components": (bad_component,)})

        with pytest.raises(dialect.UnsupportedDialectFeature):
            dialect.serialize_project_files(bad_manifest, payloads)


def test_xml_gate_rejects_doctype_unknown_tags_and_undeclared_files() -> None:
    manifest, payloads = _manifest_fixture("text")
    files = dialect.serialize_project_files(manifest, payloads)
    component_path_value = next(
        path for path in files if path.endswith(".xml") and "/Panel/" in path
    )

    poisoned = dict(files)
    poisoned[component_path_value] = poisoned[component_path_value].replace(
        b"<displayList>",
        b"<!DOCTYPE component [<!ENTITY xxe SYSTEM 'file:///secret'>]><displayList>",
    )
    poisoned["undeclared.bin"] = b"unexpected"

    codes = {item.code for item in validate_xml_files(poisoned)}
    assert "fgui.writer.xml.doctype_forbidden" in codes
    assert "fgui.writer.xml.file_set_incoherent" in codes


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        (
            lambda value: value.replace(
                b"<displayList>", b"<futureTag>\n  <displayList>", 1
            ).replace(b"</component>", b"</futureTag>\n</component>", 1),
            "fgui.writer.xml.component_invalid",
        ),
        (
            lambda value: value.replace(b"<displayList>", b'<displayList future="true">', 1),
            "fgui.writer.xml.component_invalid",
        ),
        (
            lambda value: value.replace(b"<displayList>", b"<displayList>forbidden", 1),
            "fgui.writer.xml.text_invalid",
        ),
        (
            lambda value: value.replace(b"</displayList>", b"</displayList>forbidden", 1),
            "fgui.writer.xml.tail_invalid",
        ),
        (
            lambda value: value.replace(b"\n  </displayList>", b"\n   </displayList>", 1),
            "fgui.writer.xml.tail_invalid",
        ),
        (
            lambda value: value.replace(b"<component ", b"<?writer forbidden?>\n<component ", 1),
            "fgui.writer.xml.processing_instruction_forbidden",
        ),
        (
            lambda value: value.replace(b"<component ", b"<!--forbidden-->\n<component ", 1),
            "fgui.writer.xml.comment_forbidden",
        ),
    ),
    ids=(
        "unknown-tag",
        "unknown-attribute",
        "text",
        "tail",
        "noncanonical-tail-whitespace",
        "processing-instruction",
        "comment",
    ),
)
def test_xml_gate_rejects_closed_schema_and_lexical_extensions(
    mutation: Callable[[bytes], bytes], expected_code: str
) -> None:
    manifest, payloads = _manifest_fixture("text")
    files = dialect.serialize_project_files(manifest, payloads)
    component_path_value = next(
        path for path in files if path.endswith(".xml") and "/Panel/" in path
    )
    broken = dict(files)
    broken[component_path_value] = mutation(broken[component_path_value])

    assert expected_code in {item.code for item in validate_xml_files(broken)}


@pytest.mark.parametrize(
    ("needle", "replacement", "expected_code"),
    (
        (
            b"<projectDescription ",
            b'<projectDescription future="true" ',
            "fgui.writer.xml.project_invalid",
        ),
        (
            b"<packageDescription ",
            b'<packageDescription future="true" ',
            "fgui.writer.xml.package_invalid",
        ),
        (b"<resources>", b'<resources future="true">', "fgui.writer.xml.package_invalid"),
        (
            b"<component size=",
            b'<component future="true" size=',
            "fgui.writer.xml.component_invalid",
        ),
        (b"<text id=", b'<text future="true" id=', "fgui.writer.xml.object_invalid"),
    ),
    ids=("project-root", "package-root", "resources", "component-root", "display-object"),
)
def test_xml_gate_rejects_unknown_attributes_on_every_schema_layer(
    needle: bytes, replacement: bytes, expected_code: str
) -> None:
    manifest, payloads = _manifest_fixture("text")
    files = dialect.serialize_project_files(manifest, payloads)
    broken = dict(files)
    target_path = next(path for path, value in broken.items() if needle in value)
    broken[target_path] = broken[target_path].replace(needle, replacement, 1)

    assert expected_code in {item.code for item in validate_xml_files(broken)}


@pytest.mark.parametrize(
    "hostile_path",
    (
        "CON.fairy",
        "assets/NUL/package.xml",
        "Golden-text. ",
        "assets/Generated./package.xml",
        "COM¹.fairy",
    ),
)
def test_xml_gate_rejects_windows_hostile_standalone_file_paths(hostile_path: str) -> None:
    manifest, payloads = _manifest_fixture("text")
    files = dialect.serialize_project_files(manifest, payloads)
    marker_path = next(path for path in files if path.endswith(".fairy"))
    broken = dict(files)
    broken[hostile_path] = broken.pop(marker_path)

    assert "fgui.writer.xml.file_set_incoherent" in {
        item.code for item in validate_xml_files(broken)
    }


@pytest.mark.parametrize("fixture", ("rectangle-clip", "rounded-clip", "image-mask"))
def test_root_native_mask_scope_must_cover_every_affected_display_object(
    fixture: str,
) -> None:
    manifest, payloads = _manifest_fixture(fixture)
    component = manifest.components[-1]
    root = component.objects[0]
    second_id = _object_id(("root", root.source_node_ref), f"plan:{fixture}:second-content")
    second = ManifestObject(
        id=second_id,
        sourceNodeRef=f"plan:{fixture}:second-content",
        uirNodeRef=f"uir:{fixture}:second-content",
        parentObjectRef=root.id,
        zIndex=len(root.child_object_refs),
        type="text",
        transform={"bounds": {"x": 30, "y": 12, "width": 20, "height": 12}},
        text=_text_plan(),
    )
    full_scope = (*root.mask_content_object_refs, second_id)
    full_root = root.model_copy(
        update={
            "child_object_refs": (*root.child_object_refs, second_id),
            "mask_content_object_refs": full_scope,
        }
    )
    full_component = component.model_copy(
        update={"objects": (full_root, *component.objects[1:], second)}
    )
    full_manifest = manifest.model_copy(
        update={"components": (*manifest.components[:-1], full_component)}
    )
    partial_root = full_root.model_copy(update={"mask_content_object_refs": full_scope[:-1]})
    partial_component = full_component.model_copy(
        update={"objects": (partial_root, *full_component.objects[1:])}
    )
    partial_manifest = full_manifest.model_copy(
        update={"components": (*full_manifest.components[:-1], partial_component)}
    )

    assert dialect.serialize_project_files(full_manifest, payloads)
    with pytest.raises(NewProjectManifestError) as error:
        dialect.serialize_project_files(partial_manifest, payloads)
    assert {item.code for item in error.value.diagnostics} >= {
        "fgui.writer.manifest.mask_scope_incoherent"
    }


def test_root_native_mask_scope_does_not_repeat_nested_descendants() -> None:
    manifest, _payloads = _manifest_fixture("rectangle-clip")
    component = manifest.components[-1]
    root = component.objects[0]
    content_id = root.mask_content_object_refs[0]
    content = next(item for item in component.objects if item.id == content_id)
    nested_id = _object_id(("root", root.source_node_ref), "plan:nested-content")
    nested = ManifestObject(
        id=nested_id,
        sourceNodeRef="plan:nested-content",
        uirNodeRef="uir:nested-content",
        parentObjectRef=content.id,
        zIndex=0,
        type="text",
        transform={"bounds": {"x": 2, "y": 2, "width": 8, "height": 8}},
        text=_text_plan(),
    )
    updated_content = content.model_copy(
        update={"child_object_refs": (*content.child_object_refs, nested.id)}
    )
    objects = tuple(
        updated_content if item.id == content.id else item for item in component.objects
    ) + (nested,)
    nested_component = component.model_copy(update={"objects": objects})
    nested_manifest = manifest.model_copy(
        update={"components": (*manifest.components[:-1], nested_component)}
    )

    assert validate_new_project_manifest(nested_manifest) == ()


def test_xml_gate_rejects_broken_component_reference_and_noncanonical_decimal() -> None:
    manifest, payloads = _manifest_fixture("component-reference")
    files = dialect.serialize_project_files(manifest, payloads)
    root_path = next(
        path for path in files if path.endswith(f"Root-{manifest.components[-1].id}.xml")
    )
    broken = dict(files)
    broken[root_path] = (
        broken[root_path]
        .replace(
            b'pkg="' + manifest.package.id.encode() + b'"',
            b'pkg="ffffffff"',
        )
        .replace(b'xy="8,9"', b'xy="7.5,9"')
    )

    codes = {item.code for item in validate_xml_files(broken)}
    assert "fgui.writer.xml.component_reference_invalid" in codes
    assert "fgui.writer.xml.decimal_invalid" in codes


def test_rotation_is_serialized_as_editor_614_int32() -> None:
    manifest, payloads = _manifest_fixture("text")
    files = dialect.serialize_project_files(manifest, payloads)
    component_xml = next(
        content
        for path, content in files.items()
        if path.endswith(".xml") and "/Panel/" in path
    )

    assert b'rotation="13"' in component_xml
    assert b'rotation="12.5"' not in component_xml
    assert b'xy="8,0"' in component_xml
    assert b'xy="7.5,0"' not in component_xml

    broken = dict(files)
    component_path = next(
        path for path in files if path.endswith(".xml") and "/Panel/" in path
    )
    broken[component_path] = component_xml.replace(b'xy="8,0"', b'xy="7.5,0"')
    assert "fgui.writer.xml.decimal_invalid" in {
        item.code for item in validate_xml_files(broken)
    }


def test_text_rgba_colors_are_serialized_in_editor_614_argb_order() -> None:
    manifest, payloads = _manifest_fixture("text")
    component = manifest.components[-1]
    text_object = component.objects[-1]
    assert text_object.text is not None
    updated_text = text_object.text.model_copy(
        update={"color": "#61371fff", "stroke_color": "#10203080"}
    )
    updated_object = text_object.model_copy(update={"text": updated_text})
    updated_component = component.model_copy(
        update={"objects": (*component.objects[:-1], updated_object)}
    )
    updated_manifest = manifest.model_copy(
        update={"components": (*manifest.components[:-1], updated_component)}
    )

    files = dialect.serialize_project_files(updated_manifest, payloads)
    component_xml = next(
        content
        for path, content in files.items()
        if path.endswith(".xml") and "/Panel/" in path
    )

    assert b'color="#ff61371f"' in component_xml
    assert b'strokeColor="#80102030"' in component_xml
    assert b'color="#61371fff"' not in component_xml


def test_rich_text_run_rgba_color_is_serialized_in_editor_614_argb_order() -> None:
    manifest, payloads = _manifest_fixture("rich-text")
    component = manifest.components[-1]
    text_object = component.objects[-1]
    assert text_object.text is not None
    runs = text_object.text.runs
    updated_text = text_object.text.model_copy(
        update={
            "color": "#61371fff",
            "runs": (
                runs[0].model_copy(update={"color": "#61371fff"}),
                runs[1].model_copy(update={"color": "#26931fff"}),
            ),
        }
    )
    updated_object = text_object.model_copy(update={"text": updated_text})
    updated_component = component.model_copy(
        update={"objects": (*component.objects[:-1], updated_object)}
    )
    updated_manifest = manifest.model_copy(
        update={"components": (*manifest.components[:-1], updated_component)}
    )

    files = dialect.serialize_project_files(updated_manifest, payloads)
    component_xml = next(
        content
        for path, content in files.items()
        if path.endswith(".xml") and "/Panel/" in path
    )

    assert b'color="#ff61371f"' in component_xml
    assert b"[color=#ff26931f]" in component_xml


def test_fgui_xml_preserves_canonical_figma_sibling_order() -> None:
    manifest, payloads = _manifest_fixture("container")
    component = manifest.components[-1]
    root, group, front_child = component.objects
    back_child_id = _object_id(("root", root.source_node_ref), "plan:back-child")
    back_child = front_child.model_copy(
        update={
            "id": back_child_id,
            "name": "Back child",
            "source_node_ref": "plan:back-child",
            "uir_node_ref": "uir:back-child",
            "z_index": 1,
        }
    )
    updated_group = group.model_copy(
        update={"child_object_refs": (front_child.id, back_child.id)}
    )
    updated_component = component.model_copy(
        update={"objects": (root, updated_group, front_child, back_child)}
    )
    updated_manifest = manifest.model_copy(
        update={"components": (*manifest.components[:-1], updated_component)}
    )

    files = dialect.serialize_project_files(updated_manifest, payloads)
    component_xml = next(
        content
        for path, content in files.items()
        if path.endswith(".xml") and "/Panel/" in path
    )

    assert component_xml.index(f'id="{front_child.id}"'.encode()) < component_xml.index(
        b'name="Back child"'
    )
    assert component_xml.index(b'name="Back child"') < component_xml.index(
        f'id="{group.id}"'.encode()
    )


def test_pixel_line_height_is_serialized_as_editor_614_leading() -> None:
    manifest, payloads = _manifest_fixture("text")
    component = manifest.components[-1]
    text_object = component.objects[-1]
    assert text_object.text is not None
    updated_text = text_object.text.model_copy(
        update={"font_size": 52, "line_height": 40}
    )
    updated_object = text_object.model_copy(update={"text": updated_text})
    updated_component = component.model_copy(
        update={"objects": (*component.objects[:-1], updated_object)}
    )
    updated_manifest = manifest.model_copy(
        update={"components": (*manifest.components[:-1], updated_component)}
    )

    files = dialect.serialize_project_files(updated_manifest, payloads)
    component_path = next(
        path for path in files if path.endswith(".xml") and "/Panel/" in path
    )
    component_xml = files[component_path]

    assert b'fontSize="52" leading="-12"' in component_xml
    broken = dict(files)
    broken[component_path] = component_xml.replace(b'leading="-12"', b'leading="-12.5"')
    assert "fgui.writer.xml.text_invalid" in {
        item.code for item in validate_xml_files(broken)
    }


@pytest.mark.parametrize(
    ("figma_mode", "editor_mode"),
    (("NONE", "none"), ("TRUNCATE", "none"), ("HEIGHT", "height"), ("WIDTH_AND_HEIGHT", "both")),
)
def test_figma_text_auto_resize_is_preserved_in_editor_xml(
    figma_mode: str, editor_mode: str
) -> None:
    manifest, payloads = _manifest_fixture("text")
    component = manifest.components[-1]
    text_object = component.objects[-1]
    assert text_object.text is not None
    updated_text = text_object.text.model_copy(
        update={
            "auto_resize": figma_mode,
            "style_facts": {
                "fontCandidates": text_object.text.font_candidates,
                "fontSize": text_object.text.font_size,
                "color": text_object.text.color,
                "strokeColor": text_object.text.stroke_color,
                "strokeSize": text_object.text.stroke_size,
                "textAlignHorizontal": text_object.text.horizontal_align,
                "textAlignVertical": text_object.text.vertical_align,
                "textAutoResize": figma_mode,
            },
        }
    )
    updated_object = text_object.model_copy(update={"text": updated_text})
    updated_component = component.model_copy(
        update={"objects": (*component.objects[:-1], updated_object)}
    )
    updated_manifest = manifest.model_copy(
        update={"components": (*manifest.components[:-1], updated_component)}
    )

    files = dialect.serialize_project_files(updated_manifest, payloads)
    component_xml = next(
        content
        for path, content in files.items()
        if path.endswith(".xml") and "/Panel/" in path
    )
    assert f'autoSize="{editor_mode}"'.encode() in component_xml
    assert validate_xml_files(files) == ()


def test_serializer_rechecks_manifest_and_validated_payload_closure() -> None:
    manifest, payloads = _manifest_fixture("image")
    corrupt_manifest = manifest.model_copy(
        update={"project": manifest.project.model_copy(update={"fairy_gui_version": "6.2.0"})}
    )
    with pytest.raises(Exception, match="manifest validation failed"):
        dialect.serialize_project_files(corrupt_manifest, payloads)

    forged_payload = ValidatedAssetPayload(
        resource=payloads[0].resource,
        payload=payloads[0].payload.model_copy(update={"content": b"forged"}),
    )
    with pytest.raises(dialect.UnsupportedDialectFeature, match="payload"):
        dialect.serialize_project_files(manifest, (forged_payload,))


def test_xml_gate_rejects_windows_casefold_file_collisions() -> None:
    manifest, payloads = _manifest_fixture("image")
    files = dialect.serialize_project_files(manifest, payloads)
    package_path_value = "assets/Generated/package.xml"
    resource_path_value = next(path for path in files if path.endswith(".png"))
    resource_name = PurePosixPath(resource_path_value).name
    case_variant = f"{resource_name[:-4].upper()}.png"
    duplicate_id = "0123abcd"
    broken = dict(files)
    broken[package_path_value] = broken[package_path_value].replace(
        b"  </resources>",
        (
            f'    <image id="{duplicate_id}" name="{case_variant}" '
            'path="/Img/"/>\n  </resources>'
        ).encode(),
    )
    broken[f"assets/Generated/Img/{case_variant}"] = files[resource_path_value]

    assert "fgui.writer.xml.file_set_incoherent" in {
        item.code for item in validate_xml_files(broken)
    }


def test_xml_gate_rejects_cross_type_target_id_reuse() -> None:
    manifest, payloads = _manifest_fixture("image")
    files = dialect.serialize_project_files(manifest, payloads)
    component_path_value = next(
        path for path in files if path.endswith(".xml") and "/Panel/" in path
    )
    object_id = manifest.components[-1].objects[-1].id.encode()
    package_id = manifest.package.id.encode()
    broken = dict(files)
    broken[component_path_value] = broken[component_path_value].replace(
        b'id="' + object_id + b'" name="' + object_id + b'"',
        b'id="' + package_id + b'" name="' + package_id + b'"',
    )

    assert "fgui.writer.xml.target_id_conflict" in {
        item.code for item in validate_xml_files(broken)
    }


def test_zero_sized_display_object_uses_observed_nonnegative_geometry() -> None:
    manifest, payloads = _manifest_fixture("text")
    component = manifest.components[-1]
    child = component.objects[-1]
    zero_child = child.model_copy(
        update={
            "transform": child.transform.model_copy(
                update={
                    "bounds": child.transform.bounds.model_copy(
                        update={"width": 0.0, "height": 0.0}
                    )
                }
            )
        }
    )
    zero_component = component.model_copy(update={"objects": (component.objects[0], zero_child)})
    zero_manifest = manifest.model_copy(update={"components": (zero_component,)})

    files = dialect.serialize_project_files(zero_manifest, payloads)

    component_xml = next(value for key, value in files.items() if "/Panel/" in key)
    assert b'size="0,0"' in component_xml
