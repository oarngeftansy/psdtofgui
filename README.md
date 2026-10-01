# Figma to FairyGUI plugin workflow

## PSD HIFI replacement app

The standalone local app accepts an old FairyGUI project ZIP and a HIFI PSD, then lets the user choose the target package directory and root component before mapping. It checks the two registered project fonts on the machine automatically. External PNG cutouts and a reference image are optional.

On Windows, double-click `启动PSD替换工具.cmd`. The first run installs the local service and web dependencies, builds the browser interface, opens `http://localhost:8765`, and keeps every uploaded artifact on the same machine. This entry point does not build or import a Figma plugin.

Current implementation status: the preparation workspace, old-project target picker, fixed-font checks, persisted PSD sources, stable HIFI layer inventory, and direct PSD-to-FGUI mapping workspace are available. Candidate rendering and final delivery stay blocked until every PSD visual capability has an equivalence proof.

Designers complete the entire delivery flow in the bundled Figma plugin. They never enter a server URL or pairing code, open a browser console, or install a Windows Agent. The plugin uploads only the current selection and the assets it needs.

It is an internal workflow, not a public service. It does not provide SSO, a signed installer, automatic FairyGUI refresh, or public plugin distribution.

## Writer designer workflow

1. Import the approved private plugin build into Figma.
2. Select a frame or component and refresh the plugin's current-selection summary.
3. Enter the new project name. Writer creates a self-contained FairyGUI 6.1.4 project; no template or existing project is required.
4. Review image, component, package, and diagnostic evidence. Apply only server-declared safe adjustments; regeneration creates a new candidate and invalidates the old one.
5. Acknowledge the current candidate's warnings, approve the whole candidate, then download or repeat-download its integrity-checked ZIP. **Update existing project** remains isolated in the overflow menu.

The server's archive validation, token boundaries, and package checks apply to Writer and the isolated update flow. If a request cannot reach the internal service, correct connectivity and retry from the plugin; no selection data is stored in the browser console.

## Team quick start

For teammates who only need the local Figma-to-FairyGUI workflow, clone to an ASCII path and run
`启动本机版.cmd`. It creates a machine-local environment and plugin, then starts Writer on loopback.
The first-run Figma import path and troubleshooting steps are documented in
[docs/deployment/git-clone-local-windows.md](docs/deployment/git-clone-local-windows.md).

Use an ASCII-only checkout such as `C:\src\figma-to-fgui` for pnpm work. The locked plugin verifier is known to fail from this repository's Chinese path; use an ASCII clone/copy and do not weaken its build scripts.

```powershell
git clone <internal-repository-url> C:\src\figma-to-fgui
cd C:\src\figma-to-fgui
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,server]"
pnpm --dir apps/figma-plugin install --frozen-lockfile
pnpm --dir apps/web-console install --frozen-lockfile
$env:PYTHONPATH = 'src'
```

After a fresh plugin install, verify that the lock-resolved esbuild binary exists before building. If pnpm reports ignored build scripts, have the release environment approve **only** the locked `esbuild` build through its normal `pnpm approve-builds` policy and rerun the frozen install; do not disable the policy or add an unpinned binary.

```powershell
Test-Path apps/figma-plugin/node_modules/@esbuild/win32-x64/bin/esbuild.exe
```

Build with one canonical internal HTTPS origin and the established numeric Figma plugin ID:

```powershell
$env:FGUI_SERVER_ORIGIN = 'https://fgui.internal.example'
$env:FIGMA_PLUGIN_ID = '123456789'
$env:FGUI_PLUGIN_ACCESS_TOKEN = Get-Content -Raw 'C:\ProgramData\FigmaToFGUI\plugin-access-token.txt'
pnpm --dir apps/figma-plugin test -- --run
pnpm --dir apps/figma-plugin typecheck
pnpm --dir apps/figma-plugin build
pnpm --dir apps/web-console build
Get-ChildItem apps/figma-plugin/dist/manifest.json, apps/figma-plugin/dist/code.js, apps/figma-plugin/dist/ui.html
Get-Content apps/figma-plugin/dist/manifest.json
```

