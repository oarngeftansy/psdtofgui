# Figma Plugin New-Project Writer Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a designer generate, review by change type, approve, and download a validated FairyGUI 6.1.4 new-project ZIP directly from the existing Figma plugin.

**Architecture:** Add a focused server orchestration service and plugin-only API that turn one committed Figma selection into the existing project-neutral UIR/Plan/Writer pipeline and an immutable, initially non-downloadable candidate. Reuse the existing Designer Review vocabulary, expanded into image, component/interface, and package/resource review types with unified checks and whole-candidate approval or rejection. Replace the plugin's template-driven create flow with a compact create panel followed by the review workspace, retain update behind an overflow menu, and keep Web Console behavior unchanged.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, React 18, TypeScript 5.7, Vitest, Testing Library, Figma Plugin API, existing FairyGUI 6.1.4 Writer.

## Global Constraints

- Figma plugin only; do not change the Web Console product flow.
- New-project generation must use `mapping → UIR → FGUI Plan v2 → NewProjectManifest → XML → ZIP`.
- New-project requests contain no `templateId`, existing-project ID, existing-resource ID, or Project Binding data.
- FairyGUI dialect is exactly `6.1.4`; publish target is exactly `unity`; naming policy version is exactly `1`.
- Existing-project update remains available from the plugin overflow menu and retains its existing Project Binding behavior.
- No village page, node, component, or rank-specific production logic or configuration.
- Any validation or conversion failure publishes no ZIP and exposes only stable public diagnostics.
- Candidate ZIPs are not downloadable before whole-candidate approval; rejected or stale candidates never become downloadable.
- Review types are image, component/interface, and package/resource; approval is whole-candidate only.
- Do not weaken existing payload, image-probe, XML, directory, archive, deterministic ZIP, privacy, or atomic-publish gates.
- Follow TDD for every task and commit each independently reviewable result.

---

## File Structure

- Create `src/figma_to_fgui/fgui_new_project_workflow.py`: pure orchestration from a committed selection artifact to `BuiltNewProject`; no HTTP concerns.
- Create `src/figma_to_fgui/fgui_new_project_review.py`: strict typed review projection and candidate approval guards.
- Modify `src/figma_to_fgui/service_contracts.py`: strict plugin Writer request/result views.
- Modify `src/figma_to_fgui/api.py`: authenticated plugin-only create/status/download routes and artifact lifecycle.
- Modify `apps/figma-plugin/src/project-client.ts`: strict direct Writer client and response parser.
- Create `apps/web-console/src/figma/NewProjectWriterPanel.tsx`: focused default plugin UI.
- Create `apps/web-console/src/figma/NewProjectReviewPanel.tsx`: typed review navigation, unified checks, and whole-candidate actions.
- Create `apps/web-console/src/figma/ExistingProjectUpdatePanel.tsx`: extracted legacy update UI.
- Modify `apps/web-console/src/figma/ProjectWorkflowPage.tsx`: small mode shell and overflow menu.
- Modify `apps/web-console/src/figma/plugin-entry.tsx`: select the new plugin-default product configuration.
- Modify `apps/web-console/src/styles.css`: 360px, 8px-grid plugin layout and state styling.
- Add focused Python, TypeScript, integration, packaging, and acceptance tests next to existing suites.

---

### Task 1: Pure committed-selection Writer workflow

**Files:**
- Create: `src/figma_to_fgui/fgui_new_project_workflow.py`
- Create: `tests/unit/test_fgui_new_project_workflow.py`
- Modify: `rules/default/component-mapping-candidates.json` only if loading requires no semantic change; do not add fixture data

**Interfaces:**
- Consumes: `SelectionManifest`, a closed `resources_root: Path`, a public 64-hex `selection_fingerprint`, `project_name: str`, `output_directory: Path`, and the existing default mapping catalog path.
- Produces: `build_selection_new_project(...) -> BuiltNewProject`; private helper
  `_payloads_from_selection_assets(resources: tuple[ResourcePlan, ...], assets: tuple[SelectionAsset, ...]) -> AssetPayloadSet`;
  and `NewProjectWorkflowError` containing only a tuple of public `Diagnostic` values.

- [ ] **Step 1: Write failing workflow tests**

