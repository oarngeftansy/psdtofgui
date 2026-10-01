"""Fail-closed validation for raw asset payloads used by the XML writer."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from figma_to_fgui.fgui_new_project_models import AssetPayload, AssetPayloadSet
from figma_to_fgui.fgui_plan_models import ResourcePlan
from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.svg_security import validate_safe_svg

HASH_CHUNK_SIZE: Final = 64 * 1024
MAX_ASSET_PAYLOAD_BYTES: Final = 64 * 1024 * 1024
MAX_TOTAL_ASSET_PAYLOAD_BYTES: Final = 256 * 1024 * 1024
PILLOW_PROBE_TIMEOUT_SECONDS: Final = 2.0
_FORMAT_DETAILS: Final = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FileIdentity = tuple[int, int, int, int, int, int, int]


@dataclass(frozen=True)
class ValidatedAssetPayload:
    """One resource whose retained byte payload matches its public plan facts."""

    resource: ResourcePlan
    payload: AssetPayload = field(repr=False)

    @property
    def content(self) -> bytes:
        """Return bytes only to the serializer after this input gate succeeds."""
        return self.payload.content


class NewProjectInputError(Exception):
    """Input-gate failure containing only deterministic, public diagnostics."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("New FairyGUI project input validation failed.")


def diagnostic_sort_key(
    diagnostic: Diagnostic,
) -> tuple[str, tuple[str, ...], str, str, str, int, str, str, bool]:
    """Provide one public, stable order for error reporting."""
    return (
        diagnostic.code,
        diagnostic.evidence,
        diagnostic.path or "",
        diagnostic.node_id or "",
        diagnostic.rule_id or "",
        diagnostic.rule_version or 0,
        diagnostic.message,
        diagnostic.suggested_action or "",
        diagnostic.blocks_binding,
    )


def _diagnostic(code: str, resource_id: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=Severity.ERROR,
        message=message,
        node_id=resource_id,
        blocks_binding=True,
    )


def _streamed_sha256(content: bytes) -> str:
    digest = hashlib.sha256()
    view = memoryview(content)
    for offset in range(0, len(view), HASH_CHUNK_SIZE):
        digest.update(view[offset : offset + HASH_CHUNK_SIZE])
    return digest.hexdigest()


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


def _is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
    )


