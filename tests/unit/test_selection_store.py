from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode, SelectionResource
from figma_to_fgui.models import Bounds
from figma_to_fgui.selection_store import SelectionError, SelectionStore


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGBA", (2, 2), "red").save(output, "PNG")
    return output.getvalue()


def manifest() -> SelectionManifest:
    return SelectionManifest(
        display_name="Checkout",
        top_level_nodes=(
            SelectionNode(
                id="12:4",
                name="Checkout",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=32, height=16),
                resource_keys=("hero",),
            ),
        ),
        resources=(SelectionResource(key="hero", mime_type="image/png", size=len(png_bytes())),),
    )


def test_upload_state_progresses_to_immutable_selection(tmp_path: Path) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "same-selection")

    assert upload.state == "created"
    assert store.put_manifest(upload.upload_id, "device-a", manifest()).state == "manifest_received"
    assert store.put_resource(upload.upload_id, "device-a", "hero", "image/png", png_bytes()).state == "resources_pending"

    committed = store.commit(upload.upload_id, "device-a")
    assert committed.selection_id
    assert store.get(committed.selection_id, "device-a") == committed
    assert store.artifact_path(committed.selection_id).is_dir()
    assert not (tmp_path / "figma-uploads" / upload.upload_id).exists()


def test_commit_preserves_manifest_order_for_double_digit_resource_keys(tmp_path: Path) -> None:
    store = SelectionStore(tmp_path)
    content = png_bytes()
    resources = tuple(
        SelectionResource(key=f"asset-{index}", mime_type="image/png", size=len(content))
        for index in range(1, 13)
    )
    ordered_manifest = manifest().model_copy(
        update={
            "top_level_nodes": (
                manifest().top_level_nodes[0].model_copy(
                    update={"resource_keys": tuple(resource.key for resource in resources)}
                ),
            ),
            "resources": resources,
        }
    )
    upload = store.create_upload("device-a", "double-digit-resources")
    store.put_manifest(upload.upload_id, "device-a", ordered_manifest)
    for resource in resources:
        store.put_resource(upload.upload_id, "device-a", resource.key, resource.mime_type, content)

    committed = store.commit(upload.upload_id, "device-a")

    assert store.get(committed.selection_id, "device-a") == committed


def test_failed_artifact_verification_removes_newly_published_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "failed-verification")
    store.put_manifest(upload.upload_id, "device-a", manifest())
    store.put_resource(upload.upload_id, "device-a", "hero", "image/png", png_bytes())

    def fail_verification(_version: object) -> Path:
        raise SelectionError("selection_not_found")

    monkeypatch.setattr(store, "_verify_artifact", fail_verification)
    with pytest.raises(SelectionError, match="selection_not_found"):
        store.commit(upload.upload_id, "device-a")

    selections = tmp_path / "selections"
    assert not selections.exists() or list(selections.iterdir()) == []


def test_store_never_reads_committed_resources_as_whole_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "no-read-bytes")
    store.put_manifest(upload.upload_id, "device-a", manifest())
    store.put_resource(upload.upload_id, "device-a", "hero", "image/png", png_bytes())
    committed = store.commit(upload.upload_id, "device-a")

    original = Path.read_bytes
    def fail_resource_read(path: Path) -> bytes:
        if path.name == "hero":
            pytest.fail("resource was whole-buffered")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_resource_read)
    assert store.get(committed.selection_id, "device-a") == committed


def test_store_rejects_over_pixel_limit_raster_during_upload(tmp_path: Path) -> None:
    output = BytesIO()
    Image.new("RGB", (4097, 4097), "red").save(output, "PNG")
    content = output.getvalue()
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "large-raster")
    large_manifest = manifest().model_copy(update={"resources": (SelectionResource(key="hero", mime_type="image/png", size=len(content)),)})
    store.put_manifest(upload.upload_id, "device-a", large_manifest)
    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/png", content)


def test_store_rejects_missing_duplicate_undeclared_and_wrong_owner_resources(tmp_path: Path) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "resources")
    store.put_manifest(upload.upload_id, "device-a", manifest())

    with pytest.raises(SelectionError, match="selection_resource_unknown"):
        store.put_resource(upload.upload_id, "device-a", "unknown", "image/png", png_bytes())
    with pytest.raises(SelectionError, match="selection_owner_denied"):
        store.put_resource(upload.upload_id, "device-b", "hero", "image/png", png_bytes())
    with pytest.raises(SelectionError, match="selection_resources_missing"):
        store.commit(upload.upload_id, "device-a")

    store.put_resource(upload.upload_id, "device-a", "hero", "image/png", png_bytes())
    with pytest.raises(SelectionError, match="selection_resource_duplicate"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/png", png_bytes())


def test_store_rejects_invalid_svg_and_keeps_partial_upload_unusable(tmp_path: Path) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "bad-svg")
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    bad_manifest = manifest().model_copy(
        update={"resources": (SelectionResource(key="hero", mime_type="image/svg+xml", size=len(svg)),)}
    )
    store.put_manifest(upload.upload_id, "device-a", bad_manifest)

    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/svg+xml", svg)
    with pytest.raises(SelectionError, match="selection_not_found"):
        store.get("f" * 32, "device-a")


