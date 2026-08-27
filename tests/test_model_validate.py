
from dg.model import Graph, Node
from dg.validate import structural_errors


def make(nodes: dict) -> Graph:
    return Graph(version=1, title="t", nodes=nodes)


def test_cycle_detected_with_path():
    g = make({
        "a": Node("A", deps=["c"]),
        "b": Node("B", deps=["a"]),
        "c": Node("C", deps=["b"]),
    })
    errs = structural_errors(g)
    assert any(e.startswith("cycle detected:") for e in errs)
    cyc = next(e for e in errs if e.startswith("cycle"))
    assert "->" in cyc


def test_missing_parent_rejected():
    g = make({"1.2": Node("orphan")})
    assert any("parent '1' does not exist" in e for e in structural_errors(g))


def test_cross_layer_dependency_rejected():
    g = make({"a": Node("A"), "b.x": Node("BX")})
    g.nodes["a"].deps.append("b.x")
    assert any("cross-layer" in e for e in structural_errors(g))


def test_self_and_unknown_deps():
    g = make({"a": Node("A", deps=["a", "ghost"])})
    errs = structural_errors(g)
    assert any("depends on itself" in e for e in errs)
    assert any("'ghost' does not exist" in e for e in errs)


def test_bad_status_rejected():
    g = make({"a": Node("A", status="doing")})
    assert any("invalid status" in e for e in structural_errors(g))


def test_valid_graph_passes():
    g = make({
        "setup": Node("S"),
        "auth": Node("A", deps=["setup"]),
        "auth.1": Node("A1"),
        "api": Node("P", deps=["auth"]),
    })
    assert structural_errors(g) == []
