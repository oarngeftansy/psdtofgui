# Progress

## 2026-07-28 Live Figma Selection

- User approved desktop and browser Figma support, company-internal HTTPS, pairing-code enrollment, direct selection/resource upload, and automatic task opening.
- Wrote and committed `docs/superpowers/specs/2026-07-28-live-figma-selection-plugin-design.md`.
- Official Figma documentation review corrected the plugin UI to a company-origin hosted iframe with exact-origin/plugin-ID messaging and no wildcard CORS.
- Wrote and committed `docs/superpowers/plans/2026-07-28-live-figma-selection-plugin.md` with eight independently reviewable TDD tasks.
- User selected subagent-driven execution.
- Live Task 1 complete: one-time pairing, keyed credential digests, explicit minimal scopes, revoke, safe malformed-request mapping, and bounded per-source/per-code rate limits passed independent review; 144 Python tests passed with 1 symlink-permission skip.

## 2026-07-27

- Created isolated branch and worktree: `codex/conversion-core-baseline`.
- Reviewed the implementation plan; Tasks 1-3 have no critical blocking gaps.
- Planning template read failed because the installed skill has no `templates/` directory; recorded and continued with minimal local files.

## Verification Log

## 2026-07-28

- Began the next sub-project after the local apply loop was pushed.
- User selected real project upload plus Web preview/approval before live Figma integration.
- User selected the three-column review layout, target-Package ZIP scope, whole-update approval, and React/Vite architecture.
- Revised the flow after user feedback: designers upload a FairyGUI project ZIP and technical snapshot/hash language is hidden by default.
- Confirmed ZIP safety, local Agent matching, designer-facing errors, tests, and acceptance criteria.
- Wrote and self-reviewed `docs/superpowers/specs/2026-07-28-zip-project-web-console-design.md`.
- User approved the written ZIP project and Web Console specification.
- Wrote and self-reviewed `docs/superpowers/plans/2026-07-28-zip-project-web-console.md` with eight TDD tasks.
- SDD Task 1 complete: safe ZIP extraction passed 20 focused tests and independent review after fixing pre-extraction NUL validation for file and directory headers.
- SDD Task 2 complete: uploaded-project indexing, WebP thumbnails, deterministic fingerprints, and content-addressed storage passed independent review after collision and concurrent-publication hardening.
- SDD Task 3 complete: the designer-safe ZIP upload, project/package, and protected-thumbnail API passed 95 tests plus independent review after hardening multipart errors and cleanup.
- SDD Task 4 complete: uploaded-artifact conversion, designer preview, whole-job rejection, scoped Agent artifacts, and safe legacy compatibility passed 106 tests plus independent review.
- SDD Task 5 complete: current-fingerprint project matching, no-guess selection, and pre-backup stale-write protection passed 116 tests plus independent review.
- SDD Task 6 complete: the accessible React ZIP-upload experience, safe XHR client, progress/retry states, and recent-task shell passed 10 frontend tests plus independent review.
- SDD Task 7 complete: the three-column review workspace, bounded WebP comparisons, accessible whole-update decisions, and mobile view-only mode passed 121 Python and 27 frontend tests plus independent review.
- SDD Task 8 complete: one-URL static hosting, isolated Chromium E2E, uploaded-project Agent integration, safe CLI errors, and Windows team documentation passed independent review.
- Final fresh verification: 129 Python tests passed with 1 Windows symlink-permission skip; Ruff and mypy clean; 32 frontend tests, TypeScript, Vite build, and 1 Chromium E2E passed; CLI and Git checks clean.
- Final ponytail review: no dependency, wrapper, or speculative abstraction could be removed without weakening an approved safety, persistence, UI, or verification boundary.
- Deep-user audit: this is a source-installable pilot; live Figma selection, a packaged one-time Agent folder picker/status UI, and automatic FairyGUI application refresh remain the next product bets.
- Live Figma Task 1 complete: six-digit one-use pairing, scoped credentials, revocation, and bounded abuse controls passed independent review.
- Live Figma Task 2 complete: transactional selection upload, hostile manifest/SVG/raster validation, true disk streaming, immutable publication, expiry, and designer-safe views passed 29 focused and 171 full-suite tests plus independent review.
- Live Figma Task 3 complete: live-selection conversion, authenticated owner-scoped jobs, production fixture gating, collision-safe FairyGUI asset registration, bounded streaming conversion, and Agent application passed 181 tests plus independent review.
- Live Figma Task 4 complete: installable Figma plugin foundation, exact-origin pairing, hosted accessible pairing UI, deterministic manifest/code/bootstrap artifacts, and strict credential persistence passed independent review.
- Live Figma Task 5 complete: current-selection serialization, bounded PNG/SVG export, transactional upload, exact message envelopes, safe popup recovery, and stale-attempt protection passed independent review.
- Live Figma Task 6 complete: session-scoped pairing, waiting/selection/ZIP/package/job guided flow, device revoke, exact preview ownership, bounded polling, and retry-safe UI passed independent review.
- Live Figma Task 7 complete: real FastAPI plugin harness, live selection-to-Agent integration, two-package Chromium workflow, stale-write/backup checks, and isolated process cleanup passed independent review.
- Live Figma Task 8: production `serve` requires one HTTPS public origin, a 32-byte secret file, matching built plugin manifest, persistent data path, and fixture-job disablement; CORS is single-origin and forwarded headers remain untrusted unless one explicit proxy IP is configured. Added deployment/rollback and desktop/browser acceptance runbooks.
- Task 8 review: no extra dependency or abstraction was needed; immediate deployment safeguards are explicit data persistence and loopback-only production binding. Remaining product bets are a signed/managed Agent, SSO/roles, and automatic FairyGUI refresh, none of which are shipped.
- Task 8 gateway follow-up: production now requires a distinct file-only gateway secret and denies raw `/v1/*` calls before route logic with constant-time comparison. Protected proxy injection, CORS preflight, and direct-loopback acceptance checks are documented; this remains a coarse network gateway rather than SSO/roles.
- Final live-workflow verification: fixed the Playwright server's missing development plugin secret, replaced invalid PNG/ZIP fixtures with valid payloads, consolidated the obsolete ZIP-only E2E into the real paired flow, asserted non-empty conversion output, and fixed long generated asset names overflowing the 390px review layout. Fresh results: 196 Python passed with 1 expected Windows symlink skip; Ruff and mypy clean; 61 Web tests, TypeScript, Vite build, and 1 real Chromium flow passed; 38 plugin tests and plugin TypeScript passed via the ASCII dependency cache.
- Deep-user audit: the core designer flow is understandable and recoverable, but company rollout still needs a real internal TLS hostname/proxy identity policy, managed Windows Agent packaging, and a verified FairyGUI refresh story. SSO/roles and signed automatic updates remain larger product bets.

