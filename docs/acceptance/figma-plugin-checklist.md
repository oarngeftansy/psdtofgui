# AI semantic Figma workflow release evidence

This record separates reproducible automated evidence from administrator and desktop acceptance. It contains no access tokens, prompts, node text, screenshots, raw provider responses, or other fixture content. A `PASS (automated)` entry is not evidence that a real provider, Figma Desktop, or FairyGUI was exercised.

## Release candidate

| Field | Recorded value |
|---|---|
| Evidence date/time zone | 2026-08-04, Asia/Shanghai |
| Candidate implementation commit | `8573329e1987bcb9f18092a782401eba048e40e7` (`fix: atomically attach and claim screenshots`); this evidence-only checklist update follows it |
| Evidence operator | Codex automated verification only; no administrator or desktop tester |
| Automated artifact | `packaging/figma-plugin/dist/Figma-to-FairyGUI-plugin.zip` (local ignored evidence artifact, 118801 bytes) |
| Automated artifact SHA-256 | `9cea0957814d5537e484b1a77dddc7a59a09964b41ec5b8077353f534364a248` |
| ZIP members | Exactly `INSTALL.md`, `code.js`, `manifest.json`, `ui.html` |
| Automated build configuration | Public test placeholders: `https://fgui.corp.example`, plugin ID `123456789`, and the non-secret test token marker used by `build.check.mjs` |
| Deployment build | **PENDING** - the automated artifact is not built with an approved internal origin, production plugin ID, or deployment token and must not be rolled out |

## Automated evidence

| Check | Status | Reproducible evidence |
|---|---|---|
| Python suite | PASS (automated) | `python -m pytest -q --basetemp <local ASCII temp>`: **413 passed, 2 skipped** in 15.42s |
| Python lint | PASS (automated) | `python -m ruff check src tests`: all checks passed |
| Python types | PASS (automated) | `python -m mypy src`: no issues in **38 source files** |
| Figma plugin tests | PASS (automated) | Vitest: **7 files, 112 tests passed** |
| Figma plugin types | PASS (automated) | `tsc --noEmit`: exit 0 |
| Web console tests | PASS (automated) | Vitest: **2 files, 21 tests passed**; the non-failing JSDOM navigation notice remains |
| Web console types | PASS (automated) | `tsc --noEmit`: exit 0 |
| Deterministic plugin build/package | PASS (automated) | Approved-runtime invocation `bin\fallback\pnpm.cmd --dir <isolated-short-ASCII-checkout>\repo\apps\figma-plugin run build:check`: **5 passed, 0 failed**; fresh distribution equality, deterministic ZIP bytes, exact members, fixed timestamps, and adjacent checksum were checked |
| Evidence-file whitespace | PASS (automated) | `git diff --check`: exit 0; this check is repeated after every evidence edit and before commit |

The short-ASCII checkout is required because esbuild embeds source labels affected by the physical Windows checkout and dependency-link paths. The concrete pnpm executable first reconciled dependencies in the disposable checkout and enforced its supply-chain policy. After approving only the lock-resolved `esbuild@0.25.0` install script there, the exact pnpm `run build:check` command passed. No repository lockfile, package manifest, or tracked dependency policy changed.

### Fake-provider scenarios

| Scenario | Status | What the automated harness proves |
|---|---|---|
| `structure_success` | PASS (automated fake) | A fake compatible response applies an observable semantic name and produces a valid XML/ZIP package through the public HTTP workflow |
| `screenshot_success`, consent approved | PASS (automated fake) | Consent causes only the selected-fixture PNG to be exported/uploaded; the second fake response is observable in package output |
| `screenshot_declined` | PASS (automated fake) | No screenshot export/upload occurs and the package is byte-for-byte equal to the deterministic rules-only baseline |
| `ai_failure` | PASS (automated fake) | A forced fake provider failure emits safe fallback diagnostics and returns the rules-only baseline package |
| Safe request logging | PASS (automated fake) | Success, HTTP 429, invalid JSON, and HTTP 500 cases assert that credentials, authorization headers, prompts, node data, screenshot bytes/base64, and raw response bodies are absent |

