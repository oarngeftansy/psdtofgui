# Figma Create or Update FairyGUI Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standard Figma plugin that creates a new FairyGUI project from an approved template or updates an uploaded FairyGUI ZIP, then downloads a checked project ZIP without a web console, pairing code, or Windows agent.

**Architecture:** The bundled Figma UI is the only designer-facing interface. It uploads the current Figma selection through the existing chunked selection protocol, obtains either a server-created template project or an uploaded project, requests the existing selection-to-project conversion, then asks the server to apply the generated `ChangeBundle` to an isolated project copy and stream a ZIP artifact back. The intranet server owns templates, ZIP safety, conversion, validation, packaging, retention, and plugin deployment-token validation.

**Tech Stack:** TypeScript, React, Figma Plugin API, esbuild, Vitest, Python 3.11, FastAPI/Starlette, Pydantic, SQLite, lxml, pytest.

## Global Constraints

- The designer must not open a web console, enter a server URL, or use a pairing code.
- New mode requires `project_name`, `fairygui_version`, and `target_platform`; it does not accept a project ZIP.
- Update mode requires one `.zip`; the original archive is never modified.
- The plugin uploads only the current Figma selection and required exported assets, never the whole Figma file.
- Output names are `<project>-Figma新建-YYYYMMDD-HHmm.zip` or `<project>-Figma更新-YYYYMMDD-HHmm.zip`.
- No Windows local agent is part of the create/update/download flow.
- CORS must be narrowly configured for the Figma plugin iframe; never use `allow_origins=["*"]`.
- Preserve the existing ZIP limits: 500 MiB compressed, 2 GiB uncompressed, 20,000 entries, 100 MiB per file, and compression ratio 20.
- Every behavioral change follows RED → GREEN tests; do not add runtime dependencies unless existing Python/TypeScript facilities cannot perform the task.

---

## File Structure

- `src/figma_to_fgui/plugin_access.py`: validate the configured plugin deployment token and Figma iframe origin.
- `src/figma_to_fgui/project_templates.py`: list administrator-approved templates and copy one into an isolated project root.
- `src/figma_to_fgui/project_package.py`: apply a `ChangeBundle`, validate the result, and produce a deterministic downloadable ZIP.
- `src/figma_to_fgui/service_contracts.py`: API models for template options, create-project requests, and package results.
- `src/figma_to_fgui/api.py`: expose token-protected plugin endpoints and remove pairing as a runtime prerequisite.
- `apps/figma-plugin/src/project-client.ts`: direct service client for selection, project, job, package, polling, and safe error mapping.
- `apps/figma-plugin/src/contracts.ts`: pairing-free UI/main-thread messages and workflow models.
- `apps/figma-plugin/src/code.ts`: current-selection export bridge only.
- `apps/web-console/src/figma/ProjectWorkflowPage.tsx`: bundled four-step plugin UI; this file is build input only, not a hosted route.
- `apps/web-console/src/figma/plugin-entry.tsx`: render the workflow page with build-time service configuration.
- `apps/figma-plugin/scripts/build.mjs`: inject the service origin and deployment token and build production-only UI/main bundles.
- `packaging/figma-plugin/`: create the team-installable plugin ZIP and checksum manifest.

---

### Task 1: Replace Pairing With Narrow Plugin Access

**Files:**
- Create: `src/figma_to_fgui/plugin_access.py`
- Modify: `src/figma_to_fgui/api.py`
- Modify: `src/figma_to_fgui/cli.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Test: `tests/unit/test_api.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `create_app(..., public_origin: str | None)` and FastAPI request headers.
- Produces: `PluginAccess(secret: bytes).require(request: Request) -> None`; `create_app(..., plugin_access_token: bytes | None, plugin_origins: tuple[str, ...] = ("null",))`.

- [ ] **Step 1: Write failing access and CORS tests**

