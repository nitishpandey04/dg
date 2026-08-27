"""Shared scaffolding for dg's property-based invariant harness.

Design rule: oracle functions here re-derive the read-model semantics from
the README spec instead of calling into ops.py, so an implementation bug
cannot silently match a buggy oracle. `structural_errors` is imported as-is
because the harness's structural contract is precisely "the gate stays
clean across every mutation path", not a second validator implementation.

`try_op` executes one op attempt: DgError counts as an expected rejection
(its message must match a known class) and copy-on-write purity is checked
on both success and rejection.
"""

from __future__ import annotations

import copy
import re

from hypothesis import strategies as st

from dg.errors import DgError
from dg.model import FORMAT_VERSION, Graph, from_dict, to_dict
from dg.validate import structural_errors

# -- strategies ---------------------------------------------------------------

SEGMENTS = ("a", "b", "c", "x", "1", "2", "7")


def valid_ids(max_segments: int = 3):
    return st.builds(
        ".".join,
        st.lists(st.sampled_from(SEGMENTS), min_size=1, max_size=max_segments),
    )


def titles():
    # Newlines/quotes/blank titles are legal input; renderers must cope.
    return st.text(max_size=24)


def notes():
    return st.text(max_size=60)


# -- rejection classification --------------------------------------------------

REJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("missing task", r"^no such task: '"),
    ("invalid id", r"^invalid id "),
    ("duplicate id", r" already exists$"),
    ("parent missing (add)", r"^container '.*' does not exist"),
    ("parent missing (gate)", r": parent '.*' does not exist"),
    ("self dep", r"^cannot link a task to itself$|depends on itself$"),
    ("cycle", r"^cycle detected: "),
    ("cross-layer", r"cross-layer dependency .* rejected"),
    ("unknown dep", r"dependency '.*' does not exist"),
    ("blocked start", r"^'.*' is blocked by: "),
    ("container op", r"^'.*' is a container"),
    ("state machine", r"cannot start$|is already done$|was cancelled;|is already cancelled$"),
    ("edge refs", r"does not depend on |already depends on "),
    ("remove guarded", r"is depended on by: "),
)


def classify_rejection(msg: str) -> str | None:
    for name, pat in REJECTION_PATTERNS:
        if re.search(pat, msg):
            return name
    return None


def try_op(fn, *args):
    """Run fn(*args); return (True, new_graph) or (False, the pristine input).

    Rejections must be DgError with a known-class message and must leave the
    input graph untouched; successes must return a fresh graph object.
    Callers may always reassign:  ok, g = try_op(ops.link, g, a, b)
    """
    before = copy.deepcopy(args[0])
    try:
        out = fn(*args)
    except DgError as e:
        msg = str(e)
        assert msg, f"{fn.__name__} rejected with empty message"
        kind = classify_rejection(msg)
        assert kind is not None, f"{fn.__name__}: unclassified rejection: {msg!r}"
        assert args[0] == before, f"{fn.__name__} polluted graph on rejection"
        del before
        return False, args[0]
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], Graph):
        out = out[0]  # sub_task-style (Graph, derived_id) results
    assert isinstance(out, Graph), f"{fn.__name__} returned {type(out).__name__}"
    assert out is not args[0], f"{fn.__name__} mutated its input in place"
    del before
    return True, out


# -- independent oracles --------------------------------------------------------

SETTLED_STATUSES = frozenset(("done", "cancelled"))


def o_is_container(g: Graph, nid: str) -> bool:
    prefix = nid + "."
    return any(i.startswith(prefix) for i in g.nodes)


def o_leaves(g: Graph) -> list[str]:
    return [i for i in g.nodes if not o_is_container(g, i)]


def o_status(g: Graph, nid: str) -> str:
    node = g.nodes[nid]
    if not o_is_container(g, nid):
        return node.status
    leaf_statuses = [
        g.nodes[i].status
        for i in g.nodes
        if i.startswith(nid + ".") and not o_is_container(g, i)
    ]
    if leaf_statuses and all(s in SETTLED_STATUSES for s in leaf_statuses):
        return "done"
    if any(s == "in_progress" or s in SETTLED_STATUSES for s in leaf_statuses):
        return "in_progress"
    return "pending"


def o_required(g: Graph, nid: str) -> list[str]:
    parts = nid.split(".")
    required = list(g.nodes[nid].deps)
    for k in range(1, len(parts)):
        anc = ".".join(parts[:k])
        required.extend(g.nodes[anc].deps)
    seen: set[str] = set()
    ordered: list[str] = []
    for d in required:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def o_blocked(g: Graph, nid: str) -> list[str]:
    return [d for d in o_required(g, nid) if o_status(g, d) not in SETTLED_STATUSES]


def o_sort_key(node_id: str):
    key = []
    for seg in node_id.split("."):
        key.append((0, int(seg), "") if seg.isdigit() else (1, 0, seg))
    return tuple(key)


def o_frontier(g: Graph) -> list[str]:
    ready = [
        i for i in g.nodes
        if not o_is_container(g, i)
        and g.nodes[i].status == "pending"
        and not o_blocked(g, i)
    ]
    return sorted(ready, key=o_sort_key)


# -- the invariant sweep --------------------------------------------------------


def assert_invariants(g: Graph) -> None:
    """Everything that must hold after any accepted sequence of mutations."""
    errs = structural_errors(g)
    assert errs == [], errs

    rt = from_dict(to_dict(g))
    assert rt == g and g.version == FORMAT_VERSION, "serialization round-trip drifted"

    for nid in g.nodes:
        assert o_status(g, nid) is not None
        blockers = o_blocked(g, nid)
        assert all(d in g.nodes for d in blockers), (nid, blockers)

    leaves = set(o_leaves(g))
    frontier = o_frontier(g)
    assert all(i in leaves for i in frontier), "frontier leaked a container"
    for i in frontier:
        assert g.nodes[i].status == "pending" and not o_blocked(g, i)

    # rendering must never crash regardless of generated content
    _smoke_renderers(g)


def _smoke_renderers(g: Graph) -> None:
    from dg import ops

    tree = ops.ascii_tree(g)
    mm = ops.mermaid(g)
    insp = ops.inspect_graph(g)
    assert isinstance(tree, str) and isinstance(mm, str)
    for entry in insp:
        nid = entry["id"]
        assert entry["status"] == o_status(g, nid), (nid, entry["status"])
        assert entry["blocked_by"] == o_blocked(g, nid), (nid, entry["blocked_by"])
