# Plugin Writer final acceptance — 2026-08-21

## Outcome

The public delivery path and packaged plugin passed automated acceptance. Fresh real-app GUI
acceptance is **incomplete** because the local Computer Use capture/input boundary could not control
or capture the installed Figma and FairyGUI windows. No screenshot, plugin download, Editor save, or
modal observation is claimed for this run.

The machine-readable attempt record is
[`2026-08-21-plugin-writer-final-gui-attempt.json`](2026-08-21-plugin-writer-final-gui-attempt.json).

## Public delivery E2E

The tracked integration test uploads one neutral PNG through the public selection manifest/resource
endpoints and then exercises the Writer HTTP state machine:

1. download is blocked before approval;
2. image, component/interface, Package/resource, and unified-check reviews are inspected;
3. evidence kinds are limited to `source-image`, `rendered`, or `structured-summary` as applicable;
4. one compiler-authored, typed raster-fallback issue declares `preserve-editable`; the public adjustment
   changes the generated archive and removes that warning without database mutation;
5. regeneration permanently invalidates candidate v1 for review, approval, and download;
6. the exact current warning IDs for candidate v2 are acknowledged;
7. the whole v2 candidate is approved and downloaded twice;
8. both downloads have equal bytes, filename, size, and SHA-256, and the production archive validator
   returns no diagnostics.

The observed request log contains no template, pairing, `/v1/agents/`, existing-project, or Project
Binding route/field. The demand-side mapping catalog remains allowed only at
`rules/default/component-mapping-candidates.json`.

## Packaging closure

The checked-in plugin distribution matches a fresh build made only through `scripts/build.mjs`.
Packaging tests verify approved Writer labels, the selection/new-project/review/adjustment/regenerate/
approve/download route tokens, and `defaultMode="writer"`. They also compare the SHA-256 of every
delivery ZIP member against the verified build inputs (`INSTALL.md`, `code.js`, `manifest.json`, and
`ui.html`). The Writer startup source has no template selector, FairyGUI version selector, existing ZIP
requirement, or old four-step headings; update-existing remains an isolated overflow action.

## Fresh GUI attempt

### Figma desktop

- Installed Figma launched with exactly one returned target window, `figma2fgui - Figma`.
- Accessibility text was readable, but native capture failed with
  `SetIsBorderRequired failed: 0x80004002`.
- A state-derived Figma action failed with `coordinate input geometry is unavailable`.
- The currently focused document selection could not be proven to be the authorized neutral
  representative. It was therefore not uploaded.
- No Writer selection/running/review/decision/ready state and no download or repeat download was
  observed in the real plugin.

### FairyGUI Editor 6.1.4

- The installed 6.1.4 executable launched with exactly one `FairyGUI 编辑器` window.
- Native capture failed with the same `0x80004002`; accessibility exposed only the title bar.
- `Ctrl+O` did not produce a targetable open dialog or project window.
- A fresh neutral CLI artifact was prepared and validated (`1426` bytes,
  SHA-256 `bf62cd2789a7d0a44336e28a4c257d4fe91bee34c7673738bdf3f0662217c4ca`), but it was not
  opened in the Editor. No save/close/reopen/modal/hash-after-save claim is made.

The earlier tracked
[`2026-08-20-fgui-6.1.4-new-project-editor-transcript.json`](2026-08-20-fgui-6.1.4-new-project-editor-transcript.json)
remains bounded historical evidence. It is not presented as a fresh run for this batch.

## Regression result

- Python full suite: `1146 passed, 4 skipped`.
- The historical Task 3 handoff now uses the exact truthful phrase `capture/re-capture **all six** PNGs`;
  its evidence scope is unchanged.
- Ruff: passed.
- strict mypy: `58 source files`, no issues.
- Figma plugin: `171 passed`; TypeScript and build passed; packaging checks `5 passed`.
- Web Console: `30 passed`; TypeScript and production build passed.
- `git diff --check`: passed before report finalization.

## Second whole-review fix wave (2026-08-21)

The follow-up contract wave added strict in-progress candidate parsing (artifact metadata is required
only for `awaiting_review` and `approved`), a real `202 regenerating` then authenticated GET-poll
client integration test, latest-generation idempotency, pre-allocation JSON chunk limiting, correlated
plugin locate selection changes, pending/ready/failed preview state, and server stage/progress rendering.
Review evidence is now derived from Plan/manifest relationships, missing source evidence fails closed,
source resources have an authenticated stable-ID route, and package review includes the actual added
component/resource names.

Observed gates in this wave:

- Figma plugin Vitest: `177 passed`.
- Web Console Vitest: `30 passed`.
- strict mypy (four changed Python modules): passed.
- `git diff --check`: passed.
- Focused Python Writer regression: `27 passed, 5 failed`; all five failures currently share the
  asynchronous API build ending at the public `directory-write_failed` boundary, while the same
  workflow's direct focused unit test passes. This is recorded as an unresolved blocker, not success.
- Real Figma/FairyGUI GUI acceptance remains `BLOCKED_BY_LOCAL_GUI_CAPABILITY`; no new screenshot,
  Editor-open, save, reopen, or visual-equivalence claim was made.

### Directory-write investigation and resolution

Temporary stack instrumentation reproduced the API-only failure inside the named
`new-project-build-*` worker. `write_declared_files` raised Windows `WinError 206` while creating a
component XML path nested below
`new-fgui-projects/artifacts/{build_id}/fgui-new-project-*`; the fully expanded path exceeded the
traditional Windows path limit. The direct workflow test passed because its output root was shorter.
This disproved a TestClient teardown or daemon-thread ownership race.

