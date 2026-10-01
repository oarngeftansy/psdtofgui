import pytest
from PIL import Image


def test_state_capture_uses_runtime_and_rejects_editor_fallback(monkeypatch, tmp_path):
    import figma_to_fgui.hifi_controller_states as module

    calls = []

    def send(bridge, action, params, timeout):
        calls.append((action, params))
        if action == "switch_controller":
            return {"data": {"newIndex": 1, "target": "runtime"}}
        return {
            "data": {"path": str(tmp_path / "capture.png"), "capture_source": "testView_content"}
        }

    monkeypatch.setattr(module, "_send_command", send)
    assert module.capture_runtime_state(tmp_path, {"red": 1}, 0) == tmp_path / "capture.png"
    assert calls[0] == (
        "switch_controller",
        {"controller_name": "red", "page_index": 1, "target": "runtime"},
    )
    assert all(c[0] != "screenshot" for c in calls)
    monkeypatch.setattr(
        module, "_send_command", lambda *args: {"data": {"newIndex": 1, "target": "editor"}}
    )
    with pytest.raises(ValueError, match="runtime_controller"):
        module.capture_runtime_state(tmp_path, {"red": 1}, 0)


def test_controller_matrix_covers_combinations_and_does_not_truncate():
    from figma_to_fgui.hifi_controller_states import controller_matrix

    xml = b'<component><controller name="a" pages="0,on,1,off"/><controller name="b" pages="x,one,y,two,z,three"/></component>'
    assert len(controller_matrix(xml)) == 6
    assert {tuple(s.values()) for s in controller_matrix(xml)} == {
        (a, b) for a in range(2) for b in range(3)
    }
    with pytest.raises(ValueError, match="state_budget"):
        controller_matrix(xml, limit=5)


def test_visual_changes_do_not_hide_lost_state_behavior(tmp_path):
    from figma_to_fgui.hifi_controller_states import compare_state_responses

    paths = []
    for name, color, dot in [
        ("old0", "red", False),
        ("old1", "red", True),
        ("new0", "blue", False),
        ("new1", "blue", True),
    ]:
        canvas = Image.new("RGB", (20, 20), "white")
        canvas.paste(color, (0, 10, 20, 20))
        if dot:
            canvas.paste("black", (2, 2, 5, 5))
        path = tmp_path / (name + ".png")
        canvas.save(path)
        paths.append(path)
    assert compare_state_responses(paths[:2], paths[2:])
    Image.open(paths[2]).save(paths[3])
    assert not compare_state_responses(paths[:2], paths[2:])


def test_state_direction_is_preserved_not_just_difference_bbox(tmp_path):
    from figma_to_fgui.hifi_controller_states import compare_state_responses

    paths = []
    for n, c in enumerate(["white", "black", "black", "white"]):
        p = tmp_path / f"{n}.png"
        Image.new("RGB", (4, 4), c).save(p)
        paths.append(p)
    assert not compare_state_responses(paths[:2], paths[2:])
