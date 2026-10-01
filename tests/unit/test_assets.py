from figma_to_fgui.assets import make_resource_id


def test_resource_id_is_stable_eight_characters_and_collision_safe() -> None:
    first = make_resource_id("1:2|Title", frozenset())
    second = make_resource_id("1:2|Title", frozenset())
    collided = make_resource_id("1:2|Title", frozenset({first}))
    assert first == second
    assert len(first) == 8
    assert first.isalnum() and first.lower() == first
    assert collided != first
