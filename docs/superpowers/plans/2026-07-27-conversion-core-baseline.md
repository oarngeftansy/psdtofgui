# Conversion Core Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic offline conversion core that turns a normalized Figma fixture plus a read-only FGUI project snapshot into a validated, reviewable changeset.

**Architecture:** A Python package implements immutable pipeline stages: normalize Figma data, index an FGUI snapshot, classify nodes with versioned rules, plan resources, generate staging files, validate them, and build a hashed changeset. A Typer CLI exposes each stage independently and an end-to-end `convert` command composes them without modifying the source project.

**Tech Stack:** Python 3.11+, Pydantic 2, Typer, Pillow, lxml, PyYAML, pytest, Ruff, mypy

## Global Constraints

- The conversion core must not modify the real FGUI project; it writes only to an explicit staging directory.
- All project paths are relative POSIX-style paths and must reject absolute paths and `..` segments.
- Internal geometry preserves floats; FGUI serialization rounds with Python `round()` and records every changed value as an `INFO` diagnostic.
- New FGUI resource IDs are exactly eight lowercase alphanumeric characters and unique within the target package.
- Visual nodes default to image output; simple geometry may use `graph` only when an explicit project rule allows it.
- Every classification decision records rule ID, rule version, evidence, and confidence.
- Any `ERROR` diagnostic prevents a changeset from being marked applicable.
- The same input snapshot, Figma fixture, and rule version must produce byte-identical JSON and XML outputs.
- Python code uses UTF-8, type annotations, frozen Pydantic models, and no machine-specific absolute paths.

---

## File Map

```text
pyproject.toml                         Build, dependencies, test/lint configuration
src/figma_to_fgui/__init__.py         Package version
src/figma_to_fgui/models.py           Shared immutable contracts
src/figma_to_fgui/paths.py            Safe relative-path validation
src/figma_to_fgui/normalize.py        Raw Figma JSON normalization
src/figma_to_fgui/project_index.py    Read-only FGUI snapshot index
src/figma_to_fgui/rules.py            Versioned YAML rule loading and matching
src/figma_to_fgui/classify.py         Node classification decisions
src/figma_to_fgui/assets.py           Resource planning and deterministic IDs
src/figma_to_fgui/generate.py         Staging XML and package manifest generation
src/figma_to_fgui/validate.py         XML, reference, path, and policy validation
src/figma_to_fgui/changeset.py        Hashed diff construction
src/figma_to_fgui/pipeline.py         End-to-end stage composition
src/figma_to_fgui/cli.py              Atomic CLI commands
rules/default/classification.yaml     Default classification rules
rules/default/validation.yaml         Default validation policies
tests/fixtures/figma/simple-frame.json Minimal Figma input
tests/fixtures/fgui/Sample/package.xml Minimal FGUI snapshot
tests/fixtures/fgui/Sample/Panel/.gitkeep
tests/unit/                           Focused unit tests
tests/golden/                         Deterministic output tests
```

## Task 1: Package Skeleton and Immutable Contracts

**Files:**
- Create: `pyproject.toml`
- Create: `src/figma_to_fgui/__init__.py`
- Create: `src/figma_to_fgui/models.py`
- Create: `tests/unit/test_models.py`

**Interfaces:**
- Consumes: None
- Produces: `Bounds`, `Diagnostic`, `NormalizedNode`, `ProjectResource`, `ClassificationDecision`, `ResourcePlan`, `GeneratedFile`, and `ChangeSet`

- [ ] **Step 1: Write the failing contract test**

```python
# tests/unit/test_models.py
import pytest
from pydantic import ValidationError

from figma_to_fgui.models import Bounds, Diagnostic, Severity


def test_contracts_are_immutable_and_validate_geometry() -> None:
    bounds = Bounds(x=1.5, y=2.5, width=100, height=50)
    with pytest.raises(ValidationError):
        bounds.width = 101
    with pytest.raises(ValidationError):
        Bounds(x=0, y=0, width=-1, height=10)


def test_diagnostic_has_stable_code_and_context() -> None:
    diagnostic = Diagnostic(
        code="geometry.rounded",
        severity=Severity.INFO,
        message="Rounded width from 10.4 to 10",
        node_id="12:34",
        rule_id="geometry.integer-output",
        rule_version=1,
    )
    assert diagnostic.code == "geometry.rounded"
```

- [ ] **Step 2: Run the test and verify the import failure**

Run: `python -m pytest tests/unit/test_models.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'figma_to_fgui'`.

- [ ] **Step 3: Add package configuration**

```toml
# pyproject.toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "figma-to-fgui-core"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "lxml>=5.2,<6",
  "pillow>=10.4,<12",
  "pydantic>=2.8,<3",
  "pyyaml>=6.0,<7",
  "typer>=0.12,<1",
]

[project.optional-dependencies]
dev = ["mypy>=1.11,<2", "pytest>=8.3,<9", "ruff>=0.6,<1"]

[project.scripts]
fgui-tool = "figma_to_fgui.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/figma_to_fgui"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.mypy]
python_version = "3.11"
strict = true
```

