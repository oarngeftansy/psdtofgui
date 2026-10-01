from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from psd_tools import PSDImage

from figma_to_fgui.api import create_app
from figma_to_fgui.psd_intake import PsdIntakeError, analyze_psd, inspect_psd


def test_inspect_psd_reports_source_identity_and_document_shape(tmp_path: Path) -> None:
    source = tmp_path / "screen.psd"
    PSDImage.new(mode="RGB", size=(1080, 2340), depth=16).save(source)

    report = inspect_psd(source, source_name=source.name)

    assert report.source_name == "screen.psd"
    assert report.byte_size == source.stat().st_size
    assert len(report.sha256) == 64
    assert report.width == 1080
    assert report.height == 2340
    assert report.depth == 16
    assert report.color_mode == "RGB"
    assert report.layer_count == 0
    assert report.kind_counts == {}
    assert report.blocking_issues == ()


def test_inspect_psd_rejects_non_psd_without_reading_it_as_an_image(tmp_path: Path) -> None:
    source = tmp_path / "screen.psd"
    source.write_bytes(b"not a psd")

    with pytest.raises(PsdIntakeError, match="invalid_psd"):
        inspect_psd(source, source_name=source.name)


def test_hidden_layers_do_not_block_visible_output_equivalence(tmp_path: Path) -> None:
    source = tmp_path / "hidden.psd"
    document = PSDImage.new(mode="RGB", size=(100, 100), depth=8)
    hidden = document.create_pixel_layer(
        Image.new("RGBA", (40, 40), (255, 0, 0, 255)),
        name="hidden-reference",
    )
    hidden.visible = False
    document.save(source)

    report = inspect_psd(source, source_name=source.name)

    assert report.kind_counts == {"pixel": 1}
    assert report.blocking_issues == ()


def test_psd_inspection_api_streams_and_reports_the_uploaded_source(tmp_path: Path) -> None:
    source = tmp_path / "screen.psd"
    PSDImage.new(mode="RGB", size=(1080, 2340), depth=16).save(source)
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-token",
        )
    )

    with source.open("rb") as content:
        response = client.post(
            "/v1/hifi-sources/psd/inspect",
            files={"psd": (source.name, content, "image/vnd.adobe.photoshop")},
            headers={"x-figma-plugin-token": "test-token"},
        )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["source_name"] == "screen.psd"
    assert report["width"] == 1080
    assert report["height"] == 2340
    assert report["depth"] == 16
    assert report["warnings"] == ["16_bit_pixels_must_not_be_downconverted"]
    assert not list((tmp_path / "data" / "psd-intake").glob("*.psd"))


def test_psd_inspection_api_requires_plugin_access(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-token",
        )
    )

    response = client.post("/v1/hifi-sources/psd/inspect", files={"psd": ("x.psd", b"8BPS", "image/vnd.adobe.photoshop")})

    assert response.status_code == 401


