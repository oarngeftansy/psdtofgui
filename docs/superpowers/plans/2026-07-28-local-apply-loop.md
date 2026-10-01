# Local Apply Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a fixture-backed FastAPI service and Windows CLI Agent that preview, approve, safely apply, verify, and roll back an FGUI changeset.

**Architecture:** Extend the existing Python package with versioned protocol contracts, a standard-library SQLite repository, a FastAPI application, and a filesystem transaction used by an HTTP-polling Agent. Conversion remains synchronous and artifacts remain local; tests inject all paths and exercise the full loop without touching a real project.

**Tech Stack:** Python 3.11, Pydantic 2, FastAPI, HTTPX, Typer, SQLite, pathlib, pytest, Ruff, mypy

## Global Constraints

- Windows is the only supported client platform for this slice.
- The server may send only relative paths and file payloads, never local commands or absolute project paths.
- Apply supports create and replace only; delete is rejected.
- Every write requires path containment, pre-write hash checks, backup, staged validation, atomic replacement, and final hash verification.
- Existing conversion behavior and its 18 tests must remain unchanged.
- Use SQLite and local artifact files; do not add PostgreSQL, MinIO, Redis, authentication, live Figma access, Web UI, or a tray application.
- Use TDD for every behavior and keep dependencies to FastAPI, HTTPX, and optional Uvicorn.

---

## File Map

- `src/figma_to_fgui/service_contracts.py`: versioned HTTP and Agent protocol models.
- `src/figma_to_fgui/job_store.py`: SQLite job, Agent, binding, assignment, and result persistence.
- `src/figma_to_fgui/artifacts.py`: content-addressed changeset payload storage.
- `src/figma_to_fgui/api.py`: FastAPI routes and state-transition gates.
- `src/figma_to_fgui/apply.py`: safe project filesystem transaction and rollback.
- `src/figma_to_fgui/agent.py`: local binding config, HTTP polling client, and Agent commands.
- `src/figma_to_fgui/cli.py`: mounts `serve` and `agent` commands.
- `tests/unit/test_service_contracts.py`: protocol serialization and validation.
- `tests/unit/test_job_store.py`: persistent state and idempotency.
- `tests/unit/test_artifacts.py`: payload integrity and containment.
- `tests/unit/test_api.py`: API workflow and ownership gates.
- `tests/unit/test_apply.py`: safe write transaction and rollback.
- `tests/unit/test_agent.py`: polling and report behavior.
- `tests/integration/test_local_apply_loop.py`: complete in-process workflow.

### Task 1: Versioned Service Contracts and Artifact Payloads

**Files:**
- Create: `src/figma_to_fgui/service_contracts.py`
- Create: `src/figma_to_fgui/artifacts.py`
- Test: `tests/unit/test_service_contracts.py`
- Test: `tests/unit/test_artifacts.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `JobStatus`, `FileOperation`, `ChangeFile`, `ChangeBundle`, `JobCreate`, `JobView`, `AgentRegistration`, `ProjectBinding`, `ApplyResult`.
- Produces: `ArtifactStore.put(bundle) -> str` and `ArtifactStore.get(digest) -> ChangeBundle`.

- [ ] **Step 1: Add failing contract tests**

```python
def test_change_file_rejects_delete_and_unsafe_paths() -> None:
    with pytest.raises(ValidationError):
        ChangeFile(operation="delete", relative_path="../outside.xml", after_sha256="0" * 64, content_b64="")

def test_bundle_round_trip_is_versioned() -> None:
    bundle = ChangeBundle(job_id="job-1", project_id="project-1", files=())
    assert ChangeBundle.model_validate_json(bundle.model_dump_json()) == bundle
    assert bundle.version == 1
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_service_contracts.py -v`

Expected: collection fails because `figma_to_fgui.service_contracts` does not exist.

- [ ] **Step 3: Implement minimal immutable contracts**

Use `FrozenModel`, `safe_relative_path`, `StrEnum`, and Pydantic validators. `ChangeFile` has `operation: Literal["create", "replace"]`, `relative_path`, `before_sha256: str | None`, `after_sha256`, and base64 content. Require `before_sha256` for replace and forbid it for create. `JobStatus` contains `created`, `ready_for_review`, `conversion_failed`, `approved`, `applying`, `applied`, `failed`, and `rejected`.

- [ ] **Step 4: Add failing artifact-store tests**

```python
def test_artifact_store_detects_tampering(tmp_path: Path, bundle: ChangeBundle) -> None:
    store = ArtifactStore(tmp_path)
    digest = store.put(bundle)
    (tmp_path / f"{digest}.json").write_text("{}", "utf-8")
    with pytest.raises(ArtifactIntegrityError):
        store.get(digest)