```python
# src/figma_to_fgui/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 4: Implement the immutable contracts**

```python
# src/figma_to_fgui/models.py
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class Bounds(FrozenModel):
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)


class Diagnostic(FrozenModel):
    code: str
    severity: Severity
    message: str
    node_id: str | None = None
    path: str | None = None
    rule_id: str | None = None
    rule_version: int | None = None


class NormalizedNode(FrozenModel):
    id: str
    name: str
    type: str
    bounds: Bounds
    children: tuple[NormalizedNode, ...] = ()
    text: str | None = None
    rotation: float = 0
    source_order: int = 0
    properties: dict[str, str] = Field(default_factory=dict)
    raw_style: dict[str, Any] = Field(default_factory=dict)


class ProjectResource(FrozenModel):
    id: str
    name: str
    kind: str
    package_id: str
    relative_path: str


class ClassificationDecision(FrozenModel):
    node_id: str
    output_type: str
    rule_id: str
    rule_version: int
    evidence: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class ResourcePlan(FrozenModel):
    node_id: str
    action: str
    resource_name: str
    resource_id: str
    relative_path: str


class GeneratedFile(FrozenModel):
    relative_path: str
    sha256: str
    size: int = Field(ge=0)


class ChangeSet(FrozenModel):
    version: int = 1
    applicable: bool
    source_snapshot_sha256: str
    files: tuple[GeneratedFile, ...]
    diagnostics: tuple[Diagnostic, ...]
```

- [ ] **Step 5: Install dependencies and run contract checks**

Run: `python -m pip install -e ".[dev]"`

Expected: installation succeeds and exposes `fgui-tool`.

Run: `python -m pytest tests/unit/test_models.py -v`

Expected: 2 tests PASS.

Run: `python -m mypy src/figma_to_fgui/models.py`

Expected: `Success: no issues found`.

- [ ] **Step 6: Commit the contracts**

```powershell
git add pyproject.toml src/figma_to_fgui/__init__.py src/figma_to_fgui/models.py tests/unit/test_models.py
git commit -m "feat: add conversion core contracts"
```

## Task 2: Safe Paths and Figma Normalization

**Files:**
- Create: `src/figma_to_fgui/paths.py`
- Create: `src/figma_to_fgui/normalize.py`
- Create: `tests/unit/test_paths.py`
- Create: `tests/unit/test_normalize.py`
- Create: `tests/fixtures/figma/simple-frame.json`

**Interfaces:**
- Consumes: `Bounds`, `Diagnostic`, `NormalizedNode`
- Produces: `safe_relative_path(value: str) -> str` and `normalize_document(raw: dict[str, object]) -> tuple[NormalizedNode, tuple[Diagnostic, ...]]`

- [ ] **Step 1: Write failing path-security tests**

```python
# tests/unit/test_paths.py
import pytest

from figma_to_fgui.paths import safe_relative_path


@pytest.mark.parametrize("value", ["../package.xml", "/tmp/x", "C:/x", "Panel/../../x"])
def test_rejects_paths_outside_project(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe relative path"):
        safe_relative_path(value)


def test_normalizes_windows_separators() -> None:
    assert safe_relative_path(r"Sample\Panel\Main.xml") == "Sample/Panel/Main.xml"
```

- [ ] **Step 2: Write the failing normalization test and fixture**

```json
{
  "id": "1:1",
  "name": "Main",
  "type": "FRAME",
  "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": 1080.0, "height": 1920.0},
  "children": [
    {
      "id": "1:2",
      "name": "Title",
      "type": "TEXT",
      "absoluteBoundingBox": {"x": 100.2, "y": 50.7, "width": 300.0, "height": 40.0},
      "characters": "Hello",
      "style": {"fontSize": 32, "textAlignHorizontal": "CENTER"}
    }
  ]
}
```

```python
# tests/unit/test_normalize.py
import json
from pathlib import Path

from figma_to_fgui.normalize import normalize_document


def test_normalizes_children_without_rounding_geometry() -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, diagnostics = normalize_document(raw)
    assert roots[0].children[0].bounds.x == 100.2
    assert roots[0].children[0].text == "Hello"
    assert roots[0].children[0].source_order == 0
    assert diagnostics == ()
```

- [ ] **Step 3: Run both test modules and verify failure**

Run: `python -m pytest tests/unit/test_paths.py tests/unit/test_normalize.py -v`

Expected: FAIL because `paths` and `normalize` do not exist.

- [ ] **Step 4: Implement safe path normalization**

```python
# src/figma_to_fgui/paths.py
from pathlib import PurePosixPath


def safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ":" in path.parts[0] or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {value}")
    return path.as_posix()
```

- [ ] **Step 5: Implement recursive Figma normalization**

```python
# src/figma_to_fgui/normalize.py
from typing import Any

from figma_to_fgui.models import Bounds, Diagnostic, NormalizedNode


