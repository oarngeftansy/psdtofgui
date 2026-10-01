# Local Apply Loop Design

## Goal

Build the smallest Windows-only vertical slice that proves the critical workflow: a server creates a conversion job, a user approves its preview, and a local agent safely applies the resulting changeset to a bound FairyGUI project.

This slice reuses the deterministic conversion core. It uses fixture input instead of live Figma access and postpones the browser UI, Figma plugin, PostgreSQL, MinIO, authentication, tray UI, installer, and automatic FairyGUI UI control.

## User Flow

1. The user starts the API server and Windows Agent locally.
2. The user binds an FGUI project directory through the Agent CLI.
3. The user submits a fixture and project binding to the server.
4. The server runs the existing conversion pipeline and exposes diagnostics and a file preview.
5. The user approves the job through the API.
6. The Agent polls for approved work assigned to its device, downloads the changeset, and validates the target binding.
7. The Agent verifies pre-write hashes, creates a backup, stages all output, and atomically replaces the target files.
8. The Agent validates the result and reports success. On failure it restores the backup and reports a structured error.
9. The user reloads the project in FairyGUI.

## Architecture

### API Server

The FastAPI application calls the conversion core directly. A SQLite repository stores jobs, agent registrations, project bindings, approval state, and apply results. Changeset payloads are stored under a configurable local data directory by content hash.

The server exposes:

- `POST /v1/agents/register`
- `POST /v1/projects/bind`
- `POST /v1/jobs`
- `GET /v1/jobs/{job_id}`
- `GET /v1/jobs/{job_id}/preview`
- `POST /v1/jobs/{job_id}/approve`
- `GET /v1/agents/{agent_id}/assignments/next`
- `GET /v1/jobs/{job_id}/changeset`
- `POST /v1/jobs/{job_id}/apply-result`
- `GET /health`

Conversion runs synchronously during job creation. A worker is deferred until conversion time or concurrency requires one.

### Windows Agent

The first Agent is a Python CLI so it can share the contracts and validate the protocol quickly on Windows. It provides `agent register`, `agent bind`, `agent poll --once`, and `agent run`. Bindings live in a local JSON configuration file.

Device credentials are not introduced in this unauthenticated local slice. The production Agent remains planned for .NET 8 with Windows Credential Manager and a tray UI after the protocol stabilizes.

### Shared Contracts

Pydantic contracts version all requests, responses, job states, changeset file operations, and apply results. The server never sends absolute paths or commands. The Agent resolves every relative target against the locally bound root.

## Job State Model

Allowed transitions are:

```text
created -> ready_for_review -> approved -> applying -> applied
                       |             |          |
                       -> rejected   -> failed  -> failed
created -> conversion_failed
```

Only a job without `ERROR` diagnostics may enter `ready_for_review`. Approval is idempotent. Only the Agent bound to the target project may claim an assignment. Repeating the same terminal apply result is idempotent.

## Safe Apply Transaction

For every file operation, the Agent:

1. Rejects absolute paths, empty paths, `..` segments, and paths resolving outside the bound root.
2. Verifies the current SHA-256 against the expected pre-write hash. A missing file is valid only for create.
3. Copies existing affected files to `.figma-to-fgui/backups/{job_id}/`, preserving relative paths.
4. Writes new content to temporary files inside the target directories.
5. Validates staged XML and declared hashes.
6. Uses same-volume atomic replacement for each file.
7. Revalidates final hashes.
8. Restores every existing file from backup and removes newly created files if commit or final validation fails.

The slice supports create and replace operations only. Delete operations remain disabled.

## Persistence and Configuration

- SQLite database: `.figma-to-fgui/server.db`
- Payload store: `.figma-to-fgui/artifacts/`
- Agent config: `%LOCALAPPDATA%/FigmaToFGUI/agent.json`
- API base URL: `http://127.0.0.1:8765`

Tests inject temporary paths and never touch a real user project.

## Errors and Diagnostics

API errors use stable codes and human-readable messages. Apply reports include job ID, agent ID, project ID, terminal status, changed relative paths, rollback status, and diagnostics. File contents and local absolute project paths are not returned to the server.

Expected failures include invalid state transitions, project/agent mismatch, unsafe paths, stale source hashes, malformed XML, unavailable server, partial write failure, and rollback failure.

## Testing

- Contract tests fix serialized names and protocol version.
- API tests cover creation, preview, approval gates, assignment ownership, and idempotency.
- Agent tests cover containment, hash conflicts, backups, atomic writes, and rollback.
- An integration test runs the API in-process, applies a golden changeset to a temporary FGUI fixture, verifies output hashes, and confirms the source fixture is unchanged.
- Existing conversion-core tests, Ruff, and mypy remain required.

## Dependencies

Add only FastAPI and HTTPX. SQLite, JSON configuration, hashing, temporary files, backups, polling, and filesystem transactions use the Python standard library. Uvicorn is an optional runtime dependency for launching the local server.

## Acceptance Criteria

- A fixture-backed job can be created, previewed, and approved through the API.
- Jobs containing an `ERROR` diagnostic cannot be approved or assigned.
- Only the Agent bound to the selected project can claim the approved job.
- The Agent cannot modify files outside the bound project root.
- Concurrent local changes are detected and never overwritten.
- A successful apply creates a backup and produces exactly the declared hashes.
- An injected mid-apply failure restores the original project completely.
- Repeated approval or terminal result submission does not duplicate work.
- The end-to-end workflow passes on Windows with temporary directories.

## Deferred Work

- Live Figma REST API and image downloads
- React Web Console and Figma Plugin
- PostgreSQL, MinIO, background workers, and Docker Compose
- Authentication, signed changesets, and one-time downloads
- .NET tray Agent, Credential Manager, code signing, installer, and auto-update
- Automatic FairyGUI editor refresh