```python
def test_builds_committed_selection_with_existing_writer(tmp_path: Path) -> None:
    manifest, resources = neutral_image_selection(tmp_path)
    built = build_selection_new_project(
        manifest=manifest,
        resources_root=resources,
        selection_fingerprint="a" * 64,
        project_name="Inventory",
        output_directory=tmp_path / "out",
        mapping_catalog_path=Path("rules/default/component-mapping-candidates.json"),
    )
    assert built.project_name == "Inventory"
    assert built.download_name.endswith(".zip")
    validate_new_project_archive(built.path, built.manifest)

def test_non_bindable_plan_publishes_nothing(tmp_path: Path) -> None:
    manifest, resources = component_without_definition_selection(tmp_path)
    with pytest.raises(NewProjectWorkflowError) as raised:
        build_selection_new_project(
            manifest=manifest,
            resources_root=resources,
            selection_fingerprint="b" * 64,
            project_name="Inventory",
            output_directory=tmp_path / "out",
            mapping_catalog_path=Path("rules/default/component-mapping-candidates.json"),
        )
    assert {item.code for item in raised.value.diagnostics} == {"fgui.component.definition_missing"}
    assert list((tmp_path / "out").glob("*.zip")) == []
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/unit/test_fgui_new_project_workflow.py -q`

Expected: collection fails because `fgui_new_project_workflow` does not exist.

- [ ] **Step 3: Implement the pure orchestrator**

```python
def build_selection_new_project(
    *, manifest: SelectionManifest, resources_root: Path,
    selection_fingerprint: str, project_name: str,
    output_directory: Path, mapping_catalog_path: Path,
) -> BuiltNewProject:
    conversion = selection_conversion_document(manifest, resources_root, selection_fingerprint)
    roots, normalize_diagnostics = normalize_document(conversion.raw)
    catalog = load_mapping_catalog(mapping_catalog_path)
    uir = compile_uir(roots, source_revision=selection_fingerprint,
                      selection_id=selection_fingerprint[:32], mapping_catalog=catalog)
    plan = compile_fgui_plan(uir, profile_version="fgui-6.1.4-v1", rule_version=1)
    diagnostics = (*normalize_diagnostics, *validate_uir(uir), *validate_fgui_plan(plan))
    if not plan.bindable or has_errors(diagnostics):
        raise NewProjectWorkflowError(public_diagnostics(diagnostics))
    payloads = _payloads_from_selection_assets(plan.resources, conversion.assets)
    config = NewProjectConfig(projectName=project_name, packageName="Generated",
                              fairyGuiVersion="6.1.4", publishTarget="unity")
    return build_new_project(plan, config, payloads, output_directory)
```

Keep resource matching exact by resource ID and hash. Adapt `NewProjectBuildError` outside its handler into a fresh workflow error so cause, context, private evidence, and traceback markers cannot cross the public boundary.
`_payloads_from_selection_assets` must build one `AssetPayload` per Plan resource after exact source asset ID,
MIME, SHA-256, and byte-size agreement. Define a local `_public_diagnostics` adapter that creates fresh
allow-listed `Diagnostic` objects rather than returning diagnostics or exceptions supplied by callers.

- [ ] **Step 4: Add hostile and deterministic tests**

Cover duplicate/missing resources, mismatched bytes, SVG rejection where the Plan requires raster payload, private exception text, two identical builds, selection fingerprint change, and output-directory failure. Assert every failure leaves zero published ZIPs.

- [ ] **Step 5: Run focused quality gates**

Run:

```powershell
python -m pytest tests/unit/test_fgui_new_project_workflow.py -q
ruff check src/figma_to_fgui/fgui_new_project_workflow.py tests/unit/test_fgui_new_project_workflow.py
mypy src
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/fgui_new_project_workflow.py tests/unit/test_fgui_new_project_workflow.py
git commit -m "feat: build new projects from committed selections"
```

---

### Task 2: Strict plugin Writer HTTP contract and artifact lifecycle

