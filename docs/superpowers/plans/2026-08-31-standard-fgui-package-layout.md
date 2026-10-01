# Standard FairyGUI Package Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate every new standalone FairyGUI package with fixed `Component/`, `Img/`, and `Panel/` directories and publish only genuine PNG image resources.

**Architecture:** Make paths a manifest-level invariant: component source kind selects `Panel/` or `Component/`, while resources always select `Img/*.png`. Serializers consume those canonical paths, the build layer materializes and archives all three package directories, and validators independently reject path, image-format, XML-reference, directory, or ZIP drift before publication.

**Tech Stack:** Python 3.11+, Pydantic 2, lxml, Pillow-backed isolated raster inspection, `zipfile`, pytest.

## Global Constraints

- The generated package tree is exactly `{PackageName}/Component`, `{PackageName}/Img`, `{PackageName}/Panel`, plus `package.xml`.
- Root selection components live in `Panel/`; reusable generated definitions live in `Component/`.
- Every generated image has `exportFormat="png"`, `mimeType="image/png"`, a `.png` suffix, valid PNG bytes, and a path below `Img/`.
- Never relabel JPEG, WebP, or SVG bytes as PNG; fail before publication when a non-PNG payload reaches the new-project writer.
- Preserve all three directories in the ZIP even when empty, without registering directory placeholders in `package.xml`.
- Keep the existing `{ProjectName}.fairy` marker, `assets/{PackageName}` outer layout, IDs, ordering, collision rules, and preview API URLs.
- Do not modify or migrate existing FairyGUI projects or the legacy create/update generator.

---

## File Structure

- `src/figma_to_fgui/fgui_new_project_ids.py`: owns canonical package-relative paths and required directory names.
- `src/figma_to_fgui/fgui_new_project_compile.py`: assigns root/definition paths and enforces the new-project PNG-only manifest boundary.
- `src/figma_to_fgui/fgui_new_project_validate.py`: independently validates manifest paths, required directories, and archive members.
- `src/figma_to_fgui/fgui_new_project_build.py`: materializes required empty package directories and passes them to deterministic ZIP creation.
- `src/figma_to_fgui/project_package.py`: writes explicitly requested deterministic directory members without changing update-mode defaults.
- `src/figma_to_fgui/fgui_xml_dialect_614.py`: remains path-driven; tests prove `package.xml` and component `fileName` values follow the new manifest paths.
- `tests/unit/test_fgui_new_project_ids.py`: focused path-policy tests.
- `tests/unit/test_fgui_new_project_compile.py`: root/definition classification and PNG-only compilation tests.
- `tests/unit/test_fgui_new_project_validate.py`: manifest and archive rejection tests.
- `tests/unit/test_fgui_xml_dialect_614.py`: XML registration/reference tests.
- `tests/unit/test_fgui_new_project_build.py`: empty-directory materialization tests.
- `tests/unit/test_project_package.py`: deterministic optional directory-member tests.
- `tests/unit/test_fgui_new_project_workflow.py`: end-to-end generated archive assertions.
- `tests/golden/test_fgui_new_project.py` and affected integration tests: migrate stale old-path expectations.

---

### Task 1: Canonical Component and Image Paths

**Files:**
- Modify: `src/figma_to_fgui/fgui_new_project_ids.py`
- Modify: `src/figma_to_fgui/fgui_new_project_compile.py`
- Modify: `src/figma_to_fgui/fgui_new_project_validate.py`
- Test: `tests/unit/test_fgui_new_project_ids.py`
- Test: `tests/unit/test_fgui_new_project_compile.py`
- Test: `tests/unit/test_fgui_new_project_validate.py`

**Interfaces:**
- Produces: `PACKAGE_DIRECTORIES: tuple[str, str, str]`
- Produces: `component_path(name: str, target_id: str, source_kind: Literal["root", "definition"]) -> PurePosixPath`
- Produces: `resource_path(name: str, target_id: str) -> PurePosixPath`
- Consumes: existing `ManifestComponent.source_component_kind` and deterministic target IDs.

- [ ] **Step 1: Write failing canonical path tests**

