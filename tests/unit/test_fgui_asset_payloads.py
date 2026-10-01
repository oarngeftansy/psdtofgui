from __future__ import annotations

import hashlib
import struct
import subprocess
import threading
import zlib
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageFile

import figma_to_fgui.fgui_asset_payloads as asset_payloads
from figma_to_fgui.fgui_asset_payloads import (
    NewProjectInputError,
    validate_asset_payloads,
)
from figma_to_fgui.fgui_new_project_models import AssetPayload, AssetPayloadSet
from figma_to_fgui.fgui_plan_models import ResourcePlan

ONE_PIXEL_PNG = (
    Path(__file__).parents[1] / "fixtures" / "fgui-new-project" / "resources" / "one-pixel.png"
)


class FakeProbeProcess:
    def __init__(
        self,
        *,
        output: bytes = b"",
        returncode: int = 0,
        timeout_on_first_communicate: bool = False,
    ) -> None:
        self.output = output
        self.returncode = returncode
        self.timeout_on_first_communicate = timeout_on_first_communicate
        self.communicate_calls: list[tuple[bytes | None, float | None]] = []
        self.kill_calls = 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        self.communicate_calls.append((input, timeout))
        if self.timeout_on_first_communicate and len(self.communicate_calls) == 1:
            raise subprocess.TimeoutExpired("image-probe", timeout)
        return self.output, b""

    def kill(self) -> None:
        self.kill_calls += 1


def _resource(
    content: bytes,
    *,
    resource_id: str = "resource:one-pixel",
    content_sha256: str | None = None,
    mime_type: str = "image/png",
    export_format: str = "png",
    width: int | None = 1,
    height: int | None = 1,
    nine_slice: dict[str, int] | None = None,
) -> ResourcePlan:
    return ResourcePlan(
        id=resource_id,
        sourceAssetRef="asset:one-pixel",
        logicalAssetId="logical:one-pixel",
        contentSha256=content_sha256 or hashlib.sha256(content).hexdigest(),
        exportParametersSha256="a" * 64,
        mimeType=mime_type,
        exportFormat=export_format,
        width=width,
        height=height,
        nineSlice=nine_slice,
        consumers=("node:one-pixel",),
    )


def _payloads(content: bytes, *, declared_mime_type: str = "image/png") -> AssetPayloadSet:
    return AssetPayloadSet.from_items(
        (
            AssetPayload(
                resourceId="resource:one-pixel",
                declaredMimeType=declared_mime_type,
                content=content,
            ),
        )
    )


@pytest.fixture
def one_pixel_png() -> bytes:
    return ONE_PIXEL_PNG.read_bytes()


def resources_for(mutation: str, content: bytes) -> dict[str, ResourcePlan]:
    resource = _resource(
        content,
        content_sha256="0" * 64 if mutation == "hash" else None,
        width=2 if mutation == "size" else 1,
    )
    return {} if mutation == "extra" else {resource.id: resource}


def payloads_for(mutation: str, content: bytes) -> AssetPayloadSet:
    if mutation == "missing":
        return AssetPayloadSet.from_items(())
    return _payloads(
        content,
        declared_mime_type="image/webp" if mutation == "mime" else "image/png",
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "fgui.writer.asset.missing"),
        ("extra", "fgui.writer.asset.unexpected"),
        ("hash", "fgui.writer.asset.hash_mismatch"),
        ("mime", "fgui.writer.asset.mime_mismatch"),
        ("size", "fgui.writer.asset.dimension_mismatch"),
    ],
)
def test_payload_integrity_failures_are_public_and_deterministic(
    mutation: str, code: str, one_pixel_png: bytes
) -> None:
    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads(
            resources_for(mutation, one_pixel_png), payloads_for(mutation, one_pixel_png)
        )

    assert [item.code for item in captured.value.diagnostics] == [code]


