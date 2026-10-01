# New-Project FairyGUI Writer Test Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and personally run six core Writer acceptance cases, producing a human report, canonical machine results, and one useful screenshot per case.

**Architecture:** A focused acceptance runner calls existing production/public boundaries and writes privacy-safe structured case results. A renderer converts each result into a local evidence card; Playwright captures the six cards. The real FairyGUI case reuses the tracked Editor transcript and performs a fresh single-project GUI gate before finalizing its result.

**Tech Stack:** Python 3.12, pytest, Typer CLI, existing `figma_to_fgui` Writer modules, JSON, HTML/CSS, Playwright, FairyGUI Editor 6.1.4.

## Global Constraints

- Exactly six cases: TC-01, TC-02, AC-01, TC-03, TC-04, TC-05.
- Every case has purpose, prerequisites, steps, expected, actual, PASS/FAIL, and exactly one useful screenshot.
- No Project Binding.
- Village is a generic mapping regression sample only; no page/name/node-ID special case.
- Only one neutral representative project receives real FairyGUI GUI evidence.
- Never record private absolute paths, environment values, raw asset bytes, or private exception chains.
- A missing/mismatched screenshot makes its case fail.

---

### Task 1: Acceptance Result Model and Six-Case Runner

**Files:**
- Create: `scripts/run_new_project_writer_acceptance.py`
- Create: `tests/acceptance/test_new_project_writer_acceptance.py`
- Read: `tests/fixtures/fgui-new-project/`
- Read: `tests/integration/test_village_new_project_regression.py`
- Read: `docs/validation/2026-08-18-fgui-6.1.4-new-project-editor-transcript.json`

**Interfaces:**
- Consumes: production `build_new_project`, CLI loaders, archive validator, existing generic/village fixtures.
- Produces: `run_acceptance(workspace: Path, evidence_root: Path) -> dict[str, object]` and canonical result JSON.

- [ ] **Step 1: Write failing contract tests**

```python
EXPECTED_CASES = {"TC-01", "TC-02", "AC-01", "TC-03", "TC-04", "TC-05"}

def test_acceptance_runner_has_exact_closed_case_set(tmp_path: Path) -> None:
    result = run_acceptance(REPO_ROOT, tmp_path)
    assert {case["id"] for case in result["cases"]} == EXPECTED_CASES
    assert all(case["status"] in {"PASS", "FAIL"} for case in result["cases"])
    assert all(set(case) >= {"purpose", "steps", "expected", "actual"} for case in result["cases"])

def test_acceptance_result_is_privacy_safe_and_canonical(tmp_path: Path) -> None:
    result_path = write_acceptance_results(REPO_ROOT, tmp_path)
    content = result_path.read_bytes()
    assert content.endswith(b"\n")
    assert b"C:\\\\Users" not in content
    assert content == canonical_acceptance_bytes(json.loads(content))
```

- [ ] **Step 2: Run the tests and observe RED**

Run: `python -m pytest tests/acceptance/test_new_project_writer_acceptance.py -q`  
Expected: collection/import failure because the runner does not exist.

- [ ] **Step 3: Implement the closed result schema and production-boundary cases**

```python
def case_result(case_id: str, purpose: str, expected: list[str], actual: list[str], *, passed: bool) -> dict[str, object]:
    return {
        "id": case_id,
        "purpose": purpose,
        "prerequisites": [],
        "steps": [],
        "expected": expected,
        "actual": actual,
        "status": "PASS" if passed else "FAIL",
        "screenshot": f"evidence/new-project-writer/{case_id.lower()}.png",
    }

def canonical_acceptance_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
```

Implement the six cases with these observable gates:

- TC-01 builds the generic fixture and reopens/validates the archive; record exact member names.
- TC-02 builds twice in isolated output directories; record both SHA-256 values and byte equality.
- AC-01 initially consumes the durable Editor transcript and marks `guiActionPending=true`; Task 3 replaces it with a fresh run result.
- TC-03 injects `../escape.png`; assert the stable public rejection and no `.zip` publication.
- TC-04 supplies a sparse file larger than `MAX_ASSET_PAYLOAD_BYTES`; assert rejection before Pillow probe and no publication.
- TC-05 compiles the demand-side mapping through the generic path; assert `fgui.component.definition_missing`, no publication, and the production special-case scan passes.

- [ ] **Step 4: Run focused tests and observe GREEN**

Run: `python -m pytest tests/acceptance/test_new_project_writer_acceptance.py -q`  
Expected: all Task 1 tests pass and a temporary canonical JSON result contains exactly six cases.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_new_project_writer_acceptance.py tests/acceptance/test_new_project_writer_acceptance.py
git commit -m "test: add Writer acceptance runner"
```

---

### Task 2: Evidence Cards, Screenshot Capture, and Cross-Checks

**Files:**
- Modify: `scripts/run_new_project_writer_acceptance.py`
- Modify: `tests/acceptance/test_new_project_writer_acceptance.py`
- Create: `docs/validation/evidence/new-project-writer/.gitkeep`

**Interfaces:**
- Consumes: canonical result dictionary from Task 1.
- Produces: `render_evidence_cards(result, output: Path) -> tuple[Path, ...]` and six `1440x1000` PNG screenshots.

- [ ] **Step 1: Write failing renderer and evidence-closure tests**

```python
def test_each_case_has_one_closed_evidence_card(tmp_path: Path) -> None:
    result = run_acceptance(REPO_ROOT, tmp_path)
    cards = render_evidence_cards(result, tmp_path / "cards")
    assert len(cards) == 6
    assert {path.stem for path in cards} == {case["id"].lower() for case in result["cases"]}
    for case in result["cases"]:
        html = (tmp_path / "cards" / f"{case['id'].lower()}.html").read_text("utf-8")
        assert case["id"] in html
        assert case["status"] in html
        assert all(item in html for item in case["actual"])
