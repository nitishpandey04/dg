"""Stateful fuzz: random op sequences must satisfy every dg invariant.

The machine drives the real ops API against one evolving graph. Feedback
rules pick targets from live graph state so happy paths stay dense;
wildcard draws inject deliberately broken references so rejection paths
(gate errors) are covered too. After every step — accepted or rejected —
the full invariant sweep must hold.
"""

from __future__ import annotations

import fuzz_support as fs
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dg import ops
from dg.model import Graph, id_sort_key, parent_of, sibling_ids

ROOTS = ("a", "b", "c")


def _idx(choices: int):
    return st.integers(0, choices)


class OpSequenceMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.g = Graph.new("fuzz")

    # ------------------------------------------------------------- helpers

    def pick(self, pred, i: int):
        cands = [n for n in sorted(self.g.nodes, key=id_sort_key) if pred(n)]
        return cands[i % len(cands)] if cands else None

    @staticmethod
    def _leaf(g: Graph, nid: str) -> bool:
        return not any(k.startswith(nid + ".") for k in g.nodes)

    def leaves(self, pred=lambda g, n: True):
        return [
            n for n in sorted(self.g.nodes, key=id_sort_key)
            if self._leaf(self.g, n) and pred(self.g, n)
        ]

    def siblings_of(self, nid: str) -> list[str]:
        return sorted(sibling_ids(self.g, nid), key=id_sort_key)

    def edges(self) -> list[tuple[str, str]]:
        out = []
        for nid, node in self.g.nodes.items():
            for dep in node.deps:
                out.append((nid, dep))
        return sorted(out)

    def child_slot(self, parent: str, i: int) -> str:
        n = 1 + i % 3
        while f"{parent}.{n}" in self.g.nodes:
            n += 1
        return f"{parent}.{n}"

    @rule(i=_idx(8), j=_idx(4), wild=st.booleans())
    def decompose(self, i: int, j: int, wild: bool):
        parent = self.pick(lambda _: True, i)
        if parent is None:
            return
        par_prefix = "" if parent_of(parent) is None else parent_of(parent) + "."
        kids = [
            k for k in self.g.nodes
            if k.startswith(parent + ".") and "." not in k[len(parent) + 1:]
        ]
        sibs_of_parent = sorted(sibling_ids(self.g, parent), key=id_sort_key)
        if wild:
            use = [par_prefix + "ghost", *sibs_of_parent] if j % 2 else ["nope"]
        elif kids:
            use = [kids[j % len(kids)]]
        else:
            use = []
        _, self.g = fs.try_op(
            ops.add_task, self.g, self.child_slot(parent, i),
            f"t-{parent}.{i}", list(dict.fromkeys(use)),
        )

    # ------------------------------------------------- planning mutations

    @rule(i=_idx(6))
    def add_root(self, i: int):
        rid = ROOTS[i % len(ROOTS)]
        _, self.g = fs.try_op(ops.add_task, self.g, rid, f"title {rid}", [])

    @rule(i=_idx(6))
    def add_bad_root(self, i: int):
        rid = ["Root", "", "-x", "..", "a.b.c.d"][i % 5]
        fs.try_op(ops.add_task, self.g, rid, "never", [])
        # bad ids never enter the graph; nothing else to assert here

    @rule(i=_idx(8), j=_idx(8), wild=st.booleans())
    def link(self, i: int, j: int, wild: bool):
        src = self.pick(lambda _: True, i)
        if src is None:
            return
        sibs = self.siblings_of(src)
        if wild or not sibs:
            pool = list(self.g.nodes)
            dst = pool[j % len(pool)] if pool else src
        else:
            dst = sibs[j % len(sibs)]
        _, self.g = fs.try_op(ops.link, self.g, src, dst)

    @rule(i=_idx(8), j=_idx(8))
    def unlink(self, i: int, j: int):
        es = self.edges()
        if es:
            dst, dep = es[i % len(es)]
            self.g = fs.try_op(ops.unlink, self.g, dep, dst)[1]
        elif self.g.nodes:
            a = self.pick(lambda _: True, i)
            b = self.pick(lambda _: True, j)
            fs.try_op(ops.unlink, self.g, a, b)

    @rule(
        i=_idx(8),
        title=st.one_of(st.none(), fs.titles()),
        note=st.one_of(st.none(), fs.notes()),
        clear=st.booleans(),
    )
    def edit(self, i: int, title, note, clear: bool):
        nid = self.pick(lambda _: True, i)
        if nid is not None:
            _, self.g = fs.try_op(ops.edit_task, self.g, nid, title, note, clear)

    # ----------------------------------------------- execution transitions

    @rule(i=_idx(8), wild=st.booleans())
    def start(self, i: int, wild: bool):
        ready = self.leaves(lambda g, n: g.nodes[n].status == "pending"
                            and not ops.blocked_by(g, n))
        nid = (
            self.pick(lambda _: True, i)
            if wild
            else (ready[i % len(ready)] if ready else None)
        )
        if nid is not None:
            _, self.g = fs.try_op(ops.start_task, self.g, nid)

    @rule(i=_idx(8), note=st.none() | fs.notes())
    def finish(self, i: int, note):
        nid = self.pick(lambda n: True, i)
        if nid is not None:
            _, self.g = fs.try_op(ops.finish_task, self.g, nid, note)

    @rule(i=_idx(8))
    def cancel(self, i: int):
        nid = self.pick(lambda n: True, i)
        if nid is not None:
            _, self.g = fs.try_op(ops.cancel_task, self.g, nid)

    @rule(i=_idx(8), wild=st.booleans())
    def remove_leaf(self, i: int, wild: bool):
        if wild:
            nid = self.pick(lambda n: self._leaf(self.g, n), i)
        else:
            dep_targets = {d for v in self.g.nodes.values() for d in v.deps}
            nid = self.pick(
                lambda n: self._leaf(self.g, n) and n not in dep_targets, i,
            )
        if nid is not None:
            ok, self.g = fs.try_op(ops.remove_task, self.g, nid)
            assert ok or wild

    # ------------------------------------------------------------- oracles

    @invariant()
    def all_invariants_hold(self):
        fs.assert_invariants(self.g)


