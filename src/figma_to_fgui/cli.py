import hashlib
import hmac
import ipaddress
import json
import os
import re
import stat
import time
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib.parse import urlsplit

import typer
from pydantic import ValidationError

from figma_to_fgui.classify import classify_tree
from figma_to_fgui.component_mapping import load_mapping_catalog, validate_mapping_catalog
from figma_to_fgui.data_policy import private_data_violations
from figma_to_fgui.fgui_asset_payloads import (
    MAX_ASSET_PAYLOAD_BYTES,
    MAX_TOTAL_ASSET_PAYLOAD_BYTES,
)
from figma_to_fgui.fgui_new_project_build import NewProjectBuildError, build_new_project
from figma_to_fgui.fgui_new_project_models import (
    AssetPayload,
    AssetPayloadSet,
    NewProjectConfig,
)
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_models import (
    FGUIPlanDocument,
    FGUIPlanV1Document,
    migrate_plan_v1_without_components,
)
from figma_to_fgui.fgui_plan_validate import canonical_plan_bytes, validate_fgui_plan
from figma_to_fgui.models import Severity
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.pipeline import ConversionRequest, convert
from figma_to_fgui.project_index import index_project
from figma_to_fgui.rules import load_rules
from figma_to_fgui.uir_compile import compile_uir
from figma_to_fgui.uir_models import UIRDocument
from figma_to_fgui.uir_validate import canonical_uir_bytes, validate_uir
from figma_to_fgui.validate import has_errors, validate_staging

app = typer.Typer(no_args_is_help=True)
agent_app = typer.Typer(no_args_is_help=True)
app.add_typer(agent_app, name="agent")

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


_FileIdentity = tuple[int, int, int, int, int, int, int]


def _file_identity(metadata: os.stat_result) -> _FileIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns if os.name != "nt" else 0,
        getattr(metadata, "st_file_attributes", 0),
    )