`apps/figma-plugin/dist` is a reproducible Figma import artifact, not an installer. The internal TLS and private Figma publishing requirements are in [docs/deployment/internal-https.md](docs/deployment/internal-https.md). Record release acceptance with [docs/acceptance/figma-plugin-checklist.md](docs/acceptance/figma-plugin-checklist.md).

## Production server

Production requires one HTTPS origin, a printable ASCII plugin token of at least 32 characters, a separate gateway secret file containing at least 32 bytes, and a plugin manifest whose only allowed domain exactly matches that origin. It never accepts either secret as a command-line value and disables fixture jobs.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m figma_to_fgui.cli serve `
  --production `
  --data-dir 'C:\ProgramData\FigmaToFGUI\data' `
  --public-origin 'https://fgui.internal.example' `
  --plugin-access-token-file 'C:\ProgramData\FigmaToFGUI\plugin-access-token.txt' `
  --gateway-secret-file 'C:\ProgramData\FigmaToFGUI\gateway-secret.txt' `
  --web-dist apps/web-console/dist `
  --plugin-manifest apps/figma-plugin/dist/manifest.json `
  --trusted-proxy '127.0.0.1'
```

The gateway secret is a coarse reverse-proxy boundary, not SSO or roles. Every production `/v1/*` request needs the exact `X-Figma-Gateway-Token` before endpoint logic; health and static/plugin UI remain accessible. Keep the app on loopback. The trusted proxy must strip every client-supplied instance of that header, enforce the corporate network allow-list or mTLS/auth policy, then inject the file-protected secret for browser, plugin, and Agent API requests. Never send or expose this token to JavaScript, plugin storage, logs, documentation records, or support tickets. CORS `OPTIONS` preflight has no state change and is allowed; the subsequent API request is still proxy-injected and authenticated.

`--trusted-proxy` accepts one explicit proxy IP only. Without it, forwarded headers are not trusted. For local development only, omit `--production` and keep the default loopback host; development permits fixtures and is unsuitable for a LAN or public address.

## Universal UIR v1 developer workflow

`build-uir` converts normalized Figma source facts into the deterministic,
engine-neutral UIR contract. The output is an intermediate JSON artifact, not a
FairyGUI project or FairyGUI XML.

```powershell
fgui-tool build-uir tests/fixtures/figma/simple-frame.json out/simple.uir.json `
  --source-revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa `
  --selection-id selection_simple
```

Component catalogs must be verified against the current FairyGUI project before
they can influence UIR decisions. The candidate file is never trusted directly:

```powershell
fgui-tool verify-component-mappings FairyGUI-project out/verified-component-mappings.json `
  --catalog rules/default/component-mapping-candidates.json
fgui-tool build-uir tests/fixtures/figma/simple-frame.json out/simple.uir.json `
  --source-revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa `
  --selection-id selection_simple `
  --mapping-catalog out/verified-component-mappings.json
```

FairyGUI Profile translation, project-binding confirmation, and UIR-to-FairyGUI
XML generation form the next implementation boundary.

## Generic FairyGUI plan workflow

`build-fgui-plan` converts validated UIR into a deterministic, project-neutral
FairyGUI primitive plan. The output is not XML and contains no project IDs.

```powershell
fgui-tool build-fgui-plan out/simple.uir.json out/simple.fgui-plan.json `
  --profile-version fgui-6.1.4-v1 `
  --rule-version 1
```

A future mapping file must first pass a dedicated versioned importer after its
real schema is supplied. It is not an input to this generic plan compiler until
that importer validates it.

## Verification

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
$env:CI='true'; pnpm --dir apps/figma-plugin test -- --run
pnpm --dir apps/figma-plugin typecheck
$env:FGUI_SERVER_ORIGIN='https://fgui.corp.example'; $env:FIGMA_PLUGIN_ID='123456789'; pnpm --dir apps/figma-plugin build
$env:CI='true'; pnpm --dir apps/web-console test -- --run
Push-Location apps/web-console; .\node_modules\.bin\tsc.cmd --noEmit; .\node_modules\.bin\playwright.cmd test; Pop-Location
pnpm --dir apps/web-console build
.\.venv\Scripts\python.exe -m figma_to_fgui.cli --help
git diff --check
```

Install Chromium only for browser verification:

```powershell
Push-Location apps/web-console; .\node_modules\.bin\playwright.cmd install chromium; Pop-Location
```
