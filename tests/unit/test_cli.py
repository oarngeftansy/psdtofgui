import json
import os
from pathlib import Path

import pytest
import typer
import uvicorn
from pytest import MonkeyPatch
from typer.testing import CliRunner

import figma_to_fgui.cli as cli_module
from figma_to_fgui.agent import AgentClient, AgentConfig
from figma_to_fgui.cli import (
    _load_new_project_config,
    _load_plan_v2,
    app,
    load_declared_asset_directory,
)
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_models import FGUIPlanDocument
from figma_to_fgui.fgui_plan_validate import canonical_plan_bytes, validate_fgui_plan
from figma_to_fgui.semantic_config import SemanticConfigurationError
from figma_to_fgui.service_contracts import ApplyResult, ApplyStatus
from figma_to_fgui.uir_models import UIRDocument
from figma_to_fgui.uir_validate import validate_uir


def test_help_lists_all_atomic_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "normalize",
        "build-uir",
        "build-fgui-plan",
        "build-fgui-project",
        "migrate-fgui-plan-v1",
        "index-project",
        "classify",
        "validate",
        "convert",
        "serve",
        "agent",
    ):
        assert command in result.stdout


def test_build_fgui_project_rejects_asset_manifest_traversal(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "resources": {
                    "resource:image": {
                        "filename": "../one-pixel.png",
                        "declaredMimeType": "image/png",
                    }
                }
            }
        ),
        "utf-8",
    )

    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {"resource:image": object()})


def test_build_fgui_project_rejects_undeclared_asset_file(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "manifest.json").write_text('{"resources":{}}', "utf-8")
    (assets / "extra.png").write_bytes(b"undeclared")

    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {})


def test_build_fgui_project_rejects_undeclared_asset_directory(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "manifest.json").write_text('{"resources":{}}\n', "utf-8")
    (assets / "extra").mkdir()

    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {})


def test_asset_loader_rejects_casefolded_duplicate_paths(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a.png").write_bytes(b"image")
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "resources": {
                    "resource:a": {
                        "filename": "a.png",
                        "declaredMimeType": "image/png",
                    },
                    "resource:b": {
                        "filename": "A.png",
                        "declaredMimeType": "image/png",
                    },
                }
            }
        ),
        "utf-8",
    )

    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(
            assets, {"resource:a": object(), "resource:b": object()}
        )


def test_asset_loader_reads_exactly_declared_regular_files(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "image.png").write_bytes(b"image")
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "resources": {
                    "resource:image": {
                        "filename": "image.png",
                        "declaredMimeType": "image/png",
                    }
                }
            }
        ),
        "utf-8",
    )

    payloads = load_declared_asset_directory(assets, {"resource:image": object()})

    assert payloads.payload_for("resource:image").content == b"image"
    assert payloads.payload_for("resource:image").declared_mime_type == "image/png"


def test_asset_loader_rejects_sparse_file_before_read(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    image = assets / "image.png"
    with image.open("wb") as stream:
        stream.seek(16)
        stream.write(b"x")
    (assets / "manifest.json").write_text(
        '{"resources":{"resource:image":{"declaredMimeType":"image/png","filename":"image.png"}}}',
        "utf-8",
    )
    monkeypatch.setattr(cli_module, "MAX_ASSET_PAYLOAD_BYTES", 8)

    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {"resource:image": object()})


def test_asset_loader_rejects_aggregate_encoded_bytes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a.png").write_bytes(b"aaaaaa")
    (assets / "b.png").write_bytes(b"bbbbbb")
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "resources": {
                    "resource:a": {"declaredMimeType": "image/png", "filename": "a.png"},
                    "resource:b": {"declaredMimeType": "image/png", "filename": "b.png"},
                }
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(cli_module, "MAX_ASSET_PAYLOAD_BYTES", 8)
    monkeypatch.setattr(cli_module, "MAX_TOTAL_ASSET_PAYLOAD_BYTES", 10)

    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(
            assets, {"resource:a": object(), "resource:b": object()}
        )


