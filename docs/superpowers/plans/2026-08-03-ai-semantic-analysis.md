# AI Semantic Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add provider-configurable online AI semantic classification with explicit screenshot consent, strict validation, deterministic fallback, and no change to the plugin-first FairyGUI delivery model.

**Architecture:** A focused Python semantic-analysis layer converts normalized nodes into a minimal prompt, validates structured model output, and merges safe decisions above deterministic rules. The package job exposes a resumable screenshot-consent state only when structure-only analysis is insufficient; the Figma plugin can approve or decline without blocking fallback conversion.

**Tech Stack:** Python 3.11, Pydantic 2, httpx, FastAPI, pytest, TypeScript, React, Vitest, Figma Plugin API.

## Global Constraints

- AI credentials exist only in server configuration and never enter plugin payloads, logs, artifacts, or downloads.
- Default analysis sends only the current normalized selection summary, never the full Figma file.
- A screenshot is uploaded only after explicit consent for the current job.
- AI never writes XML, images, ZIPs, or project files; `generate_staging` remains the only generator.
- Any AI failure or rejected screenshot automatically falls back to deterministic rules and remains downloadable unless existing validation produces an ERROR.
- Unknown nodes, unsafe names, invalid roles, cycles, and geometrically invalid reparenting are ignored with WARNING diagnostics.
- Automatic tests use fake AI transports and never contact a real model.
- Do not copy source or documentation from the unlicensed reference repository.

---

## File Structure

- Create `src/figma_to_fgui/semantic_models.py`: versioned semantic request/result models and allowed vocabulary.
- Create `src/figma_to_fgui/semantic_validation.py`: validate model decisions against a normalized tree and convert them into classification overrides plus diagnostics.
- Create `src/figma_to_fgui/ai_client.py`: provider-neutral OpenAI-compatible HTTP client and redacted failure types.
- Create `src/figma_to_fgui/semantic_analysis.py`: minimal summary builder, structure/screenshot orchestration, and deterministic fallback result.
- Modify `src/figma_to_fgui/models.py`: attach decision source and semantic metadata without weakening frozen/forbid behavior.
- Modify `src/figma_to_fgui/classify.py`: merge validated AI overrides above YAML rules.
- Modify `src/figma_to_fgui/pipeline.py`: optional semantic analyzer boundary.
- Modify `src/figma_to_fgui/service_contracts.py`, `src/figma_to_fgui/api.py`, and `src/figma_to_fgui/job_store.py`: resumable consent protocol and job state.
- Modify `apps/figma-plugin/src/contracts.ts`, `code.ts`, `project-client.ts`, and `ProjectWorkflowPage.tsx`: export screenshot on demand and present consent UI.
- Add focused Python and TypeScript tests beside existing suites; extend the real FastAPI plugin harness.
- Modify `docs/deployment/internal-https.md` and `.env.example`: administrator configuration and smoke procedure.

---

### Task 1: Versioned Semantic Schema

**Files:**
- Create: `src/figma_to_fgui/semantic_models.py`
- Modify: `src/figma_to_fgui/models.py`
- Test: `tests/unit/test_semantic_models.py`

**Interfaces:**
- Consumes: `NormalizedNode`, `Bounds`, and `Diagnostic` from `figma_to_fgui.models`.
- Produces: `SemanticType`, `SemanticDecision`, `SemanticResponse`, `SemanticAnalysisOutcome`, and `DecisionSource`; later tasks import these exact names.

- [ ] **Step 1: Write failing schema tests**

```python
def test_semantic_response_rejects_unknown_type_and_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticResponse.model_validate({"version": 1, "decisions": [{
            "node_id": "1:2", "semantic_type": "SCRIPT", "confidence": 0.9,
            "unexpected": "value",
        }]})

def test_semantic_decision_accepts_bounded_safe_fields() -> None:
    decision = SemanticDecision.model_validate({
        "node_id": "1:2", "semantic_type": "Button", "fgui_name": "SubmitButton",
        "children_roles": {"1:3": "title"}, "state_pages": {"0": "normal"},
        "confidence": 0.91, "risks": ["hover state absent"],
    })
    assert decision.semantic_type is SemanticType.BUTTON
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `pytest tests/unit/test_semantic_models.py -q`

Expected: FAIL with `ModuleNotFoundError: figma_to_fgui.semantic_models`.

- [ ] **Step 3: Implement strict frozen models**

```python
class SemanticType(StrEnum):
    PANEL = "Panel"
    COMPONENT = "Component"
    IMAGE = "Image"
    TEXT = "Text"
    BUTTON = "Button"
    LABEL = "Label"
    LIST = "List"
    SLIDER = "Slider"

