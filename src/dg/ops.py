from __future__ import annotations

import copy

from .errors import DgError
from .model import (
    SETTLED,
    Graph,
    Node,
    ancestors_of,
    check_id,
    children_of,
    descendants,
    id_sort_key,
    is_container,
    parent_of,
    require_node,
)
from .validate import structural_errors


def _apply(g: Graph) -> Graph:
    """Copy-on-write: mutate a clone, then pass through the validation gate."""
    c = copy.deepcopy(g)
    errs = structural_errors(c)
    if errs:
        raise DgError(errs[0])
    return c


# ---------------------------------------------------------------- planning ops


def add_task(g: Graph, node_id: str, title: str, after: list[str], note: str | None = None) -> Graph:
    check_id(node_id)
    if node_id in g.nodes:
        raise DgError(f"'{node_id}' already exists")
    par = parent_of(node_id)
    if par is not None and par not in g.nodes:
        raise DgError(f"container '{par}' does not exist; create it first ('dg add {par} ...')")
    c = copy.deepcopy(g)
    c.nodes[node_id] = _make_node(title, after, note)
    return _apply(c)


def sub_task(g: Graph, parent: str, title: str, after: list[str], note: str | None = None) -> tuple[Graph, str]:
    """The decomposition gesture: create the next numbered child under an existing node."""
    require_node(g, parent)
    n = 1
    prefix = f"{parent}."
    taken = {i[len(prefix):] for i in g.nodes if i.startswith(prefix)}
    while str(n) in taken:
        n += 1
    new_id = f"{parent}.{n}"
    return add_task(g, new_id, title, after, note), new_id


def link(g: Graph, src: str, dst: str) -> Graph:
    """dst depends on src (src must finish before dst starts)."""
    require_node(g, src)
    require_node(g, dst)
    if src == dst:
        raise DgError("cannot link a task to itself")
    if src in g.nodes[dst].deps:
        raise DgError(f"'{dst}' already depends on '{src}'")
    c = copy.deepcopy(g)
    c.nodes[dst].deps.append(src)
    return _apply(c)


def unlink(g: Graph, src: str, dst: str) -> Graph:
    require_node(g, src)
    require_node(g, dst)
    if src not in g.nodes[dst].deps:
        raise DgError(f"'{dst}' does not depend on '{src}'")
    c = copy.deepcopy(g)
    c.nodes[dst].deps.remove(src)
    return _apply(c)


def edit_task(
    g: Graph, node_id: str,
    title: str | None = None,
    note: str | None = None,
    clear_note: bool = False,
) -> Graph:
    require_node(g, node_id)
    c = copy.deepcopy(g)
    node = c.nodes[node_id]
    if title is not None:
        node.title = title
    if clear_note:
        node.note = None
    elif note is not None:
        node.note = note
    return _apply(c)


# ------------------------------------------------------------ execution ops


def start_task(g: Graph, node_id: str) -> Graph:
    node = require_node(g, node_id)
    if is_container(g, node_id):
        raise DgError(f"'{node_id}' is a container with subtasks; work its leaf tasks instead")
    if node.status != "pending":
        raise DgError(f"'{node_id}' is {node.status}, cannot start")
    unmet = blocked_by(g, node_id)
    if unmet:
        raise DgError(f"'{node_id}' is blocked by: {', '.join(unmet)}")
    c = copy.deepcopy(g)
    c.nodes[node_id].status = "in_progress"
    return _apply(c)


def finish_task(g: Graph, node_id: str, note: str | None = None) -> Graph:
    """Mark a leaf done. The note is the handoff: what successors' owners need to know."""
    node = require_node(g, node_id)
    if is_container(g, node_id):
        raise DgError(f"'{node_id}' is a container; its status derives from subtasks")
    if node.status == "done":
        raise DgError(f"'{node_id}' is already done")
    if node.status == "cancelled":
        raise DgError(f"'{node_id}' was cancelled; recreate a task if needed")
    c = copy.deepcopy(g)
    c.nodes[node_id].status = "done"
    if note:
        n = c.nodes[node_id]
        n.note = f"{n.note}\n{note}" if n.note else note
    return _apply(c)


def cancel_task(g: Graph, node_id: str) -> Graph:
    node = require_node(g, node_id)
    if is_container(g, node_id):
        raise DgError(f"'{node_id}' is a container; cancel its leaf tasks individually")
    if node.status == "cancelled":
        raise DgError(f"'{node_id}' is already cancelled")
    c = copy.deepcopy(g)
    c.nodes[node_id].status = "cancelled"
    return _apply(c)


