# Live Figma Selection Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixture Figma input with a paired TypeScript Figma plugin that sends the current desktop/browser selection to the internal HTTPS server and continues through the existing ZIP, review, approval, and Windows Agent workflow.

**Architecture:** The Web Console creates single-use pairing codes; the plugin exchanges one for a scoped revocable credential. Selection manifests and declared resources upload through an atomic session into immutable storage, then feed the existing normalization and conversion pipeline. The plugin, server, and Web Console remain independently testable and share explicit versioned contracts.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLite, standard-library HMAC/secrets, Pillow, lxml, TypeScript, Figma Plugin API, esbuild, Vitest, React/Vite Web Console, Playwright.

## Global Constraints

- Support both Windows desktop Figma and browser Figma with the same plugin code.
- Production plugin networking permits exactly one configured company-internal HTTPS origin and one explicit organization-approved plugin ID; reject HTTP, wildcard origins, and missing IDs.
- The plugin UI runs from that company origin so API calls are same-origin; do not enable wildcard CORS for a bundled null-origin iframe.
- Pairing codes are six digits, expire after ten minutes, and can be exchanged once.
- Plugin credentials are opaque, revocable, scope-limited, and stored server-side only as keyed digests.
- Limits are 20 selected top-level nodes, 5,000 total nodes, 200 MB per committed session, and 25 MB per resource.
- Font files and external URLs are never uploaded or fetched.
- Normal UI/logs never expose pairing secrets, credentials, raw node IDs, hashes, paths, XML, resource IDs, or rule evidence.
- Selection upload is transactional and idempotent; incomplete sessions never create usable selections.
- Preserve the current whole-update approval, immutable project versions, exact Agent matching, pre-write checks, backup, rollback, and stale-write behavior.
- Fixture job creation remains test/development-only and must not appear in the production Web Console.
- Add no UI component library, router, state manager, CSS framework, or server ORM.
- Use strict RED -> GREEN -> REFACTOR for each behavior task and run independent review after each task.

---

### Task 1: Pairing Codes and Scoped Plugin Credentials

**Files:**
- Create: `src/figma_to_fgui/figma_pairing.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/api.py`
- Create: `tests/unit/test_figma_pairing.py`
- Create: `tests/unit/test_figma_pairing_api.py`

**Interfaces:**
- Produces `PairingStore(db_path: Path, secret: bytes, clock: Callable[[], datetime])`.
- Produces `PairingCodeView(version=1, code, expires_at)`, `PairingExchange(version=1, code, device_name)`, `PluginCredentialView(version=1, credential, device)`, and designer-safe `FigmaDeviceView`.
- Produces `PluginPrincipal(device_id: str)` and `authenticate_plugin(authorization: str | None) -> PluginPrincipal` inside the app boundary.
- Adds `POST /v1/figma/pairings`, `POST /v1/figma/pairings/exchange`, `GET /v1/figma/devices`, and `DELETE /v1/figma/devices/{device_id}`.

- [ ] **Step 1: Write failing store tests for one-time and expiring codes**

```python
def test_pairing_code_is_single_use_and_expires(tmp_path: Path, clock: FakeClock) -> None:
    store = PairingStore(tmp_path / "pairing.db", b"s" * 32, clock)
    store.initialize()
    issued = store.create_code()
    first = store.exchange(issued.code, "Figma 浏览器")
    assert first.credential
    with pytest.raises(PairingError, match="pairing_code_invalid"):
        store.exchange(issued.code, "重复设备")
    expired = store.create_code()
    clock.advance(minutes=11)
    with pytest.raises(PairingError, match="pairing_code_expired"):
        store.exchange(expired.code, "过期设备")
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_figma_pairing.py::test_pairing_code_is_single_use_and_expires -v`

Expected: FAIL because `figma_pairing` does not exist.

- [ ] **Step 3: Implement the minimal SQLite pairing state machine**

Use `secrets.randbelow(1_000_000)` formatted with six digits. Persist only a keyed digest:

```python
def _digest(secret: bytes, purpose: bytes, value: str) -> str:
    return hmac.new(secret, purpose + b"\0" + value.encode("utf-8"), hashlib.sha256).hexdigest()
```