```python
def test_plugin_access_accepts_only_configured_token_and_null_origin(app_client):
    preflight = app_client.options(
        "/v1/figma/selections/uploads",
        headers={"Origin": "null", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-figma-plugin-token,content-type"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "null"
    assert app_client.post("/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "k"}).status_code == 401
    assert app_client.post(
        "/v1/figma/selections/uploads",
        json={"version": 1, "idempotency_key": "k"},
        headers={"X-Figma-Plugin-Token": "test-plugin-token"},
    ).status_code == 201
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/unit/test_api.py::test_plugin_access_accepts_only_configured_token_and_null_origin tests/unit/test_cli.py -q`

Expected: FAIL because `plugin_access_token`, `plugin_origins`, and `X-Figma-Plugin-Token` support do not exist.

- [ ] **Step 3: Implement the minimal access boundary**

```python
@dataclass(frozen=True)
class PluginAccess:
    secret: bytes

    def require(self, request: Request) -> None:
        supplied = request.headers.get("x-figma-plugin-token", "").encode()
        if not supplied or not hmac.compare_digest(supplied, self.secret):
            raise HTTPException(status_code=401, detail={"code": "plugin_access_denied", "message": "Plugin request was denied."})
```

Configure `CORSMiddleware` with `allow_origins=list(plugin_origins)`, `allow_methods=["GET", "POST", "PUT", "OPTIONS"]`, and `allow_headers=["content-type", "x-figma-plugin-token", "x-idempotency-key"]`. Apply `PluginAccess.require()` to selection, project, job-status, and package-download routes used by the plugin. Remove credential lookup from those routes; leave unrelated administrator and agent routes unchanged until Task 7 deletes obsolete pairing UI/API code.

- [ ] **Step 4: Run focused and full API tests**

Run: `pytest tests/unit/test_api.py tests/unit/test_cli.py tests/unit/test_figma_selection_api.py -q`

Expected: PASS with no wildcard CORS header.

- [ ] **Step 5: Commit**

```bash
git add src/figma_to_fgui/plugin_access.py src/figma_to_fgui/api.py src/figma_to_fgui/cli.py src/figma_to_fgui/service_contracts.py tests/unit/test_api.py tests/unit/test_cli.py
git commit -m "feat: authorize bundled Figma plugin directly"
```

### Task 2: Add Administrator-Approved Project Templates

**Files:**
- Create: `src/figma_to_fgui/project_templates.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/api.py`
- Modify: `src/figma_to_fgui/cli.py`
- Create: `tests/unit/test_project_templates.py`
- Modify: `tests/unit/test_api.py`

**Interfaces:**
- Consumes: a configured `templates_root: Path`; template directories containing `template.json` and a valid FairyGUI project tree.
- Produces: `TemplateOption`, `TemplateCatalog.list_options()`, `TemplateCatalog.create(template_id, project_name, destination) -> Path`; `GET /v1/figma/project-options`; `POST /v1/projects/from-template`.

- [ ] **Step 1: Write failing catalog and API tests**

```python
def test_catalog_lists_and_copies_only_declared_templates(tmp_path: Path):
    root = make_template(tmp_path, template_id="fgui-2024-web", version="2024.2", platform="web")
    catalog = TemplateCatalog(root)
    assert catalog.list_options()[0].template_id == "fgui-2024-web"
    created = catalog.create("fgui-2024-web", "Quiz", tmp_path / "created")
    assert (created / "Quiz" / "package.xml").is_file()
    assert not (created / "template.json").exists()
```

Add API assertions that an unknown template returns `template_not_found`, an invalid project name such as `../Quiz` returns `invalid_project_name`, and valid creation returns a normal `ProjectUploadView` indexed by `ProjectStore`.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/unit/test_project_templates.py tests/unit/test_api.py -q`

Expected: FAIL because the catalog and routes are absent.

- [ ] **Step 3: Implement template models and catalog**

```python
class TemplateOption(FrozenModel):
    template_id: str
    fairygui_version: str
    target_platform: str
    display_name: str

class CreateTemplateProject(VersionedModel):
    template_id: str
    project_name: str