def _read_stable_regular_file(path: Path, *, max_bytes: int | None = None) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or _is_link_or_reparse(path):
        raise ValueError
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise ValueError
        if max_bytes is not None and opened.st_size > max_bytes:
            raise ValueError
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if _file_identity(after) != _file_identity(opened) or _file_identity(current) != _file_identity(opened):
            raise ValueError
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _production_origin(value: str, *, allow_private_http: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise typer.BadParameter(
            "must be exactly one HTTPS origin", param_hint="--public-origin"
        ) from error
    if (
        "*" in value
        or "," in value
        or parsed.scheme not in ({"https", "http"} if allow_private_http else {"https"})
        or (allow_private_http and parsed.scheme != "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise typer.BadParameter("must be exactly one HTTPS origin", param_hint="--public-origin")
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise typer.BadParameter(
                "must be one private IPv4 HTTP origin in LAN mode", param_hint="--public-origin"
            ) from error
        private_networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        if address.version != 4 or not any(address in network for network in private_networks):
            raise typer.BadParameter(
                "must be one private IPv4 HTTP origin in LAN mode", param_hint="--public-origin"
            )
    return f"{parsed.scheme}://{parsed.netloc}"


def _secret_file(secret_file: Path | None, option: str) -> bytes:
    if secret_file is None:
        raise typer.BadParameter("is required in production", param_hint=option)
    try:
        if not secret_file.is_file():
            raise OSError
        secret = secret_file.read_bytes()
    except OSError as error:
        raise typer.BadParameter("must name a readable regular file", param_hint=option) from error
    if len(secret) < 32:
        raise typer.BadParameter("must contain at least 32 bytes", param_hint=option)
    return secret


def _plugin_access_token_file(secret_file: Path | None) -> bytes:
    token = _secret_file(secret_file, "--plugin-access-token-file")
    if len(token) > 256 or any(byte < 0x21 or byte > 0x7E for byte in token):
        raise typer.BadParameter(
            "must contain 32-256 printable ASCII characters",
            param_hint="--plugin-access-token-file",
        )
    return token


def _validate_plugin_manifest(path: Path | None, public_origin: str) -> None:
    if path is None:
        raise typer.BadParameter("is required in production", param_hint="--plugin-manifest")
    try:
        manifest = json.loads(path.read_text("utf-8"))
        plugin_id = manifest["id"]
        network_access = manifest["networkAccess"]
        domains = network_access["allowedDomains"]
        reasoning = network_access.get("reasoning")
    except (OSError, TypeError, ValueError, KeyError):
        raise typer.BadParameter("must be a readable production plugin manifest", param_hint="--plugin-manifest") from None
    parsed_origin = urlsplit(public_origin)
    private_http = False
    if parsed_origin.scheme == "http" and parsed_origin.hostname:
        try:
            private_http = ipaddress.ip_address(parsed_origin.hostname).is_private
        except ValueError:
            pass
    domains_valid = (
        domains == [public_origin]
        if not private_http
        else domains == ["*"] and isinstance(reasoning, str) and bool(reasoning.strip())
    )
    if not isinstance(plugin_id, str) or not plugin_id.isdecimal() or not domains_valid:
        raise typer.BadParameter(
            "must use network access compatible with the configured public origin",
            param_hint="--plugin-manifest",
        )


def _trusted_proxy(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise typer.BadParameter(
            "must be one explicit proxy IP address", param_hint="--trusted-proxy"
        ) from error


def _write_json(output: Path, value: object) -> None:
    output.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        "utf-8",
    )


def _is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _load_plan_v2(path: Path) -> FGUIPlanDocument:
    try:
        encoded = _read_stable_regular_file(path)
        raw = json.loads(encoded.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
        if not isinstance(raw, dict) or set(raw) != {
            "schemaVersion", "documentId", "sourceUirSha256", "profileVersion",
            "ruleVersion", "bindable", "roots", "nodes", "componentDefinitions",
            "resources", "masks", "decisions", "diagnostics",
        }:
            raise ValueError
        if type(raw["schemaVersion"]) is not int or raw["schemaVersion"] != 2:
            raise ValueError
        if type(raw["ruleVersion"]) is not int or type(raw["bindable"]) is not bool:
            raise ValueError
        if type(raw["profileVersion"]) is not str:
            raise ValueError
        plan = FGUIPlanDocument.model_validate(raw)
        if encoded != canonical_plan_bytes(plan):
            raise ValueError
    except (OSError, UnicodeDecodeError, ValidationError, ValueError):
        raise typer.BadParameter(
            "must be readable strict Plan v2 JSON", param_hint="PLAN"
        ) from None
    diagnostics = validate_fgui_plan(plan)
    if not plan.bindable or any(item.severity == Severity.ERROR for item in diagnostics):
        raise typer.BadParameter(
            "must be a valid bindable Plan v2 document", param_hint="PLAN"
        )
    return plan


def _load_new_project_config(path: Path) -> NewProjectConfig:
    try:
        encoded = _read_stable_regular_file(path)
        raw = json.loads(encoded.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
        if not isinstance(raw, dict) or set(raw) != {
            "projectName", "packageName", "fairyGuiVersion", "publishTarget",
            "namingPolicyVersion",
        }:
            raise ValueError
        if any(type(raw[key]) is not str for key in ("projectName", "packageName", "fairyGuiVersion", "publishTarget")):
            raise ValueError
        if type(raw["namingPolicyVersion"]) is not int:
            raise ValueError
        config = NewProjectConfig.model_validate(raw)
        if encoded != _canonical_json_bytes(config.model_dump(mode="json", by_alias=True)):
            raise ValueError
        return config
    except (OSError, UnicodeDecodeError, ValidationError, ValueError):
        raise typer.BadParameter(
            "must be readable strict new-project config JSON", param_hint="CONFIG"
        ) from None


def _safe_asset_filename(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError
    if ":" in candidate.parts[0] or unicodedata.normalize("NFC", value) != value:
        raise ValueError
    return candidate.as_posix()


def load_declared_asset_directory(
    asset_directory: Path, resources: Mapping[str, object]
) -> AssetPayloadSet:
    """Load exactly the regular files declared by one closed asset manifest."""
    try:
        if not asset_directory.is_dir() or _is_link_or_reparse(asset_directory):
            raise ValueError
        asset_directory_identity = _file_identity(asset_directory.lstat())
        manifest_path = asset_directory / "manifest.json"
        if not manifest_path.is_file() or _is_link_or_reparse(manifest_path):
            raise ValueError
        manifest_identity = _file_identity(manifest_path.lstat())
        manifest_content = _read_stable_regular_file(manifest_path)
        raw = json.loads(
            manifest_content.decode("utf-8"),
            object_pairs_hook=_no_duplicate_object,
        )
        if _file_identity(manifest_path.lstat()) != manifest_identity:
            raise ValueError
        stable_paths: dict[Path, _FileIdentity] = {
            asset_directory: asset_directory_identity,
            manifest_path: manifest_identity,
        }
        stable_content_hashes = {
            manifest_path: hashlib.sha256(manifest_content).digest(),
        }
        if not isinstance(raw, dict) or set(raw) != {"resources"}:
            raise ValueError
        declarations = raw["resources"]
        if not isinstance(declarations, dict):
            raise TypeError
        if set(declarations) != set(resources):
            raise ValueError

        seen_paths: set[str] = set()
        declared_paths: set[str] = set()
        declared_directories: set[str] = set()
        payloads: list[AssetPayload] = []
        total_asset_bytes = 0
        for resource_id in sorted(declarations):
            declaration = declarations[resource_id]
            if not isinstance(resource_id, str) or not isinstance(declaration, dict):
                raise TypeError
            if set(declaration) != {"filename", "declaredMimeType"}:
                raise ValueError
            filename = _safe_asset_filename(declaration["filename"])
            collision_key = unicodedata.normalize("NFC", filename).casefold()
            if collision_key in seen_paths:
                raise ValueError
            seen_paths.add(collision_key)
            declared_paths.add(filename)
            parts = PurePosixPath(filename).parts
            declared_directories.update(
                PurePosixPath(*parts[:index]).as_posix()
                for index in range(1, len(parts))
            )
            mime_type = declaration["declaredMimeType"]
            if not isinstance(mime_type, str) or not mime_type.strip():
                raise ValueError

            source = asset_directory.joinpath(*PurePosixPath(filename).parts)
            current = asset_directory
            for part in PurePosixPath(filename).parts:
                current = current / part
                if not current.exists() or _is_link_or_reparse(current):
                    raise ValueError
                if current != source:
                    stable_paths.setdefault(current, _file_identity(current.lstat()))
            if not source.is_file():
                raise ValueError
            source_identity = _file_identity(source.lstat())
            encoded_size = source_identity[3]
            remaining_aggregate_bytes = (
                MAX_TOTAL_ASSET_PAYLOAD_BYTES - total_asset_bytes
            )
            if (
                encoded_size > MAX_ASSET_PAYLOAD_BYTES
                or encoded_size > remaining_aggregate_bytes
            ):
                raise ValueError
            content = _read_stable_regular_file(
                source,
                max_bytes=min(MAX_ASSET_PAYLOAD_BYTES, remaining_aggregate_bytes),
            )
            if total_asset_bytes + len(content) > MAX_TOTAL_ASSET_PAYLOAD_BYTES:
                raise ValueError
            total_asset_bytes += len(content)
            if _file_identity(source.lstat()) != source_identity:
                raise ValueError
            stable_paths[source] = source_identity
            stable_content_hashes[source] = hashlib.sha256(content).digest()
            payloads.append(
                AssetPayload(
                    resourceId=resource_id,
                    declaredMimeType=mime_type,
                    content=content,
                )
            )

        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        for item in asset_directory.rglob("*"):
            if _is_link_or_reparse(item):
                raise ValueError
            if item == manifest_path:
                continue
            if item.is_file():
                actual_files.add(item.relative_to(asset_directory).as_posix())
            elif item.is_dir():
                actual_directories.add(item.relative_to(asset_directory).as_posix())
            else:
                raise ValueError
        if actual_files != declared_paths or actual_directories != declared_directories:
            raise ValueError
        for path, identity in stable_paths.items():
            if _is_link_or_reparse(path) or _file_identity(path.lstat()) != identity:
                raise ValueError
        for path, digest in stable_content_hashes.items():
            if hashlib.sha256(_read_stable_regular_file(path)).digest() != digest:
                raise ValueError
        return AssetPayloadSet.from_items(payloads)
    except (
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
    ):
        raise typer.BadParameter(
            "must be a closed safe declared asset directory", param_hint="ASSET_DIRECTORY"
        ) from None


@app.command("normalize")
def normalize_command(source: Path, output: Path) -> None:
    roots, diagnostics = normalize_document(json.loads(source.read_text("utf-8")))
    _write_json(
        output,
        {
            "roots": [root.model_dump(mode="json") for root in roots],
            "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        },
    )


@app.command("build-uir")
def build_uir_command(
    source: Path,
    output: Path,
    source_revision: Annotated[str, typer.Option("--source-revision")],
    selection_id: Annotated[str, typer.Option("--selection-id")],
    mapping_catalog: Annotated[Path | None, typer.Option("--mapping-catalog")] = None,
) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", source_revision) is None:
        raise typer.BadParameter(
            "must be 64 lowercase hexadecimal characters",
            param_hint="--source-revision",
        )
    roots, normalize_diagnostics = normalize_document(
        json.loads(source.read_text("utf-8"))
    )
    catalog = None if mapping_catalog is None else load_mapping_catalog(mapping_catalog)
    try:
        document = compile_uir(
            roots,
            source_revision=source_revision,
            selection_id=selection_id,
            mapping_catalog=catalog,
        )
    except ValueError as error:
        raise typer.BadParameter(
            str(error), param_hint="--mapping-catalog"
        ) from error
    diagnostics = (*normalize_diagnostics, *validate_uir(document))
    if has_errors(diagnostics):
        raise typer.Exit(code=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_uir_bytes(document))


@app.command("build-fgui-plan")
def build_fgui_plan_command(
    source: Path,
    output: Path,
    profile_version: Annotated[str, typer.Option("--profile-version")] = "fgui-6.1.4-v1",
    rule_version: Annotated[int, typer.Option("--rule-version", min=1)] = 1,
) -> None:
    if not profile_version.strip() or private_data_violations(profile_version):
        raise typer.BadParameter(
            "must be a public stable profile identifier",
            param_hint="--profile-version",
        )
    try:
        document = UIRDocument.model_validate_json(source.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError, ValueError):
        raise typer.BadParameter(
            "must be readable canonical UIR JSON", param_hint="SOURCE"
        ) from None
    uir_diagnostics = validate_uir(document)
    if any(item.severity == Severity.ERROR for item in uir_diagnostics):
        raise typer.BadParameter("source UIR is invalid", param_hint="SOURCE")
    plan = compile_fgui_plan(
        document, profile_version=profile_version, rule_version=rule_version
    )
    plan_diagnostics = validate_fgui_plan(plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_plan_bytes(plan))
    if not plan.bindable or any(
        item.severity == Severity.ERROR for item in plan_diagnostics
    ):
        raise typer.Exit(code=2)


@app.command("migrate-fgui-plan-v1")
def migrate_fgui_plan_v1_command(source: Path, output: Path) -> None:
    """Explicitly migrate a component-free strict Plan v1 document to v2."""
    try:
        plan_v1 = FGUIPlanV1Document.model_validate_json(source.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError, ValueError):
        raise typer.BadParameter(
            "must be readable strict Plan v1 JSON", param_hint="SOURCE"
        ) from None
    try:
        plan_v2 = migrate_plan_v1_without_components(plan_v1)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="SOURCE") from None
    diagnostics = validate_fgui_plan(plan_v2)
    error_codes = sorted(
        {item.code for item in diagnostics if item.severity == Severity.ERROR}
    )
    if error_codes:
        raise typer.BadParameter(
            "Plan v1 is not semantically valid for safe migration: "
            + ", ".join(error_codes),
            param_hint="SOURCE",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_plan_bytes(plan_v2))


@app.command("build-fgui-project")
def build_fgui_project_command(
    plan: Path,
    config: Path,
    asset_directory: Path,
    output_directory: Path,
) -> None:
    """Build and atomically publish a fresh FairyGUI 6.1.4 project archive."""
    parsed_plan = _load_plan_v2(plan)
    parsed_config = _load_new_project_config(config)
    payloads = load_declared_asset_directory(asset_directory, parsed_plan.resources)
    try:
        built = build_new_project(
            parsed_plan, parsed_config, payloads, output_directory
        )
    except NewProjectBuildError as error:
        typer.echo(
            json.dumps(
                [item.model_dump(mode="json", by_alias=True) for item in error.diagnostics],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            err=True,
        )
        raise typer.Exit(code=2) from None
    typer.echo(
        json.dumps(
            {
                "byte_size": built.byte_size,
                "download_name": built.download_name,
                "project_name": built.project_name,
                "sha256": built.sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@app.command("index-project")
def index_project_command(project_root: Path, output: Path) -> None:
    _write_json(output, index_project(project_root).model_dump(mode="json"))


@app.command("verify-component-mappings")
def verify_component_mappings_command(
    project_root: Path,
    output: Path,
    catalog: Path = Path("rules/default/component-mapping-candidates.json"),
) -> None:
    verified = validate_mapping_catalog(load_mapping_catalog(catalog), index_project(project_root))
    _write_json(output, verified.model_dump(mode="json"))


@app.command("classify")
def classify_command(source: Path, rules: Path, output: Path) -> None:
    roots, _ = normalize_document(json.loads(source.read_text("utf-8")))
    decisions = classify_tree(roots, load_rules(rules))
    _write_json(output, [item.model_dump(mode="json") for item in decisions])


@app.command("validate")
def validate_command(staging_root: Path, project_root: Path, output: Path) -> None:
    diagnostics = validate_staging(staging_root, index_project(project_root))
    _write_json(output, [item.model_dump(mode="json") for item in diagnostics])
    if has_errors(diagnostics):
        raise typer.Exit(code=2)


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
    _write_json(output, result.model_dump(mode="json"))
    if not result.applicable:
        raise typer.Exit(code=2)


@app.command("serve")
def serve_command(
    data_dir: Path | None = None,
    fixtures_root: Path = Path("tests/fixtures"),
    rules: Path = Path("rules/default/classification.yaml"),
    web_dist: Path | None = None,
    local_app: bool = False,
    production: bool = False,
    lan: bool = False,
    public_origin: str | None = None,
    plugin_access_token_file: Path | None = None,
    gateway_secret_file: Path | None = None,
    plugin_manifest: Path | None = None,
    templates_root: Path | None = None,
    trusted_proxy: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    health_instance_token: Annotated[
        str | None,
        typer.Option(hidden=True, envvar="FIGMA_TO_FGUI_HEALTH_INSTANCE_TOKEN"),
    ] = None,
) -> None:
    configured_data_dir = data_dir or Path(".figma-to-fgui")
    if web_dist is not None and (
        not web_dist.is_dir()
        or not (web_dist / "index.html").is_file()
        or not (web_dist / "assets").is_dir()
    ):
        raise typer.BadParameter(
            "must contain index.html and an assets directory",
            param_hint="--web-dist",
        )
    origin: str | None = None
    plugin_access_token: bytes | None = None
    gateway_secret: bytes | None = None
    if production:
        if public_origin is None:
            raise typer.BadParameter("is required in production", param_hint="--public-origin")
        if web_dist is None:
            raise typer.BadParameter("is required in production", param_hint="--web-dist")
        if data_dir is None:
            raise typer.BadParameter("is required in production", param_hint="--data-dir")
        if host != "127.0.0.1":
            raise typer.BadParameter("must be 127.0.0.1 in production", param_hint="--host")
        origin = _production_origin(public_origin, allow_private_http=lan)
        plugin_access_token = _plugin_access_token_file(plugin_access_token_file)
        gateway_secret = _secret_file(gateway_secret_file, "--gateway-secret-file")
        if hmac.compare_digest(plugin_access_token, gateway_secret):
            raise typer.BadParameter(
                "must differ from the plugin access token", param_hint="--gateway-secret-file"
            )
        _validate_plugin_manifest(plugin_manifest, origin)
    elif lan:
        raise typer.BadParameter("requires --production", param_hint="--lan")
    elif (
        public_origin is not None
        or gateway_secret_file is not None
        or plugin_manifest is not None
    ):
        raise typer.BadParameter("requires --production", param_hint="--production")
    elif plugin_access_token_file is not None:
        plugin_access_token = _plugin_access_token_file(plugin_access_token_file)
    if local_app:
        if production:
            raise typer.BadParameter("cannot be combined with --production", param_hint="--local-app")
        if host != "127.0.0.1":
            raise typer.BadParameter("requires host 127.0.0.1", param_hint="--local-app")
        if web_dist is None:
            raise typer.BadParameter("requires --web-dist", param_hint="--local-app")
        if plugin_access_token is None:
            raise typer.BadParameter(
                "requires --plugin-access-token-file", param_hint="--local-app"
            )
    proxy = _trusted_proxy(trusted_proxy)

    import uvicorn

    from figma_to_fgui.api import create_app
    from figma_to_fgui.semantic_config import (
        build_semantic_analyzer,
        load_semantic_service_settings,
    )

    semantic_analyzer = build_semantic_analyzer(load_semantic_service_settings(os.environ))
    try:
        application = create_app(
            configured_data_dir,
            fixtures_root,
            rules,
            web_dist=web_dist,
            health_instance_token=health_instance_token,
            plugin_access_token=plugin_access_token,
            gateway_secret=gateway_secret,
            public_origin=origin,
            allow_fixture_jobs=not production,
            templates_root=templates_root,
            semantic_analyzer=semantic_analyzer,
            local_app_access_token=plugin_access_token if local_app else None,
        )
        uvicorn.run(
            application,
            host=host,
            port=port,
            proxy_headers=proxy is not None,
            forwarded_allow_ips=proxy or "",
        )
    finally:
        if semantic_analyzer is not None:
            semantic_analyzer.close()


@agent_app.command("register")
def agent_register(
    agent_id: str,
    name: str,
    api_url: str = "http://127.0.0.1:8765",
    config_path: Path | None = None,
) -> None:
    from figma_to_fgui.agent import AgentClient, AgentConfig, default_config_path

    path = config_path or default_config_path()
    config = AgentConfig(agent_id=agent_id, name=name, api_url=api_url)
    AgentClient(config).register()
    config.save(path)


@agent_app.command("bind")
def agent_bind(project_id: str, path: Path, config_path: Path | None = None) -> None:
    from figma_to_fgui.agent import (
        AgentClient,
        AgentConfig,
        bind_local_project,
        default_config_path,
    )

    target = config_path or default_config_path()
    config = AgentConfig.load(target)
    AgentClient(config).bind(project_id)
    updated = config.model_copy(
        update={"projects": {**config.projects, project_id: bind_local_project(path)}}
    )
    updated.save(target)


@agent_app.command("poll")
def agent_poll(once: bool = True, config_path: Path | None = None) -> None:
    from figma_to_fgui.agent import AgentClient, AgentConfig, default_config_path

    config = AgentConfig.load(config_path or default_config_path())
    client = AgentClient(config)
    if once:
        result = client.poll_once()
        typer.echo("No approved job." if result is None else result.model_dump_json(indent=2))


@agent_app.command("run")
def agent_run(interval: float = 5, config_path: Path | None = None) -> None:
    import httpx

    from figma_to_fgui.agent import AgentClient, AgentConfig, default_config_path

    client = AgentClient(AgentConfig.load(config_path or default_config_path()))
    while True:
        try:
            client.poll_once()
        except httpx.HTTPError:
            pass
        time.sleep(interval)


if __name__ == "__main__":
    app()
