"""Run the closed, privacy-safe Writer acceptance matrix.

This runner intentionally composes the existing Writer and CLI boundaries.  It
does not reproduce Writer implementation details or create Project Bindings.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

_REPOSITORY_ROOT = Path(__file__).resolve(strict=True).parents[1]
for _trusted_path in (_REPOSITORY_ROOT, _REPOSITORY_ROOT / "src"):
    if str(_trusted_path) not in sys.path:
        sys.path.insert(0, str(_trusted_path))

from PIL import Image, UnidentifiedImageError
from typer.testing import CliRunner

from figma_to_fgui import cli as fgui_cli
from figma_to_fgui import fgui_asset_payloads
from figma_to_fgui.cli import (
    _load_new_project_config,
    _load_plan_v2,
    app,
    load_declared_asset_directory,
)
from figma_to_fgui.component_mapping import load_mapping_catalog, validate_mapping_catalog
from figma_to_fgui.fgui_new_project_build import build_new_project, validate_project_archive
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_validate import canonical_plan_bytes, validate_fgui_plan
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.project_index import index_project
from figma_to_fgui.uir_compile import compile_uir
from tests.support.village_writer_regression import production_special_case_violations

_CASE_IDS = ("TC-01", "TC-02", "AC-01", "TC-03", "TC-04", "TC-05")
_FIXTURE_DIRECTORY = Path("tests/fixtures/fgui-new-project")
_FRESH_EDITOR_TRANSCRIPT = Path(
    "docs/validation/2026-08-20-fgui-6.1.4-new-project-editor-transcript.json"
)
_SCREENSHOT_DIMENSIONS = (1440, 1000)
_PUBLIC_UI_URI_SCHEME = "ui"
_FRESH_GUI_FILE_HASHES = (
    (
        "GenericWriterFixture.fairy",
        "bb74d97ef5039e471184ef3efeb3932e8e4843d5e856e56c88c2aec181ac92d8",
    ),
    (
        "assets/Generated/package.xml",
        "0a90a6b2c18f6443344ec5462511bc12c27c748c079bcbc078dbf841c2d334d4",
    ),
    (
        "assets/Generated/components/root-6e07d820.xml",
        "6a804a0c74f0f6ccd8fb1c8b60b0296b05cf62f1b7bbdb3236f4a3a781f08a6a",
    ),
    (
        "assets/Generated/resources/generic-pixel-7fb786de.png",
        "4ff6ab670a58c14270e034e2090d9a432caa263a14e0a25785386b0c12f880b5",
    ),
)


def _cases_from_result(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return the only allowed six-case result set in canonical order."""
    raw_cases = result.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != len(_CASE_IDS):
        raise ValueError("Evidence requires exactly six acceptance cases.")
    if not all(isinstance(case, Mapping) for case in raw_cases):
        raise ValueError("Evidence cases must be objects.")
    cases = list(raw_cases)
    if tuple(case.get("id") for case in cases) != _CASE_IDS:
        raise ValueError("Evidence cases must use the canonical six-case order.")
    return cases


def _is_path_boundary(value: str, index: int) -> bool:
    """Return whether a slash/backslash starts its own path-like token."""
    return index == 0 or not (value[index - 1].isalnum() or value[index - 1] in "._-")


def _public_uri_end(value: str, slash_index: int) -> int | None:
    """Return the end of an allowed public URI that begins at ``slash_index``."""
    if value[slash_index : slash_index + 2] != "//" or slash_index == 0:
        return None
    scheme_end = slash_index - 1
    if value[scheme_end] != ":":
        return None
    scheme_start = scheme_end
    while scheme_start and value[scheme_start - 1].isalpha():
        scheme_start -= 1
    scheme = value[scheme_start:scheme_end].lower()
    if scheme != _PUBLIC_UI_URI_SCHEME or scheme_start > 0 and (
        value[scheme_start - 1].isalnum() or value[scheme_start - 1] in "._-"
    ):
        return None
    end = slash_index + 2
    while end < len(value) and not value[end].isspace() and value[end] not in "\"'<>":
        end += 1
    target = value[slash_index + 2 : end]
    if not target or "\\" in target:
        return None
    return end if all(character.isalnum() or character in "._-" for character in target) else None


