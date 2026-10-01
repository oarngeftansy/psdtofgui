from PIL import Image
from test_hifi_mapping import _inputs

from figma_to_fgui.hifi_visual_similarity import exact_visual_similarity, static_image_path


def test_pixel_proof_disambiguates_backdrop_without_using_names(tmp_path):
    old = tmp_path / "old.jpg"
    same = tmp_path / "same.png"
    white = tmp_path / "white.png"
    image = Image.new("RGB", (200, 300), "#755037")
    for x in range(40, 160):
        for y in range(60, 240):
            image.putpixel((x, y), (173, 130, 81))
    image.save(old, quality=98)
    image.save(same)
    Image.new("RGB", (200, 300), "white").save(white)
    assert exact_visual_similarity(old, same) >= .98
    assert exact_visual_similarity(old, white) == 0
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (200, 300), (255, 255, 255, 0)).save(transparent)
    assert exact_visual_similarity(transparent, white) == 0


def test_old_asset_resolution_uses_declared_image_and_stays_in_project(tmp_path):
    inventory, _ = _inputs()
    old = next(o for o in inventory.objects if o.object_type == "image")
    root = tmp_path / "project"
    package = root / "assets" / "MyVillage"
    component = package / "Panel" / "Home.xml"
    component.parent.mkdir(parents=True)
    (package / "package.xml").write_text('<package id="village"/>')
    (package / "Img").mkdir()
    (package / "Img" / "scene.png").write_bytes(b"png")
    component.write_text('<component><displayList><image id="background" src="image1" '
                         'fileName="Img/scene.png"/></displayList></component>')
    ref = old.model_copy(update={"local_object_id": "background", "resource_id": "image1",
                                  "component_relative_path": "assets/MyVillage/Panel/Home.xml"})
    assert static_image_path(root, ref) == package / "Img" / "scene.png"
    component.write_text('<component><displayList><image id="background" src="image1" '
                         'fileName="../../escape.png"/></displayList></component>')
    assert static_image_path(root, ref) is None