**Files:**
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/api.py`
- Modify: `src/figma_to_fgui/job_store.py`
- Modify: `tests/unit/test_service_contracts.py`
- Create: `tests/integration/test_figma_plugin_new_project_writer_api.py`

**Interfaces:**
- Consumes: committed `selection_id` plus `{version: 1, project_name: str}`.
- Produces: `POST /v1/figma/selections/{selection_id}/new-fgui-projects`, `GET /v1/new-fgui-projects/{build_id}`, review/approve/reject routes added in Task 3, and an approval-gated download route.
- Result JSON: `{version, build_id, status, stage, progress, download_name?, sha256?, byte_size?, diagnostics}` with strict builtin types and no local path. `status` includes `awaiting_review`, `approved`, and `rejected`.

- [ ] **Step 1: Write failing contract and API tests**

```python
def test_plugin_builds_and_downloads_writer_archive(client: TestClient) -> None:
    selection_id = upload_neutral_writer_selection(client)
    started = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Inventory"},
    )
    assert started.status_code == 202
    build_id = started.json()["build_id"]
    candidate = client.get(f"/v1/new-fgui-projects/{build_id}", headers=PLUGIN_HEADERS)
    assert candidate.json()["status"] == "awaiting_review"
    blocked = client.get(f"/v1/new-fgui-projects/{build_id}/download", headers=PLUGIN_HEADERS)
    assert blocked.status_code == 409
```

Also assert missing auth, another device's selection/build, invalid names, duplicate request fields, missing resources, component-definition failure, and build failure return stable errors and no downloadable artifact.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/unit/test_service_contracts.py tests/integration/test_figma_plugin_new_project_writer_api.py -q`

Expected: route is 404 and new view types are absent.

- [ ] **Step 3: Add strict request/result models**

```python
class NewFguiProjectRequest(FrozenModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    project_name: str = Field(pattern=r"^[\w\-\u4e00-\u9fff]{1,64}$")

class NewFguiProjectView(FrozenModel):
    version: Literal[1] = 1
    build_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["converting", "checking", "packaging", "awaiting_review", "approved", "rejected", "failed"]
    stage: Literal["converting", "checking", "packaging", "awaiting_review", "approved", "rejected", "failed"]
    progress: int = Field(ge=0, le=100)
    download_name: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_size: int | None = Field(default=None, ge=0)
    diagnostics: tuple[Diagnostic, ...] = ()
```

- [ ] **Step 4: Persist ownership and immutable artifact metadata**

Add a dedicated store record keyed by random `build_id`, containing owner device, selection fingerprint, request identity, stage, public result, and internal artifact path. Reuse the existing package lease/atomic transition patterns; never serialize paths in public payloads. Reconcile ready artifacts by regular-file identity, expected storage root, size, and SHA-256 before download.

- [ ] **Step 5: Implement authenticated routes**

Resolve the committed selection through `SelectionStore`, verify plugin ownership, call Task 1's orchestrator into a per-attempt directory under `data_dir`, and persist a non-downloadable candidate after the Writer returns. Download must remain `409` until Task 3 records approval. Convert all ordinary exceptions to the existing static public error envelope without chained private causes.

- [ ] **Step 6: Add idempotency, race, tamper, and cleanup tests**

Cover repeat requests, simultaneous ownership attempts, build lease expiry, replaced artifact, wrong hash/size, link/reparse artifact, download during non-ready stage, cleanup failure, and server restart reconciliation.

- [ ] **Step 7: Run focused gates and commit**

Run:

```powershell
python -m pytest tests/unit/test_service_contracts.py tests/integration/test_figma_plugin_new_project_writer_api.py -q
ruff check src/figma_to_fgui/service_contracts.py src/figma_to_fgui/api.py src/figma_to_fgui/job_store.py tests/integration/test_figma_plugin_new_project_writer_api.py
mypy src
```

Commit: `git commit -m "feat: expose plugin new-project Writer API"`

---

### Task 3: Typed multi-review projection and whole-candidate decision gate

**Files:**
- Create: `src/figma_to_fgui/fgui_new_project_review.py`
- Create: `tests/unit/test_fgui_new_project_review.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/api.py`
- Modify: `src/figma_to_fgui/job_store.py`
- Modify: `tests/integration/test_figma_plugin_new_project_writer_api.py`

**Interfaces:**
- Consumes: the immutable candidate manifest, validated Plan, source selection preview metadata, Writer diagnostics, and candidate ownership.
- Produces: `NewProjectDesignerReview` with `imageReviews`, `componentReviews`, `packageReview`, and `checks`; `POST /v1/new-fgui-projects/{build_id}/adjustments`; `POST /v1/new-fgui-projects/{build_id}/regenerate`; `POST /v1/new-fgui-projects/{build_id}/approve`; `POST /v1/new-fgui-projects/{build_id}/reject`.
- Review evidence carries an explicit `evidenceKind: "rendered" | "source-image" | "structured-summary"`; structured summaries must never be labeled as rendered previews.