def test_asset_loader_allows_exact_aggregate_boundary(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a.png").write_bytes(b"aaaaaa")
    (assets / "b.png").write_bytes(b"bbbb")
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "resources": {
                    "resource:a": {"declaredMimeType": "image/png", "filename": "a.png"},
                    "resource:b": {"declaredMimeType": "image/png", "filename": "b.png"},
                }
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(cli_module, "MAX_ASSET_PAYLOAD_BYTES", 8)
    monkeypatch.setattr(cli_module, "MAX_TOTAL_ASSET_PAYLOAD_BYTES", 10)

    payloads = load_declared_asset_directory(
        assets, {"resource:a": object(), "resource:b": object()}
    )

    assert sum(len(item.content) for item in payloads.items) == 10


def test_asset_loader_rejects_final_asset_growth_beyond_remaining_aggregate(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a.png").write_bytes(b"aaaaaa")
    final = assets / "b.png"
    final.write_bytes(b"bbbb")
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "resources": {
                    "resource:a": {"declaredMimeType": "image/png", "filename": "a.png"},
                    "resource:b": {"declaredMimeType": "image/png", "filename": "b.png"},
                }
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(cli_module, "MAX_ASSET_PAYLOAD_BYTES", 8)
    monkeypatch.setattr(cli_module, "MAX_TOTAL_ASSET_PAYLOAD_BYTES", 10)
    original_read = cli_module._read_stable_regular_file
    observed_limit: int | None = None

    def grow_during_read(path: Path, *, max_bytes: int | None = None) -> bytes:
        nonlocal observed_limit
        if path == final:
            observed_limit = max_bytes
            return b"bbbbb"  # Simulates a stable-reader fault returning post-stat growth.
        return original_read(path, max_bytes=max_bytes)

    monkeypatch.setattr(cli_module, "_read_stable_regular_file", grow_during_read)

    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(
            assets, {"resource:a": object(), "resource:b": object()}
        )

    assert observed_limit == 4


@pytest.mark.parametrize("loader,fixture", [
    (_load_plan_v2, Path("tests/fixtures/fgui-new-project/generic-plan-v2.json")),
    (_load_new_project_config, Path("tests/fixtures/fgui-new-project/config.json")),
])
def test_new_project_json_loaders_require_canonical_bytes(loader, fixture: Path, tmp_path: Path) -> None:
    payload = json.loads(fixture.read_text("utf-8"))
    noncanonical = tmp_path / fixture.name
    noncanonical.write_text(json.dumps(payload, indent=4), "utf-8")

    with pytest.raises(typer.BadParameter):
        loader(noncanonical)


def test_new_project_config_rejects_duplicate_keys_and_bool_header(tmp_path: Path) -> None:
    fixture = Path("tests/fixtures/fgui-new-project/config.json")
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(fixture.read_text("utf-8").replace('"namingPolicyVersion": 1', '"namingPolicyVersion": 1, "namingPolicyVersion": 1'), "utf-8")
    coerced = tmp_path / "coerced.json"
    coerced.write_text(fixture.read_text("utf-8").replace('"namingPolicyVersion": 1', '"namingPolicyVersion": true'), "utf-8")

    with pytest.raises(typer.BadParameter):
        _load_new_project_config(duplicate)
    with pytest.raises(typer.BadParameter):
        _load_new_project_config(coerced)


def test_plan_loader_rejects_duplicate_keys_and_numeric_string_header(tmp_path: Path) -> None:
    fixture = Path("tests/fixtures/fgui-new-project/generic-plan-v2.json")
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(fixture.read_text("utf-8").replace('"schemaVersion": 2', '"schemaVersion": 2, "schemaVersion": 2'), "utf-8")
    coerced = tmp_path / "coerced.json"
    coerced.write_text(fixture.read_text("utf-8").replace('"schemaVersion": 2', '"schemaVersion": "2"'), "utf-8")

    with pytest.raises(typer.BadParameter):
        _load_plan_v2(duplicate)
    with pytest.raises(typer.BadParameter):
        _load_plan_v2(coerced)


def test_asset_loader_rejects_manifest_duplicate_keys(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "manifest.json").write_text('{"resources":{},"resources":{}}\n', "utf-8")
    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {})


def test_asset_loader_rejects_file_changed_during_read(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    image = assets / "image.png"
    image.write_bytes(b"before")
    (assets / "manifest.json").write_text('{"resources":{"resource:image":{"declaredMimeType":"image/png","filename":"image.png"}}}\n', "utf-8")
    original_fstat = __import__("os").fstat
    calls = 0

    def raced_fstat(fd: int):
        nonlocal calls
        calls += 1
        if calls == 4:
            image.write_bytes(b"after-after")
        return original_fstat(fd)

    monkeypatch.setattr("figma_to_fgui.cli.os.fstat", raced_fstat)
    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {"resource:image": object()})


def test_asset_loader_rejects_swap_after_closure_walk(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    image = assets / "image.png"
    image.write_bytes(b"before")
    (assets / "manifest.json").write_text('{"resources":{"resource:image":{"declaredMimeType":"image/png","filename":"image.png"}}}\n', "utf-8")
    original_rglob = Path.rglob

    def raced_rglob(path: Path, pattern: str):
        items = list(original_rglob(path, pattern))
        yield from items
        image.write_bytes(b"replacement-is-longer")

    monkeypatch.setattr(Path, "rglob", raced_rglob)
    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {"resource:image": object()})


def test_asset_loader_rejects_same_size_manifest_rewrite_with_restored_mtime(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    manifest = assets / "manifest.json"
    original = '{"resources":{}}\n'
    replacement = '{"resources":[]}\n'
    assert len(original.encode()) == len(replacement.encode())
    manifest.write_text(original, "utf-8")
    original_times = manifest.stat()
    original_rglob = Path.rglob

    def raced_rglob(path: Path, pattern: str):
        yield from original_rglob(path, pattern)
        manifest.write_text(replacement, "utf-8")
        os.utime(manifest, ns=(original_times.st_atime_ns, original_times.st_mtime_ns))

    monkeypatch.setattr(Path, "rglob", raced_rglob)
    with pytest.raises(typer.BadParameter):
        load_declared_asset_directory(assets, {})


def test_build_uir_writes_canonical_valid_document(tmp_path: Path) -> None:
    source = Path("tests/fixtures/figma/simple-frame.json")
    output = tmp_path / "simple.uir.json"
    result = CliRunner().invoke(
        app,
        [
            "build-uir",
            str(source),
            str(output),
            "--source-revision",
            "a" * 64,
            "--selection-id",
            "selection_simple",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert output.read_bytes().endswith(b"\n")
    document = UIRDocument.model_validate_json(output.read_text("utf-8"))
    assert validate_uir(document) == ()


def test_build_uir_rejects_malformed_source_revision(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "build-uir",
            "tests/fixtures/figma/simple-frame.json",
            str(tmp_path / "out.json"),
            "--source-revision",
            "not-a-sha",
            "--selection-id",
            "selection_simple",
        ],
    )
    assert result.exit_code == 2
    assert "64 lowercase hexadecimal" in result.output


def test_build_fgui_plan_writes_canonical_valid_plan(tmp_path: Path) -> None:
    output = tmp_path / "generic.fgui-plan.json"

    result = CliRunner().invoke(
        app,
        [
            "build-fgui-plan",
            "tests/fixtures/fgui-plan/generic-primitives.uir.json",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    plan = FGUIPlanDocument.model_validate_json(output.read_text("utf-8"))
    assert validate_fgui_plan(plan) == ()
    assert plan.schema_version == 2
    assert output.read_bytes() == canonical_plan_bytes(plan)
    assert output.read_bytes().endswith(b"\n")


def test_migrate_fgui_plan_v1_rejects_component_references(tmp_path: Path) -> None:
    source = tmp_path / "component-v1.fgui-plan.json"
    output = tmp_path / "component-v2.fgui-plan.json"
    source.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "documentId": "plan:component",
                "sourceUirSha256": "a" * 64,
                "profileVersion": "fgui-6.1.4-v1",
                "ruleVersion": 1,
                "bindable": False,
                "roots": ["plan:instance"],
                "nodes": {
                    "plan:instance": {
                        "id": "plan:instance",
                        "uirNodeRef": "uir:instance",
                        "zIndex": 0,
                        "type": "componentReference",
                        "transform": {
                            "bounds": {"x": 0, "y": 0, "width": 100, "height": 40}
                        },
                        "component": {"candidateKey": "common_button"},
                    }
                },
            }
        ),
        "utf-8",
    )

    result = CliRunner().invoke(
        app, ["migrate-fgui-plan-v1", str(source), str(output)]
    )

    assert result.exit_code == 2
    assert "recompiled" in result.output
    assert not output.exists()


def test_migrate_fgui_plan_v1_writes_canonical_v2(tmp_path: Path) -> None:
    document = UIRDocument.model_validate_json(
        Path("tests/fixtures/fgui-plan/generic-primitives.uir.json").read_text("utf-8")
    )
    payload = compile_fgui_plan(document).model_dump(mode="json", by_alias=True)
    payload["schemaVersion"] = 1
    payload.pop("componentDefinitions")
    source = tmp_path / "component-free-v1.fgui-plan.json"
    output = tmp_path / "component-free-v2.fgui-plan.json"
    source.write_text(json.dumps(payload), "utf-8")

    result = CliRunner().invoke(
        app, ["migrate-fgui-plan-v1", str(source), str(output)]
    )

    assert result.exit_code == 0, result.output
    migrated = FGUIPlanDocument.model_validate_json(output.read_text("utf-8"))
    assert migrated.schema_version == 2
    assert migrated.component_definitions == {}
    assert output.read_bytes() == canonical_plan_bytes(migrated)


def test_build_fgui_plan_writes_diagnostics_but_exits_two_when_not_bindable(
    tmp_path: Path,
) -> None:
    output = tmp_path / "broken.fgui-plan.json"

    result = CliRunner().invoke(
        app,
        [
            "build-fgui-plan",
            "tests/fixtures/fgui-plan/generic-masks.uir.json",
            str(output),
        ],
    )

    assert result.exit_code == 2
    assert output.is_file()
    plan = FGUIPlanDocument.model_validate_json(output.read_text("utf-8"))
    assert validate_fgui_plan(plan) == ()
    assert plan.bindable is False
    assert any(
        item.code == "fgui.mask.source_missing" and item.node_id == "node:canvas"
        for item in plan.diagnostics
    )


def test_build_fgui_plan_reports_malformed_uir_as_a_parameter_error(tmp_path: Path) -> None:
    source = tmp_path / "malformed.uir.json"
    source.write_text("{not JSON", "utf-8")

    result = CliRunner().invoke(
        app,
        ["build-fgui-plan", str(source), str(tmp_path / "out.json")],
    )

    assert result.exit_code == 2
    assert "SOURCE" in result.output
    assert "Traceback" not in result.output


def test_build_fgui_plan_rejects_private_profile_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "private.fgui-plan.json"

    result = CliRunner().invoke(
        app,
        [
            "build-fgui-plan",
            "tests/fixtures/fgui-plan/generic-primitives.uir.json",
            str(output),
            "--profile-version",
            r"C:\private\profile.json",
        ],
    )

    assert result.exit_code == 2
    assert "PROFILE" in result.output.upper()
    assert not output.exists()


@pytest.mark.parametrize("invalid_case", ["unknown-field", "invalid-enum"])
def test_build_fgui_plan_reports_schema_invalid_uir_as_a_parameter_error(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    payload = json.loads(
        Path("tests/fixtures/fgui-plan/generic-primitives.uir.json").read_text(
            "utf-8"
        )
    )
    if invalid_case == "unknown-field":
        payload["unexpected"] = True
    else:
        payload["nodes"]["node:root"]["conversion"]["mode"] = "futureNative"
    source = tmp_path / f"{invalid_case}.uir.json"
    source.write_text(json.dumps(payload), "utf-8")
    output = tmp_path / "out.json"

    result = CliRunner().invoke(
        app,
        ["build-fgui-plan", str(source), str(output)],
    )

    assert result.exit_code == 2
    assert "SOURCE" in result.output
    assert "Traceback" not in result.output
    assert not output.exists()


def test_agent_poll_prints_terminal_result(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    config = AgentConfig(agent_id="agent-1", name="Desk")
    result = ApplyResult(
        job_id="job-1",
        agent_id="agent-1",
        project_id="project-1",
        status=ApplyStatus.APPLIED,
        changed_paths=("Sample/Main.xml",),
    )
    monkeypatch.setattr(AgentConfig, "load", classmethod(lambda cls, path: config))
    monkeypatch.setattr(AgentClient, "poll_once", lambda self: result)

    invoked = CliRunner().invoke(
        app,
        ["agent", "poll", "--config-path", str(tmp_path / "agent.json")],
    )
    assert invoked.exit_code == 0
    assert '"status": "applied"' in invoked.stdout
    assert "Sample/Main.xml" in invoked.stdout


def test_serve_accepts_a_built_web_console_directory(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from figma_to_fgui import api

    captured: dict[str, object] = {}
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "assets").mkdir()
    (web_dist / "index.html").write_text("<div id='root'></div>", "utf-8")
    monkeypatch.setattr(api, "create_app", lambda *args, **kwargs: captured.update(kwargs) or object())
    monkeypatch.setattr(uvicorn, "run", lambda application, **kwargs: captured.update(run=kwargs))

    result = CliRunner().invoke(
        app,
        [
            "serve",
            "--web-dist",
            str(web_dist),
        ],
        env={"FIGMA_TO_FGUI_HEALTH_INSTANCE_TOKEN": "test-instance-token"},
    )

    assert result.exit_code == 0, result.stdout
    assert captured["web_dist"] == web_dist
    assert captured["health_instance_token"] == "test-instance-token"
    assert captured["run"] == {
        "host": "127.0.0.1", "port": 8765, "proxy_headers": False, "forwarded_allow_ips": ""
    }
    assert "--health-instance-token" not in CliRunner().invoke(app, ["serve", "--help"]).stdout


def test_serve_enables_the_standalone_app_with_the_local_access_token(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from figma_to_fgui import api

    captured: dict[str, object] = {}
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "assets").mkdir()
    (web_dist / "index.html").write_text("<div id='root'></div>", "utf-8")
    token = tmp_path / "plugin-token.txt"
    token.write_text("s" * 32, "ascii")
    monkeypatch.setattr(api, "create_app", lambda *args, **kwargs: captured.update(kwargs) or object())
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    result = CliRunner().invoke(
        app,
        [
            "serve", "--local-app", "--web-dist", str(web_dist),
            "--plugin-access-token-file", str(token),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["local_app_access_token"] == b"s" * 32


def test_serve_passes_the_configured_templates_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from figma_to_fgui import api

    captured: dict[str, object] = {}
    templates = tmp_path / "templates"
    monkeypatch.setattr(api, "create_app", lambda *args, **kwargs: captured.update(kwargs) or object())
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    result = CliRunner().invoke(app, ["serve", "--templates-root", str(templates)])

    assert result.exit_code == 0, result.output
    assert captured["templates_root"] == templates


def test_serve_fails_before_uvicorn_when_enabled_ai_configuration_is_incomplete(
    monkeypatch: MonkeyPatch,
) -> None:
    started: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: started.append(args))

    result = CliRunner().invoke(
        app,
        ["serve"],
        env={
            "AI_SEMANTIC_ENABLED": "true",
            "AI_SEMANTIC_PROVIDER": "openai",
            "AI_SEMANTIC_BASE_URL": "https://api.openai.com/v1",
            "AI_SEMANTIC_MODEL": "test-model",
            "AI_SEMANTIC_API_KEY": "",
        },
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SemanticConfigurationError)
    assert "AI_SEMANTIC_API_KEY" in str(result.exception)
    assert started == []


def test_serve_closes_the_configured_ai_analyzer_when_server_stops(
    monkeypatch: MonkeyPatch,
) -> None:
    from figma_to_fgui import api, semantic_config

    closed: list[bool] = []
    captured: dict[str, object] = {}

    class Analyzer:
        def close(self) -> None:
            closed.append(True)

    analyzer = Analyzer()
    monkeypatch.setattr(
        api,
        "create_app",
        lambda *args, **kwargs: captured.update(kwargs) or object(),
    )
    monkeypatch.setattr(
        semantic_config,
        "build_semantic_analyzer",
        lambda settings: analyzer,
    )
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0, result.output
    assert captured["semantic_analyzer"] is analyzer
    assert closed == [True]


def test_serve_passes_disabled_ai_as_none_to_create_app(monkeypatch: MonkeyPatch) -> None:
    from figma_to_fgui import api

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        api,
        "create_app",
        lambda *args, **kwargs: captured.update(kwargs) or object(),
    )
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    result = CliRunner().invoke(app, ["serve"], env={"AI_SEMANTIC_ENABLED": "false"})

    assert result.exit_code == 0, result.output
    assert "semantic_analyzer" in captured
    assert captured["semantic_analyzer"] is None


def test_serve_reports_invalid_web_build_as_a_typer_parameter_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["serve", "--web-dist", str(tmp_path / "missing")])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "Usage:" in result.output
    assert "--web-dist" in result.output
    assert "Traceback" not in result.output


def _production_files(tmp_path: Path, origin: str = "https://fgui.corp.example") -> tuple[Path, Path, Path]:
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    (web_dist / "assets").mkdir()
    (web_dist / "index.html").write_text("<div id='root'></div>", "utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"id": "123456789", "networkAccess": {"allowedDomains": [origin]}}),
        "utf-8",
    )
    secret = tmp_path / "plugin-secret.bin"
    secret.write_bytes(b"s" * 32)
    return web_dist, manifest, secret


def _gateway_secret(tmp_path: Path, value: bytes = b"g" * 32) -> Path:
    secret = tmp_path / "gateway-secret.bin"
    secret.write_bytes(value)
    return secret


def test_production_rejects_non_ascii_plugin_access_token(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    web_dist, manifest, token = _production_files(tmp_path)
    token.write_bytes(bytes(range(32)))
    gateway = _gateway_secret(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--data-dir", str(tmp_path / "data"),
            "--public-origin", "https://fgui.corp.example",
            "--plugin-access-token-file", str(token),
            "--gateway-secret-file", str(gateway), "--web-dist", str(web_dist),
            "--plugin-manifest", str(manifest),
        ],
    )

    assert result.exit_code == 2
    assert "--plugin-access-token-file" in result.output


def test_production_serve_requires_safe_complete_configuration(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    result = CliRunner().invoke(app, ["serve", "--production"])

    assert result.exit_code == 2
    assert "--public-origin" in result.output
    assert "Traceback" not in result.output

    web_dist, manifest, secret = _production_files(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--public-origin", "https://fgui.corp.example",
            "--plugin-access-token-file", str(secret), "--web-dist", str(web_dist),
            "--plugin-manifest", str(manifest),
        ],
    )
    assert result.exit_code == 2
    assert "--data-dir" in result.output

    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--data-dir", str(tmp_path / "data"),
            "--public-origin", "https://fgui.corp.example", "--plugin-access-token-file", str(secret),
            "--web-dist", str(web_dist), "--plugin-manifest", str(manifest),
        ],
    )
    assert result.exit_code == 2
    assert "--gateway-secret-file" in result.output

    for option, unsafe in (
        ("--public-origin", "http://fgui.corp.example"),
        ("--public-origin", "https://one.example,https://two.example"),
        ("--public-origin", "https://*.corp.example"),
    ):
        result = CliRunner().invoke(
            app,
            [
                "serve", "--production", option, unsafe, "--plugin-access-token-file", str(secret),
                "--data-dir", str(tmp_path / "data"), "--web-dist", str(web_dist),
                "--plugin-manifest", str(manifest),
            ],
        )
        assert result.exit_code == 2
        assert "--public-origin" in result.output
        assert "Traceback" not in result.output


def test_production_serve_keeps_the_application_on_loopback(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    web_dist, manifest, secret = _production_files(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--data-dir", str(tmp_path / "data"),
            "--public-origin", "https://fgui.corp.example", "--plugin-access-token-file", str(secret),
            "--web-dist", str(web_dist), "--plugin-manifest", str(manifest), "--host", "0.0.0.0",
        ],
    )

    assert result.exit_code == 2
    assert "--host" in result.output
    assert "Traceback" not in result.output


def test_production_serve_rejects_unsafe_secret_and_manifest_without_disclosure(tmp_path: Path) -> None:
    web_dist, manifest, secret = _production_files(tmp_path)
    gateway_secret = _gateway_secret(tmp_path)
    secret.write_bytes(b"short")
    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--public-origin", "https://fgui.corp.example",
            "--data-dir", str(tmp_path / "data"), "--plugin-access-token-file", str(secret), "--web-dist", str(web_dist),
            "--plugin-manifest", str(manifest), "--gateway-secret-file", str(gateway_secret),
        ],
    )

    assert result.exit_code == 2
    assert "--plugin-access-token-file" in result.output
    assert str(secret) not in result.output
    assert "short" not in result.output
    assert "Traceback" not in result.output

    secret.write_bytes(b"s" * 32)
    manifest.write_text(json.dumps({"id": "123456789", "networkAccess": {"allowedDomains": ["https://wrong.example"]}}), "utf-8")
    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--public-origin", "https://fgui.corp.example",
            "--data-dir", str(tmp_path / "data"), "--plugin-access-token-file", str(secret), "--web-dist", str(web_dist),
            "--plugin-manifest", str(manifest), "--gateway-secret-file", str(gateway_secret),
        ],
    )

    assert result.exit_code == 2
    assert "--plugin-manifest" in result.output
    assert str(manifest) not in result.output
    assert "Traceback" not in result.output


