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

# Keep CLI diagnostics plain and deterministic. Rich wraps long option names in
# narrow/non-TTY runners, which makes parameter-error output depend on terminal
# width and breaks both machine assertions and copy/paste diagnostics.
app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
agent_app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
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
            ) from error
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