Replace old lowercase path expectations and add explicit kind coverage:

```python
def test_component_paths_follow_standard_package_folders() -> None:
    target_id = "0123abcd"
    assert component_path("Shop", target_id, "root").as_posix() == (
        "Panel/Shop-0123abcd.xml"
    )
    assert component_path("Shop Item", target_id, "definition").as_posix() == (
        "Component/Shop Item-0123abcd.xml"
    )


def test_resource_path_is_always_png_in_img() -> None:
    assert resource_path("Hero Art", "0123abcd").as_posix() == (
        "Img/Hero Art-0123abcd.png"
    )


def test_required_package_directories_are_fixed_and_case_sensitive() -> None:
    assert PACKAGE_DIRECTORIES == ("Component", "Img", "Panel")
```

- [ ] **Step 2: Run the path tests and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_fgui_new_project_ids.py -q
```

Expected: failures because `component_path` has no source-kind argument, returns `components/`, and `resource_path` returns `resources/` with a caller-provided suffix.

- [ ] **Step 3: Implement the canonical path policy**

In `fgui_new_project_ids.py`, import `Literal`, remove the resource-suffix API from these two path helpers, and implement:

```python
PACKAGE_DIRECTORIES = ("Component", "Img", "Panel")


def component_path(
    name: str,
    target_id: str,
    source_kind: Literal["root", "definition"],
) -> PurePosixPath:
    readable_name = validate_target_name(name, "component")
    stable_id = _validated_target_id(target_id)
    directory = "Panel" if source_kind == "root" else "Component"
    filename = _require_segment_length(f"{readable_name}-{stable_id}.xml")
    return validate_unique_target_paths((PurePosixPath(directory, filename),))[0]


def resource_path(name: str, target_id: str) -> PurePosixPath:
    readable_name = validate_target_name(name, "resource")
    stable_id = _validated_target_id(target_id)
    filename = _require_segment_length(f"{readable_name}-{stable_id}.png")
    return validate_unique_target_paths((PurePosixPath("Img", filename),))[0]
```

Delete `_validated_suffix` only after `rg -n "_validated_suffix" src tests` proves it has no remaining caller.

- [ ] **Step 4: Make manifest compilation pass the source kind**

In `_compile_component`, change path creation to:

```python
relativePath=component_path(safe_name, target_id, source_kind).as_posix(),
```

In `_compile_resources`, fail closed before constructing `ManifestResource` and use the PNG-only helper:

```python
if resource.export_format != "png" or resource.mime_type != "image/png":
    raise TargetNamingError("new-project image resources must be PNG")

# ...
relativePath=resource_path(name, target_id).as_posix(),
```

Keep the existing public input diagnostic conversion around `TargetNamingError` so internal details do not cross the API boundary.

- [ ] **Step 5: Update independent manifest path validation**

In `_validate_paths`, calculate expectations from declared source kind and reject non-PNG resource declarations:

```python
expected = component_path(
    validate_target_name(component.name, "component"),
    component.id,
    component.source_component_kind,
)
```

```python
is_png = resource.export_format == "png" and resource.mime_type == "image/png"
try:
    expected = resource_path(validate_target_name(resource.name, "resource"), resource.id)
except TargetNamingError:
    expected = None
if not is_png or path != expected:
    _append_once(
        diagnostics,
        seen,
        "fgui.writer.manifest.resource_path_incoherent",
        "Image resources must be PNG files directly inside the Img directory.",
        node_id=resource.id,
        path=f"$.resources.{index}.relativePath",
    )