class SemanticDecision(FrozenModel):
    node_id: str = Field(min_length=1, max_length=160)
    semantic_type: SemanticType
    fgui_name: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    children_roles: dict[str, Literal["title", "icon", "bar", "grip", "bg"]] = Field(default_factory=dict)
    state_pages: dict[str, str] = Field(default_factory=dict)
    reparent: ReparentSuggestion | None = None
    confidence: float = Field(ge=0, le=1)
    risks: tuple[str, ...] = ()

class SemanticResponse(FrozenModel):
    version: Literal[1] = 1
    decisions: tuple[SemanticDecision, ...]
    screenshot_recommended: bool = False
    screenshot_reason: str | None = Field(default=None, max_length=240)
```

Add `DecisionSource(StrEnum)` with `AI`, `RULE`, and `FALLBACK`, then add `source: DecisionSource = DecisionSource.RULE` and `semantic_name: str | None = None` to `ClassificationDecision`.

- [ ] **Step 4: Run schema and existing model tests**

Run: `pytest tests/unit/test_semantic_models.py tests/unit/test_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/figma_to_fgui/semantic_models.py src/figma_to_fgui/models.py tests/unit/test_semantic_models.py
git commit -m "feat: define semantic analysis contracts"
```

### Task 2: Safe Semantic Decision Validation and Merge

**Files:**
- Create: `src/figma_to_fgui/semantic_validation.py`
- Modify: `src/figma_to_fgui/classify.py`
- Test: `tests/unit/test_semantic_validation.py`
- Test: `tests/unit/test_classify.py`

**Interfaces:**
- Consumes: `SemanticResponse` from Task 1 and normalized roots.
- Produces: `validate_semantic_response(roots, response) -> tuple[tuple[ClassificationDecision, ...], tuple[Diagnostic, ...]]` and `classify_tree(roots, rules, overrides=())`.

- [ ] **Step 1: Write failing safety tests**

```python
def test_rejects_unknown_nodes_cycles_and_out_of_bounds_reparent() -> None:
    decisions, diagnostics = validate_semantic_response(roots, response_with_unsafe_items)
    assert {item.node_id for item in decisions} == {"safe-button"}
    assert {item.code for item in diagnostics} == {
        "semantic.unknown_node", "semantic.reparent_cycle", "semantic.reparent_outside"
    }

def test_ai_override_wins_and_rules_fill_missing_nodes() -> None:
    result = classify_tree(roots, rules, overrides=(ai_button,))
    assert decision(result, "button").source is DecisionSource.AI
    assert decision(result, "text").source is DecisionSource.RULE
```

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/unit/test_semantic_validation.py tests/unit/test_classify.py -q`

Expected: FAIL because validator and `overrides` do not exist.

- [ ] **Step 3: Implement tree indexes and conservative validation**

```python
def validate_semantic_response(roots, response):
    nodes, parents = index_tree(roots)
    accepted, diagnostics = [], []
    for item in response.decisions:
        node = nodes.get(item.node_id)
        if node is None:
            diagnostics.append(warning("semantic.unknown_node", item.node_id))
            continue
        if item.reparent and not safe_reparent(item.node_id, item.reparent.new_parent, nodes, parents):
            diagnostics.append(warning("semantic.reparent_rejected", item.node_id))
            continue
        accepted.append(ClassificationDecision(
            node_id=item.node_id,
            output_type=OUTPUT_TYPES[item.semantic_type],
            rule_id="ai.semantic.v1", rule_version=1,
            evidence=("validated structured AI decision",),
            confidence=item.confidence, source=DecisionSource.AI,
            semantic_name=item.fgui_name,
        ))
    return tuple(accepted), tuple(diagnostics)
```

Implement containment using `Bounds` and reject self-parenting, descendant-parenting, missing targets, and nodes not fully inside the proposed parent.

- [ ] **Step 4: Merge overrides by node ID before ordered rules**

```python
def classify_tree(roots, rules, overrides=()):
    override_by_id = {item.node_id: item for item in overrides}
    decisions = []
    for node in _walk(roots):
        if node.id in override_by_id:
            decisions.append(override_by_id[node.id])
            continue
        decisions.append(_classify_with_rules_or_fallback(node, rules))
    return tuple(decisions)
```

- [ ] **Step 5: Run focused and generation regression tests**