TestFuzzStructure = OpSequenceMachine.TestCase


# --------------------------------------------------------------------------
# Explicit grow-then-execute sequences: independent of stateful-machinery
# internals (adaptive rule exploration keeps machine graphs small), these
# guarantee deep structures and lifecycle traffic per example.
# --------------------------------------------------------------------------

from hypothesis import given


@st.composite
def _op_sequence(draw) -> list:
    """A phase-structured op plan: fill structure first, then run it."""
    grows = draw(st.lists(st.sampled_from(("root", "child", "link", "wild-link")), max_size=14))
    runs = draw(st.lists(st.sampled_from(("start", "finish", "cancel", "unlink-edge")), max_size=20))
    return grows + runs


def _direct_kids(g, nid):
    pre = nid + "."
    return [k for k in g.nodes if k.startswith(pre) and "." not in k[len(pre):]]


@given(_op_sequence())
@settings(deadline=None, max_examples=80)
def test_grow_then_execute_sequences(plan):
    import random

    rng = random.Random(hash(tuple(plan)) & 0xFFFF)
    g = Graph.new("grow")
    applied = {"start": 0, "finish": 0}
    for verb in plan:
        live = sorted(g.nodes, key=id_sort_key)
        if not live:
            ok, g = fs.try_op(ops.add_task, g, ROOTS[0], "first", [])
            continue
        if verb == "root":
            missing = [r for r in ROOTS if r not in g.nodes]
            name = rng.choice(missing) if missing else rng.choice(live)
            _, g = fs.try_op(ops.add_task, g, name, f"t-{name}", [])
        elif verb == "child":
            p = rng.choice(live)
            n = 1
            while f"{p}.{n}" in g.nodes:
                n += 1
            kids = _direct_kids(g, p)
            use = [rng.choice(kids)] if kids else []
            _, g = fs.try_op(ops.add_task, g, f"{p}.{n}", f"t-{p}.{n}", use)
        elif verb == "link":
            p = rng.choice(live)
            sibs = [s for s in live
                    if s != p and s.rsplit(".", 1)[0] == p.rsplit(".", 1)[0]]
            if sibs:
                _, g = fs.try_op(ops.link, g, rng.choice(sibs), p)
        elif verb == "wild-link":
            a, b = rng.choice(live), rng.choice(live + ["ghost"])
            fs.try_op(ops.link, g, a, b)
        elif verb == "start":
            ready = [
                n for n in live
                if not any(k.startswith(n + ".") for k in g.nodes)
                and g.nodes[n].status == "pending"
                and not ops.blocked_by(g, n)
            ]
            if ready:
                nid = rng.choice(ready)
                ok, g = fs.try_op(ops.start_task, g, nid)
                applied["start"] += ok
        elif verb == "finish":
            pending = [
                n for n in live
                if not any(k.startswith(n + ".") for k in g.nodes)
                and g.nodes[n].status == "in_progress"
            ]
            if pending:
                nid = rng.choice(pending)
                ok, g = fs.try_op(
                    ops.finish_task, g, nid,
                    f"handoff from {plan.index(verb)}",
                )
                applied["finish"] += ok
        elif verb == "cancel":
            active = [
                n for n in live
                if not any(k.startswith(n + ".") for k in g.nodes)
                and g.nodes[n].status != "cancelled"
            ]
            if active:
                _, g = fs.try_op(ops.cancel_task, g, rng.choice(active))
        elif verb == "unlink-edge":
            edges = [(nid, d) for nid, node in g.nodes.items() for d in node.deps]
            if edges:
                dst, dep = rng.choice(edges)
                _, g = fs.try_op(ops.unlink, g, dep, dst)

    fs.assert_invariants(g)
    structured = sum(v == "child" for v in plan)
    assert len(g.nodes) >= min(len(ROOTS), sum(v == "root" for v in plan))
    if structured >= 3:
        containers = [n for n in g.nodes if _direct_kids(g, n)]
        assert containers, "three child attempts must eventually nest structure"