def read_bounded_stable_asset_file(path: Path, *, max_bytes: int) -> bytes:
    """Read one unchanged regular file without allocating beyond ``max_bytes``."""
    before = path.lstat()
    if (
        max_bytes < 0
        or not stat.S_ISREG(before.st_mode)
        or _is_link_or_reparse(path)
        or before.st_size > max_bytes
    ):
        raise ValueError("invalid asset file")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise ValueError("asset file changed")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, HASH_CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("asset file too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            _file_identity(after) != _file_identity(opened)
            or _file_identity(current) != _file_identity(opened)
            or _is_link_or_reparse(path)
        ):
            raise ValueError("asset file changed")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _inspect_raster_in_isolated_process(content: bytes) -> tuple[str, int, int] | None:
    """Run Pillow in a subprocess so caller globals cannot affect its policy."""
    probe = Path(__file__).with_name("fgui_image_probe.py").resolve(strict=True)
    trusted_cwd = Path(sys.executable).resolve(strict=True).parent
    environment = {
        key: value
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key)) is not None
    }
    command = [
        str(Path(sys.executable).resolve(strict=True)),
        "-I",
        str(probe),
        str(MAX_ASSET_PAYLOAD_BYTES),
    ]
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            cwd=trusted_cwd,
        )
        output, _ = process.communicate(content, timeout=PILLOW_PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            process.kill()
            process.communicate()
        return None
    if process.returncode != 0:
        return None
    try:
        response = json.loads(output)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(response, dict):
        return None
    detected_format = response.get("format")
    width = response.get("width")
    height = response.get("height")
    if (
        not isinstance(detected_format, str)
        or not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or width <= 0
        or height <= 0
    ):
        return None
    return detected_format, width, height


def _nine_slice_is_in_bounds(resource: ResourcePlan, width: int, height: int) -> bool:
    nine_slice = resource.nine_slice
    return nine_slice is None or (
        nine_slice.x + nine_slice.width <= width and nine_slice.y + nine_slice.height <= height
    )


def validate_asset_payloads(
    resources: Mapping[str, ResourcePlan], payloads: AssetPayloadSet
) -> tuple[ValidatedAssetPayload, ...]:
    """Validate every supplied payload before any resource bytes reach the writer.

    This deliberately accumulates only public metadata failures.  Raw bytes and
    parser details are never placed in diagnostics or exception text.
    """
    diagnostics: list[Diagnostic] = []
    resource_keys = set(resources)
    payload_keys = set(payloads.by_resource_id)

    for resource_id in sorted(resource_keys - payload_keys):
        diagnostics.append(
            _diagnostic(
                "fgui.writer.asset.missing",
                resource_id,
                "The resource has no supplied asset payload.",
            )
        )
    for resource_id in sorted(payload_keys - resource_keys):
        diagnostics.append(
            _diagnostic(
                "fgui.writer.asset.unexpected",
                resource_id,
                "The supplied asset payload is not declared by the resource plan.",
            )
        )

    validated: dict[str, ValidatedAssetPayload] = {}
    total_payload_bytes = sum(len(payload.content) for payload in payloads.by_resource_id.values())
    aggregate_oversized = total_payload_bytes > MAX_TOTAL_ASSET_PAYLOAD_BYTES
    for resource_id in sorted(resource_keys & payload_keys):
        resource = resources[resource_id]
        payload = payloads.payload_for(resource_id)
        if resource.id != resource_id:
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.resource_key_mismatch",
                    resource_id,
                    "The resource mapping key does not match the resource identity.",
                )
            )
            continue
        if len(payload.content) > MAX_ASSET_PAYLOAD_BYTES or aggregate_oversized:
            diagnostics.append(
                _diagnostic(
                    (
                        "fgui.writer.asset.total_payload_too_large"
                        if aggregate_oversized
                        else "fgui.writer.asset.payload_too_large"
                    ),
                    resource_id,
                    "The asset payload set exceeds the Writer v1 encoded-byte limit.",
                )
            )
            continue
        if (
            resource.content_sha256 is None
            or _streamed_sha256(payload.content) != resource.content_sha256
        ):
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.hash_mismatch",
                    resource_id,
                    "The asset payload does not match the declared content hash.",
                )
            )
            continue

        if resource.export_format == "svg":
            if (
                resource.mime_type != "image/svg+xml"
                or payload.declared_mime_type != "image/svg+xml"
                or resource.width is None
                or resource.height is None
                or resource.nine_slice is not None
            ):
                diagnostics.append(
                    _diagnostic(
                        "fgui.writer.asset.svg_metadata_mismatch",
                        resource_id,
                        "The SVG resource metadata is incomplete or inconsistent.",
                    )
                )
                continue
            try:
                validate_safe_svg(payload.content)
            except ValueError:
                diagnostics.append(
                    _diagnostic(
                        "fgui.writer.asset.invalid_svg",
                        resource_id,
                        "The SVG payload is not a safe self-contained image.",
                    )
                )
                continue
            validated[resource_id] = ValidatedAssetPayload(resource=resource, payload=payload)
            continue

        raster_details = _inspect_raster_in_isolated_process(payload.content)
        if raster_details is None:
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.invalid_image",
                    resource_id,
                    "The asset payload is not a complete supported raster image.",
                )
            )
            continue
        detected_format, width, height = raster_details
        format_details = _FORMAT_DETAILS.get(detected_format)
        if format_details is None:
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.format_mismatch",
                    resource_id,
                    "The detected image format is not supported by the Writer v1 input gate.",
                )
            )
            continue
        detected_export_format, detected_mime_type = format_details
        if resource.export_format != detected_export_format:
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.format_mismatch",
                    resource_id,
                    "The detected image format does not match the resource export format.",
                )
            )
            continue
        if (
            resource.mime_type != detected_mime_type
            or payload.declared_mime_type != detected_mime_type
        ):
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.mime_mismatch",
                    resource_id,
                    "The declared MIME type does not match the detected image format.",
                )
            )
            continue
        if resource.width != width or resource.height != height:
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.dimension_mismatch",
                    resource_id,
                    "The declared resource dimensions do not match the detected image dimensions.",
                )
            )
            continue
        if not _nine_slice_is_in_bounds(resource, width, height):
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.asset.nine_slice_out_of_bounds",
                    resource_id,
                    "The declared nine-slice bounds exceed the detected image dimensions.",
                )
            )
            continue
        validated[resource_id] = ValidatedAssetPayload(resource=resource, payload=payload)

    if diagnostics:
        raise NewProjectInputError(tuple(sorted(diagnostics, key=diagnostic_sort_key)))
    return tuple(validated[resource_id] for resource_id in sorted(validated))