Store code digest, expiry, consumed timestamp, device ID/name, credential digest, created/revoked timestamps, and bounded failed-attempt counters. Exchange must mark the code consumed and insert the device in one SQLite transaction. Use constant-time digest comparison where application comparison occurs.

- [ ] **Step 4: Add failing tests for credential scope, revoke, digest disclosure, and rate limit**

Assert raw codes/credentials are absent from SQLite after their required transition, five invalid exchanges in the configured window return `pairing_rate_limited`, revoked credentials fail, and device views contain no credential fields.

- [ ] **Step 5: Add the pairing API with stable Chinese designer errors**

`create_app` receives a required production `plugin_secret: bytes | None`; development tests may provide a deterministic secret. `Authorization: Bearer <credential>` authenticates plugin-only routes. Pairing creation/device management stay on the Web Console boundary. Return safe error codes: `pairing_code_invalid`, `pairing_code_expired`, `pairing_rate_limited`, `plugin_credential_invalid`, and `plugin_credential_revoked`.

- [ ] **Step 6: Verify and commit Task 1**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest tests/unit/test_figma_pairing.py tests/unit/test_figma_pairing_api.py -v
.\.venv\Scripts\python -m ruff check src/figma_to_fgui/figma_pairing.py src/figma_to_fgui/service_contracts.py src/figma_to_fgui/api.py tests/unit/test_figma_pairing.py tests/unit/test_figma_pairing_api.py
.\.venv\Scripts\python -m mypy src
git diff --check
git add src/figma_to_fgui/figma_pairing.py src/figma_to_fgui/service_contracts.py src/figma_to_fgui/api.py tests/unit/test_figma_pairing.py tests/unit/test_figma_pairing_api.py
git commit -m "feat: pair Figma plugin devices"
```

---

### Task 2: Transactional Immutable Selection Upload

**Files:**
- Create: `src/figma_to_fgui/figma_selection.py`
- Create: `src/figma_to_fgui/selection_store.py`
- Modify: `src/figma_to_fgui/api.py`
- Create: `tests/unit/test_figma_selection.py`
- Create: `tests/unit/test_selection_store.py`
- Create: `tests/unit/test_figma_selection_api.py`

**Interfaces:**
- Produces immutable Pydantic models `SelectionManifest`, `SelectionNode`, `SelectionResource`, `SelectionWarning`, `SelectionVersion`, and designer-safe `SelectionView`.
- Produces `SelectionLimits(max_top_level=20, max_nodes=5000, max_session_bytes=200*1024*1024, max_resource_bytes=25*1024*1024)`.
- Produces `SelectionStore(data_dir: Path)` methods `create_upload(device_id, idempotency_key)`, `put_manifest`, `put_resource`, `commit`, `get`, `artifact_path`, `expire_uploads`.
- Adds plugin-authenticated upload/session endpoints and designer-safe `GET /v1/figma/selections/{selection_id}`.

- [ ] **Step 1: Write failing manifest-boundary tests**

```python
def test_manifest_rejects_more_than_twenty_top_level_nodes() -> None:
    manifest = selection_manifest(top_level=21)
    with pytest.raises(SelectionError) as caught:
        validate_selection_manifest(manifest, SelectionLimits())
    assert caught.value.code == "selection_too_large"

def test_manifest_rejects_external_urls_and_raw_font_bytes() -> None:
    manifest = selection_manifest(style={"imageUrl": "https://example.invalid/a.png"})
    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        validate_selection_manifest(manifest, SelectionLimits())