def test_store_enforces_actual_size_mime_and_expiry(tmp_path: Path) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "limits")
    store.put_manifest(upload.upload_id, "device-a", manifest())

    with pytest.raises(SelectionError, match="selection_resource_mime"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/webp", png_bytes())
    with pytest.raises(SelectionError, match="selection_resource_size"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/png", png_bytes() + b"extra")

    with store._connect() as connection:
        connection.execute("UPDATE selection_uploads SET expires_at = 0 WHERE upload_id = ?", (upload.upload_id,))
    assert store.expire_uploads() == 1
    with pytest.raises(SelectionError, match="selection_not_found"):
        store.put_manifest(upload.upload_id, "device-a", manifest())


def test_expiry_removes_resource_rows_and_retries_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SelectionStore(tmp_path)
    first = store.create_upload("device-a", "expire-first")
    second = store.create_upload("device-a", "expire-second")
    store.put_manifest(first.upload_id, "device-a", manifest())
    store.put_manifest(second.upload_id, "device-a", manifest())
    with store._connect() as connection:
        connection.execute("UPDATE selection_uploads SET expires_at = 0")
    original = store._cleanup
    attempts = 0

    def fail_once(path: Path) -> bool:
        nonlocal attempts
        attempts += 1
        return False if attempts == 1 else original(path)

    monkeypatch.setattr(store, "_cleanup", fail_once)
    assert store.expire_uploads() == 1
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM selection_upload_resources").fetchone()[0] == 1
    assert store.expire_uploads() == 1
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM selection_upload_resources").fetchone()[0] == 0


def test_store_rejects_svg_external_references(tmp_path: Path) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "external-svg")
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><image href='https://example.invalid/a.png'/></svg>"
    svg_manifest = manifest().model_copy(
        update={"resources": (SelectionResource(key="hero", mime_type="image/svg+xml", size=len(svg)),)}
    )
    store.put_manifest(upload.upload_id, "device-a", svg_manifest)

    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/svg+xml", svg)


def test_create_upload_converges_concurrent_idempotency_requests(tmp_path: Path) -> None:
    stores = (SelectionStore(tmp_path), SelectionStore(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as workers:
        uploads = list(workers.map(lambda store: store.create_upload("device-a", "race"), stores))

    assert uploads[0].upload_id == uploads[1].upload_id


def test_store_rejects_active_svg_styles_and_external_css(tmp_path: Path) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "style-svg")
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><style>@import url(https://bad.invalid/a.css)</style></svg>"
    svg_manifest = manifest().model_copy(
        update={"resources": (SelectionResource(key="hero", mime_type="image/svg+xml", size=len(svg)),)}
    )
    store.put_manifest(upload.upload_id, "device-a", svg_manifest)

    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/svg+xml", svg)


@pytest.mark.parametrize(
    "svg",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'><path style='fill:url(https://bad.invalid/a)'/></svg>",
        b"<svg xmlns='http://www.w3.org/2000/svg'><image href='other.svg'/></svg>",
    ],
)
def test_store_rejects_svg_external_style_and_href_attributes(tmp_path: Path, svg: bytes) -> None:
    store = SelectionStore(tmp_path)
    upload = store.create_upload("device-a", "svg-attrs" + str(len(svg)))
    store.put_manifest(upload.upload_id, "device-a", manifest().model_copy(update={"resources": (SelectionResource(key="hero", mime_type="image/svg+xml", size=len(svg)),)}))
    with pytest.raises(SelectionError, match="unsupported_selection_content"):
        store.put_resource(upload.upload_id, "device-a", "hero", "image/svg+xml", svg)


def test_same_idempotency_key_converges_and_publishes_no_duplicate_artifact(tmp_path: Path) -> None:
    first = SelectionStore(tmp_path)
    second = SelectionStore(tmp_path)
    upload = first.create_upload("device-a", "stable-key")
    assert second.create_upload("device-a", "stable-key").upload_id == upload.upload_id
    first.put_manifest(upload.upload_id, "device-a", manifest())
    first.put_resource(upload.upload_id, "device-a", "hero", "image/png", png_bytes())

    committed = first.commit(upload.upload_id, "device-a")
    assert second.commit(upload.upload_id, "device-a") == committed
    assert list((tmp_path / "selections").iterdir()) == [tmp_path / "selections" / committed.fingerprint]