```

- [ ] **Step 5: Run artifact test and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_artifacts.py -v`

Expected: collection fails because `figma_to_fgui.artifacts` does not exist.

- [ ] **Step 6: Implement the content-addressed store**

Serialize with sorted compact JSON, name the file by SHA-256, write through a temporary sibling with `os.replace`, and recompute the digest on every read. Do not accept caller-supplied paths.

- [ ] **Step 7: Add runtime and test dependencies**

Add `fastapi>=0.115,<1` and `httpx>=0.27,<1` to project dependencies and `uvicorn>=0.30,<1` to a `server` optional dependency. Do not add an ORM, migration tool, or filesystem library.

- [ ] **Step 8: Verify Task 1**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_service_contracts.py tests/unit/test_artifacts.py -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add pyproject.toml src/figma_to_fgui/service_contracts.py src/figma_to_fgui/artifacts.py tests/unit/test_service_contracts.py tests/unit/test_artifacts.py
git commit -m "feat: add local service contracts"
```

### Task 2: SQLite Job State Repository

**Files:**
- Create: `src/figma_to_fgui/job_store.py`
- Test: `tests/unit/test_job_store.py`

**Interfaces:**
- Consumes: protocol models from Task 1.
- Produces: `JobStore.initialize()`, `register_agent()`, `bind_project()`, `create_job()`, `get_job()`, `approve_job()`, `claim_next()`, and `record_apply_result()`.

- [ ] **Step 1: Write failing state-transition tests**

```python
def test_only_bound_agent_claims_approved_job(store: JobStore) -> None:
    store.register_agent(AgentRegistration(agent_id="agent-a", name="A"))
    store.register_agent(AgentRegistration(agent_id="agent-b", name="B"))
    store.bind_project(ProjectBinding(project_id="project-1", agent_id="agent-a"))
    store.create_job(make_ready_job("job-1", "project-1"))
    store.approve_job("job-1")
    assert store.claim_next("agent-b") is None
    assert store.claim_next("agent-a").status is JobStatus.APPLYING

def test_repeated_terminal_result_is_idempotent(store: JobStore) -> None:
    result = make_apply_result(status="applied")
    store.record_apply_result(result)
    store.record_apply_result(result)
    assert store.get_job(result.job_id).status is JobStatus.APPLIED
```

- [ ] **Step 2: Run and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_job_store.py -v`

Expected: collection fails because `figma_to_fgui.job_store` does not exist.

- [ ] **Step 3: Implement the minimal SQLite schema and transactions**

Use `sqlite3` with four tables: `agents`, `project_bindings`, `jobs`, and `apply_results`. Store complete versioned models as JSON and index only identifiers/status needed for claims. Use `BEGIN IMMEDIATE` in `claim_next` so one approved job can be claimed once. Raise `InvalidTransition`, `NotFound`, or `OwnershipMismatch` with stable error codes.

- [ ] **Step 4: Add approval-gate and duplicate-claim tests**

Cover conversion failures, jobs with `ERROR` diagnostics, repeated approval, and two sequential claims. Approval returns the existing approved job on repetition; a second claim returns no assignment.

- [ ] **Step 5: Verify Task 2**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_job_store.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/figma_to_fgui/job_store.py tests/unit/test_job_store.py
git commit -m "feat: persist local conversion jobs"
```

### Task 3: Fixture-Backed FastAPI Workflow

**Files:**
- Create: `src/figma_to_fgui/api.py`
- Test: `tests/unit/test_api.py`

**Interfaces:**
- Consumes: `convert`, `JobStore`, `ArtifactStore`, Task 1 contracts.
- Produces: `create_app(data_dir: Path, fixtures_root: Path) -> FastAPI`.

- [ ] **Step 1: Write failing API workflow test**

```python
def test_job_can_be_previewed_approved_and_claimed(client: TestClient) -> None:
    client.post("/v1/agents/register", json={"version": 1, "agent_id": "agent-1", "name": "Desk"}).raise_for_status()
    client.post("/v1/projects/bind", json={"version": 1, "project_id": "project-1", "agent_id": "agent-1"}).raise_for_status()
    created = client.post("/v1/jobs", json=fixture_job_request()).json()
    assert created["status"] == "ready_for_review"
    assert client.get(f"/v1/jobs/{created['job_id']}/preview").status_code == 200
    assert client.post(f"/v1/jobs/{created['job_id']}/approve").json()["status"] == "approved"
    assignment = client.get("/v1/agents/agent-1/assignments/next").json()
    assert assignment["status"] == "applying"