```

- [ ] **Step 2: Run tests and verify RED, then implement bounded recursive validation**

Count nodes iteratively to avoid recursion exhaustion. Bound JSON depth, individual string length, property count, declared resource count, and total declared size. Reject unknown model fields with `extra="forbid"`.

- [ ] **Step 3: Write failing session transaction tests**

Cover `created -> manifest_received -> resources_pending -> committed`, missing/duplicate/undeclared resource, MIME mismatch, 25 MB resource limit, 200 MB session limit, expired cleanup, device ownership, concurrent same-idempotency commit, immutable publication, and no partial usable metadata.

- [ ] **Step 4: Implement content validation and immutable publication**

Raster content must pass `encode_webp_preview`; SVG must parse with `lxml` while rejecting scripts, event attributes, `foreignObject`, external URL attributes, entities, and active content. Persist temporary uploads only below `data_dir / "figma-uploads"`; publish committed manifests/resources below a content-addressed `data_dir / "selections" / <fingerprint>` directory using atomic rename/convergence patterns from `ProjectStore`.

- [ ] **Step 5: Add upload APIs with bounded streaming**

Use 64 KiB chunks, enforce actual bytes independently of declarations, authenticate every write with `PluginPrincipal`, and ensure a failure in close/unlink/tree cleanup cannot skip later cleanup. Normal commit response contains only `version`, `selection_id`, `display_name`, top-level summaries, bounded preview URLs, and designer warnings.

- [ ] **Step 6: Verify and commit Task 2**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest tests/unit/test_figma_selection.py tests/unit/test_selection_store.py tests/unit/test_figma_selection_api.py -v
.\.venv\Scripts\python -m ruff check src/figma_to_fgui/figma_selection.py src/figma_to_fgui/selection_store.py src/figma_to_fgui/api.py tests/unit/test_figma_selection.py tests/unit/test_selection_store.py tests/unit/test_figma_selection_api.py
.\.venv\Scripts\python -m mypy src
git diff --check
git add src/figma_to_fgui/figma_selection.py src/figma_to_fgui/selection_store.py src/figma_to_fgui/api.py tests/unit/test_figma_selection.py tests/unit/test_selection_store.py tests/unit/test_figma_selection_api.py
git commit -m "feat: store immutable Figma selections"
```

---

### Task 3: Selection-Backed Conversion and Production Job API

**Files:**
- Modify: `src/figma_to_fgui/normalize.py`
- Modify: `src/figma_to_fgui/pipeline.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/api.py`
- Modify: `src/figma_to_fgui/job_store.py`
- Create: `tests/unit/test_live_selection_normalize.py`
- Create: `tests/integration/test_selection_job_flow.py`

**Interfaces:**
- Produces `selection_document(manifest: SelectionManifest, resources_root: Path) -> dict[str, object]`.
- Produces `convert_document(raw: dict[str, object], project_root: Path, package_name: str, staging_root: Path, classification_rules: Path) -> ChangeSet` while preserving `convert(ConversionRequest)` for CLI/tests.
- Replaces production request fields with `SelectionProjectJobCreate(version=1, selection_id, project_id, package_name)`.
- Adds `POST /v1/figma/selections/{selection_id}/projects/{project_id}/jobs`.

- [ ] **Step 1: Write failing normalization contract tests**

Build a live manifest containing frames, text, auto-layout, component properties, an instance, raster fill, and SVG declaration. Assert deterministic `NormalizedNode` order/bounds/text/properties/raw style and no server dependency on raw Figma REST fixture shape.

- [ ] **Step 2: Run RED and implement the adapter**

Keep raw upload IDs internal. Map resource keys to immutable selection-relative asset references accepted by the existing generation path. Do not add a parallel classifier or generator.

- [ ] **Step 3: Refactor the pipeline behind a document entry point with characterization tests**

```python
def convert_document(raw: dict[str, object], request: DocumentConversionRequest) -> ChangeSet:
    roots, normalization_diagnostics = normalize_document(raw)
    # existing index -> classify -> generate -> validate -> changeset sequence
```

Assert existing fixture conversion remains byte-identical before switching the production API.

- [ ] **Step 4: Write failing real selection-to-job integration test**

Pair, upload and commit a live selection, upload a FairyGUI ZIP, create a selection-backed job, read the normal designer preview, approve, and apply through the Agent to a copy. Assert the selected live text/name affects generated output and changing `tests/fixtures/figma/simple-frame.json` cannot affect the job.

- [ ] **Step 5: Implement the production route and gate fixture routes**

Add `allow_fixture_jobs: bool = False` to `create_app`. Fixture endpoints return 404 unless explicitly enabled by tests/development CLI. Web Console job creation uses only `selection_id`. Persist selection identity/fingerprint internally with the job without exposing it through `JobSummary`.