```

Validate `project_name` with `^[\w\-\u4e00-\u9fff]{1,64}$`, parse `template.json` with Pydantic, reject symlinks, copy with `shutil.copytree`, remove `template.json`, rename the template package directory to the project name, then call `index_uploaded_project()` and `ProjectStore.save_upload()` exactly as the ZIP upload route does.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/unit/test_project_templates.py tests/unit/test_api.py tests/unit/test_uploaded_project.py -q`

Expected: PASS; copied output is contained under the destination and source templates remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/figma_to_fgui/project_templates.py src/figma_to_fgui/service_contracts.py src/figma_to_fgui/api.py src/figma_to_fgui/cli.py tests/unit/test_project_templates.py tests/unit/test_api.py
git commit -m "feat: create FairyGUI projects from approved templates"
```

### Task 3: Build Downloadable Project ZIPs on the Server

**Files:**
- Create: `src/figma_to_fgui/project_package.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Create: `tests/unit/test_project_package.py`

**Interfaces:**
- Consumes: `project_root: Path`, `ChangeBundle`, mode `Literal["create", "update"]`, project name, and clock.
- Produces: `build_project_package(...) -> BuiltProjectPackage` containing `path`, `download_name`, `sha256`, `changed_paths`, and diagnostics.

- [ ] **Step 1: Write the failing packaging test**

```python
def test_package_applies_bundle_to_copy_and_keeps_source_unchanged(tmp_path: Path):
    source = make_fgui_project(tmp_path / "source")
    before = fingerprint(source)
    built = build_project_package(source, replace_title_bundle(), "update", "Quiz", tmp_path / "out", fixed_clock)
    assert fingerprint(source) == before
    with ZipFile(built.path) as archive:
        assert etree.fromstring(archive.read("Quiz/package.xml")).tag == "package"
    assert built.download_name == "Quiz-Figma更新-20260730-1530.zip"
```

Also test create naming, deterministic member ordering, ignored `.figma-to-fgui` backup files, invalid post-apply XML, and SHA-256.

- [ ] **Step 2: Run and verify RED**

Run: `pytest tests/unit/test_project_package.py -q`

Expected: FAIL because `build_project_package` does not exist.

- [ ] **Step 3: Implement copy, apply, validate, and ZIP**

```python
class BuiltProjectPackage(FrozenModel):
    path: Path
    download_name: str
    sha256: str
    changed_paths: tuple[str, ...]
```

Copy the project into a temporary directory, call existing `apply_bundle()`, call `index_uploaded_project()` as the structural validation pass, delete `.figma-to-fgui` and `.figma-to-fgui-preview`, write archive members in sorted path order with fixed ZIP timestamps, atomically move the ZIP into the package store, and hash the final bytes.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_project_package.py tests/unit/test_apply.py tests/unit/test_uploaded_project.py -q`

Expected: PASS; source fingerprint stays unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/figma_to_fgui/project_package.py src/figma_to_fgui/service_contracts.py tests/unit/test_project_package.py
git commit -m "feat: package converted FairyGUI projects on server"
```

### Task 4: Expose Package Build, Status, and Download Endpoints

**Files:**
- Modify: `src/figma_to_fgui/api.py`
- Modify: `src/figma_to_fgui/job_store.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Create: `tests/integration/test_plugin_project_delivery.py`

**Interfaces:**
- Consumes: existing `POST /v1/figma/selections/{selection_id}/projects/{project_id}/jobs` and stored `ChangeBundle`.
- Produces: `POST /v1/jobs/{job_id}/package`, `GET /v1/jobs/{job_id}/package`, `GET /v1/jobs/{job_id}/package/download`; `ProjectPackageView(status, stage, progress, download_name, sha256, diagnostics)`.

- [ ] **Step 1: Write failing create/update integration tests**

```python
def test_plugin_create_flow_returns_openable_zip(client, template_root, selection_payload):
    selection_id = upload_selection(client, selection_payload)
    project_id = create_template_project(client, "Quiz", "fgui-2024-web")
    job_id = create_selection_job(client, selection_id, project_id)
    assert client.post(f"/v1/jobs/{job_id}/package", headers=plugin_headers()).status_code == 202
    package = wait_for_package(client, job_id)
    response = client.get(f"/v1/jobs/{job_id}/package/download", headers=plugin_headers())
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('.zip"')
    assert_openable_fgui_zip(response.content)