All AI client tests use `httpx.MockTransport`; plugin end-to-end tests use only a loopback FastAPI child process and the fake semantic service. The default suite received no real AI credential, contacted no real provider, and could not incur a provider charge.

## Administrator provider smoke checks

These checks are release blockers. No real AI credential or administrator-approved company endpoint was available during this evidence run.

| Check | Status | Required next evidence |
|---|---|---|
| Official OpenAI, structure-only, non-sensitive test file | **BLOCKED / PENDING** | Administrator supplies a short-lived least-privilege key and approved model, records only pass/fail plus build ID, confirms a valid package and redacted logs, then disables the credential |
| Company OpenAI-compatible endpoint | **BLOCKED / PENDING** | Administrator supplies the approved HTTPS endpoint and runs structure-only, screenshot-approved, screenshot-declined, and forced-provider-failure deliveries |
| Real-provider log redaction | **BLOCKED / PENDING** | Administrator confirms outcome-only logs without recording keys, prompts, node text, screenshots, headers, or raw responses |
| Credentials disabled after smoke | **BLOCKED / PENDING** | Administrator disables/removes both smoke credentials and records completion without recording their values |

## Figma Desktop and FairyGUI acceptance

Figma Desktop 126.7.10 was observed running, but this session exposes no verifiable desktop-control/import interface or approved test file. No common-path FairyGUI installation was found. No desktop action below was performed.

| Check | Status | Required next evidence |
|---|---|---|
| Import the deployment build in Figma Desktop and verify its single approved HTTPS domain | **PENDING** | Desktop tester records pass/fail, tester, date, deployment build ID, and ZIP SHA-256 |
| Structure-only delivery | **PENDING** | Run with a non-sensitive selection and record outcome only |
| Consented screenshot delivery | **PENDING** | Approve the current test selection only; record outcome only |
| Declined screenshot delivery | **PENDING** | Decline once and confirm completion without screenshot upload |
| Disconnected-provider fallback without credentials | **PENDING** | Disable/disconnect the provider, confirm a safe fallback package, then restore only after the check |
| Create ZIP opens in the supported FairyGUI editor | **BLOCKED / PENDING** | FairyGUI must be installed and a desktop tester must open the generated create package |
| Update ZIP opens and original ZIP remains unchanged | **BLOCKED / PENDING** | Open the generated update package and compare the source ZIP SHA-256 before/after |
| Safe distinct create/update filenames | **PENDING** | Record pass/fail from the actual desktop downloads |

## Operator handoff procedure