- [ ] **Step 6: Verify and commit Task 3**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest tests/unit/test_live_selection_normalize.py tests/integration/test_selection_job_flow.py tests/golden -v
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m mypy src
git diff --check
git add src/figma_to_fgui/normalize.py src/figma_to_fgui/pipeline.py src/figma_to_fgui/service_contracts.py src/figma_to_fgui/api.py src/figma_to_fgui/job_store.py tests/unit/test_live_selection_normalize.py tests/integration/test_selection_job_flow.py
git commit -m "feat: convert live Figma selections"
```

---

### Task 4: Figma Plugin Foundation and Pairing UI

**Files:**
- Create: `apps/figma-plugin/package.json`
- Create: `apps/figma-plugin/pnpm-lock.yaml`
- Create: `apps/figma-plugin/tsconfig.json`
- Create: `apps/figma-plugin/manifest.template.json`
- Create: `apps/figma-plugin/scripts/build-manifest.mjs`
- Create: `apps/figma-plugin/scripts/build.mjs`
- Create: `apps/figma-plugin/src/contracts.ts`
- Create: `apps/figma-plugin/src/pairing.ts`
- Create: `apps/figma-plugin/src/code.ts`
- Create: `apps/figma-plugin/src/ui.html`
- Create: `apps/figma-plugin/src/bootstrap.ts`
- Create: `apps/figma-plugin/src/pairing.test.ts`
- Create: `apps/web-console/src/figma/PluginFramePage.tsx`
- Create: `apps/web-console/src/figma/PluginFramePage.test.tsx`

**Interfaces:**
- Produces `PluginConfig(serverOrigin: string)`, `PairingClient.exchange(code, deviceName)`, `CredentialStore` backed by `figma.clientStorage`, and typed main/UI message unions.
- Produces built `apps/figma-plugin/dist/manifest.json`, `code.js`, and a bootstrap `ui.html` that navigates to the configured company-origin plugin frame.
- Produces `/figma-plugin` as the non-null-origin UI; sensitive messages require the exact configured plugin ID, company origin, and `https://www.figma.com` target.

- [ ] **Step 1: Scaffold the smallest plugin build/test boundary**

Use exact direct dev dependencies `typescript`, `esbuild`, `vitest`, and `@figma/plugin-typings`. Scripts are `build`, `test`, and `typecheck`. No React or runtime package dependency.

- [ ] **Step 2: Write failing manifest-origin tests**

```ts
it("accepts exactly one HTTPS company origin and plugin id", () => {
  expect(buildManifest("https://fgui.corp.example", "123456789").networkAccess.allowedDomains)
    .toEqual(["https://fgui.corp.example"])
})

it.each(["http://fgui.corp.example", "*", "https://one.example,https://two.example"])(
  "rejects unsafe origin %s", origin => expect(() => buildManifest(origin, "123456789")).toThrow()
)
```

- [ ] **Step 3: Implement and verify the manifest/build scripts**

Set `id` to the explicitly configured organization plugin ID, `editorType: ["figma"]`, `documentAccess: "dynamic-page"`, and the one allowed domain. Inline only a non-sensitive redirect bootstrap into `ui.html`; the actual UI loads from `${serverOrigin}/figma-plugin`.

- [ ] **Step 4: Write pairing-state tests with a Figma clientStorage fake**

Cover six-digit local validation, success, expiry/error mapping, saved credential restore, revoke/unpair, and plugin principal status. Add message-boundary tests proving company-origin targeting from main code and exact `pluginId` plus `https://www.figma.com` targeting from the hosted iframe. Server messages must map by safe local code, never render raw `detail.message`.

- [ ] **Step 5: Implement the pairing UI**

Serve the lightweight UI from the Web Console build using semantic label/input/button/status markup, visible focus, 44px targets, system/CJK fonts, restrained blue/neutral styling, loading/double-submit prevention, and explicit paired/revoked/retry states. Main code owns the persisted credential; it sends it only to the exact company-origin iframe for same-origin API calls and never through wildcard messaging.

- [ ] **Step 6: Verify and commit Task 4**

```powershell
pnpm --dir apps/figma-plugin install --frozen-lockfile
pnpm --dir apps/figma-plugin test -- --run
pnpm --dir apps/figma-plugin typecheck
$env:FGUI_SERVER_ORIGIN='https://fgui.corp.example'; $env:FIGMA_PLUGIN_ID='123456789'; pnpm --dir apps/figma-plugin build
git diff --check
git add apps/figma-plugin
git commit -m "feat: pair the Figma selection plugin"
```

---

### Task 5: Plugin Selection Export, Assets, and Transactional Upload