Run: `pytest tests/unit/test_semantic_validation.py tests/unit/test_classify.py tests/unit/test_generate.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/semantic_validation.py src/figma_to_fgui/classify.py tests/unit/test_semantic_validation.py tests/unit/test_classify.py
git commit -m "feat: validate and merge semantic decisions"
```

### Task 3: OpenAI-Compatible Client and Minimal Prompt

**Files:**
- Create: `src/figma_to_fgui/ai_client.py`
- Create: `src/figma_to_fgui/semantic_analysis.py`
- Test: `tests/unit/test_ai_client.py`
- Test: `tests/unit/test_semantic_analysis.py`

**Interfaces:**
- Consumes: normalized roots and `SemanticResponse`.
- Produces: `AIClientConfig`, `OpenAICompatibleSemanticClient.analyze(summary, screenshot=None)`, `build_selection_summary(roots)`, and `analyze_semantics(roots, client) -> SemanticAnalysisOutcome`.

- [ ] **Step 1: Write fake-transport tests**

```python
def test_client_uses_bearer_token_without_exposing_it(httpx_mock) -> None:
    httpx_mock.add_response(json={"choices": [{"message": {"content": valid_json}}]})
    result = client.analyze({"nodes": []})
    assert result.version == 1
    assert httpx_mock.get_request().headers["Authorization"] == "Bearer secret-value"

@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_failures_raise_redacted_error(status, httpx_mock) -> None:
    httpx_mock.add_response(status_code=status, text="server included secret-value")
    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": []})
    assert "secret-value" not in str(error.value)
```

Use `httpx.MockTransport` if the repository does not already use `pytest-httpx`; do not add a dependency solely for these tests.

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_ai_client.py tests/unit/test_semantic_analysis.py -q`

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement a bounded compatible client**

```python
class AIClientConfig(FrozenModel):
    provider: Literal["openai", "openai_compatible"]
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=120)
    api_key: SecretStr
    timeout_seconds: float = Field(default=20, ge=1, le=120)

class OpenAICompatibleSemanticClient:
    def analyze(self, summary: dict[str, object], screenshot: bytes | None = None) -> SemanticResponse:
        payload = build_chat_completion_payload(self.config.model, summary, screenshot)
        response = self.http.post("/chat/completions", json=payload, headers=self._headers())
        response.raise_for_status()
        return SemanticResponse.model_validate_json(extract_content(response.json()))
```

Cap node count, string length, total serialized summary bytes, response bytes, and screenshot bytes. Convert transport, status, JSON, and validation failures into stable reason codes without response bodies.

- [ ] **Step 4: Implement minimal deterministic summary and fallback outcome**

```python
def build_selection_summary(roots):
    return {"version": 1, "nodes": [summarize(node, parent_id=None) for node in roots]}

def analyze_semantics(roots, client):
    if client is None:
        return SemanticAnalysisOutcome(overrides=(), diagnostics=(fallback_warning("disabled"),))
    try:
        response = client.analyze(build_selection_summary(roots))
    except AIAnalysisError as error:
        return SemanticAnalysisOutcome(overrides=(), diagnostics=(fallback_warning(error.code),))
    overrides, diagnostics = validate_semantic_response(roots, response)
    return SemanticAnalysisOutcome(
        overrides=overrides,
        diagnostics=diagnostics,
        screenshot_recommended=response.screenshot_recommended,
        screenshot_reason=response.screenshot_reason,
    )
```

- [ ] **Step 5: Run client, analyzer, lint, and type checks**

Run: `pytest tests/unit/test_ai_client.py tests/unit/test_semantic_analysis.py -q && ruff check src/figma_to_fgui/ai_client.py src/figma_to_fgui/semantic_analysis.py && mypy src/figma_to_fgui/ai_client.py src/figma_to_fgui/semantic_analysis.py`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/ai_client.py src/figma_to_fgui/semantic_analysis.py tests/unit/test_ai_client.py tests/unit/test_semantic_analysis.py
git commit -m "feat: add compatible AI semantic client"
```

### Task 4: Conversion Pipeline Integration and Deterministic Fallback

**Files:**
- Modify: `src/figma_to_fgui/pipeline.py`
- Modify: `src/figma_to_fgui/generate.py`
- Test: `tests/unit/test_pipeline.py`
- Test: `tests/integration/test_pipeline.py`

**Interfaces:**
- Consumes: optional `SemanticAnalyzer` protocol with `analyze(roots, screenshot=None)`.
- Produces: `convert_document(..., semantic_analyzer=None, screenshot=None)` with diagnostics merged into the existing `ChangeSet`.