def test_validates_a_real_image_and_returns_resources_in_key_order(one_pixel_png: bytes) -> None:
    first = _resource(one_pixel_png, resource_id="resource:z")
    second = _resource(one_pixel_png, resource_id="resource:a")
    payloads = AssetPayloadSet.from_items(
        (
            AssetPayload(resourceId=first.id, declaredMimeType="image/png", content=one_pixel_png),
            AssetPayload(resourceId=second.id, declaredMimeType="image/png", content=one_pixel_png),
        )
    )

    validated = validate_asset_payloads({first.id: first, second.id: second}, payloads)

    assert [item.resource.id for item in validated] == ["resource:a", "resource:z"]
    assert [item.content for item in validated] == [one_pixel_png, one_pixel_png]


def test_rejects_invalid_and_truncated_images_without_exposing_bytes() -> None:
    marker = b"private-asset-payload-marker"
    resource = _resource(marker)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(marker))

    error = captured.value
    assert [item.code for item in error.diagnostics] == ["fgui.writer.asset.invalid_image"]
    assert marker.decode() not in repr(error)
    assert marker.decode() not in str(error)
    assert marker.decode() not in repr(error.diagnostics)


def test_rejects_a_truncated_raster(one_pixel_png: bytes) -> None:
    truncated = one_pixel_png[:-20]
    resource = _resource(truncated)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(truncated))

    assert [item.code for item in captured.value.diagnostics] == ["fgui.writer.asset.invalid_image"]


def test_rejects_svg_even_when_declared_as_a_supported_raster_type() -> None:
    content = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
    resource = _resource(content, mime_type="image/png", export_format="png")

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(content))

    assert [item.code for item in captured.value.diagnostics] == ["fgui.writer.asset.invalid_image"]


def test_accepts_a_safe_self_contained_svg_resource() -> None:
    content = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><path fill="#00ff00" d="M0 0h1v1H0z"/></svg>'
    resource = _resource(content, mime_type="image/svg+xml", export_format="svg")

    validated = validate_asset_payloads(
        {resource.id: resource},
        _payloads(content, declared_mime_type="image/svg+xml"),
    )

    assert len(validated) == 1
    assert validated[0].content == content


@pytest.mark.parametrize(
    "content",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://invalid.example/a.png"/></svg>',
    ],
)
def test_rejects_active_or_external_svg_content(content: bytes) -> None:
    resource = _resource(content, mime_type="image/svg+xml", export_format="svg")

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads(
            {resource.id: resource},
            _payloads(content, declared_mime_type="image/svg+xml"),
        )

    assert [item.code for item in captured.value.diagnostics] == [
        "fgui.writer.asset.invalid_svg"
    ]


def test_rejects_decompression_bomb_payload(one_pixel_png: bytes) -> None:
    image_data = struct.pack("!IIBBBBB", 30_000, 30_000, 8, 4, 0, 0, 0)
    inflated = bytearray(one_pixel_png)
    inflated[16:29] = image_data
    inflated[29:33] = struct.pack("!I", zlib.crc32(b"IHDR" + image_data))
    content = bytes(inflated)
    resource = _resource(content, width=30_000, height=30_000)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(content))

    assert [item.code for item in captured.value.diagnostics] == ["fgui.writer.asset.invalid_image"]


def _assert_public_invalid_image(error: NewProjectInputError, marker: bytes) -> None:
    marker_text = marker.decode("ascii")
    assert [item.code for item in error.diagnostics] == ["fgui.writer.asset.invalid_image"]
    assert marker_text not in str(error)
    assert marker_text not in repr(error)
    assert marker_text not in repr(error.diagnostics)


def test_child_probe_timeout_kills_and_reaps_without_leaking_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = b"private-probe-timeout-marker"
    resource = _resource(marker)
    process = FakeProbeProcess(timeout_on_first_communicate=True)
    monkeypatch.setattr(asset_payloads.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(marker))

    _assert_public_invalid_image(captured.value, marker)
    assert process.kill_calls == 1
    assert process.communicate_calls == [
        (marker, asset_payloads.PILLOW_PROBE_TIMEOUT_SECONDS),
        (None, None),
    ]


