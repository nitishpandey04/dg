from __future__ import annotations

from .errors import DgError
from .model import STATUSES, Graph, check_id, parent_of


def structural_errors(g: Graph) -> list[str]:
    errs: list[str] = []
    for nid, node in g.nodes.items():
        try:
            check_id(nid)
        except DgError as e:
            errs.append(str(e))
        if node.status not in STATUSES:
            errs.append(f"{nid}: invalid status '{node.status}'")
        par = parent_of(nid)
        if par is not None and par not in g.nodes:
            errs.append(f"{nid}: parent '{par}' does not exist")
        for dep in node.deps:
            if dep == nid:
                errs.append(f"{nid}: depends on itself")
                continue
            if dep not in g.nodes:
                errs.append(f"{nid}: dependency '{dep}' does not exist")
                continue
            if parent_of(dep) != par:
                errs.append(
                    f"{nid}: cross-layer dependency '{dep}' rejected — deps must connect siblings"
                )
    errs.extend(_cycle_errors(g))
    return errs


def _cycle_errors(g: Graph) -> list[str]:
    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in g.nodes}
    errors: list[str] = []

    def dfs(start: str) -> None:
        stack = [(start, iter(g.nodes[start].deps))]
        color[start] = GREY
        path = [start]
        while stack:
            node, it = stack[-1]
            advanced = False
            for dep in it:
                if dep not in g.nodes or color[dep] == BLACK:
                    continue
                if color[dep] == GREY:
                    cyc = path[path.index(dep):] + [dep]
                    errors.append("cycle detected: " + " -> ".join(cyc))
                    continue
                color[dep] = GREY
                path.append(dep)
                stack.append((dep, iter(g.nodes[dep].deps)))
                advanced = True
                break
            if not advanced:
                color[node] = BLACK
                path.pop()
                stack.pop()

    for nid in g.nodes:
        if color[nid] == WHITE:
            dfs(nid)
    return errors


def validate_or_fail(g: Graph) -> None:
    """Single enforcement gate: every mutation passes through here."""
    errs = structural_errors(g)
    if errs:
        raise DgError(errs[0])