```

Change the component diagnostic message to identify `Panel`/`Component` classification rather than the removed lowercase directory.

- [ ] **Step 6: Add compiler and validator regression tests**

Compile a fixture with one definition and one root, then assert:

```python
paths = {
    (component.source_component_kind, component.relative_path)
    for component in manifest.components
}
assert any(kind == "root" and path.startswith("Panel/") for kind, path in paths)
assert any(kind == "definition" and path.startswith("Component/") for kind, path in paths)
assert all(resource.relative_path.startswith("Img/") for resource in manifest.resources)
assert all(resource.relative_path.endswith(".png") for resource in manifest.resources)
```

Add a copied manifest mutation proving `validate_new_project_manifest` reports `fgui.writer.manifest.component_path_incoherent` when a root path is moved to `Component/`, and `fgui.writer.manifest.resource_path_incoherent` when a resource declares JPG metadata or an old `resources/` path.

- [ ] **Step 7: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/unit/test_fgui_new_project_ids.py tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/figma_to_fgui/fgui_new_project_ids.py src/figma_to_fgui/fgui_new_project_compile.py src/figma_to_fgui/fgui_new_project_validate.py tests/unit/test_fgui_new_project_ids.py tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py
git commit -m "feat: standardize generated fgui package paths"
```

---

### Task 2: XML Registration and Reference Consistency

**Files:**
- Modify: `tests/unit/test_fgui_xml_dialect_614.py`
- Modify: `tests/unit/test_fgui_new_project_models.py`
- Modify: `src/figma_to_fgui/fgui_xml_dialect_614.py` only if the path-driven serializer tests expose a hard-coded old directory.

**Interfaces:**
- Consumes: canonical `ManifestComponent.relative_path` and `ManifestResource.relative_path` from Task 1.
- Produces: `package.xml` paths `/Panel/`, `/Component/`, `/Img/` and matching component `fileName` attributes.

- [ ] **Step 1: Write failing XML path assertions**

Update the manifest fixture to contain a root, a definition, and a PNG resource, then assert:

```python
package = etree.fromstring(serialize_package_xml(manifest))
assert package.xpath(
    "./resources/component[@path='/Panel/' and starts-with(@name, 'Root-')]"
)
assert package.xpath(
    "./resources/component[@path='/Component/' and starts-with(@name, 'Card-')]"
)
assert package.xpath(
    "./resources/image[@path='/Img/' and substring(@name, string-length(@name) - 3) = '.png']"
)
```

Serialize the root component and assert all generated local references use manifest paths:

```python
xml = serialize_component_xml(
    root_component,
    package_id=manifest.package.id,
    resources={item.id: item for item in manifest.resources},
    components={item.id: item for item in manifest.components},
)
assert b'fileName="Img/' in xml
assert b'fileName="Component/' in xml
assert b'fileName="resources/' not in xml
assert b'fileName="components/' not in xml
```