- [ ] **Step 1: Write failing integration tests**

```python
def test_valid_ai_override_changes_component_type(tmp_path: Path) -> None:
    result = convert_document(raw, project, "Sample", staging, rules, semantic_analyzer=fake_ai)
    assert fake_ai.calls == 1
    assert "semantic.ai_applied" in diagnostic_codes(result)

def test_ai_failure_generates_same_files_as_rules_only(tmp_path: Path) -> None:
    baseline = run_conversion(semantic_analyzer=None)
    degraded = run_conversion(semantic_analyzer=failing_ai)
    assert file_hashes(degraded) == file_hashes(baseline)
    assert "semantic.fallback" in diagnostic_codes(degraded)
```

- [ ] **Step 2: Verify the new arguments fail**

Run: `pytest tests/unit/test_pipeline.py tests/integration/test_pipeline.py -q`

Expected: FAIL because `semantic_analyzer` is not accepted.

- [ ] **Step 3: Insert analysis after normalization and before classification**

```python
roots, normalization_diagnostics = normalize_document(raw)
semantic = semantic_analyzer.analyze(roots, screenshot=screenshot) if semantic_analyzer else empty_semantic_outcome()
decisions = classify_tree(roots, load_rules(classification_rules), overrides=semantic.overrides)
```

Merge `semantic.diagnostics` between normalization and generation diagnostics. Do not let the analyzer receive `project_root`, selection asset bytes, tokens, or staging paths.

- [ ] **Step 4: Apply semantic names only through safe generator inputs**

Use `ClassificationDecision.semantic_name` when present, otherwise retain the existing name. Reuse existing collision and safe-path checks; never use an AI name as a path before validation.

- [ ] **Step 5: Run conversion regressions**

Run: `pytest tests/unit/test_pipeline.py tests/unit/test_generate.py tests/integration/test_pipeline.py -q`

Expected: PASS with identical fallback file hashes.

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/pipeline.py src/figma_to_fgui/generate.py tests/unit/test_pipeline.py tests/integration/test_pipeline.py
git commit -m "feat: integrate semantic analysis into conversion"
```

### Task 5: Resumable Screenshot Consent API

**Files:**
- Modify: `src/figma_to_fgui/service_contracts.py`
- Modify: `src/figma_to_fgui/job_store.py`
- Modify: `src/figma_to_fgui/api.py`
- Test: `tests/unit/test_api.py`
- Test: `tests/unit/test_job_store.py`

**Interfaces:**
- Produces: package stage `awaiting_screenshot_consent`; `ScreenshotConsentRequest(approved: bool)`; endpoints `POST /v1/jobs/{job_id}/semantic-screenshot-consent` and `POST /v1/jobs/{job_id}/semantic-screenshot`.
- Consumes: a PNG/WebP screenshot bounded by the existing selection image limit and tied to the authenticated job owner.

- [ ] **Step 1: Write contract and authorization tests**

```python
def test_low_confidence_job_waits_without_packaging(client) -> None:
    job = create_low_confidence_job(client)
    view = client.get(f"/v1/jobs/{job}/package", headers=plugin_headers).json()
    assert view["stage"] == "awaiting_screenshot_consent"
    assert view["screenshot_reason"]

def test_decline_resumes_with_fallback(client) -> None:
    response = client.post(f"/v1/jobs/{job}/semantic-screenshot-consent", json={"version": 1, "approved": False}, headers=plugin_headers)
    assert response.status_code == 202
    assert wait_ready(client, job)["diagnostics"][0]["code"] == "semantic.screenshot_declined"

def test_upload_requires_prior_consent_and_png_or_webp(client) -> None:
    assert upload_screenshot(client, job, b"bad", "text/plain").status_code == 415
    assert upload_screenshot(client, other_users_job, png, "image/png").status_code == 404
```

- [ ] **Step 2: Run API tests and verify missing state**

Run: `pytest tests/unit/test_api.py tests/unit/test_job_store.py -q`

Expected: FAIL because the stage and endpoints do not exist.

- [ ] **Step 3: Add versioned protocol fields and idempotent transitions**

Extend `ProjectPackageStage` and `ProjectPackageView` with optional `screenshot_reason`. Store only consent state, screenshot digest, and temporary path. Repeated identical consent returns the same state; conflicting consent returns HTTP 409.

- [ ] **Step 4: Add guarded API endpoints**

```python
@app.post("/v1/jobs/{job_id}/semantic-screenshot-consent", status_code=202)
def semantic_screenshot_consent(job_id: str, payload: ScreenshotConsentRequest) -> ProjectPackageView:
    return package_service.record_screenshot_consent(plugin_principal(), job_id, payload.approved)