def test_production_serve_requires_a_distinct_full_length_gateway_secret(tmp_path: Path) -> None:
    web_dist, manifest, plugin_secret = _production_files(tmp_path)
    gateway_secret = _gateway_secret(tmp_path, b"short")
    command = [
        "serve", "--production", "--data-dir", str(tmp_path / "data"),
        "--public-origin", "https://fgui.corp.example", "--plugin-access-token-file", str(plugin_secret),
        "--gateway-secret-file", str(gateway_secret), "--web-dist", str(web_dist),
        "--plugin-manifest", str(manifest),
    ]
    short = CliRunner().invoke(app, command)

    assert short.exit_code == 2
    assert "--gateway-secret-file" in short.output
    assert str(gateway_secret) not in short.output
    assert "Traceback" not in short.output

    gateway_secret.write_bytes(plugin_secret.read_bytes())
    same = CliRunner().invoke(app, command)

    assert same.exit_code == 2
    assert "--gateway-secret-file" in same.output
    assert "Traceback" not in same.output


def test_production_serve_configures_single_origin_without_fixture_jobs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from figma_to_fgui import api

    captured: dict[str, object] = {}
    web_dist, manifest, secret = _production_files(tmp_path)
    gateway_secret = _gateway_secret(tmp_path)
    monkeypatch.setattr(api, "create_app", lambda *args, **kwargs: captured.update(kwargs) or object())
    monkeypatch.setattr(uvicorn, "run", lambda application, **kwargs: captured.update(run=kwargs))

    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--public-origin", "https://fgui.corp.example",
            "--data-dir", str(tmp_path / "data"), "--plugin-access-token-file", str(secret), "--web-dist", str(web_dist),
            "--plugin-manifest", str(manifest), "--gateway-secret-file", str(gateway_secret),
            "--trusted-proxy", "10.0.0.7",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["plugin_access_token"] == b"s" * 32
    assert captured["gateway_secret"] == b"g" * 32
    assert captured["public_origin"] == "https://fgui.corp.example"
    assert captured["allow_fixture_jobs"] is False
    assert captured["run"] == {
        "host": "127.0.0.1", "port": 8765, "proxy_headers": True,
        "forwarded_allow_ips": "10.0.0.7",
    }