def _node(raw: dict[str, Any], source_order: int) -> NormalizedNode:
    box = raw["absoluteBoundingBox"]
    children = tuple(_node(child, index) for index, child in enumerate(raw.get("children", [])))
    properties = {
        name: str(value.get("value", ""))
        for name, value in raw.get("componentProperties", {}).items()
    }
    return NormalizedNode(
        id=str(raw["id"]),
        name=str(raw.get("name", "")),
        type=str(raw["type"]),
        bounds=Bounds(x=box["x"], y=box["y"], width=box["width"], height=box["height"]),
        children=children,
        text=raw.get("characters"),
        rotation=float(raw.get("rotation", 0)),
        source_order=source_order,
        properties=properties,
        raw_style=dict(raw.get("style", {})),
    )


def normalize_document(
    raw: dict[str, object],
) -> tuple[tuple[NormalizedNode, ...], tuple[Diagnostic, ...]]:
    return (_node(raw, 0),), ()
```

- [ ] **Step 6: Run tests and static checks**

Run: `python -m pytest tests/unit/test_paths.py tests/unit/test_normalize.py -v`

Expected: 6 tests PASS.

Run: `python -m ruff check src/figma_to_fgui/paths.py src/figma_to_fgui/normalize.py tests/unit`

Expected: `All checks passed!`.

- [ ] **Step 7: Commit normalization**

```powershell
git add src/figma_to_fgui/paths.py src/figma_to_fgui/normalize.py tests/unit tests/fixtures/figma/simple-frame.json
git commit -m "feat: normalize figma nodes safely"
```

## Task 3: Read-Only FGUI Project Index

**Files:**
- Create: `src/figma_to_fgui/project_index.py`
- Create: `tests/unit/test_project_index.py`
- Create: `tests/fixtures/fgui/Sample/package.xml`
- Create: `tests/fixtures/fgui/Sample/Panel/.gitkeep`

**Interfaces:**
- Consumes: `ProjectResource`, `safe_relative_path`
- Produces: `ProjectIndex` and `index_project(root: Path) -> ProjectIndex`

- [ ] **Step 1: Add a minimal FGUI package fixture**

```xml
<?xml version="1.0" encoding="utf-8"?>
<packageDescription id="sample01" name="Sample">
  <resources>
    <image id="img00001" name="Bg_Main.png" path="/Img/" exported="true"/>
    <component id="cmp00001" name="Existing.xml" path="/Panel/" exported="true"/>
  </resources>
</packageDescription>
```

- [ ] **Step 2: Write the failing index test**

```python
# tests/unit/test_project_index.py
from pathlib import Path

from figma_to_fgui.project_index import index_project


def test_indexes_package_resources_without_mutating_snapshot() -> None:
    root = Path("tests/fixtures/fgui")
    before = (root / "Sample/package.xml").read_bytes()
    index = index_project(root)
    assert index.packages["Sample"] == "sample01"
    assert index.by_name["Bg_Main.png"].id == "img00001"
    assert index.by_name["Existing.xml"].kind == "component"
    assert (root / "Sample/package.xml").read_bytes() == before
```

- [ ] **Step 3: Run the test and verify failure**

Run: `python -m pytest tests/unit/test_project_index.py -v`

Expected: FAIL because `project_index` does not exist.

- [ ] **Step 4: Implement the read-only indexer**

```python
# src/figma_to_fgui/project_index.py
from pathlib import Path

from lxml import etree
from pydantic import Field

from figma_to_fgui.models import FrozenModel, ProjectResource
from figma_to_fgui.paths import safe_relative_path


class ProjectIndex(FrozenModel):
    packages: dict[str, str] = Field(default_factory=dict)
    by_name: dict[str, ProjectResource] = Field(default_factory=dict)
    ids_by_package: dict[str, frozenset[str]] = Field(default_factory=dict)


def index_project(root: Path) -> ProjectIndex:
    packages: dict[str, str] = {}
    by_name: dict[str, ProjectResource] = {}
    ids: dict[str, set[str]] = {}
    for manifest in sorted(root.glob("*/package.xml")):
        tree = etree.parse(str(manifest))
        package = tree.getroot()
        package_name = manifest.parent.name
        package_id = str(package.attrib["id"])
        packages[package_name] = package_id
        ids[package_name] = set()
        for element in package.xpath("./resources/*"):
            kind = str(element.tag)
            resource_id = str(element.attrib["id"])
            name = str(element.attrib["name"])
            path = str(element.attrib.get("path", "/"))
            relative = safe_relative_path(f"{package_name}/{path.strip('/')}/{name}")
            resource = ProjectResource(
                id=resource_id,
                name=name,
                kind=kind,
                package_id=package_id,
                relative_path=relative,
            )
            by_name[name] = resource
            ids[package_name].add(resource_id)
    return ProjectIndex(
        packages=packages,
        by_name=by_name,
        ids_by_package={name: frozenset(values) for name, values in ids.items()},
    )