def test_psd_analysis_emits_stable_hifi_ir_layer_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "screen.psd"
    source.write_bytes(b"8BPSpayload")

    class Mode:
        name = "RGB"

    class Blend:
        name = "NORMAL"

    class Layer:
        def __init__(
            self,
            layer_id: int,
            name: str,
            kind: str,
            parent: object,
            bounds: tuple[int, int, int, int],
            *,
            text: str | None = None,
        ) -> None:
            self.layer_id = layer_id
            self.name = name
            self.kind = kind
            self.parent = parent
            self.left, self.top, self.right, self.bottom = bounds
            self.visible = True
            self.opacity = 255
            self.blend_mode = Blend()
            self.clipping = False
            self.text = text

        def has_effects(self) -> bool:
            return bool(getattr(self, "effects", ()))

        def has_mask(self) -> bool:
            return False

        def has_vector_mask(self) -> bool:
            return False

        def is_visible(self) -> bool:
            return True

    class Document:
        width = 1080
        height = 2340
        depth = 8
        color_mode = Mode()

        def __init__(self) -> None:
            group = Layer(10, "Header", "group", self, (0, 0, 1080, 300))
            title = Layer(11, "Title", "type", group, (40, 50, 440, 110), text="开始游戏")
            title.font_names = ("HYZhengYuan-GES",)
            title.transform = (1.0, 0.0, 0.0, 1.0, 40.0, 50.0)
            title.engine_dict = {
                "StyleRun": {
                    "RunArray": [{
                        "StyleSheet": {"StyleSheetData": {
                            "Font": 0,
                            "FontSize": 36.0,
                            "FauxBold": False,
                            "FauxItalic": False,
                            "Leading": 42.0,
                            "Tracking": 0,
                            "FillColor": {"Values": [1.0, 1.0, 0.8, 0.2]},
                        }}
                    }],
                    "RunLengthArray": [4],
                },
                "ParagraphRun": {
                    "RunArray": [{"ParagraphSheet": {"Properties": {"Justification": 1}}}]
                },
                "AntiAlias": 4,
            }
            class ColorOverlay:
                def __init__(self) -> None:
                    self.enabled = True
                    self.blend_mode = b"Nrml"
                    self.opacity = 100.0
                    self.color = {b"Rd  ": 255.0, b"Grn ": 204.0, b"Bl  ": 51.0}

            title.effects = (ColorOverlay(),)
            self._layers = [group, title]

        def descendants(self) -> list[Layer]:
            return self._layers

    monkeypatch.setattr("figma_to_fgui.psd_intake.PSDImage.open", lambda _path: Document())

    first = analyze_psd(source, source_name=source.name)
    second = analyze_psd(source, source_name=source.name)

    assert first == second
    assert [layer.native_id for layer in first.layers] == [10, 11]
    assert first.layers[0].parent_id is None
    assert first.layers[1].parent_id == first.layers[0].id
    assert first.layers[1].path == ("Header", "Title")
    assert first.layers[1].text == "开始游戏"
    assert first.layers[1].text_style is not None
    assert first.layers[1].text_style.runs[0].font_name == "HYZhengYuan-GES"
    assert first.layers[1].text_style.runs[0].font_size == 36
    assert first.layers[1].text_style.runs[0].fill_rgba == (1.0, 0.8, 0.2, 1.0)
    assert first.layers[1].text_style.paragraph_justification == 1
    assert first.layers[1].effects[0].kind == "ColorOverlay"
    assert first.layers[1].effects[0].enabled is True
    assert first.layers[1].effects[0].blend_mode == "normal"
    assert first.layers[1].effects[0].color_rgba == (1.0, 0.8, 0.2, 1.0)
    assert first.layers[1].bounds == (40, 50, 440, 110)
    assert first.layers[1].id.startswith(f"psd-layer:{first.inspection.sha256}:11")


def test_psd_source_upload_persists_source_and_hifi_ir_once(tmp_path: Path) -> None:
    source = tmp_path / "screen.psd"
    PSDImage.new(mode="RGB", size=(1080, 2340), depth=16).save(source)
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-token",
        )
    )
    headers = {"x-figma-plugin-token": "test-token"}

    with source.open("rb") as content:
        created = client.post(
            "/v1/hifi-sources/psd",
            files={"psd": (source.name, content, "image/vnd.adobe.photoshop")},
            headers=headers,
        )
    with source.open("rb") as content:
        duplicate = client.post(
            "/v1/hifi-sources/psd",
            files={"psd": (source.name, content, "image/vnd.adobe.photoshop")},
            headers=headers,
        )

    assert created.status_code == 201, created.text
    assert duplicate.status_code == 201, duplicate.text
    payload = created.json()
    assert payload == duplicate.json()
    assert payload["version"] == 1
    assert payload["source_id"] == payload["inspection"]["sha256"]
    assert payload["layers"] == []
    source_root = tmp_path / "data" / "hifi-sources" / "psd" / payload["source_id"]
    assert (source_root / "source.psd").read_bytes() == source.read_bytes()
    assert (source_root / "hifi-ir.json").is_file()
    assert len(list((tmp_path / "data" / "hifi-sources" / "psd").glob("*/source.psd"))) == 1

    fetched = client.get(f"/v1/hifi-sources/psd/{payload['source_id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == payload

    composite = client.get(
        f"/v1/hifi-sources/psd/{payload['source_id']}/composite", headers=headers
    )
    assert composite.status_code == 200
    assert composite.headers["content-type"] == "image/png"
    assert composite.headers["x-psd-source-sha256"] == payload["source_id"]
    with Image.open(BytesIO(composite.content)) as image:
        assert image.size == (1080, 2340)

    (source_root / "source.psd").write_bytes(b"8BPStampered")
    corrupted = client.get(f"/v1/hifi-sources/psd/{payload['source_id']}", headers=headers)
    assert corrupted.status_code == 409
    assert corrupted.json()["detail"]["code"] == "psd_source_corrupt"