def test_lan_production_accepts_only_an_explicit_private_http_origin(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from figma_to_fgui import api

    captured: dict[str, object] = {}
    origin = "http://192.168.1.100:8780"
    web_dist, manifest, secret = _production_files(tmp_path, origin)
    manifest.write_text(
        json.dumps({
            "id": "123456789",
            "networkAccess": {
                "allowedDomains": ["*"],
                "reasoning": "Connects to the organization's private LAN Figma-to-FairyGUI service.",
            },
        }),
        "utf-8",
    )
    gateway_secret = _gateway_secret(tmp_path)
    monkeypatch.setattr(api, "create_app", lambda *args, **kwargs: captured.update(kwargs) or object())
    monkeypatch.setattr(uvicorn, "run", lambda application, **kwargs: None)

    result = CliRunner().invoke(
        app,
        [
            "serve", "--production", "--lan", "--public-origin", origin,
            "--data-dir", str(tmp_path / "data"), "--plugin-access-token-file", str(secret),
            "--gateway-secret-file", str(gateway_secret), "--web-dist", str(web_dist),
            "--plugin-manifest", str(manifest),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["public_origin"] == origin

    for index, unsafe in enumerate((
        "http://8.8.8.8:8780", "http://169.254.1.1:8780",
        "http://172.32.0.1:8780", "http://192.0.0.1:8780", "https://fgui.corp.example",
    )):
        unsafe_root = tmp_path / f"unsafe-{index}"
        unsafe_root.mkdir()
        _, unsafe_manifest, _ = _production_files(unsafe_root, unsafe)
        rejected = CliRunner().invoke(
            app,
            [
                "serve", "--production", "--lan", "--public-origin", unsafe,
                "--data-dir", str(tmp_path / "data"), "--plugin-access-token-file", str(secret),
                "--gateway-secret-file", str(gateway_secret), "--web-dist", str(web_dist),
                "--plugin-manifest", str(unsafe_manifest),
            ],
        )
        assert rejected.exit_code == 2
        assert "--public-origin" in rejected.output