def remove_task(g: Graph, node_id: str) -> Graph:
    """Delete a leaf that should never have existed. Unlike cancel, it leaves
    no trace in the graph. Guarded: containers must be emptied first, and the
    leaf must have no dependents - unlink those edges explicitly instead."""
    require_node(g, node_id)
    if is_container(g, node_id):
        kids = children_of(g, node_id)
        raise DgError(
            f"'{node_id}' is a container with {len(kids)} children; remove them individually"
        )
    dependents = sorted(i for i, n in g.nodes.items() if node_id in n.deps)
    if dependents:
        raise DgError(
            f"'{node_id}' is depended on by: {', '.join(dependents)} - unlink those edges first"
        )
    c = copy.deepcopy(g)
    del c.nodes[node_id]
    return _apply(c)


# ------------------------------------------------------------- read model


def effective_status(g: Graph, node_id: str) -> str:
    """Leaf => stored status. Container => derived from descendant leaves:
    all settled -> done; any activity or partial completion -> in_progress; else pending."""
    node = require_node(g, node_id)
    if not is_container(g, node_id):
        return node.status
    leaf_statuses = [
        g.nodes[i].status
        for i in descendants(g, node_id)
        if not is_container(g, i)
    ]
    if leaf_statuses and all(s in SETTLED for s in leaf_statuses):
        return "done"
    if any(s == "in_progress" or s in SETTLED for s in leaf_statuses):
        return "in_progress"
    return "pending"


def blocked_by(g: Graph, node_id: str) -> list[str]:
    """Unmet dependencies, including gates inherited from ancestor containers."""
    node = require_node(g, node_id)
    required = list(node.deps)
    for anc in ancestors_of(node_id):
        required.extend(g.nodes[anc].deps)
    seen: set[str] = set()
    unmet: list[str] = []
    for dep in required:
        if dep in seen:
            continue
        seen.add(dep)
        if effective_status(g, dep) not in SETTLED:
            unmet.append(dep)
    return unmet


def frontier(g: Graph) -> list[str]:
    """Every pending leaf whose dependencies (incl. inherited gates) are settled."""
    out = [
        nid for nid, node in g.nodes.items()
        if not is_container(g, nid)
        and node.status == "pending"
        and not blocked_by(g, nid)
    ]
    return sorted(out, key=id_sort_key)


def inspect_graph(g: Graph, root_id: str | None = None) -> list[dict]:
    """Machine-readable view (`show --json`): derived status and blockers per node."""
    if root_id is None:
        scope = set(g.nodes)
    else:
        require_node(g, root_id)
        scope = set(descendants(g, root_id)) | {root_id}
    out = []
    for nid in sorted(scope, key=id_sort_key):
        node = g.nodes[nid]
        out.append({
            "id": nid,
            "title": node.title,
            "status": effective_status(g, nid),
            "leaf": not is_container(g, nid),
            "deps": list(node.deps),
            "blocked_by": blocked_by(g, nid),
            "note": node.note,
        })
    return out


def ascii_tree(g: Graph, root_id: str | None = None) -> str:
    """Two-space indented subtree view. Pending nodes show unmet blockers inline."""
    roots = [root_id] if root_id else _root_ids(g)
    for rid in roots:
        require_node(g, rid)

    def label(nid: str) -> str:
        eff = effective_status(g, nid)
        line = f"{nid}  {g.nodes[nid].title}  [{eff}]"
        if eff == "pending":
            unmet = blocked_by(g, nid)
            if unmet:
                shown = ", ".join(unmet[:3]) + (", ..." if len(unmet) > 3 else "")
                line += f"  (needs: {shown})"
        if g.nodes[nid].note:
            line += f"  # {g.nodes[nid].note.splitlines()[-1]}"
        return line

    def emit(nid: str, indent: str, lines: list[str]) -> None:
        lines.append(indent + label(nid))
        for kid in children_of(g, nid):
            emit(kid, indent + "  ", lines)

    lines: list[str] = []
    for rid in roots:
        emit(rid, "", lines)
    return "\n".join(lines)


def mermaid(g: Graph, root_id: str | None = None) -> str:
    if root_id is None:
        scope = set(g.nodes)
    else:
        require_node(g, root_id)
        scope = set(descendants(g, root_id)) | {root_id}
    def safe(nid: str) -> str:
        return nid.replace(".", "__")
    lines = ["flowchart TD"]
    for nid in sorted(scope, key=id_sort_key):
        eff = effective_status(g, nid)
        title = g.nodes[nid].title.replace('"', "'").replace("\n", " ")
        lines.append(f'    {safe(nid)}["{nid} {title} [{eff}]"]')
    for nid in sorted(scope, key=id_sort_key):
        for dep in g.nodes[nid].deps:
            if dep in scope:
                lines.append(f"    {safe(dep)} --> {safe(nid)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- helpers


def _make_node(title: str, after: list[str], note: str | None) -> Node:
    return Node(title=title, deps=list(dict.fromkeys(after)), note=note)


def _root_ids(g: Graph) -> list[str]:
    return sorted([i for i in g.nodes if "." not in i], key=id_sort_key)
