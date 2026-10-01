from test_hifi_nested import nested_case


def _node(node_id: str, name: str, node_type: str, bounds, properties):
    from figma_to_fgui.figma_selection import SelectionNode
    from figma_to_fgui.models import Bounds

    return SelectionNode(
        id=node_id,
        name=name,
        type=node_type,
        bounds=Bounds(x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3]),
        properties=properties,
    )


def _with_children(source, children):
    frame = source.top_level_nodes[0].model_copy(update={"children": tuple(children)})
    return source.model_copy(update={"top_level_nodes": (frame,)})


def test_zero_size_pixel_layer_is_structural_and_covered(tmp_path):
    from figma_to_fgui.hifi_mapping import build_mapping, require_psd_coverage

    _root, inventory, source, _ = nested_case(tmp_path)
    empty = _node("empty-pixel", "Empty Pixel", "IMAGE", (0, 0, 0, 0), {"psdKind": "pixel"})
    draft = build_mapping(inventory, _with_children(source, [empty]))
    item = next(i for i in draft.items if i.figma_node_id == "empty-pixel")
    assert item.status == "structural"
    assert item.action == "preserve_structure"
    assert item not in [i for i in draft.items if i.action is None]
    require_psd_coverage(draft, _with_children(source, [empty]))


def test_zero_size_masked_shape_is_structural(tmp_path):
    from figma_to_fgui.hifi_mapping import build_mapping, require_psd_coverage

    _root, inventory, source, _ = nested_case(tmp_path)
    empty_shape = _node(
        "empty-shape", "多边形 631", "VECTOR", (0, -19, 0, 0),
        {"psdKind": "shape", "hasPixelMask": False, "hasVectorMask": True,
         "hasEffects": False, "clipping": False, "blendMode": "normal"},
    )
    source = _with_children(source, [empty_shape])
    draft = build_mapping(inventory, source)
    item = next(i for i in draft.items if i.figma_node_id == "empty-shape")
    assert item.status == "structural"
    assert item.action == "preserve_structure"
    require_psd_coverage(draft, source)


def test_zero_size_masked_group_is_structural(tmp_path):
    from figma_to_fgui.hifi_mapping import build_mapping, require_psd_coverage

    _root, inventory, source, _ = nested_case(tmp_path)
    empty_group = _node(
        "empty-group", "组 37", "GROUP", (0, 0, 0, 0),
        {"psdKind": "group", "hasPixelMask": True, "hasVectorMask": False,
         "hasEffects": False, "clipping": False, "blendMode": "pass_through"},
    )
    source = _with_children(source, [empty_group])
    draft = build_mapping(inventory, source)
    item = next(i for i in draft.items if i.figma_node_id == "empty-group")
    assert item.status == "structural"
    assert item.action == "preserve_structure"
    require_psd_coverage(draft, source)


def test_positive_size_masked_layer_is_not_structural(tmp_path):
    from figma_to_fgui.hifi_mapping import build_mapping

    _root, inventory, source, _ = nested_case(tmp_path)
    masked = _node(
        "masked-leaf", "矩形 3050", "VECTOR", (53, 1608, 51, 6),
        {"psdKind": "shape", "hasPixelMask": True, "hasVectorMask": True,
         "hasEffects": False, "clipping": False, "blendMode": "normal"},
    )
    draft = build_mapping(inventory, _with_children(source, [masked]))
    item = next(i for i in draft.items if i.figma_node_id == "masked-leaf")
    assert item.status != "structural"
    assert item.action is None


def test_zero_size_text_layer_is_structural(tmp_path):
    from figma_to_fgui.hifi_mapping import build_mapping, require_psd_coverage

    _root, inventory, source, _ = nested_case(tmp_path)
    empty_text = _node("empty-text", "Empty Text", "TEXT", (0, 0, 0, 0), {"psdKind": "type"})
    source = _with_children(source, [empty_text])
    draft = build_mapping(inventory, source)
    item = next(i for i in draft.items if i.figma_node_id == "empty-text")
    assert item.status == "structural"
    assert item.action == "preserve_structure"
    require_psd_coverage(draft, source)
