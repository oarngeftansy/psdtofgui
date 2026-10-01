# Findings

- Repository starts with documentation only; there is no existing runtime convention to preserve.
- The approved first implementation batch has no dependency on Figma network access or the missing mapping file.
- The implementation plan intentionally keeps the conversion core offline and read-only with respect to source FGUI fixtures.
- Third-party packages `lxml` and `PyYAML` do not ship mypy types; narrow import overrides keep project code strict without adding stub-only dependencies.
- Generated resource IDs track both indexed and newly planned IDs, so the package cannot receive duplicate IDs within one plan.
- On this Windows machine, editable package metadata mis-encodes the Chinese workspace segment; pytest and documented development commands use the repository `src` path directly. This does not affect wheel contents.
- Pillow is not needed until the later asset-rendering subsystem, so the baseline does not carry it as an unused dependency.

## Deep User Audit

- UX designers should upload a familiar project ZIP; snapshot, SHA-256, XML, internal ID, and changeset language belongs only in advanced details.
- The Web Console layout is fixed as a three-column review workspace: understandable changes, visual before/after, and checks plus whole-update approval.
- The Agent still needs one local folder binding for automatic application, but exact internal project matching should avoid repeated selection.

- The next slice can prove the highest-risk boundary without live Figma or production infrastructure: API approval plus safe local apply and rollback.
- A Python CLI Agent is the minimum protocol-validation vehicle; the approved production target remains a .NET 8 Windows tray application.
- Immediate usability issue fixed: one-shot polling now prints applied/failed status and changed relative paths.
- Remaining deep-user pain: users still create and approve jobs through Swagger/PowerShell, the server uses fixture snapshots rather than an Agent-uploaded real project snapshot, and FairyGUI reload remains manual.
- Observable next acceptance: a Web Console shows preview/approval; Agent snapshot upload supplies pre-write hashes; a supported FairyGUI refresh mechanism removes the manual reload step.

- Real job: deterministically prove conversion rules offline before connecting them to Figma, a server, or a local writer.
- Immediate friction fixed: Windows setup and the read-only source boundary are explicit in `README.md`.
- Remaining product gap: users cannot yet fetch live Figma nodes, render image assets, update `package.xml`, or apply changes to a local FairyGUI project.
- Acceptance for the next phase: a service task accepts a Figma URL/node ID, returns the same versioned core diagnostics, and produces a downloadable preview without weakening source immutability.
- Larger follow-on work remains the approved API/Web service, Windows Agent, Figma Plugin, and Codex Plugin phases.
# Live Figma Selection Findings

- Official Figma docs confirm the main plugin sandbox reads the scene while browser APIs live in the UI iframe.
- A bundled UI iframe has a null origin and secure API calls would require wildcard CORS; the production design instead navigates the iframe to the company HTTPS origin.
- Non-null-origin sensitive messaging must include the exact plugin ID and target `https://www.figma.com`; main-to-UI messages target the configured company origin.
- `networkAccess.allowedDomains` can restrict the plugin to the one internal HTTPS origin; local development belongs in `devAllowedDomains` only.

## Task 3 Review Findings

- Current adapter copies raw manifest IDs into generated XML IDs, and it leaves resource references unused because `generate_staging` emits only panel XML.
- Runtime fixture guards still expose fixture handlers and their schemas in production OpenAPI; registration must be conditional.
- The current live-job route bypasses selection ownership via `artifact_path`; it must authenticate `SELECTION_READ_OWN_STATUS` and use `SelectionStore.get` before locating the artifact.
- Existing advanced preview remains intentionally available to existing callers, but all selection-derived XML, diagnostics, and normal/advanced API payloads must be raw-ID-free.

## Task 3 Second Review Findings

- A selection image must be a package resource before panel XML can reference it: panel `src` values now resolve to deterministic, collision-safe image IDs appended to the existing `package.xml` resource list.
- The adapter holds only an opaque asset token plus a verified, resolved path, declared metadata, content SHA-256, and selection artifact fingerprint; its raw conversion document contains no bytes, paths, or resource keys.
- Source resources are hash-verified and copied in 64 KiB chunks. An 8 MiB selection-input cap runs before staging and an independent 8 MiB changeset cap runs before any base64 encoding or artifact publication.
- A pre-existing image may be reused only when its normalized package-relative path is exactly the generated assets path and the target file's size and SHA-256 match. Any mismatch receives a deterministic full-SHA suffix and a fresh collision-safe resource ID, preserving the unrelated resource and file.
- `SelectionDocument` remains a dict-compatible public composition value while carrying typed assets privately. Its copy operation retains that context; plain JSON loses no paths but then fails explicitly if resource references cannot be resolved.

## Task 8 Deployment Backlog

- Before honoring forwarded client-address headers, define and validate a trusted reverse-proxy IP policy. Pairing rate limits currently bucket direct `request.client.host`; behind an unconfigured proxy this could collapse to the shared proxy address.

## Task 5 Implementation Findings

- Plugin upload must call the existing authenticated routes in order: create upload with the idempotency key, put a JSON manifest, put each declared resource with its exact MIME type, then commit. The server provides no client-side upload token or resource URL.
- The plugin’s only sensitive bridge is already exact-origin: main accepts hosted messages only from the configured company origin and the hosted page targets `https://www.figma.com` with the numeric plugin ID. Task 5 must extend that union without widening it.
- The plugin worktree’s `node_modules` is unusable in the Chinese path; locked plugin checks must run from the required fresh ASCII temp copy.