def test_child_probe_nonzero_exit_is_a_public_invalid_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = b"private-probe-returncode-marker"
    resource = _resource(marker)
    process = FakeProbeProcess(returncode=1)
    monkeypatch.setattr(asset_payloads.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(marker))

    _assert_public_invalid_image(captured.value, marker)
    assert process.communicate_calls == [(marker, asset_payloads.PILLOW_PROBE_TIMEOUT_SECONDS)]


@pytest.mark.parametrize(
    "output",
    (
        b"not-json",
        b"[]",
        b'{"format": 5, "width": 1, "height": 1}',
        b'{"format": "PNG", "width": 0, "height": 1}',
        b'{"format": "PNG", "width": true, "height": 1}',
        b'{"format": "PNG", "width": 1, "height": false}',
    ),
)
def test_child_probe_invalid_public_response_is_a_public_invalid_image(
    monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    marker = b"private-probe-response-marker"
    resource = _resource(marker)
    process = FakeProbeProcess(output=output)
    monkeypatch.setattr(asset_payloads.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(marker))

    _assert_public_invalid_image(captured.value, marker)
    assert process.communicate_calls == [(marker, asset_payloads.PILLOW_PROBE_TIMEOUT_SECONDS)]


def test_child_probe_creation_error_is_a_public_invalid_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = b"private-probe-oserror-marker"
    resource = _resource(marker)

    def raise_os_error(*args: Any, **kwargs: Any) -> None:
        raise OSError(marker.decode("ascii"))

    monkeypatch.setattr(asset_payloads.subprocess, "Popen", raise_os_error)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(marker))

    _assert_public_invalid_image(captured.value, marker)


def test_probe_uses_isolated_absolute_script_and_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = b"private-probe-environment-marker"
    resource = _resource(marker)
    process = FakeProbeProcess(returncode=1)
    captured: dict[str, object] = {}
    monkeypatch.setenv("WRITER_PRIVATE_SECRET", "must-not-leak")
    monkeypatch.setenv("PYTHONPATH", "hostile-shadow-path")

    def capture(*args: object, **kwargs: object) -> FakeProbeProcess:
        captured["command"] = args[0]
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return process

    monkeypatch.setattr(asset_payloads.subprocess, "Popen", capture)

    with pytest.raises(NewProjectInputError):
        validate_asset_payloads({resource.id: resource}, _payloads(marker))

    command = captured["command"]
    assert isinstance(command, list)
    assert command[1] == "-I"
    assert Path(command[0]).is_absolute()
    assert Path(command[2]).is_absolute()
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert "WRITER_PRIVATE_SECRET" not in environment
    assert "PYTHONPATH" not in environment


def test_hostile_cwd_cannot_shadow_isolated_pillow_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, one_pixel_png: bytes
) -> None:
    hostile = tmp_path / "PIL"
    hostile.mkdir()
    executed = tmp_path / "executed"
    (hostile / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(executed)!r}).write_text('owned')\n",
        "utf-8",
    )
    monkeypatch.chdir(tmp_path)
    resource = _resource(one_pixel_png)

    validated = validate_asset_payloads({resource.id: resource}, _payloads(one_pixel_png))

    assert len(validated) == 1
    assert not executed.exists()


def test_in_memory_payload_caps_reject_before_probe(
    monkeypatch: pytest.MonkeyPatch, one_pixel_png: bytes
) -> None:
    resource = _resource(one_pixel_png)
    monkeypatch.setattr(asset_payloads, "MAX_ASSET_PAYLOAD_BYTES", len(one_pixel_png) - 1)
    called = False

    def reject_probe(_content: bytes) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(asset_payloads, "_inspect_raster_in_isolated_process", reject_probe)

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(one_pixel_png))

    assert [item.code for item in captured.value.diagnostics] == [
        "fgui.writer.asset.payload_too_large"
    ]
    assert not called