- Task 3 review remediation started: review identified raw selection IDs in generated output, runtime-only fixture route gates, unauthenticated selection jobs, unused resource references, and unsafe fixture mutation in the integration test. Root cause traced to direct adapter copying and unconditional FastAPI decorators.
- Task 3 review remediation GREEN: 4 focused tests pass. Selection adapter now hashes content/position into opaque IDs, resource content into opaque asset names, generator writes referenced assets, production registration omits fixture routes, and live job creation authenticates `selection:read-own-status` then uses the owner-bound selection lookup.
- Task 3 review remediation verification: focused plus golden 5 passed; full Python suite 175 passed with 1 Windows symlink-permission skip; Ruff, mypy, and diff check are clean after correcting one import-order lint failure.

- Task 3 second review remediation RED: package registration lacked a staged `package.xml`; the adapter buffered resource bytes; multi-resource input was not rejected before staging; and a forced bundle limit still produced a reviewable artifact.
- Task 3 second review remediation GREEN: generated image resources are registered in preserved package XML and panel `src` IDs resolve after Agent apply; asset context now carries a verified resolved path, size, SHA-256, and immutable artifact fingerprint; resources stream in 64 KiB chunks; conversion and changeset budgets fail closed before artifact publication.
- Task 3 final review remediation GREEN: same-name resources now reuse only exact normalized-path and SHA-256 matches; incompatible entries remain untouched while a deterministic full-SHA-suffixed image is registered. `selection_document(...)` again composes directly with `convert_document(...)` through private dict-compatible context, and unknown package names fail before any package filesystem access.

