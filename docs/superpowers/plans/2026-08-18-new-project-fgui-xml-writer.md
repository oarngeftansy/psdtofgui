# New-Project FairyGUI XML Writer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, fail-closed pipeline that turns a validated self-contained FGUI Plan plus verified asset payloads into a complete ZIP project that FairyGUI Editor 6.1.4 opens directly.

**Architecture:** Upgrade the generation contract to FGUI Plan v2 for self-contained component definitions, compile inputs into a validated `NewProjectManifest`, and serialize that manifest through a version-specific FairyGUI 6.1.4 dialect. Write only into a temporary project, validate the in-memory XML, on-disk project, and reopened ZIP, then atomically publish.

**Tech Stack:** Python 3.11+, Pydantic 2, lxml 5, Pillow 11+, Typer, pytest, standard-library hashlib/tempfile/zipfile.

## Global Constraints

- The official product output is one complete FairyGUI project ZIP, directly openable by FairyGUI Editor 6.1.4.
- Each build creates one project and one generated package; every Plan root becomes one top-level component.
- The writer consumes only `Validated FGUI Plan + NewProjectConfig + AssetPayloadSet` and never scans an existing project.
- Real FairyGUI package/component/resource IDs exist only in `NewProjectManifest` and generated files; never write them into UIR or Plan.
- Component references require complete self-contained Plan v2 definitions; candidate mappings are not component definitions.
- `fairyGuiVersion` accepts exactly `6.1.4`; `publishTarget` accepts exactly `unity` in v1.
- Unknown Plan features, unsupported dialect features, missing data, and validation failures fail closed and publish no ZIP.
- Output XML, paths, IDs, diagnostics, ZIP metadata, and ZIP bytes are deterministic.
- Do not add Project Binding, existing-resource reuse, incremental update, Controller, Gear, List, or complex Auto Layout behavior.
- Do not branch on the village fixture's page name, node IDs, dimensions, or structure; use it only after generic fixtures pass.

---

## File Structure

- `src/figma_to_fgui/fgui_plan_models.py`: FGUI Plan v2 document and self-contained component-definition models.
- `src/figma_to_fgui/fgui_plan_compile.py`: compile component definitions or safe fallback from UIR; emit v2.
- `src/figma_to_fgui/fgui_plan_validate.py`: v2 graph, definition ownership, recursion, and canonicalization rules.
- `src/figma_to_fgui/fgui_new_project_models.py`: strict config, payload metadata, manifest, generated-file, and result models.
- `src/figma_to_fgui/fgui_new_project_ids.py`: normalized names, safe paths, deterministic target IDs, and collision handling.
- `src/figma_to_fgui/fgui_asset_payloads.py`: load and validate resource bytes against `ResourcePlan` using Pillow/hashlib.
- `src/figma_to_fgui/fgui_new_project_compile.py`: pure Plan/config/assets-to-Manifest compilation.
- `src/figma_to_fgui/fgui_xml_dialect_614.py`: only FairyGUI 6.1.4 XML and `.fairy` serialization rules.
- `src/figma_to_fgui/fgui_new_project_validate.py`: manifest, XML, on-disk project, and reopened-archive gates.
- `src/figma_to_fgui/fgui_new_project_build.py`: temporary-directory orchestration, deterministic ZIP, and atomic publication.
- `src/figma_to_fgui/cli.py`: `build-fgui-project` command.
- `tests/fixtures/fgui-editor-6.1.4/minimal/`: checked-in minimal project created and resaved by the real editor.
- `tests/fixtures/fgui-new-project/`: generic Plan v2, config, and small image payload fixtures.
- Focused test modules mirror each new production module; integration and golden tests cover the full CLI.

### Task 1: Lock the FairyGUI 6.1.4 Project Dialect

**Files:**
- Create: `tests/fixtures/fgui-editor-6.1.4/minimal/Minimal.fairy`
- Create: `tests/fixtures/fgui-editor-6.1.4/minimal/assets/Generated/package.xml`
- Create: `tests/fixtures/fgui-editor-6.1.4/minimal/assets/Generated/components/Root.xml`
- Create: `tests/unit/test_fgui_xml_dialect_614.py`
- Create: `src/figma_to_fgui/fgui_xml_dialect_614.py`

**Interfaces:**
- Consumes: a minimal project created, opened, and saved by FairyGUI Editor 6.1.4.
- Produces: `parse_editor_fixture(root: Path) -> EditorDialectFixture` and constants `FAIRYGUI_VERSION`, `PROJECT_SUFFIX`, `ASSETS_DIRECTORY` used by all later tasks.