```

- [ ] **Step 5: Run the index test**

Run: `python -m pytest tests/unit/test_project_index.py -v`

Expected: 1 test PASS.

- [ ] **Step 6: Commit the indexer**

```powershell
git add src/figma_to_fgui/project_index.py tests/unit/test_project_index.py tests/fixtures/fgui
git commit -m "feat: index fgui project snapshots"
```

## Task 4: Versioned Classification Rules

**Files:**
- Create: `rules/default/classification.yaml`
- Create: `src/figma_to_fgui/rules.py`
- Create: `src/figma_to_fgui/classify.py`
- Create: `tests/unit/test_classify.py`

**Interfaces:**
- Consumes: `NormalizedNode`, `ClassificationDecision`
- Produces: `Rule`, `load_rules(path: Path) -> tuple[Rule, ...]`, and `classify_tree(roots, rules) -> tuple[ClassificationDecision, ...]`

- [ ] **Step 1: Define the first explicit rules**

```yaml
# rules/default/classification.yaml
rules:
  - id: node.text
    version: 1
    priority: 1000
    when:
      type: TEXT
    action:
      outputType: TEXT
    confidence: 1.0
  - id: node.top-level-panel
    version: 1
    priority: 900
    when:
      type: FRAME
      minWidth: 540
      minChildren: 1
    action:
      outputType: PANEL
    confidence: 0.95
  - id: node.inline-fallback
    version: 1
    priority: 0
    when: {}
    action:
      outputType: INLINE
    confidence: 0.5
```

- [ ] **Step 2: Write the failing classification test**

```python
# tests/unit/test_classify.py
import json
from pathlib import Path

from figma_to_fgui.classify import classify_tree
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.rules import load_rules


def test_classification_records_rule_evidence_and_confidence() -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    by_node = {decision.node_id: decision for decision in decisions}
    assert by_node["1:1"].output_type == "PANEL"
    assert by_node["1:1"].rule_id == "node.top-level-panel"
    assert by_node["1:2"].output_type == "TEXT"
    assert by_node["1:2"].confidence == 1.0
```

- [ ] **Step 3: Run the test and verify failure**

Run: `python -m pytest tests/unit/test_classify.py -v`

Expected: FAIL because rule modules do not exist.

- [ ] **Step 4: Implement rule loading**

```python
# src/figma_to_fgui/rules.py
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from figma_to_fgui.models import FrozenModel


class Rule(FrozenModel):
    id: str
    version: int
    priority: int
    when: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, str]
    confidence: float


def load_rules(path: Path) -> tuple[Rule, ...]:
    data = yaml.safe_load(path.read_text("utf-8"))
    rules = (Rule.model_validate(item) for item in data["rules"])
    return tuple(sorted(rules, key=lambda rule: (-rule.priority, rule.id)))
```

- [ ] **Step 5: Implement deterministic rule matching**

```python
# src/figma_to_fgui/classify.py
from collections.abc import Iterable

from figma_to_fgui.models import ClassificationDecision, NormalizedNode
from figma_to_fgui.rules import Rule