def _html_closing_tag_end(value: str, slash_index: int) -> int | None:
    """Allow escaped literal closing tags without treating their slash as a path."""
    if slash_index == 0 or value[slash_index - 1] != "<":
        return None
    end = value.find(">", slash_index + 1)
    if end == -1:
        return None
    name = value[slash_index + 1 : end]
    if name and all(character.isalnum() or character in "-:" for character in name):
        return end + 1
    return None


def _contains_private_absolute_path(value: str) -> bool:
    """Recognize cross-platform absolute paths without confusing public codes."""
    index = 0
    while index < len(value):
        character = value[index]
        if (
            character.isalpha()
            and _is_path_boundary(value, index)
            and index + 2 < len(value)
            and value[index + 1] == ":"
            and value[index + 2] in "\\/"
        ):
            return True
        if character == "/":
            uri_end = _public_uri_end(value, index)
            if uri_end is not None:
                index = uri_end
                continue
            tag_end = _html_closing_tag_end(value, index)
            if tag_end is not None:
                index = tag_end
                continue
            if _is_path_boundary(value, index):
                return True
        elif character == "\\" and (
            index + 1 < len(value) and value[index + 1] == "\\" or _is_path_boundary(value, index)
        ):
            return True
        index += 1
    return False


def _validate_public_text(value: object, *, field: str) -> str:
    """Reject private absolute paths before publishing public acceptance data."""
    if not isinstance(value, str):
        raise TypeError(f"Public acceptance {field} must be text.")
    if _contains_private_absolute_path(value):
        raise ValueError("Public acceptance data must not include private absolute paths.")
    return value


def _validate_public_value(value: object, *, field: str) -> None:
    """Recursively apply the public-text policy before JSON serialization."""
    if isinstance(value, str):
        _validate_public_text(value, field=field)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_public_text(key, field=f"{field} key")
            _validate_public_value(item, field=field)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_public_value(item, field=field)


def _card_text(value: object, *, field: str) -> str:
    """Validate and escape a public card field before putting it in HTML."""
    value = _validate_public_text(value, field=field)
    return html.escape(value, quote=True)


def _card_text_list(case: Mapping[str, object], field: str) -> list[str]:
    raw_items = case.get(field)
    if not isinstance(raw_items, list):
        raise TypeError(f"Evidence card {field} must be a list.")
    return [_card_text(item, field=field) for item in raw_items]


def _render_list(items: list[str]) -> str:
    return "\n".join(f"<li>{item}</li>" for item in items)


def _render_case_html(case: Mapping[str, object]) -> str:
    """Render one self-contained, fixed-viewport evidence card."""
    case_id = _card_text(case.get("id"), field="id")
    status = _card_text(case.get("status"), field="status")
    if status not in {"PASS", "FAIL"}:
        raise ValueError("Evidence card status must be PASS or FAIL.")
    status_class = "pass" if status == "PASS" else "fail"
    purpose = _card_text(case.get("purpose"), field="purpose")
    prerequisites = _card_text_list(case, "prerequisites")
    steps = _card_text_list(case, "steps")
    expected = _card_text_list(case, "expected")
    actual = _card_text_list(case, "actual")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1440, initial-scale=1">