- [ ] **Step 1: Create and resave the minimal editor fixture**

In FairyGUI Editor 6.1.4, create project `Minimal`, target Unity, package `Generated`, and empty component `Root` sized `320x180`. Save, close, reopen, save again, then copy only the `.fairy` marker, `package.xml`, and `Root.xml` into the paths above. Record the editor version in `tests/fixtures/fgui-editor-6.1.4/README.md`:

```markdown
# FairyGUI 6.1.4 dialect fixture

Created, reopened, and resaved with FairyGUI Editor 6.1.4.
Project: Minimal; publish target: Unity; package: Generated; component: Root 320x180.
The fixture is format evidence only. Production IDs are generated deterministically.
```

- [ ] **Step 2: Write the failing characterization test**

```python
def test_minimal_editor_fixture_is_recognized() -> None:
    fixture = parse_editor_fixture(FIXTURES / "fgui-editor-6.1.4/minimal")
    assert fixture.project_name == "Minimal"
    assert fixture.package_name == "Generated"
    assert fixture.component_names == ("Root",)
    assert fixture.component_sizes == ((320, 180),)
```

- [ ] **Step 3: Run the characterization test to verify it fails**

Run: `python -m pytest -q tests/unit/test_fgui_xml_dialect_614.py`
Expected: FAIL because `figma_to_fgui.fgui_xml_dialect_614` does not exist.

- [ ] **Step 4: Implement the strict fixture parser**

```python
FAIRYGUI_VERSION = "6.1.4"
PROJECT_SUFFIX = ".fairy"
ASSETS_DIRECTORY = "assets"

class EditorDialectFixture(FrozenModel):
    project_name: str
    package_name: str
    component_names: tuple[str, ...]
    component_sizes: tuple[tuple[int, int], ...]

def parse_editor_fixture(root: Path) -> EditorDialectFixture:
    markers = tuple(root.glob(f"*{PROJECT_SUFFIX}"))
    manifests = tuple((root / ASSETS_DIRECTORY).glob("*/package.xml"))
    if len(markers) != 1 or len(manifests) != 1:
        raise ValueError("fixture must contain one project marker and one package")
    package_root = manifests[0].parent
    package = etree.parse(str(manifests[0]), SAFE_XML_PARSER).getroot()
    resources = package.find("resources")
    if resources is None:
        raise ValueError("fixture package has no resources")
    components = tuple(item for item in resources if etree.QName(item).localname == "component")
    names = tuple(Path(item.attrib["name"]).stem for item in components)
    roots = tuple(
        etree.parse(
            str(package_root / item.attrib.get("path", "/").strip("/") / item.attrib["name"]),
            SAFE_XML_PARSER,
        ).getroot()
        for item in components
    )
    sizes = tuple(tuple(int(value) for value in item.attrib["size"].split(",")) for item in roots)
    return EditorDialectFixture(
        project_name=markers[0].stem,
        package_name=package_root.name,
        component_names=names,
        component_sizes=cast(tuple[tuple[int, int], ...], sizes),
    )
```

- [ ] **Step 5: Run the test and commit**

Run: `python -m pytest -q tests/unit/test_fgui_xml_dialect_614.py`
Expected: PASS.

```bash
git add tests/fixtures/fgui-editor-6.1.4 tests/unit/test_fgui_xml_dialect_614.py src/figma_to_fgui/fgui_xml_dialect_614.py
git commit -m "test: lock FairyGUI 6.1.4 project dialect"
```

### Task 2: Introduce FGUI Plan v2 Self-Contained Components

**Files:**
- Modify: `src/figma_to_fgui/fgui_plan_models.py`
- Modify: `src/figma_to_fgui/fgui_plan_compile.py`
- Modify: `src/figma_to_fgui/fgui_plan_validate.py`
- Modify: `src/figma_to_fgui/cli.py`
- Modify: `tests/unit/test_fgui_plan_models.py`
- Modify: `tests/unit/test_fgui_plan_compile.py`
- Modify: `tests/unit/test_fgui_plan_validate.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: validated UIR and existing v1 node/resource/mask/decision types.
- Produces: `FGUIPlanDocument` with `schemaVersion=2` and `componentDefinitions`; `ComponentReferencePlan.definition_ref`; `migrate_plan_v1_without_components(plan) -> FGUIPlanDocument`.

- [ ] **Step 1: Write failing v2 model and graph tests**

```python
def test_plan_v2_component_reference_requires_owned_definition() -> None:
    plan = make_plan_v2(
        nodes={"instance": component_node(definition_ref="definition:button")},
        component_definitions={},
    )
    assert diagnostic_codes(validate_fgui_plan(plan)) == {
        "fgui.plan.component_definition_missing"
    }