**Files:**
- Create: `apps/figma-plugin/src/selection.ts`
- Create: `apps/figma-plugin/src/assets.ts`
- Create: `apps/figma-plugin/src/upload.ts`
- Create: `apps/figma-plugin/src/selection.test.ts`
- Create: `apps/figma-plugin/src/assets.test.ts`
- Create: `apps/figma-plugin/src/upload.test.ts`
- Modify: `apps/figma-plugin/src/code.ts`
- Modify: `apps/web-console/src/figma/PluginFramePage.tsx`

**Interfaces:**
- Produces `serializeSelection(nodes: readonly SceneNode[]) -> SelectionManifest`.
- Produces `preflightSelection(nodes) -> SelectionPreflight` with counts, estimated bytes, warnings, and `sendable`.
- Produces `exportDeclaredAssets(manifest, lookup) -> AsyncIterable<ExportedResource>` with bounded concurrency.
- Produces `SelectionUploader.send(manifest, resources, idempotencyKey, onProgress) -> SelectionView`.

- [ ] **Step 1: Write failing serializer tests using a typed Figma API fake**

Cover deterministic multiple selection, invisible/locked nodes, frame/group/component/instance/text mapping, auto-layout/constraints, component properties, font metadata without font bytes, unsupported video/prototype warnings, 20/5,000 limits, and no nodes outside `figma.currentPage.selection`.

- [ ] **Step 2: Implement iterative selection walking and preflight**

Use a stack rather than unbounded recursion. Generate upload-local opaque resource keys from deterministic selection order, not raw node IDs. Do not read the entire Figma document.

- [ ] **Step 3: Write failing asset-export tests**

Assert SVG uses `exportAsync({format: "SVG"})`, raster uses PNG bytes, duplicate asset references export once, single/total size estimates block send, concurrency never exceeds four, and one export failure reports the selected display label without producing a committed selection.

- [ ] **Step 4: Implement bounded export and upload session orchestration**

The uploader creates the session, puts the manifest, streams resources sequentially or with at most four requests, commits with the original idempotency key, and retries only idempotent steps. It never logs bearer credentials or raw node IDs.

- [ ] **Step 5: Implement designer send/open flow**

Show selection name/count/asset estimate/warnings before send. After commit, the hosted company-origin iframe attempts `window.open(url, "_blank", "noopener")`; always retain **打开任务** and **复制链接** actions. Popup failure is not an upload failure. Credentials, manifests, and resource bytes may cross main/UI only with the exact origin/plugin-ID message contract.

- [ ] **Step 6: Verify and commit Task 5**

```powershell
pnpm --dir apps/figma-plugin test -- --run
pnpm --dir apps/figma-plugin typecheck
$env:FGUI_SERVER_ORIGIN='https://fgui.corp.example'; $env:FIGMA_PLUGIN_ID='123456789'; pnpm --dir apps/figma-plugin build
git diff --check
git add apps/figma-plugin/src
git commit -m "feat: upload the current Figma selection"
```

---

### Task 6: Web Console Pairing and Live Selection Continuation

**Files:**
- Modify: `apps/web-console/src/api.ts`
- Create: `apps/web-console/src/figma/PairingPanel.tsx`
- Create: `apps/web-console/src/figma/PairingPanel.test.tsx`
- Create: `apps/web-console/src/figma/SelectionSummary.tsx`
- Create: `apps/web-console/src/figma/SelectionSummary.test.tsx`
- Modify: `apps/web-console/src/upload/UploadPage.tsx`
- Modify: `apps/web-console/src/upload/UploadPage.test.tsx`
- Modify: `apps/web-console/src/App.tsx`
- Modify: `apps/web-console/src/styles.css`

**Interfaces:**
- Consumes Task 1 pairing/device APIs, Task 2 selection summary, and Task 3 selection-backed job creation.
- Produces a guided state model: `pairing -> waiting_for_selection -> selection_received -> project_uploaded -> ready_for_review`.

- [ ] **Step 1: Write failing typed API-client tests**

Verify pairing creation/device list/revoke, selection polling/read, selection-backed job request without `fixture_name`, and local safe error mapping that ignores raw server messages.

- [ ] **Step 2: Write failing UI state tests**

Cover six-digit code with expiry countdown, regenerate/cancel, paired device list/revoke, waiting empty state, selection arrival, names/thumbnails/warnings, popup recovery URL, retained Selection across ZIP retry, package choice, and live job navigation.