- [ ] **Step 1: Write failing typed-review tests**

```python
def test_projects_candidate_into_three_review_types(candidate: CandidateFixture) -> None:
    review = build_new_project_designer_review(candidate.manifest, candidate.plan, candidate.diagnostics)
    assert review.image_reviews[0].evidence_kind == "source-image"
    assert review.component_reviews[0].evidence_kind in {"rendered", "structured-summary"}
    assert review.package_review.components_added >= 1
    assert review.package_review.resource_closure_valid is True

def test_error_check_blocks_whole_candidate_approval(client: TestClient, blocked_build: str) -> None:
    response = client.post(f"/v1/new-fgui-projects/{blocked_build}/approve", headers=PLUGIN_HEADERS)
    assert response.status_code == 409
    assert client.get(f"/v1/new-fgui-projects/{blocked_build}/download", headers=PLUGIN_HEADERS).status_code == 409
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/unit/test_fgui_new_project_review.py tests/integration/test_figma_plugin_new_project_writer_api.py -q`

Expected: review module and decision routes are absent.

- [ ] **Step 3: Implement strict review models**

Define frozen, extra-forbid models for:

```python
class NewProjectImageReview(FrozenModel):
    resource_id: str
    label: str
    evidence_kind: Literal["source-image"]
    source_preview_url: str | None
    generated_asset_url: str
    width: int
    height: int
    nine_slice: bool

class NewProjectComponentReview(FrozenModel):
    component_id: str
    label: str
    evidence_kind: Literal["rendered", "structured-summary"]
    rendered_preview_url: str | None
    object_count: int
    text_count: int
    resource_refs: int
    component_refs: int

class NewProjectDesignerReview(FrozenModel):
    version: Literal[1] = 1
    build_id: str
    image_reviews: tuple[NewProjectImageReview, ...]
    component_reviews: tuple[NewProjectComponentReview, ...]
    package_review: NewProjectPackageReview
    checks: tuple[DesignerCheck, ...]
    warning_ids: tuple[str, ...]
    approvable: bool
```

Expose generated image bytes only through authenticated, build-owned preview routes. Component `rendered_preview_url` is present only when a real renderer produced and validated those bytes; otherwise emit the structured summary with no image URL.

- [ ] **Step 4: Implement the state machine and decision routes**

Allowed terminal transitions are `awaiting_review -> approved` and `awaiting_review -> rejected`. Actionable diagnostics additionally allow `awaiting_review -> adjusting -> regenerating -> awaiting_review` for a new generation. Adjustment requests use a closed strategy enum (`preserve-editable`, `rasterize-subtree`, `include-contained-definition`) and bind the current candidate ID, generation, selection fingerprint, issue ID, and UIR node ID. Regeneration reruns the complete Writer pipeline from the immutable committed selection, creates a new candidate ID and generation, and permanently invalidates the prior candidate for review, approval, preview, and download. Approval is idempotent only for the same owner and exact current generation; rejection invalidates the download capability permanently. Any error check, artifact mismatch, selection-generation mismatch, stale generation, unsupported strategy, or expired lease blocks approval/regeneration. Warning acknowledgement is included as an exact tuple of review check IDs in the approve request and must match the current review.

- [ ] **Step 5: Add decision integrity tests**

Cover cross-owner review access, guessed IDs, missing preview evidence, forged rendered label, warning acknowledgement mismatch, strategy/issue/node mismatch, arbitrary strategy rejection, regeneration from immutable input, old-candidate invalidation, double approve, approve-after-reject, reject-after-approve, artifact mutation, stale review generation, and download before/after each terminal state.

- [ ] **Step 6: Run focused gates and commit**

Run:

```powershell
python -m pytest tests/unit/test_fgui_new_project_review.py tests/integration/test_figma_plugin_new_project_writer_api.py -q
ruff check src/figma_to_fgui/fgui_new_project_review.py tests/unit/test_fgui_new_project_review.py
mypy src
```

Commit: `git commit -m "feat: add typed plugin Writer review gate"`.

---

### Task 4: Strict TypeScript Writer and review client

**Files:**
- Modify: `apps/figma-plugin/src/project-client.ts`
- Modify: `apps/figma-plugin/src/project-client.test.ts`