- User approved the fixture-backed API Server and Windows Agent vertical slice.
- Wrote `docs/superpowers/specs/2026-07-28-local-apply-loop-design.md`.
- First combined patch failed because mojibake in the old planning file prevented exact context matching; no partial changes were applied.
- Design self-review found no placeholders, contradictory transitions, or unbounded filesystem operations.
- User approved the written specification.
- Wrote and self-reviewed `docs/superpowers/plans/2026-07-28-local-apply-loop.md` with six TDD tasks.
- Task 1 RED: service contract and artifact tests failed because both modules were absent.
- Task 1 GREEN: 10 tests pass; Ruff and mypy are clean after narrowing the protocol constant to `Literal[1]`.
- Task 2 RED: job-store tests failed because the repository module was absent.
- Task 2 GREEN: 5 state, ownership, claim, and idempotency tests pass; Ruff and mypy are clean.
- Task 3 environment: installed declared FastAPI, HTTPX, and Uvicorn dependencies; narrowed FastAPI below 0.120 to avoid the newer Starlette TestClient deprecation.
- Task 3 RED: API tests failed because the API module was absent after dependencies were installed.
- Task 3 GREEN: 5 API workflow, ownership, path rejection, not-found, and health tests pass; Ruff and mypy are clean.
- Task 4 RED: apply tests failed because the filesystem transaction module was absent.
- Task 4 GREEN: create/replace, stale hash, malformed XML, and injected rollback pass; symlink escape test skips when Windows link privilege is unavailable.
- Task 5 RED: Agent tests failed because the polling module was absent and CLI help lacked the new groups.
- Task 5 GREEN: Agent config, success/failure reporting, empty polling, and CLI help pass; full suite is 46 passed and 1 privilege-related skip.
- Task 6 integration: the in-process API-to-Agent workflow passed on its first composition run and verifies declared output hashes plus fixture immutability.
- Added team-copyable Windows setup, server, binding, approval, polling, backup, and limitation documentation.
- Deep-user immediate fix RED/GREEN: `agent poll` now prints the terminal result instead of completing silently.
- Ponytail review: no dependency, abstraction, or duplicated layer can be removed without weakening an explicit protocol, persistence, or rollback boundary.
- Final verification: 48 passed, 1 Windows symlink-privilege skip; Ruff clean; mypy clean; CLI exposes seven top-level commands and the `agent poll --once` option.
- Packaging verification: built `figma_to_fgui_core-0.1.0-py3-none-any.whl` successfully after approved build-backend download.

| Command | Result |
|---|---|
| Baseline | Pending |
| `python -m pytest tests/unit/test_models.py -v` | Blocked: global Python has no pytest; create `.venv` and install declared dev dependencies |
| `.venv` pytest after install | Editable install encoded the Chinese workspace path incorrectly; configure pytest `pythonpath = ["src"]` |
| Task 1 tests | PASS: 2 pytest tests; mypy clean |
| Task 2 tests | PASS: 6 pytest tests; Ruff and mypy clean |
| Task 3 initial verification | pytest 9/9 and Ruff passed; mypy blocked by missing third-party `lxml` stubs |
| Task 3 type-check resolution | Added a local mypy override for `lxml.*` instead of another runtime dependency |
| Batch 1 final verification | PASS: 9 pytest tests, Ruff clean, mypy clean |
| Task 4 initial verification | Behavior test and Ruff passed; mypy blocked by missing PyYAML stubs, resolved with the existing third-party import override |
| Task 5 verification | PASS: deterministic ID test, Ruff, and mypy |
| Batch 2 final verification | PASS: 12 pytest tests, Ruff clean, mypy clean |
| Task 7 verification | PASS: validation gate tests, Ruff, and mypy |
| Task 8 verification | PASS: stable tree hash and ERROR applicability gate; Ruff and mypy clean |
| Task 9 golden generation attempt | Editable install path encoding also affects plain Python commands; run development commands with `PYTHONPATH=src` |
| Task 9 initial full verification | 18 tests, Ruff, and mypy passed; CLI without `PYTHONPATH=src` reproduced the known editable-path encoding limitation |
| Task 9 CLI verification | PASS with documented `PYTHONPATH=src`; all five commands listed and end-to-end convert returned `applicable: true` |
| Ponytail review | Removed unused Pillow dependency; clarified Windows isolated-environment and read-only source workflow |
| Final verification | PASS: 18 tests, Ruff, mypy, five-command CLI, deterministic XML/changeset, and source fixture immutability |
