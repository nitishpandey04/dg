"""Read-model fuzz: dg's derived views must match an independent re-derivation.

Differential layer: every accepted intermediate graph gets scanned with
ops.effective_status / blocked_by / frontier and against the o_* oracles
from fuzz_support (which are written from the spec, not shared code).
Metamorphic layer encodes load-bearing semantic laws: cancel releases
successors; settling shrinks blocker sets; leaf/settled rollups behave per
README even for the quirky corners (mixed done+cancelled -> done,
all-cancelled containers -> done).
"""

from __future__ import annotations

import fuzz_support as fs
from hypothesis import given, settings
from hypothesis import strategies as st

from dg import ops
from dg.model import Graph

ROOTS = ("a", "b", "c")


@st.composite
def _structure_plan(draw):
    """Mostly-valid structure-building plans (roots, children, links)."""
    return draw(
        st.lists(
            st.tuples(
                st.sampled_from(("root", "child", "link")),
                st.integers(0, 7),
            ),
            min_size=2,
            max_size=16,
        )
    )


def _direct_kids(g: Graph, nid: str) -> list[str]:
    pre = nid + "."
    return [k for k in g.nodes if k.startswith(pre) and "." not in k[len(pre):]]


def _apply_structure_step(g: Graph, verb: str, i: int) -> Graph:
    import random

    rng = random.Random(i)
    live = sorted(g.nodes)
    if verb == "root":
        missing = [r for r in ROOTS if r not in g.nodes]
        name = rng.choice(missing) if missing else rng.choice(live)
        _, g = fs.try_op(ops.add_task, g, name, f"title {name}", [])
        return g
    parent = rng.choice(live)
    if verb == "child":
        n = 1
        while f"{parent}.{n}" in g.nodes:
            n += 1
        kids = _direct_kids(g, parent)
        use = [rng.choice(kids)] if kids else []
        _, g = fs.try_op(ops.add_task, g, f"{parent}.{n}", f"t{parent}.{n}", use)
    elif verb == "link":
        sibs = [
            s for s in live
            if s != parent and s.rsplit(".", 1)[0] == parent.rsplit(".", 1)[0]
        ]
        if sibs:
            _, g = fs.try_op(ops.link, g, rng.choice(sibs), parent)
    return g


@settings(deadline=None, max_examples=100)
@given(_structure_plan())
def test_read_model_matches_oracles_at_every_step(plan):
    import random

    rng = random.Random(plan[0][1])
    g = Graph.new("diff")
    for verb, i in plan:
        if not g.nodes and verb != "root":
            g = ops.add_task(g, ROOTS[0], "first", [])
            continue
        g = _apply_structure_step(g, verb, i * 13 + rng.randrange(8))
        _assert_differential(g)


@settings(deadline=None, max_examples=60)
@given(_structure_plan())
def test_cancel_releases_successors(plan):
    g = Graph.new("meta")
    for verb, i in plan:
        if not g.nodes and verb != "root":
            g = ops.add_task(g, ROOTS[0], "first", [])
            continue
        g = _apply_structure_step(g, verb, i * 29 + len(plan))

    # find a leaf that is pending but NOT ready (has unmet requirements)
    blocked = [
        n for n in sorted(g.nodes)
        if not _direct_kids(g, n)
        and g.nodes[n].status == "pending"
        and ops.blocked_by(g, n)
    ]
    if not blocked:
        return
    before = frozenset(fs.o_frontier(g))
    ok, g = fs.try_op(ops.cancel_task, g, blocked[0])
    if not ok:
        return
    after = frozenset(fs.o_frontier(g))
    # cancelling a non-frontier leaf may only release others, never remove
    assert before <= after, (sorted(before - after), plan)
    fs.assert_invariants(g)


@settings(deadline=None, max_examples=40)
@given(st.data())
def test_settling_shrinks_blocker_sets(data):
    g = Graph.new("settle")
    _, g = fs.try_op(ops.add_task, g, "a", "A", [])
    _, g = fs.try_op(ops.sub_task, g, "a", "kid", [])
    kid = "a.1"
    _, g = fs.try_op(ops.add_task, g, "b", "B", [])
    _, g = fs.try_op(ops.link, g, "a", "b")  # b depends on sibling-root a
    # container gate: a's un-settled child blocks sibling b
    assert "a" in ops.blocked_by(g, "b")
    assert "a.1" not in ops.blocked_by(g, "b")  # gates bind descendants, no edge needed

    action = data.draw(st.sampled_from(["done", "cancel"]))
    if action == "done":
        _, g = fs.try_op(ops.finish_task, g, kid, None)
    else:
        _, g = fs.try_op(ops.cancel_task, g, kid)
    assert "a" not in ops.blocked_by(g, "b"), action
    fs.assert_invariants(g)


@settings(deadline=None, max_examples=12)
@given(st.lists(st.sampled_from(["done", "cancelled"]), min_size=1, max_size=3))
def test_any_settled_leaf_combination_rolls_container_to_done(leaf_states):
    """README quirk under test: mixed done+cancelled, even all-cancelled,
    still roll a container up to 'done'."""
    g = Graph.new("rollup")
    _, g = fs.try_op(ops.add_task, g, "z", "container", [])
    parent = "z"
    for wanted in leaf_states:
        _, g = fs.try_op(ops.sub_task, g, parent, f"k{len(g.nodes)}", [])
        kid = max(
            [k for k in _direct_kids(g, parent)], key=lambda s: int(s.rsplit(".", 1)[1])
        )
        _, g = fs.try_op(ops.start_task, g, kid)
        if wanted == "done":
            _, g = fs.try_op(ops.finish_task, g, kid, None)
        else:
            _, g = fs.try_op(ops.cancel_task, g, kid)
    assert all(g.nodes[k].status in ("done", "cancelled")
               for k in _direct_kids(g, parent))
    assert ops.effective_status(g, parent) == "done"
    assert fs.o_status(g, parent) == "done"
    fs.assert_invariants(g)


def _assert_differential(g: Graph) -> None:
    for nid in g.nodes:
        eff = ops.effective_status(g, nid)
        ora = fs.o_status(g, nid)
        assert eff == ora, (nid, eff, ora)
        blk = ops.blocked_by(g, nid)
        oblk = fs.o_blocked(g, nid)
        assert blk == oblk, (nid, blk, oblk)
    assert ops.frontier(g) == fs.o_frontier(g)
    insp = {e["id"]: e for e in ops.inspect_graph(g)}
    for nid, entry in insp.items():
        assert entry["status"] == fs.o_status(g, nid)
        assert entry["blocked_by"] == fs.o_blocked(g, nid)


@settings(deadline=None, max_examples=15)
@given(st.permutations(["a", "a.2", "a.10", "b.1", "zz"]))
def test_numeric_before_alpha_id_ordering(order):
    g = Graph.new("sort")
    for nid in order:
        parts = nid.split(".")
        for k in range(1, len(parts)):
            anc = ".".join(parts[:k])
            if anc not in g.nodes:
                g = ops.add_task(g, anc, f"t-{anc}", [])
        if nid not in g.nodes:
            g = ops.add_task(g, nid, f"t-{nid}", [])
    assert ops.frontier(g) == ["a.2", "a.10", "b.1", "zz"]
