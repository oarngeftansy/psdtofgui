from lxml import etree
from PIL import Image
from test_hifi_nested import nested_case

from figma_to_fgui.hifi_nested import inspect_component_tree
from figma_to_fgui.hifi_state_visuals import _palette, _state_roles, derive_state_nodes


def test_state_roles_require_a_complete_controller_scoped_visual_definition():
    document = etree.fromstring(
        '<component><controller name="badge" pages="0,Normal,1,Num,2,New"/>'
        '<displayList><image id="a"><gearDisplay controller="badge" pages="0"/></image>'
        '<text id="b"><gearDisplay controller="badge" pages="1,2"/></text>'
        '</displayList></component>'
    )
    assert _state_roles(document) == {"a": "normal", "b": "text"}
    document.xpath("./displayList/image")[0].find("gearDisplay").set("controller", "other")
    assert _state_roles(document) is None


def test_derived_state_art_is_new_and_only_for_eligible_components(tmp_path):
    root, inventory, _, _ = nested_case(tmp_path)
    child = root / "assets/MyVillage/Common/Button_Common.xml"
    child.write_text(
        '<component size="120,44"><controller name="badge" pages="0,Normal,1,Num,2,New"/>'
        '<displayList><image id="bg" name="bg" xy="0,0" size="40,40" src="old">'
        '<gearDisplay controller="badge" pages="0"/></image>'
        '<text id="title" name="title" xy="40,10" size="70,24" text="99">'
        '<gearDisplay controller="badge" pages="1,2"/></text></displayList></component>',
        encoding="utf-8",
    )
    inventory = inspect_component_tree(root, inventory.target)
    style = tmp_path / "style.png"
    image = Image.new("RGBA", (60, 20), (249, 202, 129, 255))
    for x in range(20):
        for y in range(20):
            image.putpixel((x, y), (253, 243, 233, 255))
    image.save(style)
    nodes, resources = derive_state_nodes(root, inventory, style, tmp_path / "generated")
    assert len(nodes) == 4  # two instances, each with an image and dynamic text
    assert len(resources) == 1  # shared definition has one new image asset
    assert {node.properties["generatedStateRole"] for node in nodes} == {"normal", "text"}
    assert all(node.properties["generatedStateOwner"] for node in nodes)
    assert (tmp_path / "generated" / resources[0].key).read_bytes().startswith(b"\x89PNG")
    assert _palette(style)[0] != _palette(style)[1]
