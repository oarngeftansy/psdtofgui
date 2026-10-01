from collections.abc import Iterable, Iterator

from figma_to_fgui.models import NormalizedNode


def walk_nodes(nodes: Iterable[NormalizedNode]) -> Iterator[NormalizedNode]:
    """Walk normalized nodes in deterministic pre-order without recursion."""
    stack = list(reversed(tuple(nodes)))
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))