```

Mirror the test for uploaded update mode and assert the uploaded artifact fingerprint is unchanged. Add failure cases for nonexistent bundle, apply conflict, failed validation, duplicate package request, and download before ready.

- [ ] **Step 2: Run and verify RED**

Run: `pytest tests/integration/test_plugin_project_delivery.py -q`

Expected: FAIL with package routes missing.

- [ ] **Step 3: Implement an idempotent server-side package state**

Persist package state keyed by `job_id`; accept `{"version": 1, "mode": "create" | "update", "project_name": "Quiz"}` and return the existing result for duplicate requests. Run `build_project_package` in `BackgroundTasks`; stages are exactly `uploading`, `parsing`, `converting`, `checking`, `packaging`, `ready`, `failed`. Download with `FileResponse`, quoted safe filename, `Cache-Control: no-store`, and plugin-token enforcement.

- [ ] **Step 4: Run integration and regression tests**

Run: `pytest tests/integration/test_plugin_project_delivery.py tests/integration/test_selection_job_flow.py tests/unit/test_api.py -q`

Expected: PASS for both create and update flows.

- [ ] **Step 5: Commit**

```bash
git add src/figma_to_fgui/api.py src/figma_to_fgui/job_store.py src/figma_to_fgui/service_contracts.py tests/integration/test_plugin_project_delivery.py
git commit -m "feat: deliver created and updated project archives"
```

### Task 5: Simplify the Figma Main Thread to Selection Export Only

**Files:**
- Modify: `apps/figma-plugin/src/contracts.ts`
- Modify: `apps/figma-plugin/src/code.ts`
- Delete: `apps/figma-plugin/src/pairing.ts`
- Delete: `apps/figma-plugin/src/bootstrap.ts`
- Modify: `apps/figma-plugin/src/pairing.test.ts` (rename to `bridge.test.ts`)
- Modify: `apps/figma-plugin/src/selection.test.ts`

**Interfaces:**
- Consumes: Figma `currentPage.selection` and UI messages.
- Produces: `selection-preflight`, `selection-export`, `selection-error`, and `selection-changed`; no credential or pairing messages.

- [ ] **Step 1: Write failing bridge tests**

```ts
it("starts unpaired-free and reports current selection", () => {
  startPlugin(config, runtime);
  expect(runtime.showUI).toHaveBeenCalledOnce();
  expect(runtime.ui.postMessage).toHaveBeenCalledWith(
    expect.objectContaining({ type: "selection-preflight" }),
    { origin: "*" },
  );
});
```

Assert `figma.on("selectionchange")` refreshes preflight and that no generated code contains `pairing`, `credential`, or `clientStorage`.

- [ ] **Step 2: Run and verify RED**

Run: `cd apps/figma-plugin && node node_modules/vitest/vitest.mjs run src/bridge.test.ts src/selection.test.ts`

Expected: FAIL because start still restores pairing credentials.

- [ ] **Step 3: Remove pairing controller and narrow contracts**

```ts
export type UiToMainMessage =
  | { type: "selection-preflight" }
  | { type: "selection-export"; attempt: string };

export type MainToUiMessage =
  | { type: "selection-preflight"; preflight: SelectionPreflight }
  | { type: "selection-export"; attempt: string; manifest: SelectionManifest; resources: ExportedResource[] }
  | { type: "selection-error"; attempt: string; code: string };