A minimal long-output-path regression reproduced the same `directory-write` gate before the fix.
The single root-cause change stages the temporary project beside the build output directory
(`output.parent`) instead of inside it, retaining same-volume atomic publication while removing the
extra build-ID path segment. The regression and the former failing focused set then passed:
`33 passed`.

Post-fix gates: full Python JUnit recorded `1151 tests`, `0 failures`, `0 errors`, `4 skipped`;
Ruff passed; strict mypy passed for all `58` source files; plugin Vitest/TypeScript remained
`177 passed`; Web Console Vitest/TypeScript remained `30 passed`. The plugin packaging subprocess
was re-attempted but did not produce a complete terminal result in this environment, so the earlier
verified `5 passed` packaging result remains the latest closed packaging evidence.

## Final review closure wave

- Source evidence now uses an explicitly plugin-accessible, ownership-checked resource route. The
  TypeScript client accepts only the exact bounded route shape, and public integration downloads the
  source bytes and verifies their SHA-256. Missing or failed evidence remains approval-blocking.
- Component hierarchy is validated wholly in manifest object-ID space, while geometry/text use the
  combined root and component-definition Plan node maps. Nested root and definition components are
  covered without comparing Plan node IDs to compiled object IDs.
- Worker callbacks persist observable monotonic `converting 5 → checking 55 → packaging 80 →
  awaiting_review 100` stages. Regeneration polling forwards these server views instead of showing an
  old 100% artifact.
- Manual non-locate selection changes abort the local operation, invalidate local review state, reject
  an active server review candidate, and use an operation token so stale promise handlers cannot restore it.
- A repeated Generate after approval creates generation 2 with a new build ID instead of returning the
  approved generation into a review-only path.
- The 360 px review renders source/generated evidence side by side, labels generated-only evidence
  honestly, exposes component-reference counts, keeps tab ARIA references mounted-only, and displays
  selection name plus an abstract structure thumbnail.
- Tracked plugin `dist` was rebuilt from current production sources. Package parity and required current
  route/schema/locate/regeneration tokens passed `5/5`.

Final gates: Python `1153 tests, 0 failures, 0 errors, 4 skipped`; Ruff passed; strict mypy passed for
`58` files; plugin Vitest `177 passed`, TypeScript passed; Web Vitest `30 passed`, TypeScript and Vite
production build passed; package parity `5 passed`; diff check passed. GUI acceptance remains honestly
`BLOCKED_BY_LOCAL_GUI_CAPABILITY`.

The current final review package targets implementation commit `95ea956` at
`.superpowers/sdd/writer-acceptance-final-review-95ea956.md`.

## Final attempt-isolation and lease-recovery wave

- Adjust, regenerate, and approve now share the generation path's operation-token guard across stage
  callbacks, successful continuations, errors, and cleanup. A manual selection change or cancel advances
  the token, aborts the request, clears local review state, and best-effort rejects any server candidate
  already known for that operation. Deferred-promise tests prove late adjustment, regeneration, and
  approval completions cannot restore review, download, or replace idle with a failure.
- Expired new-project recovery now updates every stage it selects (`converting`, `checking`,
  `packaging`, and `regenerating`) and reports only rows actually transitioned. Parameterized tests
  prove expired checking and packaging leases become failed, lose ownership, and are not counted twice.
- Public diagnostic mapping remains intentionally local to its two different trust boundaries: the Web
  panel maps typed workflow errors to user copy, while the Python API owns its public diagnostic
  allow-list. Combining those policies was not a tiny safe change and was therefore not expanded in
  this narrowly scoped wave.

Observed gates for this wave: focused JobStore `27 passed`; Web Console `33 passed`; full Python
`1151 passed, 4 skipped`; Ruff passed; strict mypy passed for `58` files; plugin Vitest `177 passed`;
Web Console TypeScript, Vitest, and production build passed; plugin TypeScript and production build
passed; freshly rebuilt tracked plugin distribution and package parity `5 passed`; `git diff --check`
passed. The first full-Python attempt used a long worktree-local pytest base path and reproduced five
known Windows path-limit failures; the authoritative rerun used the short writable base
`C:\Users\<user>\Documents\figma转fgui\.ptf` and passed. GUI acceptance remains
`BLOCKED_BY_LOCAL_GUI_CAPABILITY`; no new GUI evidence is claimed.

The final review package targets implementation commit `3ee5a2a` at
`.superpowers/sdd/writer-acceptance-final-review-3ee5a2a.md`.

### Live regeneration identity follow-up

Regeneration stage callbacks now synchronously publish the server-authored candidate into the active
candidate ref before scheduling React state. If selection changes or the user cancels in that window,
the invalidation request targets the live replacement build/generation rather than superseded v1.
A deferred regression emits generation 2 with a distinct build ID through `onStage`, keeps the main
regeneration promise pending, changes selection, and proves only generation 2 is rejected while stale
continuations remain unable to restore the review.

Focused gates: Web Console `33 passed`, TypeScript and Vite production build passed; related plugin
project-client/bridge tests `89 passed`; freshly rebuilt tracked plugin dist and package parity
`5 passed`; `git diff --check` passed. No GUI acceptance was rerun, and the honest status remains
`BLOCKED_BY_LOCAL_GUI_CAPABILITY`.