Follow [Release ownership and build](../deployment/internal-https.md#release-ownership-and-build) and [Optional AI semantic service](../deployment/internal-https.md#optional-ai-semantic-service). The steps below are intentionally self-contained; substitute only values from approved secret/configuration stores and never paste their values into this checklist, a command argument, source, logs, screenshots, or a support record.

### 1. Build the deployment artifact

Use an ASCII-only checkout and the approved release shell. Load the plugin token from its access-controlled secret file into the environment without printing it:

```powershell
cd <isolated-short-ASCII-checkout>\repo
$env:FGUI_SERVER_ORIGIN = '<approved-internal-https-origin>'
$env:FIGMA_PLUGIN_ID = '<production-numeric-plugin-id>'
$env:FGUI_PLUGIN_ACCESS_TOKEN = Get-Content -Raw '<approved-plugin-token-file>'
pnpm --dir apps/figma-plugin install --frozen-lockfile
pnpm --dir apps/figma-plugin test -- --run
pnpm --dir apps/figma-plugin typecheck
node apps/figma-plugin/scripts/build.mjs
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/figma-plugin/build-package.ps1
pnpm --dir apps/web-console install --frozen-lockfile
pnpm --dir apps/web-console build
Get-Content apps/figma-plugin/dist/manifest.json
Get-FileHash packaging/figma-plugin/dist/Figma-to-FairyGUI-plugin.zip -Algorithm SHA256
Get-Content packaging/figma-plugin/dist/checksums.sha256
```

Confirm that the manifest has exactly the approved HTTPS origin, the ZIP hash equals `checksums.sha256`, and the ZIP has exactly the four recorded members. Replace the automated placeholder artifact metadata above with this deployment build ID, hash, date, and accountable operator before desktop acceptance.

### 2. Configure isolated staging AI smoke tests

Use a dedicated non-sensitive fixture containing no customer copy, credentials, personal data, unreleased art, or production screenshots. Record only its approved fixture identifier and expected deterministic output hash, never its content. Configure these fields only in the staging service account's approved secret/environment store:

```ini
AI_SEMANTIC_ENABLED=true
AI_SEMANTIC_PROVIDER=openai
AI_SEMANTIC_BASE_URL=https://api.openai.com/v1
AI_SEMANTIC_MODEL=<approved-test-model>
AI_SEMANTIC_API_KEY=<short-lived-least-privilege-key-in-secret-store>
AI_SEMANTIC_TIMEOUT_SECONDS=20
AI_SEMANTIC_CONFIDENCE_THRESHOLD=0.75
AI_SEMANTIC_MAX_RETRIES=2
AI_SEMANTIC_MAX_CONCURRENCY=4
```

Run one official OpenAI structure-only delivery. Then set `AI_SEMANTIC_PROVIDER=openai_compatible` and `AI_SEMANTIC_BASE_URL` to the administrator-approved company HTTPS endpoint, with its approved model/key, and run structure-only, screenshot-approved, and screenshot-declined deliveries. Approval may upload only the current fixture screenshot; decline must upload none.

For the forced-failure delivery, use only isolated staging: an administrator may stop the staging provider temporarily or select a known-invalid staging model that returns a controlled provider error. Do not redirect credentials to an arbitrary endpoint, do not use plaintext HTTP or an untrusted certificate, and do not disrupt production. Confirm the deterministic fallback package and safe reason code, then immediately restore the approved endpoint/model and verify recovery.

Capture only sanitized outcome logs. Search them for the test key, `Authorization`, fixture node IDs/text, prompt fragments, screenshot encoding, and a unique fake/raw-response marker; every search must return no match. Record only tester, date, deployment build/hash, provider class, scenario pass/fail, safe reason code, and source/output hashes in this checklist.

### 3. Remove smoke credentials and finish desktop acceptance

Remove the short-lived keys and AI values from staging, set `AI_SEMANTIC_ENABLED=false`, restart, and confirm one deterministic no-AI delivery. Then perform every pending Figma Desktop/FairyGUI row above. Do not roll out until the restored configuration, disabled credentials, deployment artifact hash, and all manual results are recorded without secret or fixture content.

## Rollout decision

**NOT READY FOR ROLLOUT.** Automated evidence passes, but the produced ZIP is a public-placeholder test build and all real-provider and manual Figma/FairyGUI acceptance remains pending. Rollout may proceed only after an administrator replaces the automated artifact with a verified deployment build, records its new SHA-256, and every pending row above has an accountable pass result.

## HIFI replacement addendum — 2026-09-15

| Check | Status | Evidence or required action |
| --- | --- | --- |
| Upload old project and choose package/directory/root component | **PASS** | Automated plugin API acceptance |
| Resolve matched, extra, missing, ambiguous, and blocked objects | **PASS** | Every mapping item has an explicit final action |
| Replace a Lowfi image with uploaded HIFI material | **PASS** | Candidate writes the uploaded PNG under `Img/HIFI`, registers it in `package.xml`, and retargets the private image |
| Preserve old IDs, shared references, and target scope | **PASS** | Machine acceptance JSON and integration test |
| Bind Editor confirmations to the reviewed candidate hash | **PASS** | Wrong hash is rejected with `hifi_candidate_stale` |
| Candidate ZIP equals approved delivery ZIP | **PASS** | Matching recorded SHA-256 |
| Open, save, close, reopen, and save in FairyGUI 6.1.4 | **BLOCKED / PENDING** | Install FairyGUI 6.1.4 and record the manual result against the candidate SHA-256 |

See `docs/validation/2026-09-15-hifi-replacement-acceptance.md` for the exact automated evidence and remaining Editor check.