def test_plan_v2_rejects_recursive_definition_graph() -> None:
    plan = make_recursive_component_plan_v2()
    assert "fgui.plan.component_definition_cycle" in diagnostic_codes(validate_fgui_plan(plan))
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest -q tests/unit/test_fgui_plan_models.py tests/unit/test_fgui_plan_validate.py`
Expected: FAIL because schema v2 and definitions are absent.

- [ ] **Step 3: Add strict v2 models and canonical validation**

```python
class ComponentDefinitionPlan(PlanModel):
    id: NonBlankString
    name: NonBlankString
    root_node_ref: NonBlankString = Field(alias="rootNodeRef")
    nodes: dict[str, FGUIPlanNode]

class ComponentReferencePlan(PlanModel):
    candidate_key: NonBlankString = Field(alias="candidateKey")
    definition_ref: NonBlankString = Field(alias="definitionRef")
    variant_properties: dict[str, str] = Field(default_factory=dict, alias="variantProperties")
    overrides: dict[str, object] = Field(default_factory=dict)

class FGUIPlanDocument(PlanModel):
    schema_version: Literal[2] = Field(default=2, alias="schemaVersion")
    component_definitions: dict[str, ComponentDefinitionPlan] = Field(
        default_factory=dict, alias="componentDefinitions"
    )
```

Validate dictionary-key/ID agreement, definition-local tree integrity, single ownership, reference closure, reachability, unused definitions, finite depth, and iterative cycle detection. Preserve candidate keys only as public provenance.

- [ ] **Step 4: Add the explicit safe v1 migration**

```python
def migrate_plan_v1_without_components(plan: FGUIPlanV1Document) -> FGUIPlanDocument:
    if any(node.type == PlanNodeType.COMPONENT_REFERENCE for node in plan.nodes.values()):
        raise ValueError("Plan v1 with component references must be recompiled")
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["schemaVersion"] = 2
    payload["componentDefinitions"] = {}
    return FGUIPlanDocument.model_validate(payload)
```

- [ ] **Step 5: Update compilation and CLI tests**

Make `compile_fgui_plan` emit v2. A verified component may emit a reference only when the UIR carries a complete generatable definition; otherwise apply its approved raster fallback or emit an unsupported decision. Add CLI assertions that canonical v2 bytes round-trip and v1 component references fail with exit code 2.

- [ ] **Step 6: Run and commit**

Run: `python -m pytest -q tests/unit/test_fgui_plan_models.py tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_plan_validate.py tests/unit/test_cli.py`
Expected: PASS.

```bash
git add src/figma_to_fgui/fgui_plan_models.py src/figma_to_fgui/fgui_plan_compile.py src/figma_to_fgui/fgui_plan_validate.py src/figma_to_fgui/cli.py tests/unit/test_fgui_plan_models.py tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_plan_validate.py tests/unit/test_cli.py
git commit -m "feat: add self-contained FairyGUI plan v2"
```

### Task 3: Add New-Project Config, Payload, and Manifest Models

**Files:**
- Create: `src/figma_to_fgui/fgui_new_project_models.py`
- Create: `tests/unit/test_fgui_new_project_models.py`

**Interfaces:**
- Produces: `NewProjectConfig`, `AssetPayload`, `AssetPayloadSet`, `ManifestPackage`, `ManifestComponent`, `ManifestResource`, `ManifestObject`, `NewProjectManifest`, `BuiltNewProject`.

- [ ] **Step 1: Write strict-model RED tests**

```python
@pytest.mark.parametrize("version,target", [("6.1.5", "unity"), ("6.1.4", "unreal")])
def test_config_rejects_unsupported_dialect(version: str, target: str) -> None:
    with pytest.raises(ValidationError):
        NewProjectConfig(projectName="Demo", packageName="Generated", fairyGuiVersion=version, publishTarget=target)

