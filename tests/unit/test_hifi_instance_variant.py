import shutil

import pytest
from lxml import etree
from PIL import Image
from test_hifi_nested import nested_case

from figma_to_fgui.hifi_instance_variant import create_instance_visual_variant


def test_variant_preserves_existing_instance_and_child_ids_in_another_package(tmp_path):
    root, inventory, _, _ = nested_case(tmp_path)
    child_path = root / "assets/MyVillage/Common/Button_Common.xml"
    child_path.write_text(child_path.read_text().replace("<graph id=", "<image id=").replace("</graph>", "</image>"))
    image = tmp_path / "replacement.png"
    Image.new("RGBA", (120, 44), (255, 240, 210, 255)).save(image)
    original = child_path.read_bytes()
    root_path = root / inventory.target.component_relative_path
    before = etree.parse(str(root_path))
    before_ids = before.xpath("./displayList/*/@id")

    result = create_instance_visual_variant(
        root,
        parent_component_path=inventory.target.component_relative_path,
        instance_id="b",
        visual_child_id="bg",
        replacement_png=image,
    )
    after = etree.parse(str(root_path))
    variant = etree.parse(str(root / result.variant_component))
    assert child_path.read_bytes() == original
    assert after.xpath("./displayList/*/@id") == before_ids
    assert after.xpath("string(./displayList/component[@id='a']/@src)") == "sharedbtn1"
    assert after.xpath("string(./displayList/component[@id='b']/@src)") == result.component_resource_id
    assert variant.xpath("./displayList/*/@id") == ["bg", "title"]
    assert variant.xpath("string(./displayList/image[@id='bg']/@src)") == result.image_resource_id
    assert [c.get("name") for c in variant.findall("controller")] == ["enabled"]
    assert (root / result.image_path).read_bytes() == image.read_bytes()
    with pytest.raises(ValueError, match="collision"):
        create_instance_visual_variant(
            root,
            parent_component_path=inventory.target.component_relative_path,
            instance_id="b",
            visual_child_id="bg",
            replacement_png=image,
        )


def test_variant_rejects_non_image_child_without_mutating_project(tmp_path):
    root, inventory, _, _ = nested_case(tmp_path)
    image = tmp_path / "replacement.png"
    Image.new("RGBA", (120, 44), (255, 240, 210, 255)).save(image)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    with pytest.raises(ValueError, match="image_missing"):
        create_instance_visual_variant(
            root,
            parent_component_path=inventory.target.component_relative_path,
            instance_id="b",
            visual_child_id="bg",
            replacement_png=image,
        )
    assert (root / inventory.target.component_relative_path).read_bytes() == (
        candidate / inventory.target.component_relative_path
    ).read_bytes()


def test_variant_rejects_corrupt_png_before_writing(tmp_path):
    root, inventory, _, _ = nested_case(tmp_path)
    child_path = root / "assets/MyVillage/Common/Button_Common.xml"
    child_path.write_text(child_path.read_text().replace("<graph id=", "<image id=").replace("</graph>", "</image>"))
    parent_path = root / inventory.target.component_relative_path
    before = parent_path.read_bytes()
    image = tmp_path / "corrupt.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"broken")

    with pytest.raises(ValueError, match="image_invalid"):
        create_instance_visual_variant(
            root,
            parent_component_path=inventory.target.component_relative_path,
            instance_id="b",
            visual_child_id="bg",
            replacement_png=image,
        )
    assert parent_path.read_bytes() == before