```

- [ ] **Step 2: Run and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_api.py -v`

Expected: collection fails because `figma_to_fgui.api` does not exist.

- [ ] **Step 3: Implement `create_app` and routes**

Initialize `JobStore` and `ArtifactStore` inside the app factory. Resolve fixture names only beneath the injected fixture root. For job creation, copy the source fixture to a temporary project/staging area, call `convert`, turn generated files into base64 `ChangeFile` payloads, persist the artifact, then persist `ready_for_review` or `conversion_failed`. Map repository exceptions to structured `409` or `404` responses.

- [ ] **Step 4: Add failure and ownership tests**

Cover unknown fixture, path escape fixture name, approval with an `ERROR`, wrong Agent claim, missing job, idempotent approval, and artifact tampering. Assert no error response exposes an absolute filesystem path.

- [ ] **Step 5: Verify Task 3**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_api.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/figma_to_fgui/api.py tests/unit/test_api.py
git commit -m "feat: expose local conversion API"
```

### Task 4: Safe Apply Transaction

**Files:**
- Create: `src/figma_to_fgui/apply.py`
- Test: `tests/unit/test_apply.py`

**Interfaces:**
- Consumes: `ChangeBundle` and `ChangeFile`.
- Produces: `apply_bundle(project_root: Path, bundle: ChangeBundle, replace_file: Callable = os.replace) -> ApplySummary`.

- [ ] **Step 1: Write failing containment and stale-hash tests**

```python
def test_apply_rejects_stale_replace_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "Sample.xml"
    target.write_text("local edit", "utf-8")
    with pytest.raises(SourceConflict):
        apply_bundle(tmp_path, replace_bundle(before_sha256="0" * 64))
    assert target.read_text("utf-8") == "local edit"

def test_apply_rejects_symlink_escape(tmp_path: Path, outside: Path) -> None:
    make_directory_link(tmp_path / "linked", outside)
    with pytest.raises(UnsafeTarget):
        apply_bundle(tmp_path, create_bundle("linked/file.xml"))
```

- [ ] **Step 2: Run and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_apply.py -v`

Expected: collection fails because `figma_to_fgui.apply` does not exist.

- [ ] **Step 3: Implement preflight, backup, staging, and commit**

Preflight every operation before creating a backup. Resolve parents and reject any target whose resolved path is outside `project_root.resolve()`. Decode base64 with validation, verify declared hashes, parse staged `.xml` with lxml, back up existing files under `.figma-to-fgui/backups/{job_id}`, write temporary siblings, and commit with injected `replace_file`.

- [ ] **Step 4: Write a failing mid-commit rollback test**

```python
def test_mid_commit_failure_restores_every_file(tmp_path: Path) -> None:
    original = snapshot(tmp_path)
    replacer = fail_on_second_replace()
    with pytest.raises(ApplyFailed) as error:
        apply_bundle(tmp_path, two_file_bundle(tmp_path), replace_file=replacer)
    assert snapshot(tmp_path, exclude_backups=True) == original
    assert error.value.rollback_succeeded is True
```

- [ ] **Step 5: Run the rollback test and verify RED**

Expected: FAIL because committed files are not yet restored.

- [ ] **Step 6: Implement complete rollback**

Track committed creates and replaces. On failure, restore replaces from backup and remove committed creates. Preserve backups for inspection. If rollback itself fails, raise `ApplyFailed(rollback_succeeded=False)` containing relative paths only.

- [ ] **Step 7: Verify Task 4**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_apply.py -v`

Expected: PASS, with the symlink test skipped only when Windows link creation is unavailable to the current user.

- [ ] **Step 8: Commit**

```powershell
git add src/figma_to_fgui/apply.py tests/unit/test_apply.py
git commit -m "feat: apply changesets transactionally"
```

### Task 5: Windows Agent CLI and Polling Client

**Files:**
- Create: `src/figma_to_fgui/agent.py`
- Create: `tests/unit/test_agent.py`
- Modify: `src/figma_to_fgui/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: API endpoints, `apply_bundle`, and shared contracts.
- Produces: `AgentConfig.load/save`, `AgentClient.register/bind/poll_once`, and Typer `agent` command group.

- [ ] **Step 1: Write failing config and polling tests**

