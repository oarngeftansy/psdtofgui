# Live Figma Selection Plugin Design

## Goal

Replace the fixture-backed Figma input with a real Figma plugin that works in both the Windows desktop client and browser Figma. A UX designer pairs the plugin with the company-internal HTTPS Web Console, selects design content, sends it directly to the server, uploads a FairyGUI project ZIP, reviews the complete update, and hands it to the existing Windows Agent.

This phase delivers the live Figma selection boundary. It reuses the existing ZIP ingestion, conversion, review, approval, backup, rollback, and local-project protection pipeline.

## Product flow

1. The designer opens the company-internal Web Console.
2. The console displays a six-digit, one-time pairing code.
3. The designer opens the Figma plugin in either desktop or browser Figma and enters the code.
4. The plugin stores the resulting revocable plugin credential in Figma client storage.
5. The designer selects one or more supported nodes.
6. The plugin shows a preflight summary: top-level selection count, approximate total nodes, asset count and size, and unsupported content.
7. The designer explicitly chooses **发送到 FairyGUI 工具**.
8. The plugin exports normalized selection data and supported assets directly to the internal server.
9. After the server commits a valid immutable selection, the plugin opens the corresponding Web task URL. It also keeps an **打开任务** action and a copyable link for popup-blocked browsers.
10. The Web Console shows the received Figma selection and asks for a FairyGUI project ZIP.
11. The existing flow continues: package selection, conversion, checks, three-column review, whole-update approval, and Windows Agent application.

The normal designer flow never requires a Figma token, file key, node ID, hash, XML path, local project path, or server storage identifier.

## Architecture

### Figma plugin

Create `apps/figma-plugin` as a TypeScript Figma plugin with a small HTML bootstrap and a company-origin hosted plugin UI. Do not add React, a component library, a state manager, or a second design system.

The bundled bootstrap immediately navigates the plugin iframe to an HTTPS UI page on the exact configured company origin. This gives the iframe a controlled non-null origin, so API requests use same-origin policy rather than requiring wildcard CORS. The main Figma sandbox remains the only code that can read the scene; the hosted iframe owns browser networking and presentation.

The plugin contains four focused units:

- `pairing`: exchanges a one-time code for a plugin credential and supports unpairing;
- `selection`: walks only the current Figma selection and produces the shared normalized node contract;
- `assets`: exports supported raster and vector resources with bounded concurrency;
- `upload`: runs in the company-origin iframe, creates a server upload session, transfers the manifest and resources received from the main sandbox, and commits the selection atomically.

The plugin manifest permits network access only to the configured company HTTPS origin. The build receives that origin and the organization-approved Figma plugin ID explicitly and rejects missing IDs, HTTP, wildcard, or multi-origin production configuration. Desktop and browser Figma use the same plugin implementation.

Messages containing credentials or selection payloads are origin-targeted. The hosted iframe sends to `https://www.figma.com` with the exact plugin ID, and the main code sends only to the configured company origin. Generic `"*"` messaging is limited to the non-sensitive initial bootstrap required by Figma and never carries credentials, selection data, or resources.

### Server pairing boundary

The Web Console creates a six-digit pairing code that:

- expires after ten minutes;
- can be exchanged exactly once;
- is invalidated after a successful exchange or explicit cancellation;
- is rate-limited by source and code;
- never appears in normal logs after exchange.

Successful exchange returns an opaque revocable plugin credential. The server stores only a secure credential digest and metadata needed to list and revoke paired devices. The credential permits selection upload and reading the uploader's own selection status only. It cannot access other projects, advanced review artifacts, Agent assignments, or administration routes.

The Web Console lists paired Figma devices using designer-safe labels and provides a one-click revoke action.

### Immutable selection storage

Selection upload follows a transaction:

1. create an upload session;
2. upload the normalized manifest;
3. upload declared resource blobs;
4. commit the session;
5. validate, fingerprint internally, and publish an immutable Selection version.

