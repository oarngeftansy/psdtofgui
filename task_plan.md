# Live Figma Selection Plugin

## Goal

Replace fixture Figma input with a paired plugin that works in desktop and browser Figma and continues through the existing ZIP, review, approval, and Windows Agent workflow.

## Phases

- [x] Confirm desktop/browser, internal HTTPS, pairing-code, and direct-selection-upload scope
- [x] Write, officially validate, self-review, and obtain approval for the specification
- [x] Write and self-review the eight-task implementation plan
- [x] Execute Tasks 1-8 with TDD and independent review
- [x] Final branch review, product audit, verification, and handoff

## Current

Tasks 1-8 complete. Final verification and handoff completed; deployment remains an operator action.

## Task 5 Final Remediation

- [x] Vector and boolean/vector-like layers declare deterministic opaque SVG resources even without image fills; resource declarations and lookup share one iterative plan.
- [x] Visual metadata is recursively bounded and sanitized while preserving colors, gradients, transforms, and effect geometry; unsafe URLs, image identifiers, bytes, and raw IDs are omitted.
- [x] `/figma/selections/:selectionId` is a real Web Console route that safely restores the short-lived, designer-safe committed selection view without persisting credentials.
- [x] Hosted export attempts use a returned opaque attempt token plus manifest signature, credential snapshot, generation, and upload-run guards; new preflights, credential changes, and unpairing invalidate old messages/promises.
- [x] Rebuilt plugin `dist` after the source contract changes.

## Task 5 Follow-up Review Remediation

- [x] Popup recovery caches only a validated, designer-safe selection view in same-origin local storage for at most 15 minutes; the landing route never calls the protected selection API.
- [x] Fill, stroke, effect, and text style identifiers are represented only by deterministic selection-local `style-N` tokens.
- [x] The metadata sanitizer strips path/file fields and variants in addition to URL, hash, byte, and ID fields.
- [x] A failed upload clears its pending attempt so a designer retry sends a new attempt token while retaining the same idempotency key.

Verification: plugin Vitest 36 tests and TypeScript typecheck passed; the locked build passed with the configured ASCII esbuild binary. Web Console Vitest 46 tests, TypeScript typecheck, and production Vite build passed.

## Decisions

- Plugin directly exports only the explicit current selection; no Figma REST token.
- Company-internal HTTPS server; six-digit one-time pairing code.
- Company-origin hosted plugin iframe avoids wildcard CORS required by a null-origin bundled UI.
- Sensitive main/UI messages use exact origin, plugin ID, and Figma target.
- Existing ZIP, conversion, review, Agent, backup, rollback, and stale-write boundaries remain.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Combined spec/plan correction patch missed an exact plan sentence | 1 | Split the correction into stable spec and plan patches |
| Task 5 plan-range inspection script used invalid PowerShell `$Path:` interpolation | 1 | Used direct `Get-Content` range selection instead |
| Task 5 hosted-frame RED test had unbalanced nested `MessageEvent` literals | 1 | Replaced the nested literals with named payload values before rerunning |
| ASCII-plugin esbuild build could not read its source directory under the filesystem sandbox | 1 | Rerun the exact locked build with required escalation; do not weaken the build script |

## Task 3 Review Remediation

- [x] Add RED regressions for opaque node IDs, resource materialization, production route registration, and owned selection-job access.
- [x] Replace selection adapter IDs and resource references with deterministic opaque values; materialize assets through the existing generator.
- [x] Register fixture routes only in explicit development/test mode and require `selection:read-own-status` ownership for live jobs.
- [x] Replace fixture mutation integration proof with an isolated fixture copy; verify focused, golden, suite, Ruff, mypy, and diff; update report and commit.

- Verification note: Ruff found one import-order failure in the new integration test; sorted `shutil` before `sqlite3` before rerunning lint.

## Task 3 Second Review Remediation

- [x] Add RED coverage for package resource registration/reference resolution and streaming conversion limits.
- [x] Generate deterministic collision-free package image resources, preserve package XML structure, and reference the registered resource IDs.
- [x] Introduce internal selection asset metadata/context, chunked copying, and conversion/bundle byte limits without raw asset bytes in raw documents.
- [x] Verify, update report, and commit the second review remediation.

## Task 3 Final Review Remediation

- [x] Add RED probes for same-name resource collisions, direct public composition, and pre-join package validation.
- [x] Preserve unrelated same-name resources, restore composition safely, and use full collision-safe asset identities.
- [x] Verify, update report, and commit the final review remediation.