```

Call preflight immediately after `showUI`, subscribe to `selectionchange`, and keep the existing snapshot-before-export rule.

- [ ] **Step 4: Run plugin tests and typecheck**

Run: `cd apps/figma-plugin && node node_modules/vitest/vitest.mjs run --exclude scripts/** && node --test scripts/build-manifest.test.mjs && node node_modules/typescript/bin/tsc --noEmit`

Expected: PASS and no pairing strings in `dist/code.js` after build.

- [ ] **Step 5: Commit**

```bash
git add -A apps/figma-plugin/src
git commit -m "refactor: remove Figma pairing bridge"
```

### Task 6: Implement the Direct Plugin Project Client

**Files:**
- Create: `apps/figma-plugin/src/project-client.ts`
- Create: `apps/figma-plugin/src/project-client.test.ts`
- Modify: `apps/figma-plugin/src/upload.ts`
- Modify: `apps/figma-plugin/src/upload.test.ts`

**Interfaces:**
- Consumes: `serverOrigin`, `pluginToken`, `SelectionManifest`, exported resources, optional ZIP, and create parameters.
- Produces: `ProjectWorkflowClient.options()`, `createProject()`, `uploadProject()`, `createJob()`, `buildPackage()`, `waitForPackage()`, `downloadPackage()` and typed `WorkflowStage` callbacks.

- [ ] **Step 1: Write failing client-flow tests**

```ts
it("runs create mode without uploading a zip", async () => {
  const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
  const result = await client.runCreate(selection, resources, {
    projectName: "Quiz",
    templateId: "fgui-2024-web",
  }, onStage);
  expect(requestPaths).not.toContain("/v1/projects/uploads");
  expect(result.downloadName).toMatch(/^Quiz-Figma新建-/);
});
```

Add update-mode ZIP upload, absolute URL resolution, `X-Figma-Plugin-Token` on every request, retry only for idempotent requests, abort support, distinct safe messages for network/ZIP/template/conversion/package errors, and polling timeout.

- [ ] **Step 2: Run and verify RED**

Run: `cd apps/figma-plugin && node node_modules/vitest/vitest.mjs run src/project-client.test.ts src/upload.test.ts`

Expected: FAIL because `ProjectWorkflowClient` is absent.

- [ ] **Step 3: Implement the smallest orchestration client**

Use existing `SelectionUploader` after changing it from `credential` to `{ serverOrigin, pluginToken }`. Build URLs with `new URL(path, serverOrigin)`. Upload project ZIP with `FormData`. Poll package status every 500 ms in tests through an injected wait function and every 1,000 ms in production. Return a `Blob` plus server-provided safe filename; do not call `window.open`.

- [ ] **Step 4: Run client and plugin tests**

Run: `cd apps/figma-plugin && node node_modules/vitest/vitest.mjs run src/project-client.test.ts src/upload.test.ts src/selection.test.ts`

Expected: PASS for create, update, abort, and error mapping.

- [ ] **Step 5: Commit**

```bash
git add apps/figma-plugin/src/project-client.ts apps/figma-plugin/src/project-client.test.ts apps/figma-plugin/src/upload.ts apps/figma-plugin/src/upload.test.ts
git commit -m "feat: run project delivery from Figma plugin"
```

### Task 7: Replace the Pairing Screen With the Four-Step Workflow UI

**Files:**
- Create: `apps/web-console/src/figma/ProjectWorkflowPage.tsx`
- Create: `apps/web-console/src/figma/ProjectWorkflowPage.test.tsx`
- Modify: `apps/web-console/src/figma/plugin-entry.tsx`
- Delete: `apps/web-console/src/figma/PluginFramePage.tsx`
- Delete: `apps/web-console/src/figma/PluginFramePage.test.tsx`
- Modify: `apps/web-console/src/styles.css`
- Modify: `apps/web-console/src/App.tsx`
- Modify: `apps/figma-plugin/src/ui.html`

**Interfaces:**
- Consumes: main-thread selection messages and `ProjectWorkflowClient`.
- Produces: accessible UI states `selection`, `mode`, `project-input`, `running`, `result`; browser download through a temporary object URL.

- [ ] **Step 1: Write failing designer-flow tests**

```tsx
it("creates a project without showing pairing or zip input", async () => {
  render(<ProjectWorkflowPage client={client} />);
  receivePreflight(sendableSelection);
  await user.click(screen.getByRole("radio", { name: "新建 FairyGUI 工程" }));
  await user.type(screen.getByLabelText("工程名称"), "Quiz");
  await user.selectOptions(screen.getByLabelText("FairyGUI 版本"), "fgui-2024-web");
  expect(screen.queryByLabelText("工程 ZIP")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "开始创建" }));
  expect(await screen.findByRole("button", { name: "下载 FairyGUI 工程 ZIP" })).toBeEnabled();
});
```

Add update mode, empty selection, refresh selection, invalid file, progress stages, warning/error result, retry preserving inputs, duplicate-click prevention, keyboard focus, and no pairing/web-console text.

- [ ] **Step 2: Run and verify RED**

Run: `cd apps/web-console && node node_modules/vitest/vitest.mjs run src/figma/ProjectWorkflowPage.test.tsx`

Expected: FAIL because the page does not exist.

- [ ] **Step 3: Implement the workflow page**

Render exactly four numbered sections: `1. Figma 选择`, `2. 新建或更新`, `3. 生成与检查`, `4. 下载工程`. Use native radio, text input, select, file input, progress, status, and alert elements. For download, create an object URL, click an `<a download={downloadName}>`, then revoke the URL. Remove `/figma-plugin` from the hosted `App` router so the component is bundle-only.

- [ ] **Step 4: Run UI tests, accessibility assertions, and build**

Run: `cd apps/web-console && node node_modules/vitest/vitest.mjs run src/figma/ProjectWorkflowPage.test.tsx && node node_modules/typescript/bin/tsc --noEmit`

Expected: PASS; rendered UI contains neither `配对码` nor `Web Console`.

- [ ] **Step 5: Commit**

```bash
git add -A apps/web-console/src/figma apps/web-console/src/App.tsx apps/web-console/src/styles.css apps/figma-plugin/src/ui.html
git commit -m "feat: add create and update workflow to Figma plugin"
```

### Task 8: Build a Production Plugin and Team-Installable Package

**Files:**
- Modify: `apps/figma-plugin/scripts/build.mjs`
- Modify: `apps/figma-plugin/scripts/build-manifest.mjs`
- Modify: `apps/figma-plugin/scripts/build.check.mjs`
- Modify: `apps/figma-plugin/manifest.template.json`
- Create: `packaging/figma-plugin/build-package.ps1`
- Create: `packaging/figma-plugin/README.md`
- Modify: `docs/deployment/internal-https.md`
- Modify: `docs/acceptance/figma-plugin-checklist.md`

**Interfaces:**
- Consumes: `FGUI_SERVER_ORIGIN`, `FIGMA_PLUGIN_ID`, `FGUI_PLUGIN_ACCESS_TOKEN`, built plugin files.
- Produces: `dist/Figma-to-FairyGUI-plugin.zip` containing `manifest.json`, `code.js`, `ui.html`, `INSTALL.md`, plus `checksums.sha256` beside it.

- [ ] **Step 1: Extend failing build checks**

Assert the generated main/UI bundles contain no `import(`, pairing endpoints, `Web Console`, or development React diagnostics; assert manifest `networkAccess` includes only the configured HTTPS service; assert the delivery ZIP contains exactly the four install files and no token in `manifest.json` or documentation.

- [ ] **Step 2: Run and verify RED**

Run: `cd apps/figma-plugin && node --test scripts/build.check.mjs`

Expected: FAIL because access-token injection and delivery packaging are absent.

- [ ] **Step 3: Implement production build and packaging**

Define `__FGUI_PLUGIN_ACCESS_TOKEN__` only in the bundled UI JavaScript, keep `process.env.NODE_ENV="production"`, and validate the origin/token environment variables. Have PowerShell build the archive with `System.IO.Compression.ZipArchive`, add files in ordinal name order, and assign every entry the fixed timestamp `2000-01-01T00:00:00Z`; do not use `Compress-Archive`, whose source timestamps make output nondeterministic. `INSTALL.md` describes Figma development import for pilot use and organization-private plugin publishing for team rollout.

- [ ] **Step 4: Run deterministic build/package checks**

Run: `$env:FGUI_SERVER_ORIGIN='https://fgui.internal.example'; $env:FIGMA_PLUGIN_ID='123456789'; $env:FGUI_PLUGIN_ACCESS_TOKEN='test-token'; node apps/figma-plugin/scripts/build.mjs; powershell -File packaging/figma-plugin/build-package.ps1`

Expected: two builds produce identical file hashes; ZIP has the exact expected members.

- [ ] **Step 5: Commit**

```bash
git add apps/figma-plugin/scripts apps/figma-plugin/manifest.template.json packaging/figma-plugin docs/deployment/internal-https.md docs/acceptance/figma-plugin-checklist.md
git commit -m "build: package installable Figma project workflow"
```

### Task 9: End-to-End Verification and Obsolete-Flow Removal

**Files:**
- Modify: `tests/integration/test_live_figma_web_agent_loop.py` (replace with `test_figma_plugin_project_delivery.py`)
- Modify: `apps/figma-plugin/test/plugin-harness.ts`
- Modify: `apps/figma-plugin/test/plugin-harness.test.ts`
- Delete: obsolete pairing-only web tests and fixtures found by `rg -l "pairing|Web Console" apps tests`
- Modify: `README.md`

**Interfaces:**
- Consumes: packaged plugin, intranet-style HTTPS server, one approved template fixture, one existing FairyGUI ZIP fixture.
- Produces: automated create/update proof plus completed real Figma/FairyGUI acceptance checklist.

- [ ] **Step 1: Write the failing end-to-end acceptance test**

The test must run both modes through the same public HTTP contract, assert distinct download filenames, open both archives, verify their XML and resources, assert the source template and uploaded ZIP hashes are unchanged, and prove no agent registration/assignment endpoint was called.

- [ ] **Step 2: Run and verify RED**

Run: `pytest tests/integration/test_figma_plugin_project_delivery.py -q`

Expected: FAIL until every create/update path and obsolete route removal is complete.

- [ ] **Step 3: Remove obsolete user-facing pairing/web-agent paths**

Delete plugin pairing code, pairing page route, pairing copy, and workflow tests that require the browser console or Windows agent. Keep migration compatibility only where an external API consumer is proven by tests; otherwise delete dead routes and models. Update README to the four-step plugin workflow.

- [ ] **Step 4: Run fresh full verification**

Run:

```powershell
pytest -q
node apps/figma-plugin/node_modules/vitest/vitest.mjs run --root apps/figma-plugin --exclude scripts/**
node --test apps/figma-plugin/scripts/build-manifest.test.mjs
node apps/web-console/node_modules/vitest/vitest.mjs run --root apps/web-console
node apps/figma-plugin/node_modules/typescript/bin/tsc -p apps/figma-plugin/tsconfig.json --noEmit
node apps/web-console/node_modules/typescript/bin/tsc -p apps/web-console/tsconfig.json --noEmit
node --test apps/figma-plugin/scripts/build.check.mjs
```

Expected: all commands exit 0; no generated plugin code contains `pairing`, `Web Console`, `clientStorage`, or `/v1/agents/`.

- [ ] **Step 5: Perform real desktop acceptance**

Install the generated package in Figma desktop. Verify current-selection refresh, one template-based create, one ZIP-based update, one network-offline retry, and both downloads. Open both downloaded projects in the supported FairyGUI version and record the result in `docs/acceptance/figma-plugin-checklist.md` with exact build hash and date.

- [ ] **Step 6: Commit**

```bash
git add -A tests apps/figma-plugin/test README.md docs/acceptance/figma-plugin-checklist.md
git commit -m "test: verify plugin-only FairyGUI project delivery"
```