- [ ] **Step 2: Run XML tests and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_models.py -q
```

Expected: old fixture construction and lowercase path assertions fail.

- [ ] **Step 3: Migrate fixture construction to the canonical helpers**

Use explicit source kinds in every component helper call:

```python
relativePath=component_path("Root", root_component_id, "root").as_posix()
```

```python
relativePath=component_path("Card", definition_id, "definition").as_posix()
```

Use the suffix-free image helper:

```python
relativePath=resource_path(name, resource_id).as_posix()
```

Update direct model fixtures from `components/Root.xml` and `resources/Hero.png` to kind-correct `Panel/Root.xml`, `Component/Card.xml`, and `Img/Hero.png` values.

- [ ] **Step 4: Keep serialization generic and path-driven**

Verify these existing lines remain the only path derivation in `serialize_package_xml` and object serialization:

```python
path=_package_virtual_directory(component.relative_path)
```

```python
attributes["fileName"] = resource.relative_path
attributes["fileName"] = target.relative_path
```

If any old literal is found by the RED test or this search, replace it with the matching manifest field:

```powershell
rg -n 'components/|resources/' src/figma_to_fgui/fgui_xml_dialect_614.py
```

Expected after correction: no matches.

- [ ] **Step 5: Run XML tests and commit**

Run:

```powershell
python -m pytest tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_models.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/figma_to_fgui/fgui_xml_dialect_614.py tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_models.py
git commit -m "test: lock standard fgui xml resource paths"
```

---

### Task 3: Preserve Required Empty Directories in Deterministic Archives

**Files:**
- Modify: `src/figma_to_fgui/project_package.py`
- Modify: `src/figma_to_fgui/fgui_new_project_build.py`
- Modify: `src/figma_to_fgui/fgui_new_project_validate.py`
- Test: `tests/unit/test_project_package.py`
- Test: `tests/unit/test_fgui_new_project_build.py`
- Test: `tests/unit/test_fgui_new_project_validate.py`

**Interfaces:**
- Consumes: `PACKAGE_DIRECTORIES` from Task 1.
- Produces: `required_package_directories(manifest: NewProjectManifest) -> tuple[str, ...]` returning project-root-relative directory names with trailing `/` only when used as ZIP members.
- Changes: `write_deterministic_zip(project_root: Path, target: Path, *, directory_members: tuple[str, ...] = ()) -> None`.

- [ ] **Step 1: Write a failing deterministic ZIP directory test**

Add a test that does not alter update-mode behavior by default:

```python
def test_deterministic_zip_writes_only_explicit_directory_members(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    (root / "assets/Shop/Panel").mkdir(parents=True)
    (root / "Shop.fairy").write_bytes(b"marker")
    target = tmp_path / "project.zip"

    write_deterministic_zip(
        root,
        target,
        directory_members=(
            "assets/Shop/Component/",
            "assets/Shop/Img/",
            "assets/Shop/Panel/",
        ),
    )

    with ZipFile(target) as archive:
        names = archive.namelist()
        assert "assets/Shop/Component/" in names
        assert "assets/Shop/Img/" in names
        assert "assets/Shop/Panel/" in names
        assert archive.getinfo("assets/Shop/Img/").is_dir()
```

- [ ] **Step 2: Run ZIP test and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_project_package.py -q
```

Expected: `write_deterministic_zip` rejects the new keyword argument.

- [ ] **Step 3: Implement safe deterministic directory members**

Extend `write_deterministic_zip` with a keyword-only tuple. Validate every member with `safe_relative_path(value.removesuffix("/"))`, require exactly one trailing slash in the ZIP name, and write Unix directory metadata before files:

```python
def write_deterministic_zip(
    project_root: Path,
    target: Path,
    *,
    directory_members: tuple[str, ...] = (),
) -> None:
    directories = tuple(
        sorted(
            f"{safe_relative_path(value.removesuffix('/'))}/"
            for value in directory_members
        )
    )
    if len(directories) != len(set(directories)):
        raise ValueError("duplicate ZIP directory member")
    # existing file member discovery remains unchanged
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative_path in directories:
            info = ZipInfo(relative_path, date_time=_ZIP_TIMESTAMP)
            info.create_system = 3
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(info, b"")
        # existing regular-file loop follows
```

Preserve the default empty tuple so update-mode archives remain byte-compatible.

- [ ] **Step 4: Define and materialize required package directories**

In `fgui_new_project_validate.py`, add:

```python
def required_package_directories(manifest: NewProjectManifest) -> tuple[str, ...]:
    return tuple(
        f"{manifest.package.relative_path}/{name}"
        for name in PACKAGE_DIRECTORIES
    )
```

In `write_declared_files`, create those directories after `project_root.mkdir()`:

```python
for relative in required_package_directories(manifest):
    project_root.joinpath(*PurePosixPath(relative).parts).mkdir(parents=True, exist_ok=True)
```

Import `PurePosixPath`, `required_package_directories`, and pass directory ZIP members during build:

```python
directory_members = tuple(
    f"{path}/" for path in required_package_directories(manifest)
)
write_deterministic_zip(
    temporary,
    candidate,
    directory_members=tuple(
        f"{manifest.project.project_name}/{path}"
        for path in directory_members
    ),
)
```

The directory members are relative to `temporary`, because the archive intentionally includes the project-name prefix.

- [ ] **Step 5: Make directory and archive validation exact**

Extend `expected_directories` in `validate_project_directory`:

```python
expected_directories.update(required_package_directories(manifest))
```

In `validate_project_archive`, split directory and file entries. Require directory entries to equal the project-prefixed required package directories, permit only Unix directory mode for those entries, and keep file closure equal to `_declared_project_paths(manifest)`:

```python
directory_names = tuple(sorted(item.filename for item in infos if item.is_dir()))
expected_directory_names = tuple(
    f"{prefix}{path}/" for path in required_package_directories(manifest)
)
file_infos = tuple(item for item in infos if not item.is_dir())
if directory_names != expected_directory_names:
    return invalid
if tuple(sorted(item.filename[len(prefix):] for item in file_infos)) != _declared_project_paths(manifest):
    return invalid
```

Retain duplicate/casefold checks across both directory and file member names, encrypted-entry rejection, safe relative path validation, CRC testing, and regular-file mode validation.

- [ ] **Step 6: Add build and validator tests for empty and malformed directories**

Build a manifest with no definitions and no resources, then assert the filesystem and archive contain all required directories. Mutate separate ZIP fixtures to omit `Img/`, add lowercase `img/`, and place a regular file at the directory-member name; each must return `fgui.writer.project.archive_invalid`.

Core archive assertion:

```python
with ZipFile(built.path) as archive:
    names = set(archive.namelist())
    prefix = f"{built.project_name}/assets/{built.manifest.package.name}"
    assert f"{prefix}/Component/" in names
    assert f"{prefix}/Img/" in names
    assert f"{prefix}/Panel/" in names
```

- [ ] **Step 7: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/unit/test_project_package.py tests/unit/test_fgui_new_project_build.py tests/unit/test_fgui_new_project_validate.py -q
```

Expected: PASS.

Commit:

```powershell
git add src/figma_to_fgui/project_package.py src/figma_to_fgui/fgui_new_project_build.py src/figma_to_fgui/fgui_new_project_validate.py tests/unit/test_project_package.py tests/unit/test_fgui_new_project_build.py tests/unit/test_fgui_new_project_validate.py
git commit -m "feat: preserve standard package directories"
```

---

### Task 4: End-to-End PNG and Layout Regression Migration

**Files:**
- Modify: `tests/unit/test_fgui_new_project_workflow.py`
- Modify: `tests/golden/test_fgui_new_project.py`
- Modify: `tests/integration/test_fgui_new_project_cli.py`
- Modify: `tests/integration/test_figma_plugin_new_project_delivery_e2e.py`
- Modify: any additional new-project test returned by the exact stale-path search below.

**Interfaces:**
- Consumes: the canonical manifest, XML, materialization, and archive behavior from Tasks 1-3.
- Produces: a full workflow guarantee that downloaded archives contain only the standard package layout and genuine PNG resources.

- [ ] **Step 1: Add the workflow acceptance assertion**

In the workflow test that builds and reopens a real archive, assert the complete package contract:

```python
with ZipFile(built.path) as archive:
    names = archive.namelist()
    package_prefix = (
        f"{built.project_name}/{built.manifest.package.relative_path}"
    )
    assert f"{package_prefix}/Component/" in names
    assert f"{package_prefix}/Img/" in names
    assert f"{package_prefix}/Panel/" in names
    assert not any("/components/" in name or "/resources/" in name for name in names)

    png_names = [name for name in names if name.startswith(f"{package_prefix}/Img/") and not name.endswith("/")]
    assert png_names
    assert all(name.endswith(".png") for name in png_names)
    assert all(archive.read(name).startswith(b"\x89PNG\r\n\x1a\n") for name in png_names)
```

- [ ] **Step 2: Run workflow and golden tests to capture stale expectations**

Run:

```powershell
python -m pytest tests/unit/test_fgui_new_project_workflow.py tests/golden/test_fgui_new_project.py tests/integration/test_fgui_new_project_cli.py tests/integration/test_figma_plugin_new_project_delivery_e2e.py -q
```

Expected: failures identify assertions and fixture paths still using `components/` or `resources/`.

- [ ] **Step 3: Migrate every stale new-project path assertion**

Use this exact search:

```powershell
rg -n 'components/|resources/' tests/unit/test_fgui_new_project* tests/unit/test_fgui_xml_dialect_614.py tests/golden/test_fgui_new_project.py tests/integration/test_fgui_new_project* tests/integration/test_figma_plugin_new_project*
```

For filesystem/archive assertions, replace:

```python
"/components/"  # root component
```

with:

```python
"/Panel/"
```

Use `"/Component/"` only where the fixture explicitly targets a generated definition. Replace image filesystem paths `resources/<name>.<suffix>` with `Img/<name>.png`. Do not rename HTTP routes such as `/previews/components/` or `/resources/{resource_key}` because those are stable APIs rather than package paths.

- [ ] **Step 4: Add a non-PNG fail-closed workflow test**

Mutate a valid plan resource to JPG metadata while retaining the committed payload and assert no archive is published:

```python
invalid_resource = resource.model_copy(
    update={"mime_type": "image/jpeg", "export_format": "jpg"}
)
invalid_plan = plan.model_copy(
    update={"resources": {invalid_resource.id: invalid_resource}}
)

with pytest.raises(NewProjectBuildError):
    build_new_project(invalid_plan, config, payloads, tmp_path / "output")
assert not (tmp_path / "output" / "artifact.zip").exists()
```

If the model aliases require serialized construction, create the copied value with `mimeType="image/jpeg"` and `exportFormat="jpg"` through `model_validate`; the assertion and publication boundary remain identical.

- [ ] **Step 5: Run all new-project tests**

Run:

```powershell
python -m pytest tests/unit/test_fgui_new_project_ids.py tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_build.py tests/unit/test_fgui_new_project_workflow.py tests/unit/test_project_package.py tests/golden/test_fgui_new_project.py tests/integration/test_fgui_new_project_cli.py tests/integration/test_figma_plugin_new_project_delivery_e2e.py -q
```

Expected: PASS.

- [ ] **Step 6: Run static checks and the broad regression suite**

Run:

```powershell
python -m ruff check src/figma_to_fgui/fgui_new_project_ids.py src/figma_to_fgui/fgui_new_project_compile.py src/figma_to_fgui/fgui_new_project_validate.py src/figma_to_fgui/fgui_new_project_build.py src/figma_to_fgui/project_package.py tests/unit/test_fgui_new_project_ids.py tests/unit/test_fgui_new_project_compile.py tests/unit/test_fgui_new_project_validate.py tests/unit/test_fgui_xml_dialect_614.py tests/unit/test_fgui_new_project_build.py tests/unit/test_fgui_new_project_workflow.py tests/unit/test_project_package.py
python -m mypy src
python -m pytest -q --basetemp .pytest-standard-package-layout
```

Expected: Ruff clean, mypy succeeds, and pytest reports no failures. Preserve unrelated pre-existing worktree changes and do not stage them.

- [ ] **Step 7: Commit the end-to-end migration**

Stage only files changed for this feature, verify the staged diff, and commit:

```powershell
git add tests/unit/test_fgui_new_project_workflow.py tests/golden/test_fgui_new_project.py tests/integration/test_fgui_new_project_cli.py tests/integration/test_figma_plugin_new_project_delivery_e2e.py
git diff --cached --check
git diff --cached --stat
git commit -m "test: verify standard generated package layout"
```

If the stale-path search required additional test files, add those exact files to the same commit after reviewing their diffs.

---

## Final Verification

- [ ] Run `rg -n 'components/|resources/' src/figma_to_fgui/fgui_new_project_ids.py src/figma_to_fgui/fgui_new_project_compile.py src/figma_to_fgui/fgui_new_project_validate.py src/figma_to_fgui/fgui_xml_dialect_614.py` and confirm there are no package-filesystem literals using the old directories.
- [ ] Open one produced ZIP and confirm `assets/Shop/Component/`, `assets/Shop/Img/`, and `assets/Shop/Panel/` are present.
- [ ] Parse its `assets/Shop/package.xml` and confirm registrations use only `/Component/`, `/Img/`, and `/Panel/`.
- [ ] Confirm every file below `assets/Shop/Img/` starts with the PNG signature and ends in `.png`.
- [ ] Confirm `validate_project_archive()` returns an empty diagnostic tuple for the final archive.
- [ ] Confirm `git status --short` shows only the user's pre-existing unrelated changes outside the feature commits.