```

- [ ] **Step 2: Run renderer tests and observe RED**

Run: `python -m pytest tests/acceptance/test_new_project_writer_acceptance.py -q`  
Expected: failure because `render_evidence_cards` is missing.

- [ ] **Step 3: Implement deterministic local evidence cards**

Each card must show case ID, status, purpose, expected vs actual, and the decisive evidence. Escape all dynamic HTML with `html.escape`. Use a fixed viewport-friendly layout and no network fonts/assets.

```python
def render_evidence_cards(result: Mapping[str, object], output: Path) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    rendered = []
    for case in result["cases"]:
        target = output / f"{case['id'].lower()}.html"
        target.write_text(render_case_html(case), "utf-8", newline="\n")
        rendered.append(target)
    return tuple(rendered)
```

- [ ] **Step 4: Add screenshot closure checks**

Add a test that requires each referenced PNG to exist, have PNG signature, nonzero dimensions, and a SHA-256 recorded back into the final result. Screenshot creation itself runs through the Playwright step in Task 3; the test accepts an explicit `--screenshots-pending` development mode but final report generation does not.

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m pytest tests/acceptance/test_new_project_writer_acceptance.py -q`  
Expected: renderer/closure tests pass in pending mode.

```bash
git add scripts/run_new_project_writer_acceptance.py tests/acceptance/test_new_project_writer_acceptance.py docs/validation/evidence/new-project-writer/.gitkeep
git commit -m "test: render Writer acceptance evidence cards"
```

---

### Task 3: Execute, Capture, Report, and Verify

**Files:**
- Modify: `docs/validation/2026-08-20-new-project-writer-test-results.json`
- Create: `docs/validation/2026-08-20-new-project-writer-test-acceptance.md`
- Create: `docs/validation/evidence/new-project-writer/tc-01.png`
- Create: `docs/validation/evidence/new-project-writer/tc-02.png`
- Create: `docs/validation/evidence/new-project-writer/ac-01.png`
- Create: `docs/validation/evidence/new-project-writer/tc-03.png`
- Create: `docs/validation/evidence/new-project-writer/tc-04.png`
- Create: `docs/validation/evidence/new-project-writer/tc-05.png`
- Modify: `.claude/memory/wiki.md`

**Interfaces:**
- Consumes: runner/cards from Tasks 1-2 and the neutral `GenericWriterFixture.fairy` Editor artifact.
- Produces: final durable acceptance pack with all links and screenshot hashes closed.

- [ ] **Step 1: Run the automated five-case portion**

Run: `python scripts/run_new_project_writer_acceptance.py --workspace . --output docs/validation/2026-08-20-new-project-writer-test-results.json --cards .acceptance-cards`  
Expected: TC-01, TC-02, TC-03, TC-04, TC-05 PASS; AC-01 reports pending fresh GUI action.

- [ ] **Step 2: Capture the five automated evidence cards**

Open each local card in Chromium with Playwright at `1440x1000`, take a full-page screenshot, and write the corresponding PNG. Re-open each PNG and verify it visibly contains the case ID, PASS status, and decisive evidence text.

- [ ] **Step 3: Execute the real FairyGUI acceptance case**

Use FairyGUI Editor 6.1.4 on only `GenericWriterFixture.fairy`: open → Ctrl+S → close → reopen → Ctrl+S → close. Record the exact returned window title, absence of observed modal windows, and pre/post SHA-256 for the `.fairy`, component XML, package XML, and PNG. Do not claim screenshot/accessibility details the Unity window API cannot provide.

- [ ] **Step 4: Finalize AC-01 and capture its evidence card**

Update the machine result with the fresh action transcript and four hash matches, render `ac-01.html`, and capture `ac-01.png`. The screenshot must display the exact title, two save rounds, zero remaining windows, and 4/4 hash parity.

- [ ] **Step 5: Generate and test the human report**

Generate the Markdown table and six detail sections from the machine result. Each section links exactly one screenshot and repeats purpose, steps, expected, actual, and status. Add tests that all relative links resolve and every screenshot SHA matches the JSON.

- [ ] **Step 6: Run final verification**

Run:

```bash
python -m pytest -q --basetemp C:/Users/<user>/Documents/figma转fgui/source/.pt-acceptance
python -m ruff check .
python -m mypy src
git diff --check
```

Expected: all tests pass; Ruff/mypy/diff-check clean; all six cases PASS and all six screenshots exist.

- [ ] **Step 7: Commit the acceptance pack**

```bash
git add scripts/run_new_project_writer_acceptance.py tests/acceptance/test_new_project_writer_acceptance.py docs/validation/2026-08-20-new-project-writer-test-results.json docs/validation/2026-08-20-new-project-writer-test-acceptance.md docs/validation/evidence/new-project-writer .claude/memory/wiki.md
git commit -m "test: publish Writer acceptance evidence"
```