An incomplete, expired, oversized, or invalid session never becomes usable. Temporary data is server-owned and removed after commit, failure, or expiry. A client-generated idempotency key makes a repeated commit converge on the same Selection rather than creating duplicate tasks.

Selection metadata belongs in SQLite. Immutable manifests and resources use the existing content-addressed artifact boundary. Normal responses expose only an opaque `selection_id`, display name, top-level summaries, preview URLs, and designer-safe warnings.

## Selection contract

The live plugin must produce input compatible with the current normalization and conversion pipeline rather than introduce a second converter.

The manifest includes:

- selected top-level nodes in deterministic Figma order;
- node type, display name, bounds, rotation, visibility, opacity, and source order;
- layout direction, spacing, padding, alignment, sizing behavior, constraints, clips-content, and corner data when applicable;
- fills, strokes, effects, and supported style references in a bounded JSON representation;
- text characters, font family/style name, size, line height, letter spacing, alignment, and text auto-resize data;
- component, instance, variant, and exposed-property relationships needed by classification;
- declared asset references using upload-local opaque keys rather than raw Figma node IDs.

The server maps this manifest into the existing `NormalizedNode` tree and feeds the existing classification, generation, validation, and designer-preview pipeline.

Raw Figma node IDs may exist inside the encrypted/controlled immutable selection artifact for support correlation, but they are not included in normal API responses, designer UI, or ordinary logs.

## Asset policy

Supported resource outputs are:

- raster content exported as PNG or WebP;
- vector content exported as sanitized SVG;
- controlled selection thumbnails generated as bounded WebP.

The plugin does not upload font files. It sends only font names and typography properties. The server does not fetch external URLs referenced by plugin input.

The server rejects executable content, active SVG constructs, external SVG references, unknown binary types, declared/actual MIME mismatches, duplicate resource keys, and undeclared blobs. All previews pass through the existing bounded image decoder and WebP encoder.

## Limits

The first production limits are:

- at most 20 selected top-level nodes;
- at most 5,000 total nodes;
- at most 200 MB across the committed upload session;
- at most 25 MB for one resource;
- bounded plugin-side export concurrency;
- bounded server-side JSON depth, string lengths, resource count, decoded image pixels, and SVG size.

The plugin performs an early preflight for fast feedback, and the server independently enforces every limit. Limit failures use Chinese designer copy and do not create a usable Selection.

## API contract

Add versioned endpoints equivalent to:

- `POST /v1/figma/pairings` — create a pairing code from the authenticated/local Web Console session;
- `POST /v1/figma/pairings/exchange` — exchange the one-time code for a plugin credential;
- `GET /v1/figma/devices` — list paired devices;
- `DELETE /v1/figma/devices/{device_id}` — revoke a device;
- `POST /v1/figma/selections/uploads` — begin a plugin upload session;
- `PUT /v1/figma/selections/uploads/{upload_id}/manifest` — upload the bounded manifest;
- `PUT /v1/figma/selections/uploads/{upload_id}/resources/{resource_key}` — upload a declared resource;
- `POST /v1/figma/selections/uploads/{upload_id}/commit` — publish an immutable Selection;
- `GET /v1/figma/selections/{selection_id}` — designer-safe selection summary;
- `POST /v1/figma/selections/{selection_id}/projects/{project_id}/jobs` — create a conversion job.

The current designer job request replaces `fixture_name` with `selection_id`. Fixture-backed creation remains available only to tests and an explicit development configuration; it is not exposed in the production Web Console.

## Web Console changes

The upload entry page becomes a short guided flow:

1. connect or confirm Figma pairing;
2. wait for or show the received Selection;
3. upload the FairyGUI ZIP;
4. choose a detected package;
5. create and open the review.

The page shows selection display names, safe thumbnails, selected top-level count, and plain-language warnings. It supports an empty waiting state, pairing expiry, revoked device, plugin upload progress, failed selection validation, popup-blocked recovery, ZIP upload failure, and retry without losing a committed Selection.