def _walk(nodes: Iterable[NormalizedNode]) -> Iterable[NormalizedNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _matches(node: NormalizedNode, rule: Rule) -> tuple[bool, tuple[str, ...]]:
    evidence: list[str] = []
    expected_type = rule.when.get("type")
    if expected_type is not None and node.type != expected_type:
        return False, ()
    if expected_type is not None:
        evidence.append(f"type={node.type}")
    min_width = rule.when.get("minWidth")
    if min_width is not None and node.bounds.width < float(min_width):
        return False, ()
    if min_width is not None:
        evidence.append(f"width>={min_width}")
    min_children = rule.when.get("minChildren")
    if min_children is not None and len(node.children) < int(min_children):
        return False, ()
    if min_children is not None:
        evidence.append(f"children>={min_children}")
    return True, tuple(evidence or ("fallback",))


def classify_tree(
    roots: tuple[NormalizedNode, ...], rules: tuple[Rule, ...]
) -> tuple[ClassificationDecision, ...]:
    decisions: list[ClassificationDecision] = []
    for node in _walk(roots):
        for rule in rules:
            matched, evidence = _matches(node, rule)
            if matched:
                decisions.append(
                    ClassificationDecision(
                        node_id=node.id,
                        output_type=rule.action["outputType"],
                        rule_id=rule.id,
                        rule_version=rule.version,
                        evidence=evidence,
                        confidence=rule.confidence,
                    )
                )
                break
    return tuple(decisions)
```

- [ ] **Step 6: Run rule tests and type checks**

Run: `python -m pytest tests/unit/test_classify.py -v`

Expected: 1 test PASS.

Run: `python -m mypy src/figma_to_fgui/rules.py src/figma_to_fgui/classify.py`

Expected: `Success: no issues found`.

- [ ] **Step 7: Commit the rule engine**

```powershell
git add rules/default/classification.yaml src/figma_to_fgui/rules.py src/figma_to_fgui/classify.py tests/unit/test_classify.py
git commit -m "feat: classify nodes with versioned rules"
```

## Task 5: Deterministic Resource Planning

**Files:**
- Create: `src/figma_to_fgui/assets.py`
- Create: `tests/unit/test_assets.py`

**Interfaces:**
- Consumes: `NormalizedNode`, `ClassificationDecision`, `ProjectIndex`, `ResourcePlan`
- Produces: `make_resource_id(seed: str, occupied: frozenset[str]) -> str` and `plan_resources(...) -> tuple[ResourcePlan, ...]`

- [ ] **Step 1: Write failing deterministic-ID tests**

```python
# tests/unit/test_assets.py
from figma_to_fgui.assets import make_resource_id


def test_resource_id_is_stable_eight_characters_and_collision_safe() -> None:
    first = make_resource_id("1:2|Title", frozenset())
    second = make_resource_id("1:2|Title", frozenset())
    collided = make_resource_id("1:2|Title", frozenset({first}))
    assert first == second
    assert len(first) == 8
    assert first.isalnum() and first.lower() == first
    assert collided != first
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/unit/test_assets.py -v`

Expected: FAIL because `assets` does not exist.

- [ ] **Step 3: Implement deterministic IDs and basic resource plans**

```python
# src/figma_to_fgui/assets.py
import hashlib

from figma_to_fgui.models import ClassificationDecision, NormalizedNode, ResourcePlan
from figma_to_fgui.project_index import ProjectIndex


def make_resource_id(seed: str, occupied: frozenset[str]) -> str:
    attempt = 0
    while True:
        digest = hashlib.sha256(f"{seed}|{attempt}".encode()).hexdigest()[:8]
        if digest not in occupied:
            return digest
        attempt += 1


def plan_resources(
    roots: tuple[NormalizedNode, ...],
    decisions: tuple[ClassificationDecision, ...],
    index: ProjectIndex,
    package_name: str,
) -> tuple[ResourcePlan, ...]:
    nodes: dict[str, NormalizedNode] = {}

    def collect(node: NormalizedNode) -> None:
        nodes[node.id] = node
        for child in node.children:
            collect(child)

    for root in roots:
        collect(root)
    occupied = index.ids_by_package.get(package_name, frozenset())
    plans: list[ResourcePlan] = []
    for decision in decisions:
        if decision.output_type not in {"IMAGE", "PANEL"}:
            continue
        node = nodes[decision.node_id]
        name = f"Bg_{node.name.replace(' ', '_')}.png"
        plans.append(
            ResourcePlan(
                node_id=node.id,
                action="GENERATE",
                resource_name=name,
                resource_id=make_resource_id(f"{node.id}|{name}", occupied),
                relative_path=f"{package_name}/Img/{name}",
            )
        )
    return tuple(plans)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_assets.py -v`

Expected: 1 test PASS.

- [ ] **Step 5: Commit resource planning**

```powershell
git add src/figma_to_fgui/assets.py tests/unit/test_assets.py
git commit -m "feat: plan deterministic fgui resources"
```

## Task 6: Staging XML Generation and Rounding Diagnostics

**Files:**
- Create: `src/figma_to_fgui/generate.py`
- Create: `tests/unit/test_generate.py`

**Interfaces:**
- Consumes: normalized nodes, classification decisions, resource plans, `Diagnostic`
- Produces: `generate_staging(..., staging_root: Path) -> tuple[GeneratedFile, tuple[Diagnostic, ...]]`

- [ ] **Step 1: Write the failing generation test**

```python
# tests/unit/test_generate.py
import json
from pathlib import Path

from lxml import etree

from figma_to_fgui.classify import classify_tree
from figma_to_fgui.generate import generate_staging
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.rules import load_rules


def test_generates_parseable_xml_and_reports_rounding(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    files, diagnostics = generate_staging(roots, decisions, "Sample", tmp_path)
    output = tmp_path / "Sample/Panel/Panel_Sample_Main.xml"
    etree.parse(str(output))
    text = output.read_text("utf-8")
    assert 'xy="100,51"' in text
    assert any(item.code == "geometry.rounded" for item in diagnostics)
    assert files[0].relative_path == "Sample/Panel/Panel_Sample_Main.xml"
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/unit/test_generate.py -v`

Expected: FAIL because `generate` does not exist.

- [ ] **Step 3: Implement deterministic XML serialization**

```python
# src/figma_to_fgui/generate.py
import hashlib
from pathlib import Path

from lxml import etree

from figma_to_fgui.models import (
    ClassificationDecision,
    Diagnostic,
    GeneratedFile,
    NormalizedNode,
    Severity,
)
from figma_to_fgui.paths import safe_relative_path


def _integer(value: float, node_id: str, field: str) -> tuple[int, Diagnostic | None]:
    result = round(value)
    if result == value:
        return result, None
    return result, Diagnostic(
        code="geometry.rounded",
        severity=Severity.INFO,
        message=f"Rounded {field} from {value} to {result}",
        node_id=node_id,
        rule_id="geometry.integer-output",
        rule_version=1,
    )


def generate_staging(
    roots: tuple[NormalizedNode, ...],
    decisions: tuple[ClassificationDecision, ...],
    package_name: str,
    staging_root: Path,
) -> tuple[tuple[GeneratedFile, ...], tuple[Diagnostic, ...]]:
    decision_by_id = {item.node_id: item for item in decisions}
    diagnostics: list[Diagnostic] = []
    files: list[GeneratedFile] = []
    for root in roots:
        if decision_by_id[root.id].output_type != "PANEL":
            continue
        relative = safe_relative_path(
            f"{package_name}/Panel/Panel_{package_name}_{root.name}.xml"
        )
        target = staging_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        component = etree.Element("component", name=f"Panel_{package_name}_{root.name}")
        display = etree.SubElement(component, "displayList")
        for child in sorted(root.children, key=lambda item: item.source_order):
            x, dx = _integer(child.bounds.x - root.bounds.x, child.id, "x")
            y, dy = _integer(child.bounds.y - root.bounds.y, child.id, "y")
            width, dw = _integer(child.bounds.width, child.id, "width")
            height, dh = _integer(child.bounds.height, child.id, "height")
            diagnostics.extend(item for item in (dx, dy, dw, dh) if item is not None)
            if decision_by_id[child.id].output_type == "TEXT":
                etree.SubElement(
                    display,
                    "text",
                    id=child.id.replace(":", "_"),
                    name=child.name,
                    xy=f"{x},{y}",
                    size=f"{width},{height}",
                    autoSize="none",
                    text=child.text or "",
                )
        payload = etree.tostring(
            component, encoding="utf-8", xml_declaration=True, pretty_print=True
        )
        target.write_bytes(payload)
        files.append(
            GeneratedFile(
                relative_path=relative,
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
            )
        )
    return tuple(files), tuple(diagnostics)
```

- [ ] **Step 4: Run generation tests**

Run: `python -m pytest tests/unit/test_generate.py -v`

Expected: 1 test PASS.

- [ ] **Step 5: Commit staging generation**

```powershell
git add src/figma_to_fgui/generate.py tests/unit/test_generate.py
git commit -m "feat: generate deterministic staging xml"
```

## Task 7: Validation Policies and Applicable Gate

**Files:**
- Create: `rules/default/validation.yaml`
- Create: `src/figma_to_fgui/validate.py`
- Create: `tests/unit/test_validate.py`

**Interfaces:**
- Consumes: staging directory and `ProjectIndex`
- Produces: `validate_staging(staging_root: Path, index: ProjectIndex) -> tuple[Diagnostic, ...]` and `has_errors(diagnostics) -> bool`

- [ ] **Step 1: Define validation policy metadata**

```yaml
# rules/default/validation.yaml
policies:
  - id: xml.parseable
    version: 1
    severity: ERROR
  - id: path.project-relative
    version: 1
    severity: ERROR
  - id: resource-id.eight-chars
    version: 1
    severity: ERROR
  - id: text.fixed-size
    version: 1
    severity: ERROR
```

- [ ] **Step 2: Write failing validation tests**

```python
# tests/unit/test_validate.py
from pathlib import Path

from figma_to_fgui.project_index import ProjectIndex
from figma_to_fgui.validate import has_errors, validate_staging


def test_invalid_xml_and_missing_text_autosize_are_errors(tmp_path: Path) -> None:
    panel = tmp_path / "Sample/Panel/Bad.xml"
    panel.parent.mkdir(parents=True)
    panel.write_text('<component><displayList><text text="x"/></displayList></component>', "utf-8")
    diagnostics = validate_staging(tmp_path, ProjectIndex())
    assert has_errors(diagnostics)
    assert {item.code for item in diagnostics} == {"text.fixed-size"}


def test_well_formed_empty_component_has_no_errors(tmp_path: Path) -> None:
    panel = tmp_path / "Sample/Panel/Good.xml"
    panel.parent.mkdir(parents=True)
    panel.write_text("<component><displayList/></component>", "utf-8")
    assert not has_errors(validate_staging(tmp_path, ProjectIndex()))
```

- [ ] **Step 3: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_validate.py -v`

Expected: FAIL because `validate` does not exist.

- [ ] **Step 4: Implement validation and error gating**

```python
# src/figma_to_fgui/validate.py
from pathlib import Path

from lxml import etree

from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.project_index import ProjectIndex


def validate_staging(
    staging_root: Path, index: ProjectIndex
) -> tuple[Diagnostic, ...]:
    del index
    diagnostics: list[Diagnostic] = []
    for path in sorted(staging_root.rglob("*.xml")):
        relative = path.relative_to(staging_root).as_posix()
        try:
            tree = etree.parse(str(path))
        except etree.XMLSyntaxError as error:
            diagnostics.append(
                Diagnostic(
                    code="xml.parseable",
                    severity=Severity.ERROR,
                    message=str(error),
                    path=relative,
                    rule_id="xml.parseable",
                    rule_version=1,
                )
            )
            continue
        for text in tree.xpath("//text[not(@autoSize='none')]"):
            diagnostics.append(
                Diagnostic(
                    code="text.fixed-size",
                    severity=Severity.ERROR,
                    message="FGUI text must use autoSize=none",
                    path=relative,
                    node_id=text.attrib.get("id"),
                    rule_id="text.fixed-size",
                    rule_version=1,
                )
            )
    return tuple(diagnostics)


def has_errors(diagnostics: tuple[Diagnostic, ...]) -> bool:
    return any(item.severity is Severity.ERROR for item in diagnostics)
```

- [ ] **Step 5: Run validation tests**

Run: `python -m pytest tests/unit/test_validate.py -v`

Expected: 2 tests PASS.

- [ ] **Step 6: Commit validation**

```powershell
git add rules/default/validation.yaml src/figma_to_fgui/validate.py tests/unit/test_validate.py
git commit -m "feat: validate generated fgui staging files"
```

## Task 8: Hashed Changesets and Concurrent-Modification Protection

**Files:**
- Create: `src/figma_to_fgui/changeset.py`
- Create: `tests/unit/test_changeset.py`

**Interfaces:**
- Consumes: source snapshot, staging directory, diagnostics
- Produces: `hash_tree(root: Path) -> str` and `build_changeset(source_root, staging_root, diagnostics) -> ChangeSet`

- [ ] **Step 1: Write failing deterministic changeset tests**

```python
# tests/unit/test_changeset.py
from pathlib import Path

from figma_to_fgui.changeset import build_changeset, hash_tree
from figma_to_fgui.models import Diagnostic, Severity


def test_tree_hash_is_stable_and_changes_when_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    file = source / "package.xml"
    file.write_text("<package/>", "utf-8")
    first = hash_tree(source)
    assert hash_tree(source) == first
    file.write_text("<package id='changed'/>", "utf-8")
    assert hash_tree(source) != first


def test_error_diagnostic_marks_changeset_not_applicable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    source.mkdir()
    staging.mkdir()
    (staging / "file.txt").write_text("new", "utf-8")
    error = Diagnostic(code="x", severity=Severity.ERROR, message="blocked")
    changeset = build_changeset(source, staging, (error,))
    assert not changeset.applicable
    assert changeset.files[0].relative_path == "file.txt"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_changeset.py -v`

Expected: FAIL because `changeset` does not exist.

- [ ] **Step 3: Implement stable tree hashing and changeset construction**

```python
# src/figma_to_fgui/changeset.py
import hashlib
from pathlib import Path

from figma_to_fgui.models import ChangeSet, Diagnostic, GeneratedFile, Severity
from figma_to_fgui.paths import safe_relative_path


def hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = safe_relative_path(path.relative_to(root).as_posix())
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_changeset(
    source_root: Path,
    staging_root: Path,
    diagnostics: tuple[Diagnostic, ...],
) -> ChangeSet:
    files: list[GeneratedFile] = []
    for path in sorted(item for item in staging_root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        files.append(
            GeneratedFile(
                relative_path=safe_relative_path(path.relative_to(staging_root).as_posix()),
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
            )
        )
    return ChangeSet(
        applicable=not any(item.severity is Severity.ERROR for item in diagnostics),
        source_snapshot_sha256=hash_tree(source_root),
        files=tuple(files),
        diagnostics=diagnostics,
    )
```

- [ ] **Step 4: Run changeset tests**

Run: `python -m pytest tests/unit/test_changeset.py -v`

Expected: 2 tests PASS.

- [ ] **Step 5: Commit changesets**

```powershell
git add src/figma_to_fgui/changeset.py tests/unit/test_changeset.py
git commit -m "feat: build hashed conversion changesets"
```

## Task 9: Pipeline, Atomic CLI, and Golden Test

**Files:**
- Create: `src/figma_to_fgui/pipeline.py`
- Create: `src/figma_to_fgui/cli.py`
- Create: `tests/golden/test_simple_conversion.py`
- Create: `tests/golden/expected/Panel_Sample_Main.xml`
- Create: `README.md`

**Interfaces:**
- Consumes: all previous stage interfaces
- Produces: `convert(request: ConversionRequest) -> ChangeSet`, CLI commands `normalize`, `index-project`, `classify`, `validate`, and `convert`

- [ ] **Step 1: Write the failing golden test**

```python
# tests/golden/test_simple_conversion.py
from pathlib import Path

from figma_to_fgui.pipeline import ConversionRequest, convert


def test_simple_fixture_matches_golden_output(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    result = convert(
        ConversionRequest(
            figma_json=Path("tests/fixtures/figma/simple-frame.json"),
            project_root=Path("tests/fixtures/fgui"),
            package_name="Sample",
            staging_root=staging,
            classification_rules=Path("rules/default/classification.yaml"),
        )
    )
    actual = (staging / "Sample/Panel/Panel_Sample_Main.xml").read_bytes()
    expected = Path("tests/golden/expected/Panel_Sample_Main.xml").read_bytes()
    assert actual == expected
    assert result.applicable
```

- [ ] **Step 2: Run the golden test and verify failure**

Run: `python -m pytest tests/golden/test_simple_conversion.py -v`

Expected: FAIL because `pipeline` and the expected fixture do not exist.

- [ ] **Step 3: Implement pipeline composition**

```python
# src/figma_to_fgui/pipeline.py
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from figma_to_fgui.changeset import build_changeset
from figma_to_fgui.classify import classify_tree
from figma_to_fgui.generate import generate_staging
from figma_to_fgui.models import ChangeSet
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.project_index import index_project
from figma_to_fgui.rules import load_rules
from figma_to_fgui.validate import validate_staging


class ConversionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    figma_json: Path
    project_root: Path
    package_name: str
    staging_root: Path
    classification_rules: Path


def convert(request: ConversionRequest) -> ChangeSet:
    raw = json.loads(request.figma_json.read_text("utf-8"))
    roots, normalization_diagnostics = normalize_document(raw)
    index = index_project(request.project_root)
    decisions = classify_tree(roots, load_rules(request.classification_rules))
    _, generation_diagnostics = generate_staging(
        roots, decisions, request.package_name, request.staging_root
    )
    validation_diagnostics = validate_staging(request.staging_root, index)
    diagnostics = (
        normalization_diagnostics + generation_diagnostics + validation_diagnostics
    )
    return build_changeset(request.project_root, request.staging_root, diagnostics)
```

- [ ] **Step 4: Generate and review the golden XML once**

Run:

```powershell
python -c "from pathlib import Path; from figma_to_fgui.pipeline import ConversionRequest,convert; convert(ConversionRequest(figma_json=Path('tests/fixtures/figma/simple-frame.json'),project_root=Path('tests/fixtures/fgui'),package_name='Sample',staging_root=Path('.tmp-golden'),classification_rules=Path('rules/default/classification.yaml')))"
```

Expected: `.tmp-golden/Sample/Panel/Panel_Sample_Main.xml` is created and parses successfully.

After manually comparing it with the fixture input, copy its exact bytes to `tests/golden/expected/Panel_Sample_Main.xml` using the repository's normal file-editing mechanism, then remove `.tmp-golden` through a recoverable workspace cleanup.

- [ ] **Step 5: Implement atomic CLI commands**

```python
# src/figma_to_fgui/cli.py
import json
from pathlib import Path

import typer

from figma_to_fgui.classify import classify_tree
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.pipeline import ConversionRequest, convert
from figma_to_fgui.project_index import index_project
from figma_to_fgui.rules import load_rules
from figma_to_fgui.validate import validate_staging

app = typer.Typer(no_args_is_help=True)


@app.command("normalize")
def normalize_command(source: Path, output: Path) -> None:
    roots, diagnostics = normalize_document(json.loads(source.read_text("utf-8")))
    output.write_text(
        json.dumps(
            {
                "roots": [root.model_dump(mode="json") for root in roots],
                "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        "utf-8",
    )


@app.command("index-project")
def index_project_command(project_root: Path, output: Path) -> None:
    index = index_project(project_root)
    output.write_text(index.model_dump_json(indent=2), "utf-8")


@app.command("classify")
def classify_command(source: Path, rules: Path, output: Path) -> None:
    roots, _ = normalize_document(json.loads(source.read_text("utf-8")))
    decisions = classify_tree(roots, load_rules(rules))
    output.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in decisions],
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        "utf-8",
    )


@app.command("validate")
def validate_command(staging_root: Path, project_root: Path, output: Path) -> None:
    diagnostics = validate_staging(staging_root, index_project(project_root))
    output.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in diagnostics],
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        "utf-8",
    )


@app.command("convert")
def convert_command(
    figma_json: Path,
    project_root: Path,
    package_name: str,
    staging_root: Path,
    output: Path,
    rules: Path = Path("rules/default/classification.yaml"),
) -> None:
    result = convert(
        ConversionRequest(
            figma_json=figma_json,
            project_root=project_root,
            package_name=package_name,
            staging_root=staging_root,
            classification_rules=rules,
        )
    )
    output.write_text(result.model_dump_json(indent=2), "utf-8")
    if not result.applicable:
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Add exact CLI documentation**

```markdown
# Figma to FGUI Conversion Core

This package converts offline Figma JSON fixtures into a validated FGUI staging tree. It never writes to the source project.

```powershell
python -m pip install -e ".[dev]"
fgui-tool convert tests/fixtures/figma/simple-frame.json tests/fixtures/fgui Sample .staging changeset.json
```

Exit code `0` means the changeset is applicable. Exit code `2` means at least one `ERROR` diagnostic blocked application.
```

- [ ] **Step 7: Run the complete verification suite**

Run: `python -m pytest -v`

Expected: all unit and golden tests PASS.

Run: `python -m ruff check .`

Expected: `All checks passed!`.

Run: `python -m mypy src/figma_to_fgui`

Expected: `Success: no issues found`.

Run: `fgui-tool --help`

Expected: help lists `normalize`, `index-project`, `classify`, `validate`, and `convert`.

- [ ] **Step 8: Commit the baseline pipeline**

```powershell
git add src/figma_to_fgui/pipeline.py src/figma_to_fgui/cli.py tests/golden README.md
git commit -m "feat: add offline conversion pipeline"
```

## Final Review Gate

- [ ] Verify every generated path passes `safe_relative_path`.
- [ ] Verify no test or source file contains a developer-specific absolute path.
- [ ] Verify source FGUI fixtures are byte-identical before and after the full test suite.
- [ ] Verify a deliberately malformed XML fixture yields an `ERROR` and `applicable=false`.
- [ ] Verify two consecutive conversions produce identical XML and changeset JSON.
- [ ] Compare all requirements in the approved platform design against this phase-one scope; record server, Agent, Figma Plugin, and Codex Plugin work as separate follow-on plans rather than adding them to this baseline.