def test_asset_payload_set_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate resource id"):
        AssetPayloadSet.from_items((payload("image:a"), payload("image:a")))
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_models.py`
Expected: FAIL with module not found.

- [ ] **Step 3: Implement frozen, extra-forbid contracts**

```python
class NewProjectConfig(FrozenModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_name: NonBlankString = Field(alias="projectName")
    package_name: NonBlankString = Field(alias="packageName")
    fairy_gui_version: Literal["6.1.4"] = Field(alias="fairyGuiVersion")
    publish_target: Literal["unity"] = Field(alias="publishTarget")
    naming_policy_version: Literal[1] = Field(default=1, alias="namingPolicyVersion")

class AssetPayload(FrozenModel):
    resource_id: NonBlankString = Field(alias="resourceId")
    declared_mime_type: NonBlankString = Field(alias="declaredMimeType")
    content: bytes

class NewProjectManifest(FrozenModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    project: NewProjectConfig
    package: ManifestPackage
    components: tuple[ManifestComponent, ...]
    resources: tuple[ManifestResource, ...]
```

Define every manifest reference as a distinct nonblank string field and forbid opaque dictionaries except immutable public provenance.

- [ ] **Step 4: Run and commit**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_models.py`
Expected: PASS.

```bash
git add src/figma_to_fgui/fgui_new_project_models.py tests/unit/test_fgui_new_project_models.py
git commit -m "feat: define new FairyGUI project contracts"
```

### Task 4: Validate Resource Payloads Fail-Closed

**Files:**
- Create: `src/figma_to_fgui/fgui_asset_payloads.py`
- Create: `tests/unit/test_fgui_asset_payloads.py`
- Create: `tests/fixtures/fgui-new-project/resources/one-pixel.png`

**Interfaces:**
- Consumes: `Mapping[str, ResourcePlan]`, `AssetPayloadSet`.
- Produces: `validate_asset_payloads(resources, payloads) -> tuple[ValidatedAssetPayload, ...]`; raises `NewProjectInputError` carrying sorted public diagnostics.

- [ ] **Step 1: Write RED tests for content integrity**

```python
@pytest.mark.parametrize("mutation,code", [
    ("missing", "fgui.writer.asset.missing"),
    ("extra", "fgui.writer.asset.unexpected"),
    ("hash", "fgui.writer.asset.hash_mismatch"),
    ("mime", "fgui.writer.asset.mime_mismatch"),
    ("size", "fgui.writer.asset.dimension_mismatch"),
])
def test_payload_integrity_failures_are_public_and_deterministic(mutation: str, code: str) -> None:
    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads(resources_for(mutation), payloads_for(mutation))
    assert [item.code for item in captured.value.diagnostics] == [code]
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest -q tests/unit/test_fgui_asset_payloads.py`
Expected: FAIL with module not found.

- [ ] **Step 3: Implement streamed hash and Pillow verification**

```python
def validate_asset_payloads(
    resources: Mapping[str, ResourcePlan], payloads: AssetPayloadSet
) -> tuple[ValidatedAssetPayload, ...]:
    diagnostics: list[Diagnostic] = []
    # Compare exact sorted key sets, hashlib.sha256(content), Pillow-detected format/size,
    # declared MIME, export format, and nine-slice bounds.
    if diagnostics:
        raise NewProjectInputError(tuple(sorted(diagnostics, key=diagnostic_sort_key)))
    return tuple(validated[key] for key in sorted(validated))
```

Configure Pillow to reject decompression bombs and truncated/corrupt images. SVG is rejected in Writer v1 unless a separately implemented safe parser exists; do not trust MIME or suffix.

- [ ] **Step 4: Run and commit**

Run: `python -m pytest -q tests/unit/test_fgui_asset_payloads.py`
Expected: PASS.

```bash
git add src/figma_to_fgui/fgui_asset_payloads.py tests/unit/test_fgui_asset_payloads.py tests/fixtures/fgui-new-project/resources/one-pixel.png
git commit -m "feat: verify FairyGUI asset payloads"
```

### Task 5: Generate Safe Names and Deterministic Target IDs

**Files:**
- Create: `src/figma_to_fgui/fgui_new_project_ids.py`
- Create: `tests/unit/test_fgui_new_project_ids.py`

**Interfaces:**
- Produces: `validate_target_name(value, kind) -> str`, `TargetIdAllocator.allocate(kind, logical_key) -> str`, `component_path(name, target_id) -> PurePosixPath`, `resource_path(name, target_id, suffix) -> PurePosixPath`.

- [ ] **Step 1: Write RED tests for identity and path attacks**

```python
def test_ids_do_not_depend_on_insertion_order() -> None:
    first = allocate_all([("component", "b"), ("component", "a")])
    second = allocate_all([("component", "a"), ("component", "b")])
    assert first == second

@pytest.mark.parametrize("name", ["../Escape", "CON", "a/b", "A\u0000B", "é/../x"])
def test_target_names_fail_closed(name: str) -> None:
    with pytest.raises(TargetNamingError):
        validate_target_name(name, "component")
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_ids.py`
Expected: FAIL with module not found.

- [ ] **Step 3: Implement canonical allocation**

```python
def id_digest(kind: str, logical_key: str) -> str:
    return hashlib.sha256(f"{kind}:{logical_key}".encode("utf-8")).hexdigest()

class TargetIdAllocator:
    def allocate_all(self, requests: Iterable[tuple[str, str]]) -> dict[tuple[str, str], str]:
        ordered = sorted(set(requests))
        assigned = {request: id_digest(*request)[:8] for request in ordered}
        reverse: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for request, target_id in assigned.items():
            reverse[target_id].append(request)
        collisions = tuple(items for items in reverse.values() if len(items) > 1)
        if collisions:
            raise TargetIdCollisionError(collisions)
        return assigned
```

Normalize names with NFC only for comparison, preserve the validated original for readability, compare paths case-insensitively, and append a stable digest to component/resource filenames.

- [ ] **Step 4: Run and commit**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_ids.py`
Expected: PASS.

```bash
git add src/figma_to_fgui/fgui_new_project_ids.py tests/unit/test_fgui_new_project_ids.py
git commit -m "feat: allocate deterministic FairyGUI identities"
```

### Task 6: Compile and Validate NewProjectManifest

**Files:**
- Create: `src/figma_to_fgui/fgui_new_project_compile.py`
- Create: `src/figma_to_fgui/fgui_new_project_validate.py`
- Create: `tests/unit/test_fgui_new_project_compile.py`
- Create: `tests/unit/test_fgui_new_project_validate.py`

**Interfaces:**
- Consumes: `FGUIPlanDocument`, `NewProjectConfig`, `tuple[ValidatedAssetPayload, ...]`.
- Produces: `compile_new_project_manifest(plan, config, assets) -> NewProjectManifest`, `validate_new_project_manifest(manifest) -> tuple[Diagnostic, ...]`, `canonical_manifest_bytes(manifest) -> bytes`.

- [ ] **Step 1: Write RED tests for pure compilation and ownership**

```python
def test_manifest_is_canonical_across_mapping_order() -> None:
    first = compile_new_project_manifest(plan_with_order("forward"), CONFIG, ASSETS)
    second = compile_new_project_manifest(plan_with_order("reverse"), CONFIG, ASSETS)
    assert canonical_manifest_bytes(first) == canonical_manifest_bytes(second)

def test_manifest_rejects_shared_object_ownership() -> None:
    manifest = manifest_with_object_in_two_components()
    assert "fgui.writer.manifest.object_owner_conflict" in codes(
        validate_new_project_manifest(manifest)
    )
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py`
Expected: FAIL because compiler/validator modules do not exist.

- [ ] **Step 3: Implement the pure compiler**

```python
def compile_new_project_manifest(
    plan: FGUIPlanDocument,
    config: NewProjectConfig,
    assets: tuple[ValidatedAssetPayload, ...],
) -> NewProjectManifest:
    input_diagnostics = validate_fgui_plan(plan)
    if not plan.bindable or has_errors(input_diagnostics):
        raise NewProjectInputError(writer_input_diagnostics(input_diagnostics))
    allocator = TargetIdAllocator.from_plan(plan, config)
    definitions = compile_definitions_topologically(plan, allocator)
    roots = compile_roots(plan, allocator)
    resources = compile_resources(plan, assets, allocator)
    manifest = NewProjectManifest(
        project=config,
        package=compile_package(config, allocator),
        components=(*definitions, *roots),
        resources=resources,
    )
    diagnostics = validate_new_project_manifest(manifest)
    if has_errors(diagnostics):
        raise NewProjectManifestError(diagnostics)
    return manifest
```

Compilation must preserve parent-local geometry, exact `children` order, visibility, mask roles, resource consumers, raster-subtree consumption, and definition-before-root topological order.

- [ ] **Step 4: Implement iterative manifest validation**

Check key/ID agreement, unique case-folded paths, exactly one owner per object, closed resource/component/mask references, no component cycles, all resources consumed, all consumers present, no duplicate raster descendants, finite depth, and deterministic diagnostic ordering.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py`
Expected: PASS.

```bash
git add src/figma_to_fgui/fgui_new_project_compile.py src/figma_to_fgui/fgui_new_project_validate.py tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py
git commit -m "feat: compile validated FairyGUI project manifests"
```

### Task 7: Serialize the 6.1.4 Project, Package, Components, and Resources

**Files:**
- Modify: `src/figma_to_fgui/fgui_xml_dialect_614.py`
- Modify: `src/figma_to_fgui/fgui_new_project_validate.py`
- Modify: `tests/unit/test_fgui_xml_dialect_614.py`
- Create: `tests/golden/expected/minimal-new-project/`

**Interfaces:**
- Consumes: validated `NewProjectManifest` and validated resource payloads.
- Produces: `serialize_project_marker`, `serialize_package_xml`, `serialize_component_xml`, `serialize_project_files`; `validate_xml_files(files) -> tuple[Diagnostic, ...]`.

- [ ] **Step 1: Write RED golden tests for every supported object**

```python
@pytest.mark.parametrize("fixture", [
    "container", "text", "rich-text", "image", "loader",
    "component-reference", "raster-subtree", "nine-slice",
    "rectangle-clip", "rounded-clip", "image-mask",
])
def test_dialect_serialization_matches_approved_golden(fixture: str) -> None:
    files = serialize_project_files(load_manifest(fixture), load_payloads(fixture))
    assert files == load_golden_files(fixture)
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest -q tests/unit/test_fgui_xml_dialect_614.py`
Expected: FAIL because serialization entry points are absent.

- [ ] **Step 3: Implement library-built deterministic XML**

```python
SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)

def xml_bytes(root: etree._Element) -> bytes:
    return etree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
        standalone=None,
    ).replace(b"\r\n", b"\n")

def serialize_component_xml(component: ManifestComponent) -> bytes:
    root = etree.Element("component", name=component.name, size=format_size(component.size))
    display_list = etree.SubElement(root, "displayList")
    for object_ in component.objects:
        display_list.append(serialize_object(object_))
    return xml_bytes(root)
```

Implement a closed dispatch table for the seven Plan node types and three supported native mask paths. Any unregistered enum raises `UnsupportedDialectFeature`; never omit an object. Use exact tag/attribute/path conventions proven by Task 1 fixtures.

- [ ] **Step 4: Implement XML gate validation**

Reparse every XML byte string with `SAFE_XML_PARSER`; verify exact allowed root tags, required attributes, target-ID syntax, package resource declarations, component `src/pkg` types, mask roles/order, and no external entity/DOCTYPE. Validate that every expected manifest file exists exactly once and no undeclared file is returned.

- [ ] **Step 5: Approve goldens through the real editor**

Write the generic serialized project to a temporary directory, open it in FairyGUI Editor 6.1.4, verify no repair/migration prompt, save, and compare XML structure. Accept a golden only after differences are either eliminated or documented as editor-only volatile metadata excluded by the dialect.

- [ ] **Step 6: Run and commit**

Run: `python -m pytest -q tests/unit/test_fgui_xml_dialect_614.py`
Expected: PASS for all listed fixtures.

```bash
git add src/figma_to_fgui/fgui_xml_dialect_614.py src/figma_to_fgui/fgui_new_project_validate.py tests/unit/test_fgui_xml_dialect_614.py tests/golden/expected/minimal-new-project
git commit -m "feat: serialize FairyGUI 6.1.4 project XML"
```

### Task 8: Build, Reopen, Validate, and Atomically Publish the ZIP

**Files:**
- Create: `src/figma_to_fgui/fgui_new_project_build.py`
- Create: `tests/unit/test_fgui_new_project_build.py`
- Modify: `src/figma_to_fgui/fgui_new_project_validate.py`

**Interfaces:**
- Consumes: Plan, config, payloads, output directory.
- Produces: `build_new_project(plan, config, payloads, output_directory) -> BuiltNewProject`; `validate_project_directory`; `validate_project_archive`.

- [ ] **Step 1: Write RED atomicity and byte-parity tests**

```python
def test_repeated_builds_are_byte_identical(tmp_path: Path) -> None:
    first = build_fixture(tmp_path / "one")
    second = build_fixture(tmp_path / "two")
    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()

def test_failure_before_publish_leaves_no_zip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(builder, "validate_project_archive", raise_archive_error)
    with pytest.raises(NewProjectBuildError):
        build_fixture(tmp_path)
    assert list(tmp_path.glob("*.zip")) == []
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_build.py`
Expected: FAIL with module not found.

- [ ] **Step 3: Implement five-gate orchestration**

```python
def build_new_project(
    plan: FGUIPlanDocument,
    config: NewProjectConfig,
    payloads: AssetPayloadSet,
    output_directory: Path,
) -> BuiltNewProject:
    assets = validate_asset_payloads(plan.resources, payloads)          # input gate
    manifest = compile_new_project_manifest(plan, config, assets)       # manifest gate
    files = serialize_project_files(manifest, assets)
    require_clean(validate_xml_files(manifest, files))                  # XML gate
    with TemporaryDirectory(prefix="fgui-new-project-", dir=output_directory) as raw:
        temporary = Path(raw)
        project_root = write_declared_files(temporary, manifest, files, assets)
        require_clean(validate_project_directory(project_root, manifest))
        candidate = temporary / "candidate.zip"
        write_deterministic_zip(project_root.parent, candidate)
        require_clean(validate_project_archive(candidate, manifest))   # ZIP gate
        published = atomic_publish(candidate, output_directory, manifest)
    return BuiltNewProject.from_path(published, manifest)
```

Reuse the deterministic ZIP metadata policy from `project_package.py` by extracting a shared non-mutating helper if necessary; do not route through update-mode bundle application. Reject links/reparse points, undeclared files, output overlap, archive traversal, duplicate members, CRC failure, and content-hash mismatch.

- [ ] **Step 4: Add fault injection for every boundary**

Parametrize failures at XML validation, directory write, project validation, ZIP write, archive validation, and `os.replace`. Assert no published ZIP, no modification of a pre-existing unrelated file, and deterministic public diagnostic codes.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_build.py tests/unit/test_project_package.py`
Expected: PASS.

```bash
git add src/figma_to_fgui/fgui_new_project_build.py src/figma_to_fgui/fgui_new_project_validate.py src/figma_to_fgui/project_package.py tests/unit/test_fgui_new_project_build.py tests/unit/test_project_package.py
git commit -m "feat: atomically build deterministic FairyGUI projects"
```

### Task 9: Add the New-Project CLI and Generic End-to-End Fixtures

**Files:**
- Modify: `src/figma_to_fgui/cli.py`
- Modify: `tests/unit/test_cli.py`
- Create: `tests/integration/test_fgui_new_project_cli.py`
- Create: `tests/fixtures/fgui-new-project/generic-plan-v2.json`
- Create: `tests/fixtures/fgui-new-project/config.json`
- Create: `tests/fixtures/fgui-new-project/assets/manifest.json`

**Interfaces:**
- Produces command: `fgui-tool build-fgui-project PLAN CONFIG ASSET_DIRECTORY OUTPUT_DIRECTORY`.

- [ ] **Step 1: Write the RED CLI happy-path and failure tests**

```python
def test_build_fgui_project_cli_emits_verified_zip(tmp_path: Path) -> None:
    result = runner.invoke(app, [
        "build-fgui-project", str(PLAN), str(CONFIG), str(ASSETS), str(tmp_path)
    ])
    assert result.exit_code == 0
    artifact = next(tmp_path.glob("*.zip"))
    assert ZipFile(artifact).testzip() is None

def test_build_fgui_project_cli_publishes_nothing_for_unbindable_plan(tmp_path: Path) -> None:
    result = invoke_build(tmp_path, plan=UNBINDABLE_PLAN)
    assert result.exit_code == 2
    assert list(tmp_path.glob("*.zip")) == []
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest -q tests/unit/test_cli.py tests/integration/test_fgui_new_project_cli.py`
Expected: FAIL because the command is not registered.

- [ ] **Step 3: Implement strict file loading and safe CLI output**

```python
@app.command("build-fgui-project")
def build_fgui_project_command(
    plan: Path, config: Path, asset_directory: Path, output_directory: Path
) -> None:
    parsed_plan = load_canonical_plan_v2(plan)
    parsed_config = NewProjectConfig.model_validate_json(config.read_text("utf-8"))
    payloads = load_declared_asset_directory(asset_directory, parsed_plan.resources)
    try:
        built = build_new_project(parsed_plan, parsed_config, payloads, output_directory)
    except NewProjectError as error:
        typer.echo(canonical_diagnostics_json(error.diagnostics), err=True)
        raise typer.Exit(code=2) from None
    typer.echo(built.model_dump_json(by_alias=True, exclude={"path"}))
```

The asset directory loader must use a JSON manifest mapping `resourceId` to safe relative filename and declared MIME. It must reject undeclared files, links, absolute paths, traversal, and case-folded duplicate paths.

- [ ] **Step 4: Run and commit**

Run: `python -m pytest -q tests/unit/test_cli.py tests/integration/test_fgui_new_project_cli.py`
Expected: PASS.

```bash
git add src/figma_to_fgui/cli.py tests/unit/test_cli.py tests/integration/test_fgui_new_project_cli.py tests/fixtures/fgui-new-project
git commit -m "feat: expose new FairyGUI project builder CLI"
```

### Task 10: Complete Generic, Village, Security, and Real-Editor Acceptance

**Files:**
- Create: `tests/golden/test_fgui_new_project.py`
- Create: `tests/integration/test_village_new_project_regression.py`
- Create: `tests/unit/test_fgui_new_project_security.py`
- Create: `docs/validation/2026-08-18-fgui-6.1.4-new-project-acceptance.md`
- Modify: `.claude/memory/wiki.md`
- Append: `.claude/memory/learnings.md` only if new pitfalls were discovered.

**Interfaces:**
- Consumes: completed Writer CLI and generic/village fixtures.
- Produces: byte-level golden proof, security proof, editor-open proof, and durable project memory.

- [ ] **Step 1: Add broad generic and adversarial tests**

```python
def test_generic_project_zip_matches_golden(tmp_path: Path) -> None:
    built = build_generic_project(tmp_path)
    assert built.path.read_bytes() == GOLDEN_ZIP.read_bytes()

@pytest.mark.parametrize("attack", [
    "zip-slip", "absolute-path", "symlink", "reparse-point", "xml-doctype",
    "duplicate-member", "casefold-collision", "oversized-image", "deep-component-cycle",
])
def test_writer_rejects_attack_without_artifact(tmp_path: Path, attack: str) -> None:
    with pytest.raises(NewProjectError):
        build_attack_fixture(tmp_path, attack)
    assert list(tmp_path.glob("*.zip")) == []
```

- [ ] **Step 2: Run generic/security suites before village regression**

Run: `python -m pytest -q tests/unit/test_fgui_new_project_security.py tests/golden/test_fgui_new_project.py`
Expected: PASS.

- [ ] **Step 3: Add village as a non-specialized regression**

Compile the fixed village UIR through the same Plan v2 and Writer APIs. Assert no code/config contains the village page name or fixture node IDs, the ZIP validates, and its component/resource counts derive from the Plan rather than fixed expectations embedded in production code.

Run: `python -m pytest -q tests/integration/test_village_new_project_regression.py`
Expected: PASS or an explicit upstream unsupported diagnostic for a capability intentionally outside v1; never a Writer-side special case.

- [ ] **Step 4: Perform and record FairyGUI Editor 6.1.4 acceptance**

Open the generic generated ZIP after extraction in FairyGUI Editor 6.1.4. Verify project opens directly, all top-level and generated definition components appear, references/resources/masks/nine-slice render, there is no repair or migration prompt, and a save produces no semantic XML change. Record exact editor version, artifact SHA-256, commands, screenshots paths, save-diff result, and any approved non-semantic metadata in the acceptance document.

- [ ] **Step 5: Run the complete verification matrix**

```text
python -m pytest -q tests/unit/test_fgui_plan_models.py tests/unit/test_fgui_plan_compile.py tests/unit/test_fgui_plan_validate.py
python -m pytest -q tests/unit/test_fgui_new_project_models.py tests/unit/test_fgui_asset_payloads.py tests/unit/test_fgui_new_project_ids.py
python -m pytest -q tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_build.py
python -m pytest -q tests/integration/test_fgui_new_project_cli.py tests/integration/test_village_new_project_regression.py tests/golden/test_fgui_new_project.py
python -m pytest -q
python -m ruff check src tests
python -m mypy src
git diff --check
```

Expected: all tests pass except documented pre-existing conditional skips; Ruff and mypy report zero errors; diff check is clean.

- [ ] **Step 6: Update memory and commit acceptance evidence**

Update `wiki.md` with the final test counts, generated artifact hash, Editor 6.1.4 result, implemented scope, and remaining unsupported boundaries. Append only genuinely new process lessons to `learnings.md`.

```bash
git add tests/golden/test_fgui_new_project.py tests/integration/test_village_new_project_regression.py tests/unit/test_fgui_new_project_security.py docs/validation/2026-08-18-fgui-6.1.4-new-project-acceptance.md .claude/memory/wiki.md .claude/memory/learnings.md
git commit -m "test: verify new FairyGUI project generation"
```

## Plan Self-Review

- Spec coverage: Plan v2 self-contained definitions, payload verification, deterministic identity, single-package Manifest, all current supported node types, masks, nine-slice, five validation gates, atomic publication, CLI, generic fixtures, village-only regression, and real Editor 6.1.4 acceptance each have an owning task.
- Scope: Project Binding, existing-resource reuse, incremental update, Controller/Gear/List, and sample-specific logic remain excluded.
- Type consistency: `NewProjectConfig`, `AssetPayloadSet`, `ValidatedAssetPayload`, `NewProjectManifest`, `BuiltNewProject`, and the compile/validate/build entry points are introduced before use.
- Dependency direction: Plan → input verification → IDs/models → Manifest → dialect → build → CLI; no XML serializer reads UIR/Figma or guesses capabilities.