- [ ] **Step 3: Implement the minimal guided page**

Keep one page and the existing pathname routing; do not add a router/state framework. Poll only while waiting and stop on selection, unmount, or error. Use accessible status announcements, visible labels/focus, disabled async actions, no horizontal overflow, and `prefers-reduced-motion`.

- [ ] **Step 4: Remove production fixture language and calls**

Delete `fixture_name: "simple-frame.json"` from the Web API client. Normal UI copy must not say example/fixture design. Development fixture behavior remains reachable only from Python tests/explicit development mode.

- [ ] **Step 5: Verify and commit Task 6**

```powershell
$env:CI='true'
pnpm --dir apps/web-console test -- --run
Push-Location apps/web-console; .\node_modules\.bin\tsc.cmd --noEmit; Pop-Location
pnpm --dir apps/web-console build
git diff --check
git add apps/web-console/src
git commit -m "feat: continue live Figma selections in the console"
```

---

### Task 7: Live Plugin-to-Agent Integration and Browser E2E

**Files:**
- Create: `tests/helpers/figma_selections.py`
- Create: `tests/integration/test_live_figma_web_agent_loop.py`
- Create: `apps/figma-plugin/test/plugin-harness.ts`
- Create: `apps/figma-plugin/test/plugin-harness.test.ts`
- Modify: `apps/web-console/e2e/global-setup.ts`
- Create: `apps/web-console/e2e/live-figma-selection.spec.ts`
- Modify: `apps/web-console/playwright.config.ts`

**Interfaces:**
- Produces a reusable plugin harness that runs the real serializer/uploader against a Figma API fake and real FastAPI server.
- Extends the nonce-authenticated isolated Playwright server with deterministic plugin pairing/upload helpers, without browser network mocking.

- [ ] **Step 1: Write the failing Python end-to-end integration**

Pair a device, upload a real selection manifest plus PNG/SVG assets, commit it, upload a real FairyGUI ZIP, create a live selection job, inspect normal preview, approve, apply with the Agent, verify output and backup, then edit a local affected file and prove a second live selection job returns `local_project_changed` without another backup.

- [ ] **Step 2: Run RED and implement only missing composition seams**

Do not bypass HTTP contracts by writing directly to stores. Assert fixture JSON mutation cannot affect live job output and normal responses contain none of the forbidden fields.

- [ ] **Step 3: Add plugin harness contract tests**

Run real plugin selection serialization and upload orchestration with fake `figma.currentPage.selection`, `figma.clientStorage`, `exportAsync`, and UI messaging. Assert desktop/browser use the same core path and only link-opening behavior varies by iframe capability.

- [ ] **Step 4: Add real Chromium Web E2E**

Start at `/`, create a pairing code, simulate the real plugin HTTP exchange/upload contract without route interception, observe selection arrival, upload ZIP, choose package, create live review, expand/collapse advanced details, approve, and assert the concrete waiting-for-Agent state. At 1440px verify the review columns; at 390px verify view-only controls and no horizontal overflow.

- [ ] **Step 5: Preserve isolated E2E startup guarantees**

Each run uses OS-temp data, an exact per-run health nonce, child liveness checks, no reusable server, cleanup on failure/success, no fixed-service false readiness, and no worktree test artifacts.