@app.post("/v1/jobs/{job_id}/semantic-screenshot", status_code=202)
async def semantic_screenshot(job_id: str, request: Request) -> ProjectPackageView:
    media_type = require_media_type(request, {"image/png", "image/webp"})
    body = await bounded_body(request, MAX_SEMANTIC_SCREENSHOT_BYTES)
    return package_service.attach_semantic_screenshot(plugin_principal(), job_id, media_type, body)
```

Ensure rejection resumes conversion immediately and that accepted consent alone does not upload anything.

- [ ] **Step 5: Run API, CORS, auth, and package tests**

Run: `pytest tests/unit/test_api.py tests/unit/test_job_store.py tests/unit/test_project_package.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/service_contracts.py src/figma_to_fgui/job_store.py src/figma_to_fgui/api.py tests/unit/test_api.py tests/unit/test_job_store.py
git commit -m "feat: add screenshot consent protocol"
```

### Task 6: Figma Screenshot Export and Consent UI

**Files:**
- Modify: `apps/figma-plugin/src/contracts.ts`
- Modify: `apps/figma-plugin/src/code.ts`
- Modify: `apps/figma-plugin/src/project-client.ts`
- Modify: `apps/web-console/src/figma/ProjectWorkflowPage.tsx`
- Test: `apps/figma-plugin/src/bridge.test.ts`
- Test: `apps/figma-plugin/src/project-client.test.ts`
- Test: `apps/web-console/src/figma/ProjectWorkflowPage.test.tsx`

**Interfaces:**
- Adds bridge request/response `semantic-screenshot-export` carrying only PNG bytes for the snapshotted current selection.
- Adds client callbacks `onScreenshotConsent(request) -> Promise<boolean>` and `requestSemanticScreenshot(jobId, screenshot)`.

- [ ] **Step 1: Write failing bridge, client, and UI tests**

```ts
it("exports only the snapshotted selection after consent", async () => {
  figmaRuntime.ui.onmessage!({ type: "semantic-screenshot-export", attempt: "a1" }, origin);
  expect(selectedNode.exportAsync).toHaveBeenCalledWith({ format: "PNG", constraint: { type: "SCALE", value: 1 } });
});

it("declining screenshot resumes without requesting export", async () => {
  await runWorkflow({ onScreenshotConsent: async () => false });
  expect(fetchImpl).toHaveBeenCalledWith(expect.stringContaining("semantic-screenshot-consent"), expect.objectContaining({ body: JSON.stringify({ version: 1, approved: false }) }));
  expect(postToFigma).not.toHaveBeenCalledWith(expect.objectContaining({ type: "semantic-screenshot-export" }));
});
```

- [ ] **Step 2: Run plugin tests and verify failures**

Run: `pnpm --dir apps/figma-plugin test && pnpm --dir apps/web-console test`

Expected: FAIL on missing messages, stage parser, and consent UI.

- [ ] **Step 3: Add snapshot-bound screenshot export**

```ts
// Append these members to the existing unions in contracts.ts without changing
// the existing selection-preflight and selection-export members.
| { type: "semantic-screenshot-export"; attempt: string }