**Interfaces:**
- Consumes: uploaded `SelectionView`, project name, stage callback, abort signal, timeout.
- Produces: `runNewProjectWriter(manifest, resources, {projectName}, onStage?, options?) -> Promise<NewProjectWriterCandidate>`, `reviewNewProject(buildId)`, `setNewProjectAdjustment(buildId, adjustment)`, `regenerateNewProject(buildId)`, `approveNewProject(buildId, warningIds)`, `rejectNewProject(buildId)`, and approval-gated `downloadNewProject(buildId)`.
- `NewProjectWriterCandidate` contains build and typed review metadata but no ZIP blob; it deliberately has no `ProjectView` or template fields.

- [ ] **Step 1: Write failing client tests**

```ts
it("uploads selection, starts Writer, polls, and downloads the exact archive", async () => {
  const candidate = await client.runNewProjectWriter(manifest, resources, { projectName: "Inventory" }, onStage);
  expect(requests.start.body).toEqual({ version: 1, project_name: "Inventory" });
  expect(JSON.stringify(requests.start.body)).not.toContain("template");
  expect(candidate.build.status).toBe("awaiting_review");
  await expect(client.downloadNewProject(candidate.build.buildId)).rejects.toMatchObject({ code: "review_required" });
  await client.approveNewProject(candidate.build.buildId, candidate.review.warningIds);
  const result = await client.downloadNewProject(candidate.build.buildId);
  expect(result.downloadName).toBe("Inventory.zip");
  expect(await result.blob.arrayBuffer()).toEqual(expectedZip.buffer);
});
```

Cover exact response parsing, filename safety, hash/size agreement, authentication mapping, validation diagnostics, cancellation, timeout, non-ready download, and malformed JSON.

- [ ] **Step 2: Run tests and verify RED**

Run: `npm test -- --run src/project-client.test.ts` from `apps/figma-plugin`.

Expected: `runNewProjectWriter` is undefined.

- [ ] **Step 3: Implement parser and workflow**

Add `review_required` and `stale_candidate` to `WorkflowErrorCode`, then add `parseNewProjectBuild`, `parseNewProjectReview`, `waitForNewProjectBuild`, adjustment/regeneration/decision methods, and the approval-gated download. Reuse the existing deadline/abort machinery and `SelectionUploader`; do not call `options()`, `createProject()`, `createJob()`, or `buildPackage()` in the new method. Verify downloaded blob size and SHA-256 before returning it.

- [ ] **Step 4: Run TypeScript gates and commit**

Run:

```powershell
npm test -- --run src/project-client.test.ts
npm run typecheck
```

Expected: pass. Commit: `git commit -m "feat: add plugin Writer client"`.

---

### Task 5: Compact plugin create UI, typed review workspace, and isolated update mode

**Files:**
- Create: `apps/web-console/src/figma/NewProjectWriterPanel.tsx`
- Create: `apps/web-console/src/figma/NewProjectReviewPanel.tsx`
- Create: `apps/web-console/src/figma/ExistingProjectUpdatePanel.tsx`
- Modify: `apps/web-console/src/figma/ProjectWorkflowPage.tsx`
- Modify: `apps/web-console/src/figma/ProjectWorkflowPage.test.tsx`
- Modify: `apps/web-console/src/figma/plugin-entry.tsx`
- Modify: `apps/web-console/src/styles.css`

**Interfaces:**
- `ProjectWorkflowPage` gains `defaultMode?: "writer" | "legacy"`; plugin entry passes `writer`, Web Console callers retain `legacy` by default.
- `NewProjectWriterPanel` consumes `runNewProjectWriter` and the selection bridge; it creates a candidate rather than downloading.
- `NewProjectReviewPanel` consumes typed review, approve/reject, preview, and gated download methods.
- `ExistingProjectUpdatePanel` consumes the unchanged `runUpdate` contract.

- [ ] **Step 1: Write failing interaction and layout tests**

```tsx
it("shows one decision and one primary action in plugin Writer mode", async () => {
  render(<ProjectWorkflowPage defaultMode="writer" client={client} />);
  sendSelection({ displayName: "Inventory", nodeCount: 28, assetCount: 4 });
  expect(screen.getByText("Inventory")).toBeVisible();
  expect(screen.getByLabelText("工程名称")).toBeVisible();
  expect(screen.getByRole("button", { name: "生成候选工程" })).toBeEnabled();
  expect(screen.queryByText("新建或更新")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("FairyGUI 版本")).not.toBeInTheDocument();
});
```