def test_in_memory_aggregate_cap_is_deterministic(
    monkeypatch: pytest.MonkeyPatch, one_pixel_png: bytes
) -> None:
    first = _resource(one_pixel_png, resource_id="resource:a")
    second = _resource(one_pixel_png, resource_id="resource:b")
    payloads = AssetPayloadSet.from_items(
        (
            AssetPayload(resourceId=first.id, declaredMimeType="image/png", content=one_pixel_png),
            AssetPayload(resourceId=second.id, declaredMimeType="image/png", content=one_pixel_png),
        )
    )
    monkeypatch.setattr(asset_payloads, "MAX_TOTAL_ASSET_PAYLOAD_BYTES", len(one_pixel_png))

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({first.id: first, second.id: second}, payloads)

    assert [item.code for item in captured.value.diagnostics] == [
        "fgui.writer.asset.total_payload_too_large",
        "fgui.writer.asset.total_payload_too_large",
    ]


def test_oversized_image_is_rejected_without_changing_callers_pixel_limit(
    one_pixel_png: bytes,
) -> None:
    image_data = struct.pack("!IIBBBBB", 30_000, 30_000, 8, 4, 0, 0, 0)
    inflated = bytearray(one_pixel_png)
    inflated[16:29] = image_data
    inflated[29:33] = struct.pack("!I", zlib.crc32(b"IHDR" + image_data))
    content = bytes(inflated)
    resource = _resource(content, width=30_000, height=30_000)
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None

        with pytest.raises(NewProjectInputError) as captured:
            validate_asset_payloads({resource.id: resource}, _payloads(content))

        assert [item.code for item in captured.value.diagnostics] == [
            "fgui.writer.asset.invalid_image"
        ]
        assert Image.MAX_IMAGE_PIXELS is None
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def test_truncated_image_is_rejected_without_changing_callers_truncated_setting(
    one_pixel_png: bytes,
) -> None:
    truncated = one_pixel_png[:-20]
    resource = _resource(truncated)
    previous_setting = ImageFile.LOAD_TRUNCATED_IMAGES
    try:
        ImageFile.LOAD_TRUNCATED_IMAGES = True

        with pytest.raises(NewProjectInputError) as captured:
            validate_asset_payloads({resource.id: resource}, _payloads(truncated))

        assert [item.code for item in captured.value.diagnostics] == [
            "fgui.writer.asset.invalid_image"
        ]
        assert ImageFile.LOAD_TRUNCATED_IMAGES is True
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_setting


def test_external_pillow_updates_are_not_overwritten_during_validation(
    monkeypatch: pytest.MonkeyPatch, one_pixel_png: bytes
) -> None:
    started = threading.Event()
    release = threading.Event()
    original_popen = asset_payloads.subprocess.Popen
    errors: list[NewProjectInputError] = []
    resource = _resource(one_pixel_png)
    previous_limit = Image.MAX_IMAGE_PIXELS
    previous_setting = ImageFile.LOAD_TRUNCATED_IMAGES

    def delayed_popen(*args: object, **kwargs: object) -> object:
        child = original_popen(*args, **kwargs)
        started.set()
        assert release.wait(timeout=2)
        return child

    def validate_in_thread() -> None:
        try:
            validate_asset_payloads({resource.id: resource}, _payloads(one_pixel_png))
        except NewProjectInputError as error:  # pragma: no cover - asserted by the parent thread
            errors.append(error)

    try:
        monkeypatch.setattr(asset_payloads.subprocess, "Popen", delayed_popen)
        worker = threading.Thread(target=validate_in_thread)
        worker.start()
        assert started.wait(timeout=2)
        Image.MAX_IMAGE_PIXELS = None
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        release.set()
        worker.join(timeout=5)

        assert not worker.is_alive()
        assert errors == []
        assert Image.MAX_IMAGE_PIXELS is None
        assert ImageFile.LOAD_TRUNCATED_IMAGES is True
    finally:
        release.set()
        Image.MAX_IMAGE_PIXELS = previous_limit
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_setting


def test_rejects_nine_slice_outside_detected_dimensions(one_pixel_png: bytes) -> None:
    resource = _resource(one_pixel_png, nine_slice={"x": 1, "y": 0, "width": 1, "height": 1})

    with pytest.raises(NewProjectInputError) as captured:
        validate_asset_payloads({resource.id: resource}, _payloads(one_pixel_png))

    assert [item.code for item in captured.value.diagnostics] == [
        "fgui.writer.asset.nine_slice_out_of_bounds"
    ]