The existing review workspace remains the approval surface. It receives real live-selection conversion output with no fixture language.

## Error and recovery behavior

- Invalid/expired pairing: the plugin keeps the current Figma selection and requests a new code.
- Revoked credential: the next authenticated operation stops and returns to pairing.
- Export failure: the plugin identifies the unsupported or failed selected item without uploading a partial usable Selection.
- Interrupted upload: retry reuses the idempotency key or starts a clean session after expiry.
- Validation failure: the Web Console receives only a stable error code and designer-safe message; raw node IDs and resource evidence stay in controlled diagnostics.
- Automatic-open failure: the plugin displays **打开任务** and a copyable internal HTTPS link.
- ZIP, conversion, review, and Agent failures retain the existing safe recovery rules.

## Deployment

The server is deployed on a company-internal HTTPS origin reachable from employee browsers and the Figma desktop/browser plugin iframe. Production configuration must provide:

- the exact public internal origin;
- TLS certificate trusted by company devices;
- the hosted plugin UI route on that same origin, restrictive same-origin API behavior, and no wildcard CORS;
- persistent SQLite/artifact storage for the pilot;
- a secret used for credential hashing/signing;
- upload/session cleanup scheduling;
- proxy/body limits aligned with application limits.

The server must not be exposed as the current unauthenticated localhost pilot. Pairing codes protect plugin enrollment but are not a substitute for company Web Console access control; company SSO remains a later deployment hardening option.

## Testing

### Plugin unit and contract tests

Use a Figma API test double to verify deterministic selection walking, supported node mapping, typography, component/instance relationships, asset declaration/export, limits, pairing state, retry, idempotency, revocation, and safe UI copy.

### Server tests

Cover one-time pairing, expiry, rate limits, credential digest storage, permission scope, device revoke, session state transitions, cleanup, manifest bounds, MIME/content checks, sanitized SVG, image bombs, idempotent commit, immutable storage, safe response disclosure, and conversion using a live Selection rather than a fixture.

### Integration and browser tests

Run a plugin-harness export into the real FastAPI app, upload a real FairyGUI ZIP, create a selection-backed job, review, approve, let the Windows Agent apply to a copied local project, and verify backup/stale-write behavior.

Chromium E2E starts at the pairing page, simulates the plugin upload contract, and completes the Web workflow at desktop and mobile view-only widths. The existing nonce-authenticated isolated test server boundary remains mandatory.

### Manual Figma acceptance

Perform one final run in Windows desktop Figma and one in browser Figma:

- pair using a fresh code;
- select supported frames/components;
- inspect preflight;
- send and open the internal task;
- confirm Selection names/thumbnails;
- complete ZIP, review, and approval;
- revoke the device and prove subsequent upload requires pairing again.

Acceptance also verifies that sensitive main/UI messages use the configured company origin, exact plugin ID, and `https://www.figma.com` target rather than wildcard postMessage.

## Acceptance criteria

- The same plugin works in desktop and browser Figma.
- A designer completes pairing without a Figma token or technical identifier.
- Only the current explicit selection is exported.
- Selection and assets reach the internal server transactionally and within enforced limits.
- The Web Console automatically opens or provides a reliable recovery link.
- Production job creation no longer uses fixture Figma JSON.
- Existing conversion, whole-review, Agent matching, backup, rollback, and stale-write guarantees remain intact.
- Normal UI and logs do not expose pairing secrets, credentials, raw node IDs, hashes, paths, XML, or rule evidence.
- Automated plugin, server, integration, and browser suites pass, followed by desktop and browser Figma manual acceptance.

## Deferred scope

- Company SSO and organization-wide authorization policy;
- public-internet deployment;
- multiplayer collaborative selection sessions;
- automatic FairyGUI application refresh/control;
- Windows tray packaging and signed installer;
- per-file approval;
- arbitrary external font or URL downloading.