Add tests for overflow-menu update entry, selection refresh clearing stale candidates, locked controls, stage copy, actionable diagnostics, all three review types, rendered-vs-summary evidence labels, warning acknowledgement, blocked approval, whole-candidate reject, approval-gated first download, `再次下载`, retry, cancellation on unmount, and exactly one primary action per state.

- [ ] **Step 2: Run tests and verify RED**

Run: `npm test -- --run src/figma/ProjectWorkflowPage.test.tsx` from `apps/web-console`.

Expected: old four-step UI assertions demonstrate the new tests fail.

- [ ] **Step 3: Extract update behavior without changing it**

Move the existing archive field, upload/update orchestration, screenshot-consent support, progress, diagnostics, and download behavior into `ExistingProjectUpdatePanel`. Keep existing tests passing before changing the default plugin panel.

- [ ] **Step 4: Implement the Writer panel state machine**

Use explicit states `idle | exporting | running | reviewing | adjusting | regenerating | approving | rejected | failed | ready`; derive button label and disabled state from this union. Track candidate ID/generation and selected closed adjustments; regeneration clears every prior review acknowledgement and replaces the current candidate. Store the successful `Blob` and name only after approval for repeat download. Accept selection export only for the active attempt and reject mismatched attempts exactly as today.

- [ ] **Step 5: Implement the approved visual system**

Apply the approved 360px single-column layout: 16px horizontal padding, 8px spacing scale, current-selection blueprint card, one project-name field, read-only `FairyGUI 6.1.4` pill, collapsed settings, and bottom action region. Use existing local/system fonts only; preserve keyboard focus, accessible labels, `prefers-reduced-motion`, and readable error contrast.

After candidate generation, replace the create panel with the review workspace. Use object-type navigation for `图片`, `组件 / 界面`, and `Package / 资源`; show unified checks alongside the current item; label every preview as rendered, source image, or structured summary. An actionable issue exposes `定位到图层`, `查看原因`, and only its server-declared safe strategies. Once selected, the primary action is `重新生成候选`; the old generation is visibly invalidated. Otherwise expose `返回调整` plus `确认并下载 ZIP`. Do not add per-file approval controls.

- [ ] **Step 6: Prove Web Console does not change**

Keep `defaultMode="legacy"` as the component default and add a regression snapshot/role test for the Web Console entry. Only `plugin-entry.tsx` opts into `writer`.

- [ ] **Step 7: Run UI gates and commit**

Run:

```powershell
npm test -- --run src/figma/ProjectWorkflowPage.test.tsx
npm run typecheck
```

Expected: pass. Commit: `git commit -m "feat: streamline plugin Writer workflow"`.

---

### Task 6: Plugin package, public end-to-end integration, and no-special-case closure

**Files:**
- Modify: `tests/integration/test_figma_plugin_project_delivery.py`
- Create: `tests/integration/test_figma_plugin_new_project_delivery.py`
- Modify: `tests/integration/test_village_new_project_regression.py`
- Modify: `apps/figma-plugin/scripts/build.check.mjs`
- Modify: `packaging/figma-plugin/README.md`

**Interfaces:**
- Consumes: the public plugin HTTP contract and built plugin bundle.
- Produces: an independently reopenable Writer ZIP and proof the distributed plugin invokes the new route without template fields.

- [ ] **Step 1: Write a failing public E2E test**

Upload the tracked neutral PNG selection through the same manifest/resource endpoints used by the plugin, invoke the new Writer endpoint, assert pre-approval download is blocked, inspect all three typed review sections, exercise one server-declared safe adjustment and regenerate, prove the old candidate cannot be approved/downloaded, acknowledge the new generation's warnings, approve the complete current candidate, then download twice. Assert byte equality, exact SHA/size, and reopen via `validate_new_project_archive`. Track called URLs and assert the create path never calls `/v1/projects/from-template`, `/v1/agents/`, or pairing endpoints.

- [ ] **Step 2: Extend the production special-case scanner**

Scan all `src/figma_to_fgui`, `apps/figma-plugin/src`, `apps/web-console/src/figma`, and `rules/default` production files for the fixture-unique village markers already maintained by `tests/support/village_writer_regression.py`. Add injection tests proving a marker in the new API service or Writer panel is caught.