- [ ] **Step 6: Verify and commit Task 7**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest tests/integration/test_live_figma_web_agent_loop.py -v
pnpm --dir apps/figma-plugin test -- --run
$env:CI='true'; pnpm --dir apps/web-console test -- --run
Push-Location apps/web-console; .\node_modules\.bin\playwright.cmd test; Pop-Location
git diff --check
git status --short --untracked-files=all
git add tests/helpers/figma_selections.py tests/integration/test_live_figma_web_agent_loop.py apps/figma-plugin/test apps/web-console/e2e apps/web-console/playwright.config.ts
git commit -m "test: verify the live Figma selection workflow"
```

---

### Task 8: Internal HTTPS Deployment, Team Install, and Final Acceptance

**Files:**
- Modify: `src/figma_to_fgui/cli.py`
- Modify: `README.md`
- Create: `docs/deployment/internal-https.md`
- Create: `docs/acceptance/figma-plugin-checklist.md`
- Modify: `.gitignore`
- Modify: `progress.md`

**Interfaces:**
- Produces production CLI configuration for `--public-origin`, `--plugin-secret-file`, proxy-aware HTTPS origin validation, persistent data location, and fixture-job disablement.
- Produces company deployment and manual desktop/browser Figma acceptance instructions.

- [ ] **Step 1: Write failing CLI configuration tests**

Reject missing/short secret, HTTP/wildcard/multiple origins, mismatched configured origin, production fixture jobs, and missing Web build/plugin manifest. Assert errors are concise Typer parameter errors without tracebacks or secret/path disclosure.

- [ ] **Step 2: Implement minimal production configuration**

Read the secret from an explicitly named file with restrictive deployment guidance; do not accept it as a command-line literal. Keep localhost development behavior explicit and separate from `--production`. Emit restrictive CORS headers for the single origin and document reverse-proxy body/time limits matching application limits.

- [ ] **Step 3: Update team-copyable setup and recovery docs**

Document plugin build/import/publishing for desktop and browser Figma, internal HTTPS/TLS/DNS, pairing/revoke, selection limits, ZIP/review/Agent flow, backups, stale local changes, popup recovery, log redaction, persistence/cleanup, and rollback. Do not claim SSO, signed installer, automatic FairyGUI refresh, or public deployment.

- [ ] **Step 4: Create the manual acceptance checklist**

Require one desktop Figma and one browser Figma run: fresh pair, select frames/components, preflight, send, automatic/open-task recovery, selection thumbnails, ZIP/package/review/approval, Agent apply, revoke, and rejected subsequent upload. Record tester/date/build/origin and pass/fail only; never record pairing codes or credentials.

- [ ] **Step 5: Run product, security, and ponytail reviews**

Walk the flow as a daily UX designer. Fix immediate terminology, retry, focus, and disclosure issues. Review pairing/upload endpoints for permission and resource exhaustion. Inspect the complete diff for unused dependencies, duplicate serializers/models, speculative abstractions, and native/standard-library replacements; apply safe reductions and rerun affected tests.

- [ ] **Step 6: Run complete verification**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy src
$env:CI='true'
pnpm --dir apps/figma-plugin test -- --run
pnpm --dir apps/figma-plugin typecheck
$env:FGUI_SERVER_ORIGIN='https://fgui.corp.example'; $env:FIGMA_PLUGIN_ID='123456789'; pnpm --dir apps/figma-plugin build
pnpm --dir apps/web-console test -- --run
Push-Location apps/web-console; .\node_modules\.bin\tsc.cmd --noEmit; .\node_modules\.bin\playwright.cmd test; Pop-Location
pnpm --dir apps/web-console build
.\.venv\Scripts\python -m figma_to_fgui.cli --help
git diff --check
git status --short --untracked-files=all
```

Expected: all Python/plugin/Web tests, lint, typing, builds, Chromium E2E, CLI, and Git hygiene checks pass; the only allowed skip remains the existing Windows symlink-privilege test when the OS denies symlink creation.

- [ ] **Step 7: Commit Task 8**

```powershell
git add src/figma_to_fgui/cli.py README.md docs/deployment/internal-https.md docs/acceptance/figma-plugin-checklist.md .gitignore progress.md
git commit -m "docs: ship the internal live Figma workflow"
```

## Plan Self-Review

- Spec coverage: desktop/browser plugin, pairing/revoke, scoped credentials, transactional uploads, limits, normalized node/resource contracts, live conversion, Web continuation, isolated integration/E2E, internal HTTPS deployment, and manual acceptance each map to a task.
- Scope: SSO, public deployment, signed installer/tray, automatic FairyGUI refresh, external font/URL fetching, multiplayer selection, and per-file approval remain explicitly deferred.
- Type consistency: `SelectionManifest`, `SelectionVersion`, `SelectionView`, `SelectionStore`, `SelectionProjectJobCreate`, `PairingStore`, and `PluginPrincipal` retain the same names across producer/consumer tasks.
- Dependency check: server behavior uses current runtime dependencies plus the standard library; the plugin adds only TypeScript, esbuild, Vitest, and Figma typings as direct development dependencies.
- Placeholder scan: no TBD/TODO, undefined neighboring interface, or instruction to add unspecified validation/error handling remains.