| { type: "semantic-screenshot-export"; attempt: string; mimeType: "image/png"; bytes: Uint8Array }
```

Reject empty or changed selection snapshots and bound output dimensions/bytes before posting to the UI.

- [ ] **Step 4: Parse waiting stage and implement consent flow**

When polling returns `awaiting_screenshot_consent`, pause the normal package loop and call the UI callback once. On approval, send consent, request the bridge export, upload bytes, then resume polling. On rejection, send rejection and resume polling without exporting.

- [ ] **Step 5: Render plain-language consent UI**

Display: `仅靠图层结构无法可靠判断部分组件。是否允许上传当前选择的截图辅助识别？` with `允许并继续` and `不上传，按规则继续`. Lock other workflow controls while awaiting the choice; preserve the original workflow parameter snapshot.

- [ ] **Step 6: Run focused tests and deterministic build check**

Run: `pnpm --dir apps/figma-plugin test && pnpm --dir apps/web-console test && pnpm --dir apps/figma-plugin run build:check`

Expected: all PASS and checked-in bundles match deterministic rebuilds.

- [ ] **Step 7: Commit**

```bash
git add apps/figma-plugin/src apps/web-console/src/figma apps/figma-plugin/dist
git commit -m "feat: add semantic screenshot consent flow"
```

### Task 7: End-to-End Proof, Configuration, and Redaction

**Files:**
- Modify: `.env.example`
- Modify: `docs/deployment/internal-https.md`
- Modify: `apps/figma-plugin/test/fastapi-service.ts`
- Modify: `apps/figma-plugin/test/plugin-harness.test.ts`
- Create: `tests/integration/test_ai_semantic_package.py`

**Interfaces:**
- Consumes all prior tasks.
- Produces a fake OpenAI-compatible service fixture and documented administrator smoke procedure.

- [ ] **Step 1: Write three end-to-end scenarios**

```python
@pytest.mark.parametrize("scenario", ["structure_success", "screenshot_success", "ai_failure"])
def test_ai_semantic_package_scenarios(scenario, fake_ai_server, project_fixture):
    result = run_package_workflow(scenario, fake_ai_server, project_fixture)
    assert result.archive_is_valid
    assert result.xml_is_valid
    assert result.original_project_sha256 == result.original_project_sha256_after
```

Extend the TypeScript harness to prove the plugin completes structure-only, consented screenshot, declined screenshot, and server failure workflows against the real FastAPI app.

- [ ] **Step 2: Run scenarios and verify missing fake service integration**

Run: `pytest tests/integration/test_ai_semantic_package.py -q && pnpm --dir apps/figma-plugin test -- plugin-harness.test.ts`

Expected: FAIL until configuration and fixtures are wired.

- [ ] **Step 3: Add explicit server configuration documentation**

```ini
AI_SEMANTIC_ENABLED=false
AI_SEMANTIC_PROVIDER=openai
AI_SEMANTIC_BASE_URL=https://api.openai.com/v1
AI_SEMANTIC_MODEL=
AI_SEMANTIC_API_KEY=
AI_SEMANTIC_TIMEOUT_SECONDS=20
AI_SEMANTIC_CONFIDENCE_THRESHOLD=0.75
```

Document that `openai_compatible` requires an administrator-approved HTTPS URL, production startup fails closed if enabled fields are incomplete, and real smoke tests use only a non-sensitive Figma fixture.

- [ ] **Step 4: Add redaction assertions**

Capture application logs for successful, 429, invalid JSON, and 500 responses. Assert the API key, Authorization header, node text, prompt, screenshot bytes, and model response body are absent.

- [ ] **Step 5: Run the complete verification matrix**

Run: `pytest -q`

Expected: all Python tests PASS with only documented skips.

Run: `ruff check src tests && mypy src`

Expected: PASS.

Run: `pnpm --dir apps/figma-plugin test && pnpm --dir apps/web-console test && pnpm --dir apps/figma-plugin run build:check`

Expected: all TypeScript tests and deterministic packaging checks PASS.

- [ ] **Step 6: Commit**

```bash
git add .env.example docs/deployment/internal-https.md apps/figma-plugin/test tests/integration/test_ai_semantic_package.py
git commit -m "test: verify AI semantic delivery fallback"
```

### Task 8: Final Review and Release Evidence

**Files:**
- Modify: `docs/acceptance/figma-plugin-checklist.md`

**Interfaces:**
- Consumes the verified build artifact and administrator fake/real smoke results.
- Produces a release checklist that distinguishes automated proof from manual Figma/FairyGUI acceptance.

- [ ] **Step 1: Record automated evidence**

Add the exact commit, test counts, build archive SHA-256, fake-provider scenarios, and confirmation that no real provider was contacted by the default suite.

- [ ] **Step 2: Perform administrator smoke checks**

With a non-sensitive test file, run one official OpenAI structure-only request and one company-compatible request. Confirm redacted logs, then disable credentials. Do not record keys, prompts, node text, screenshots, or raw responses.

- [ ] **Step 3: Perform manual desktop acceptance**

Import the built plugin in Figma Desktop, run one structure-only task, one consented screenshot task, one declined screenshot task, and one disconnected-provider fallback. Open the resulting create/update ZIPs in the supported FairyGUI editor and record pass/fail without claiming unperformed checks.

- [ ] **Step 4: Re-run release checks after evidence edits**

Run: `git diff --check && pytest -q && pnpm --dir apps/figma-plugin run build:check`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/acceptance/figma-plugin-checklist.md
git commit -m "docs: record AI semantic acceptance"
```
