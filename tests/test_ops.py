import pytest

from dg import ops
from dg.errors import DgError
from dg.model import Graph, Node, id_sort_key


def fresh(*nodes) -> Graph:
    """Nodes are ('id',) or ('id', ['dep', ...]); titles default to the id."""
    g = Graph.new("test")
    for nid, *rest in nodes:
        deps = rest[0] if rest else []
        g.nodes[nid] = Node(nid, deps=list(deps))
    return g


# ------------------------------------------------------------------ planning


def test_add_child_requires_existing_parent():
    g = Graph.new("t")
    with pytest.raises(DgError, match="container 'x' does not exist"):
        ops.add_task(g, "x.1", "child", [])


def test_sub_task_auto_numbers_children():
    g = fresh(("root",))
    g, first = ops.sub_task(g, "root", "one", [])
    g, second = ops.sub_task(g, "root", "two", [])
    assert (first, second) == ("root.1", "root.2")


def test_link_duplicate_rejected():
    g = fresh(("a",), ("b",))
    g = ops.link(g, "a", "b")
    with pytest.raises(DgError, match="already depends"):
        ops.link(g, "a", "b")


# ------------------------------------------------------------------ execution


def test_start_requires_settled_deps():
    g = fresh(("a",), ("b", ["a"]))
    with pytest.raises(DgError, match="blocked by: a"):
        ops.start_task(g, "b")
    g = ops.finish_task(g, "a")
    g = ops.start_task(g, "b")
    assert g.nodes["b"].status == "in_progress"


def test_container_ops_rejected():
    g = fresh(("root",))
    g, _ = ops.sub_task(g, "root", "kid", [])
    with pytest.raises(DgError, match="container"):
        ops.start_task(g, "root")
    with pytest.raises(DgError, match="container"):
        ops.finish_task(g, "root")


def test_done_note_merges():
    g = fresh(("a",))
    g = ops.finish_task(g, "a", note="first")
    g = ops.edit_task(g, "a", note=None)
    # edit with note=None keeps note untouched
    assert g.nodes["a"].note == "first"


# ------------------------------------------------------------------ read model


def test_frontier_diamond():
    # a -> (b, c) -> d : two parallel ready tasks, then the join point
    g = fresh(("a",), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"]))
    assert ops.frontier(g) == ["a"]
    g = ops.finish_task(g, "a")
    assert ops.frontier(g) == ["b", "c"]
    g = ops.finish_task(g, "b")
    g = ops.finish_task(g, "c")
    assert ops.frontier(g) == ["d"]
    g = ops.finish_task(g, "d")
    assert ops.frontier(g) == []


def test_effective_status_rollup():
    g = fresh(("root",))
    g, k1 = ops.sub_task(g, "root", "one", [])
    g, k2 = ops.sub_task(g, "root", "two", [])
    assert ops.effective_status(g, "root") == "pending"
    g = ops.start_task(g, k1)
    assert ops.effective_status(g, "root") == "in_progress"
    g = ops.finish_task(g, k1)
    assert ops.effective_status(g, "root") == "in_progress"
    g = ops.finish_task(g, k2)
    assert ops.effective_status(g, "root") == "done"


def test_gate_inheritance_across_layers():
    """External deps of a container gate its children, even without explicit edges.
    The predecessor itself stays ready until finished."""
    g = fresh(("pre",), ("box",))
    g, kid_id = ops.sub_task(g, "box", "inside", [])   # kid has NO declared deps
    assert ops.frontier(g) == sorted([kid_id, "pre"], key=id_sort_key)
    g = ops.link(g, "pre", "box")                      # pre -> box sequences the subtree
    assert ops.frontier(g) == ["pre"]                  # kid now gated; pre untouched
    g = ops.finish_task(g, "pre")
    assert ops.frontier(g) == [kid_id]


def test_blocked_by_reports_inherited_gates():
    g = fresh(("pre",), ("box", ["pre"]))
    g, kid_id = ops.sub_task(g, "box", "kid", [])
    assert ops.blocked_by(g, kid_id) == ["pre"]


def test_cancel_is_terminal_for_frontier():
    g = fresh(("a",), ("b", ["a"]))
    g = ops.cancel_task(g, "a")
    assert ops.frontier(g) == ["b"]  # cancelled predecessors release successors
    with pytest.raises(DgError):
        ops.finish_task(g, "a")


# ------------------------------------------------------------------ removal


def test_remove_rules():
    g = fresh(("a",), ("b", ["a"]))
    with pytest.raises(DgError, match="depended on by"):
        ops.remove_task(g, "a")          # referenced -> unlink explicitly first
    g = ops.remove_task(g, "b")          # unreferenced leaf goes cleanly
    assert "b" not in g.nodes and "a" in g.nodes


def test_remove_container_refused():
    g = fresh(("box",))
    g, kid = ops.sub_task(g, "box", "k", [])
    with pytest.raises(DgError, match="container"):
        ops.remove_task(g, "box")
    assert ops.remove_task(g, kid).nodes.keys() == {"box"}


# ------------------------------------------------------------------ inspection


def test_inspect_view():
    g = fresh(("a",), ("b", ["a"]), ("c",))
    g, kid = ops.sub_task(g, "c", "leaf", [])
    view = {n["id"]: n for n in ops.inspect_graph(g)}
    assert view["b"]["blocked_by"] == ["a"]
    assert view["c"]["leaf"] is False and view[kid]["leaf"] is True
    assert view[kid]["blocked_by"] == []  # c has no external gate


def test_render_and_show_smoke():
    g = fresh(("a",), ("b", ["a"]))
    g, kid_id = ops.sub_task(g, "b", "kid", [])
    text = ops.mermaid(g)
    assert text.startswith("flowchart TD")
    assert "a --> sg_b" in text              # gate edge lands on the container box
    assert "subgraph sg_b[" in text
    assert 'b__1["' in text                  # inner node still defined inside
    tree = ops.ascii_tree(g)
    assert kid_id in tree and "(needs: a)" in tree


def test_mermaid_containment_and_flat_scope():
    g = fresh(("pre",), ("box",))
    g, one_id = ops.sub_task(g, "box", "one", [])
    ops.sub_task(g, "box", "two", [one_id])
    text = ops.mermaid(g)

    assert text.count("subgraph ") == 1                 # one box, no nesting here
    assert "pre --> sg_box" in text                     # external gate targets the group
    assert "box__1 --> box__2" in text                  # sibling chain inside the box
    # leaves defined exactly once each, nothing else gets a stray node def
    for frag in ('box__1["', 'box__2["', 'pre["'):
        assert text.count(frag) == 1

    flat = ops.mermaid(g, "box.2")                      # scoping a leaf: no boxes
    assert "subgraph" not in flat
    assert flat.count('box__2["') == 1