<title>{case_id} Writer acceptance evidence</title>
<style>
@page {{ size: 1440px 1000px; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: 1440px; height: 1000px; }}
body {{ background: #f4f7fb; color: #132238; font: 16px/1.25 Arial, sans-serif; }}
main {{ display: grid; grid-template-rows: auto auto minmax(0, 1fr); gap: 14px; height: 1000px; padding: 34px 48px; }}
header {{ display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #b9c7d8; padding-bottom: 14px; }}
h1, h2, p {{ margin: 0; }}
h1 {{ font-size: 34px; }}
h2 {{ font-size: 16px; letter-spacing: .04em; text-transform: uppercase; }}
.status {{ border-radius: 999px; color: #fff; font-weight: 700; padding: 7px 16px; }}
.pass {{ background: #18794e; }}
.fail {{ background: #bb2d3b; }}
.purpose {{ font-size: 21px; font-weight: 600; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-content: start; }}
section {{ background: #fff; border: 1px solid #cbd6e2; border-radius: 10px; padding: 12px 16px; }}
.decisive {{ grid-column: 1 / -1; border-left: 8px solid #2368a2; }}
ul, ol {{ margin: 6px 0 0; padding-left: 22px; }}
li {{ margin: 2px 0; overflow-wrap: anywhere; }}
</style>
</head>
<body>
<main data-layout-contract="1440x1000-no-scroll">
  <header><h1>{case_id} · Writer acceptance</h1><span class="status {status_class}">{status}</span></header>
  <p class="purpose">{purpose}</p>
  <div class="grid">
    <section><h2>Prerequisites</h2><ul>{_render_list(prerequisites)}</ul></section>
    <section><h2>Steps</h2><ol>{_render_list(steps)}</ol></section>
    <section><h2>Expected</h2><ul>{_render_list(expected)}</ul></section>
    <section class="decisive"><h2>Actual / Decisive evidence</h2><ul>{_render_list(actual)}</ul></section>
  </div>
</main>
</body>
</html>
"""


def render_evidence_cards(result: Mapping[str, object], output: Path) -> tuple[Path, ...]:
    """Write the six local, self-contained HTML evidence cards in canonical order."""
    cases = _cases_from_result(result)
    output.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for case in cases:
        case_id = case["id"]
        assert isinstance(case_id, str)
        target = output / f"{case_id.lower()}.html"
        target.write_text(_render_case_html(case), encoding="utf-8", newline="\n")
        rendered.append(target)
    return tuple(rendered)


def capture_current_cards_with_edge(
    cards: tuple[Path, ...], capture_root: Path, *, node: Path, edge: Path
) -> None:
    """Capture the just-rendered cards with the explicitly selected Edge runtime."""
    if not node.is_file() or not edge.is_file():
        raise ValueError("Current card capture requires readable Node and Edge executables.")
    playwright = node.parents[1] / "node_modules/playwright-core"
    targets = [capture_root / "evidence/new-project-writer" / f"{card.stem}.png" for card in cards]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
const {{ chromium }} = require({json.dumps(str(playwright))});
const cards = {json.dumps([card.resolve().as_uri() for card in cards])};
const targets = {json.dumps([str(target) for target in targets])};
(async () => {{
  const browser = await chromium.launch({{ executablePath: {json.dumps(str(edge))}, headless: true }});
  const page = await browser.newPage({{ viewport: {{ width: 1440, height: 1000 }} }});
  for (let index = 0; index < cards.length; index += 1) {{
    await page.goto(cards[index]);
    const layout = await page.evaluate(() => {{
      const boxes = [...document.querySelectorAll('.decisive li')].map((item) => item.getBoundingClientRect());
      return document.documentElement.scrollWidth <= 1440 && document.documentElement.scrollHeight <= 1000 && boxes.every((box) => box.left >= 0 && box.top >= 0 && box.right <= 1440 && box.bottom <= 1000);
    }});
    if (!layout) throw new Error('card layout exceeds viewport');
    await page.screenshot({{ path: targets[index] }});
  }}
  await browser.close();
}})().catch(() => process.exit(1));
"""
    completed = subprocess.run([str(node), "-e", script], capture_output=True, check=False, text=True)
    if completed.returncode != 0 or not all(target.is_file() for target in targets):
        raise ValueError("Current card capture failed.")


def _png_snapshot(path: Path) -> tuple[str, tuple[int, int], bytes]:
    """Decode hash, dimensions, and pixels from one immutable PNG byte snapshot."""
    content = path.read_bytes()
    digest = sha256(content)
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != "PNG":
                raise ValueError("Screenshot is not a PNG.")
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image.load()
            dimensions = image.size
            rgba = image.convert("RGBA").tobytes()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("Screenshot is not a fully decodable PNG.") from error
    if not all(dimensions):
        raise ValueError("Screenshot PNG dimensions must be nonzero.")
    return digest.hexdigest(), dimensions, rgba


def finalize_screenshot_closure(
    result: Mapping[str, object],
    evidence_root: Path,
    *,
    screenshots_pending: bool = False,
    current_capture_root: Path | None = None,
) -> dict[str, object]:
    """Return a result with six verified screenshot hashes, or fail closed.

    Pending mode is explicit and never records screenshot hashes. It exists only
    for Tasks 1-2, before Task 3 captures the cards with Playwright.
    """
    cases = _cases_from_result(result)
    if not screenshots_pending and current_capture_root is None:
        raise ValueError("Strict screenshot closure requires current rendered card captures.")
    final: dict[str, object] = dict(result)
    finalized_cases: list[dict[str, object]] = []
    for case in cases:
        case_id = case["id"]
        assert isinstance(case_id, str)
        expected_reference = f"evidence/new-project-writer/{case_id.lower()}.png"
        if case.get("screenshot") != expected_reference:
            raise ValueError(f"{case_id}: screenshot reference is not the one required PNG.")
        closed_case = dict(case)
        if screenshots_pending:
            closed_case.pop("screenshotSha256", None)
            finalized_cases.append(closed_case)
            continue
        screenshot = evidence_root / expected_reference
        if not screenshot.is_file():
            raise ValueError(f"{case_id}: required screenshot is missing.")
        try:
            actual_hash, dimensions, expected_rgba = _png_snapshot(screenshot)
        except OSError as error:
            raise ValueError(f"{case_id}: required screenshot cannot be read.") from error
        except ValueError as error:
            raise ValueError(f"{case_id}: screenshot validation failed.") from error
        if dimensions != _SCREENSHOT_DIMENSIONS:
            raise ValueError(f"{case_id}: screenshot dimensions must be 1440x1000.")
        if current_capture_root is not None:
            current_capture = current_capture_root / expected_reference
            try:
                _, current_dimensions, current_rgba = _png_snapshot(current_capture)
            except (OSError, ValueError) as error:
                raise ValueError(f"{case_id}: current card capture cannot be verified.") from error
            if dimensions != current_dimensions or expected_rgba != current_rgba:
                raise ValueError(f"{case_id}: screenshot differs from the current rendered card.")
        expected_hash = closed_case.get("screenshotSha256")
        if expected_hash is not None and expected_hash != actual_hash:
            raise ValueError(f"{case_id}: screenshot SHA-256 does not match.")
        closed_case["screenshotSha256"] = actual_hash
        finalized_cases.append(closed_case)
    final["cases"] = finalized_cases
    return final


def canonical_acceptance_bytes(value: Mapping[str, object]) -> bytes:
    """Encode canonical public-only acceptance results."""
    _validate_public_value(value, field="result")
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _fresh_gui_transcript() -> dict[str, object]:
    """Return the exact bounded facts allowed in the fresh Editor evidence."""
    return {
        "schemaVersion": 1,
        "projectName": "GenericWriterFixture",
        "editorVersion": "6.1.4",
        "returnedWindowTitles": ["GenericWriterFixture", "GenericWriterFixture"],
        "saveRounds": ["open-save-close", "reopen-save-close"],
        "delayedCloseObservation": True,
        "finalWindowCount": 0,
        "modalObserved": False,
        "stateScreenshot": {"supported": False, "errorCode": "0x80004002"},
        "files": [
            {
                "path": path,
                "preSha256": digest,
                "postSha256": digest,
                "match": True,
            }
            for path, digest in _FRESH_GUI_FILE_HASHES
        ],
        "provenance": {
            "evidenceKind": "window-title-and-byte-hash",
            "representativeOnly": True,
        },
    }


def _read_fresh_gui_transcript(workspace: Path) -> bool:
    """Accept only the tracked, root-observed fresh GUI transcript verbatim."""
    try:
        transcript = json.loads((workspace / _FRESH_EDITOR_TRANSCRIPT).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return transcript == _fresh_gui_transcript()


def _closed_cases_for_report(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Validate the final result fields that a durable human report must repeat."""
    _validate_public_value(result, field="result")
    cases = _cases_from_result(result)
    for case in cases:
        case_id = case["id"]
        if not isinstance(case_id, str):
            raise TypeError("Acceptance case ID must be text.")
        expected_reference = f"evidence/new-project-writer/{case_id.lower()}.png"
        screenshot_hash = case.get("screenshotSha256")
        if case.get("screenshot") != expected_reference:
            raise ValueError(f"{case_id}: report requires the canonical relative screenshot.")
        if (
            not isinstance(screenshot_hash, str)
            or len(screenshot_hash) != 64
            or any(character not in "0123456789abcdef" for character in screenshot_hash)
        ):
            raise ValueError(f"{case_id}: report requires a closed screenshot SHA-256.")
        if case.get("status") not in {"PASS", "FAIL"}:
            raise ValueError(f"{case_id}: report requires PASS or FAIL status.")
        for field in ("purpose", "prerequisites", "steps", "expected", "actual"):
            if field == "purpose":
                _validate_public_text(case.get(field), field=field)
            else:
                _card_text_list(case, field)
    return cases


def _markdown_list(case: Mapping[str, object], field: str) -> str:
    """Render a validated report list without adding links or external assets."""
    values = _card_text_list(case, field)
    return "\n".join(f"- {value}" for value in values)


def render_acceptance_report(result: Mapping[str, object]) -> str:
    """Render the six-section human report from a fully closed machine result."""
    cases = _closed_cases_for_report(result)
    summary_rows = ["| Case | Status |", "| --- | --- |"]
    summary_rows.extend(f"| {case['id']} | {case['status']} |" for case in cases)
    sections = [
        "# New-Project Writer Test Acceptance",
        "",
        "This report is generated from the closed machine result. Each case has one local evidence card screenshot.",
        "",
        *summary_rows,
    ]
    for case in cases:
        purpose = _validate_public_text(case["purpose"], field="purpose")
        screenshot = _validate_public_text(case["screenshot"], field="screenshot")
        screenshot_hash = case["screenshotSha256"]
        assert isinstance(screenshot_hash, str)
        sections.extend(
            [
                "",
                f"## {case['id']} — {case['status']}",
                "",
                "### Purpose",
                "",
                purpose,
                "",
                "### Prerequisites",
                "",
                _markdown_list(case, "prerequisites"),
                "",
                "### Steps",
                "",
                _markdown_list(case, "steps"),
                "",
                "### Expected",
                "",
                _markdown_list(case, "expected"),
                "",
                "### Actual",
                "",
                _markdown_list(case, "actual"),
                "",
                f"Screenshot: [{case['id']} evidence]({screenshot})",
                "",
                f"Screenshot SHA-256: `{screenshot_hash}`",
            ]
        )
        if case["id"] == "AC-01":
            sections.extend(
                [
                    "",
                    "Durable transcript: [AC-01 durable transcript](2026-08-20-fgui-6.1.4-new-project-editor-transcript.json)",
                    "",
                    *[
                        f"Transcript file SHA-256: `{item[1]}` ({item[0]})"
                        for item in _FRESH_GUI_FILE_HASHES
                    ],
                ]
            )
    return "\n".join(sections) + "\n"


def write_acceptance_report(
    result: Mapping[str, object],
    target: Path,
    *,
    current_capture_root: Path | None = None,
) -> Path:
    """Write a report only after re-verifying screenshots against fresh captures."""
    closed = finalize_screenshot_closure(
        result, target.parent, current_capture_root=current_capture_root
    )
    content = render_acceptance_report(closed)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def _case_result(
    case_id: str,
    purpose: str,
    prerequisites: list[str],
    steps: list[str],
    expected: list[str],
    actual: list[str],
    *,
    passed: bool,
) -> dict[str, object]:
    return {
        "id": case_id,
        "purpose": purpose,
        "prerequisites": prerequisites,
        "steps": steps,
        "expected": expected,
        "actual": actual,
        "screenshot": f"evidence/new-project-writer/{case_id.lower()}.png",
        "status": "PASS" if passed else "FAIL",
    }


def _safe_case(
    case_id: str,
    purpose: str,
    prerequisites: list[str],
    steps: list[str],
    expected: list[str],
    operation: Callable[[], tuple[bool, list[str]]],
) -> dict[str, object]:
    """Keep unexpected local failures out of the published public result."""
    try:
        passed, actual = operation()
    except Exception:  # noqa: BLE001 - an acceptance result must not leak local detail.
        passed, actual = False, ["result=unexpected_failure"]
    return _case_result(
        case_id,
        purpose,
        prerequisites,
        steps,
        expected,
        actual,
        passed=passed,
    )


def _load_generic_writer_input(workspace: Path):
    fixture = workspace / _FIXTURE_DIRECTORY
    plan = _load_plan_v2(fixture / "generic-plan-v2.json")
    config = _load_new_project_config(fixture / "config.json")
    payloads = load_declared_asset_directory(fixture / "assets", plan.resources)
    return plan, config, payloads


def _zip_publication_exists(output_directory: Path) -> bool:
    return output_directory.exists() and any(output_directory.glob("*.zip"))


def _tc_01(workspace: Path, evidence_root: Path) -> tuple[bool, list[str]]:
    plan, config, payloads = _load_generic_writer_input(workspace)
    built = build_new_project(plan, config, payloads, evidence_root / "tc-01")
    diagnostics = validate_project_archive(built.path, built.manifest)
    with ZipFile(built.path) as archive:
        members = archive.namelist()
        archive_ok = archive.testzip() is None
    return (
        not diagnostics and archive_ok,
        [
            f"publishedZipFilename={built.path.name}",
            f"archiveMembers={','.join(members)}",
            f"archiveValidatorClean={'true' if not diagnostics else 'false'}",
            f"archiveReopen={'true' if archive_ok else 'false'}",
        ],
    )


def _tc_02(workspace: Path, evidence_root: Path) -> tuple[bool, list[str]]:
    plan, config, payloads = _load_generic_writer_input(workspace)
    first = build_new_project(plan, config, payloads, evidence_root / "tc-02-first")
    second = build_new_project(plan, config, payloads, evidence_root / "tc-02-second")
    equal = first.path.read_bytes() == second.path.read_bytes()
    return (
        equal and first.sha256 == second.sha256,
        [
            f"firstSha256={first.sha256}",
            f"secondSha256={second.sha256}",
            f"byteEquality={'true' if equal else 'false'}",
        ],
    )


def _ac_01(workspace: Path) -> tuple[bool, list[str]]:
    valid = _read_fresh_gui_transcript(workspace)
    if not valid:
        return False, ["freshTranscriptValid=false"]
    return valid, [
        "freshTranscriptValid=true",
        "editorVersion=6.1.4",
        "returnedWindowTitle=GenericWriterFixture",
        "saveRounds=open-save-close,reopen-save-close",
        "delayedCloseObservation=true",
        "finalWindowCount=0",
        "modalObserved=false",
        "stateScreenshot=unsupported(0x80004002)",
        "fileHashParity=4/4",
        "transcript=2026-08-20-fgui-6.1.4-new-project-editor-transcript.json",
    ]


def _run_build_cli(
    plan: Path, config: Path, asset_directory: Path, output: Path, rejection: str
) -> tuple[bool, str]:
    invocation = CliRunner().invoke(
        app,
        [
            "build-fgui-project",
            str(plan),
            str(config),
            str(asset_directory),
            str(output),
        ],
    )
    return invocation.exit_code == 2 and rejection in invocation.output, invocation.output


def _tc_03(workspace: Path, evidence_root: Path) -> tuple[bool, list[str]]:
    with TemporaryDirectory(dir=evidence_root, prefix="tc-03-") as raw:
        temporary = Path(raw)
        assets = temporary / "assets"
        shutil.copytree(workspace / _FIXTURE_DIRECTORY / "assets", assets)
        manifest = json.loads((assets / "manifest.json").read_text("utf-8"))
        manifest["resources"]["resource:image"]["filename"] = "../escape.png"
        (assets / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            "utf-8",
        )
        output = temporary / "output"
        rejected, _ = _run_build_cli(
            workspace / _FIXTURE_DIRECTORY / "generic-plan-v2.json",
            workspace / _FIXTURE_DIRECTORY / "config.json",
            assets,
            output,
            "ASSET_DIRECTORY",
        )
        published = _zip_publication_exists(output)
    return (
        rejected and not published,
        [
            "rejection=ASSET_DIRECTORY" if rejected else "rejection=unexpected",
            f"zipPublished={'true' if published else 'false'}",
        ],
    )


def _tc_04(workspace: Path, evidence_root: Path) -> tuple[bool, list[str]]:
    oversized_asset_read_called = False
    oversized: Path | None = None
    original_read = fgui_cli._read_stable_regular_file

    def observe_stable_read(path: Path, *, max_bytes: int | None = None) -> bytes:
        nonlocal oversized_asset_read_called
        if oversized is not None and path == oversized:
            oversized_asset_read_called = True
        return original_read(path, max_bytes=max_bytes)

    fgui_cli._read_stable_regular_file = observe_stable_read
    try:
        with TemporaryDirectory(dir=evidence_root, prefix="tc-04-") as raw:
            temporary = Path(raw)
            assets = temporary / "assets"
            shutil.copytree(workspace / _FIXTURE_DIRECTORY / "assets", assets)
            oversized = assets / "one-pixel.png"
            with oversized.open("wb") as content:
                content.seek(fgui_asset_payloads.MAX_ASSET_PAYLOAD_BYTES)
                content.write(b"\0")
            output = temporary / "output"
            rejected, _ = _run_build_cli(
                workspace / _FIXTURE_DIRECTORY / "generic-plan-v2.json",
                workspace / _FIXTURE_DIRECTORY / "config.json",
                assets,
                output,
                "ASSET_DIRECTORY",
            )
            published = _zip_publication_exists(output)
    finally:
        fgui_cli._read_stable_regular_file = original_read
    return (
        rejected and not oversized_asset_read_called and not published,
        [
            "rejection=ASSET_DIRECTORY" if rejected else "rejection=unexpected",
            f"maxAssetPayloadBytes={fgui_asset_payloads.MAX_ASSET_PAYLOAD_BYTES}",
            f"sparseDeclaredBytes={fgui_asset_payloads.MAX_ASSET_PAYLOAD_BYTES + 1}",
            f"oversizedAssetReadCalled={'true' if oversized_asset_read_called else 'false'}",
            f"rejectedBeforeFullRead={'false' if oversized_asset_read_called else 'true'}",
            f"zipPublished={'true' if published else 'false'}",
        ],
    )


def _tc_05(workspace: Path, evidence_root: Path) -> tuple[bool, list[str]]:
    raw = json.loads(
        (workspace / "tests/fixtures/uir/village-ascend-normalized.json").read_text("utf-8")
    )
    roots, normalize_diagnostics = normalize_document(raw)
    catalog = validate_mapping_catalog(
        load_mapping_catalog(workspace / "rules/default/component-mapping-candidates.json"),
        index_project(workspace / "tests/fixtures/uir/common-project"),
    )
    uir = compile_uir(
        roots,
        source_revision="7" * 64,
        selection_id="selection_village_ascend",
        mapping_catalog=catalog,
    )
    plan = compile_fgui_plan(uir)
    diagnostics = (*normalize_diagnostics, *plan.diagnostics, *validate_fgui_plan(plan))
    codes = {diagnostic.code for diagnostic in diagnostics}
    special_case_scan = production_special_case_violations(workspace=workspace) == ()
    output = evidence_root / "tc-05-output"
    with TemporaryDirectory(dir=evidence_root, prefix="tc-05-") as raw_directory:
        plan_path = Path(raw_directory) / "village-plan-v2.json"
        plan_path.write_bytes(canonical_plan_bytes(plan))
        rejected, _ = _run_build_cli(
            plan_path,
            workspace / _FIXTURE_DIRECTORY / "config.json",
            workspace / _FIXTURE_DIRECTORY / "assets",
            output,
            "PLAN",
        )
    published = _zip_publication_exists(output)
    passed = (
        "fgui.component.definition_missing" in codes
        and not plan.bindable
        and special_case_scan
        and rejected
        and not published
    )
    return (
        passed,
        [
            "diagnostic=fgui.component.definition_missing"
            if "fgui.component.definition_missing" in codes
            else "diagnostic=missing",
            f"productionSpecialCaseScan={'true' if special_case_scan else 'false'}",
            f"cliPublishAttempt={'true' if rejected else 'false'}",
            "rejection=PLAN" if rejected else "rejection=unexpected",
            f"zipPublished={'true' if published else 'false'}",
        ],
    )


def run_acceptance(workspace: Path, evidence_root: Path) -> dict[str, object]:
    """Execute exactly the six Writer acceptance cases using public production seams."""
    root = workspace.resolve(strict=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    cases = [
        _safe_case(
            "TC-01",
            "Build the generic neutral Writer fixture and reopen its published ZIP.",
            ["Canonical generic Plan v2, config, and declared asset manifest are tracked."],
            [
                "Load through CLI loaders.",
                "Build through Writer.",
                "Reopen through archive validator.",
            ],
            ["A valid archive has the exact deterministic member list."],
            lambda: _tc_01(root, evidence_root),
        ),
        _safe_case(
            "TC-02",
            "Prove deterministic archive bytes for the generic Writer fixture.",
            ["The generic fixture is valid for new-project generation."],
            ["Build twice into isolated output directories.", "Compare both published archives."],
            ["Both SHA-256 values match and archive bytes are equal."],
            lambda: _tc_02(root, evidence_root),
        ),
        _safe_case(
            "AC-01",
            "Record the fresh neutral FairyGUI Editor acceptance transcript.",
            [
                "The tracked fresh FairyGUI Editor 6.1.4 transcript is available.",
                "Only GenericWriterFixture is the real Editor representative.",
            ],
            [
                "Validate the exact returned titles and two save rounds.",
                "Validate delayed close observation, final window count, modal result, and hash parity.",
            ],
            [
                "Two saved rounds return GenericWriterFixture, no modal is observed, and final windows are zero.",
                "The four declared files have equal pre/post SHA-256 values; the state screenshot API is unsupported.",
            ],
            lambda: _ac_01(root),
        ),
        _safe_case(
            "TC-03",
            "Reject an asset manifest path traversal before archive publication.",
            ["The generic fixture asset manifest is copied into isolated test storage."],
            [
                "Inject ../escape.png.",
                "Invoke the public build CLI.",
                "Inspect output publication.",
            ],
            ["ASSET_DIRECTORY is rejected and no ZIP is published."],
            lambda: _tc_03(root, evidence_root),
        ),
        _safe_case(
            "TC-04",
            "Reject an oversized sparse asset before any Pillow probe or archive publication.",
            ["The generic fixture asset manifest is copied into isolated test storage."],
            [
                "Create a sparse asset larger than MAX_ASSET_PAYLOAD_BYTES.",
                "Invoke the public build CLI.",
            ],
            ["ASSET_DIRECTORY is rejected, Pillow is not probed, and no ZIP is published."],
            lambda: _tc_04(root, evidence_root),
        ),
        _safe_case(
            "TC-05",
            "Fail closed for a demand-side component mapping without a generatable definition.",
            ["The village fixture remains a generic mapping regression input."],
            [
                "Compile mapping to UIR.",
                "Compile UIR to Plan.",
                "Run the production special-case scan.",
            ],
            ["fgui.component.definition_missing blocks publication without a Writer special case."],
            lambda: _tc_05(root, evidence_root),
        ),
    ]
    assert tuple(case["id"] for case in cases) == _CASE_IDS
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    return {
        "schemaVersion": 2,
        "provenance": {
            "codeCommitUnderTest": commit,
            "executedAt": datetime.now().astimezone().isoformat(),
            "fairyGuiVersion": "6.1.4",
        },
        "cases": cases,
    }


def write_acceptance_results(
    workspace: Path,
    evidence_root: Path,
    *,
    screenshots_pending: bool = False,
    current_capture_root: Path | None = None,
) -> Path:
    """Write a machine result only after explicit pending or screenshot closure."""
    evidence_root.mkdir(parents=True, exist_ok=True)
    target = evidence_root / "new-project-writer-acceptance-results.json"
    result = finalize_screenshot_closure(
        run_acceptance(workspace, evidence_root),
        evidence_root,
        screenshots_pending=screenshots_pending,
        current_capture_root=current_capture_root,
    )
    target.write_bytes(canonical_acceptance_bytes(result))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Writer acceptance matrix.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cards", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--edge", type=Path)
    parser.add_argument("--screenshots-pending", action="store_true")
    arguments = parser.parse_args()
    if arguments.report is not None and arguments.screenshots_pending:
        parser.error("A final acceptance report requires closed screenshots.")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    result = run_acceptance(arguments.workspace, arguments.output.parent)
    try:
        if arguments.screenshots_pending:
            final = finalize_screenshot_closure(result, arguments.output.parent, screenshots_pending=True)
        else:
            if arguments.node is None or arguments.edge is None:
                parser.error("Strict finalization requires --node and --edge for current card capture.")
            with TemporaryDirectory(dir=arguments.output.parent, prefix="current-cards-") as raw:
                capture_root = Path(raw)
                current_cards = render_evidence_cards(result, capture_root / "cards")
                capture_current_cards_with_edge(
                    current_cards, capture_root, node=arguments.node, edge=arguments.edge
                )
                final = finalize_screenshot_closure(
                    result, arguments.output.parent, current_capture_root=capture_root
                )
                if arguments.report is not None:
                    write_acceptance_report(
                        final, arguments.report, current_capture_root=capture_root
                    )
    except ValueError as error:
        parser.error(str(error))
    if arguments.cards is not None:
        render_evidence_cards(final, arguments.cards)
    arguments.output.write_bytes(canonical_acceptance_bytes(final))


if __name__ == "__main__":
    main()