- [ ] **Step 3: Verify packaged plugin content**

Build the plugin and assert `dist/ui.html` contains `生成候选工程`, the three review type labels, `确认并下载 ZIP`, and the new endpoint token, but does not contain the old visible four-step copy or require a template option at startup. Do not hand-edit `dist/ui.html`; regenerate it through the existing build script.

- [ ] **Step 4: Run integration and packaging gates**

Run:

```powershell
python -m pytest tests/integration/test_figma_plugin_new_project_delivery.py tests/integration/test_village_new_project_regression.py -q
npm test
npm run typecheck
npm run build
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration apps/figma-plugin packaging/figma-plugin/README.md
git commit -m "test: verify plugin Writer delivery"
```

---

### Task 7: Full regression and real plugin/FairyGUI acceptance

**Files:**
- Create: `docs/validation/2026-08-20-figma-plugin-writer-acceptance.md`
- Create: `docs/validation/2026-08-20-figma-plugin-writer-transcript.json`
- Create: `docs/validation/evidence/figma-plugin-writer/selection.png`
- Create: `docs/validation/evidence/figma-plugin-writer/running.png`
- Create: `docs/validation/evidence/figma-plugin-writer/review-image.png`
- Create: `docs/validation/evidence/figma-plugin-writer/review-component.png`
- Create: `docs/validation/evidence/figma-plugin-writer/review-package.png`
- Create: `docs/validation/evidence/figma-plugin-writer/review-decision.png`
- Create: `docs/validation/evidence/figma-plugin-writer/ready.png`
- Modify: `.claude/memory/wiki.md`
- Modify: `.claude/memory/learnings.md` only if a new reusable lesson is discovered

**Interfaces:**
- Consumes: the packaged development plugin, a neutral generatable Figma frame, and FairyGUI Editor 6.1.4.
- Produces: auditable screenshots/transcript and final test results; screenshots are real application captures, not browser evidence cards.

- [ ] **Step 1: Run complete automated verification**

Run:

```powershell
python -m pytest -q --basetemp C:\pt-plugin-writer
ruff check .
mypy src
npm test --prefix apps/figma-plugin
npm run typecheck --prefix apps/figma-plugin
npm test --prefix apps/web-console
npm run typecheck --prefix apps/web-console
npm run build --prefix apps/figma-plugin
git diff --check
```

Record exact pass/skip/warning counts. Any unrelated failure must be reproduced and documented; do not claim full green when it is not green.

- [ ] **Step 2: Execute the real Figma plugin flow**

Load the built development plugin, select a neutral frame, verify the selection card, enter a project name, click `生成候选工程`, and observe running state. Inspect one image review, one component/interface review, the package/resource review, and unified checks; verify any structured summary is not labeled as rendered. Confirm pre-approval download is unavailable, approve the whole candidate, then verify download plus `再次下载`. Capture selection, running, each review type, unified decision, and ready states at native plugin scale with no private desktop content.

- [ ] **Step 3: Validate the downloaded artifact**

Record filename, byte size, and SHA-256. Reopen it with the production archive validator. Open the extracted `.fairy` project in FairyGUI Editor 6.1.4, save, close, reopen, save, close, and compare the declared file hashes before/after.

- [ ] **Step 4: Write strict transcript and report**

The JSON transcript must contain exact application versions, code commit, timestamps with timezone, selection label, route mode `new-project-writer`, candidate ID, three review-type observations, evidence kinds, unified checks, whole-candidate decision, proof download was blocked before approval, download facts, archive validation, Editor rounds, observed modal state, screenshot paths/hashes, and any unsupported capture API. The Markdown report must link every evidence file relatively and make no visual or accessibility claim that was not directly observed.

- [ ] **Step 5: Update durable memory**

Update `wiki.md` with the actual plugin availability, route, exact test results, and limited real-GUI evidence. Append to `learnings.md` only for a genuinely new reusable lesson; otherwise state in the task report that there is no new learning. Keep Web Console and Project Binding scope explicit.

- [ ] **Step 6: Commit and request final review**

```bash
git add docs/validation .claude/memory/wiki.md .claude/memory/learnings.md
git commit -m "test: accept Figma plugin Writer workflow"
```

Request a strict review from the design-spec base through HEAD. The branch is complete only when both Standards and Spec verdicts pass with no Critical or Important findings.