```python
def test_poll_once_applies_assignment_and_reports_success(tmp_path: Path) -> None:
    transport = httpx.MockTransport(local_service_handler())
    client = AgentClient(config=bound_config(tmp_path), transport=transport)
    result = client.poll_once()
    assert result.status == "applied"
    assert (tmp_path / "Sample.xml").exists()

def test_config_never_serializes_project_path_to_apply_result(tmp_path: Path) -> None:
    result = AgentClient(bound_config(tmp_path), transport=failing_transport()).poll_once()
    assert str(tmp_path) not in result.model_dump_json()
```

- [ ] **Step 2: Run and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_agent.py -v`

Expected: collection fails because `figma_to_fgui.agent` does not exist.

- [ ] **Step 3: Implement config and one-shot polling**

Store agent ID, API URL, and local project bindings in versioned JSON. Use `httpx.Client` with finite connect/read timeouts. `poll_once` claims one assignment, downloads its bundle, checks the local project binding, calls `apply_bundle`, and always attempts to submit a terminal result. Map filesystem exceptions to stable diagnostics without absolute paths.

- [ ] **Step 4: Add Agent CLI commands**

Mount a nested Typer group with `register`, `bind`, `poll --once`, and `run --interval 5`. `run` uses `time.sleep` between polls and catches only expected network/service errors so Ctrl+C exits normally. Add `serve --data-dir --fixtures-root --host 127.0.0.1 --port 8765`, importing Uvicorn only inside the command.

- [ ] **Step 5: Verify Agent and CLI tests**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/unit/test_agent.py tests/unit/test_cli.py -v`

Expected: PASS and CLI help lists `serve` and `agent` without requiring Uvicorn at import time.

- [ ] **Step 6: Commit**

```powershell
git add src/figma_to_fgui/agent.py src/figma_to_fgui/cli.py tests/unit/test_agent.py tests/unit/test_cli.py
git commit -m "feat: add Windows polling agent"
```

### Task 6: End-to-End Loop, Documentation, and Full Verification

**Files:**
- Create: `tests/integration/test_local_apply_loop.py`
- Modify: `README.md`
- Modify: `progress.md`

**Interfaces:**
- Consumes: all tasks above.
- Produces: a reproducible local workflow and team setup commands.

- [ ] **Step 1: Write the failing end-to-end test**

The test copies `tests/fixtures/fgui` to a temporary project, starts `create_app` through `ASGITransport`, registers an Agent, binds the project, creates and approves a fixture-backed job, runs `poll_once`, then asserts the terminal API state is `applied`, output hashes match the bundle, a backup exists, and the repository fixture hash is unchanged.

- [ ] **Step 2: Run and verify RED**

Run: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m pytest tests/integration/test_local_apply_loop.py -v`

Expected: FAIL at the first missing or mismatched integration boundary, not from test setup.

- [ ] **Step 3: Make only integration corrections**

Align route serialization, injected transports, and fixture packaging. Do not add new product behavior or bypass contract validation.

- [ ] **Step 4: Document the Windows local workflow**

Add commands for creating the venv, installing `.[dev,server]`, starting `fgui-tool serve`, registering/binding the Agent, creating and approving a fixture job through the generated `/docs` page, running `fgui-tool agent poll --once`, locating backups, and recovering from reported failures. State clearly that this slice is local and unauthenticated.

- [ ] **Step 5: Run focused and complete verification**

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest -v
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy src
.\.venv\Scripts\python -m figma_to_fgui.cli --help
git diff --check
```

Expected: all tests pass, Ruff and mypy report no issues, CLI lists the original five commands plus `serve` and `agent`, and `git diff --check` is clean.

- [ ] **Step 6: Run ponytail review**

Inspect the complete diff for unused dependencies, duplicated serialization, unnecessary abstractions, dead code, and standard-library replacements. Apply safe simplifications, then repeat Step 5.

- [ ] **Step 7: Commit**

```powershell
git add README.md tests/integration/test_local_apply_loop.py progress.md
git commit -m "test: verify local apply loop"
```

## Plan Self-Review

- Spec coverage: API creation/preview/approval, ownership, safe path resolution, stale hashes, backup, atomic commit, rollback, idempotency, and Windows end-to-end verification each map to a task.
- Scope: live Figma, Web/Figma UI, production infrastructure, authentication, and .NET packaging remain deferred.
- Type consistency: `ChangeBundle`, `ChangeFile`, `JobStatus`, `ApplyResult`, `JobStore`, `ArtifactStore`, `apply_bundle`, and `AgentClient.poll_once` retain the same names across tasks.
- Placeholder scan: no implementation placeholders remain; integration corrections are explicitly limited to existing boundaries.
