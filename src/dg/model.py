from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import DgError

FORMAT_VERSION = 1

STATUSES = ("pending", "in_progress", "done", "cancelled")
SETTLED = frozenset(("done", "cancelled"))

SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ID_RE = re.compile(r"^(?:[a-z0-9][a-z0-9_-]*)(?:\.[a-z0-9][a-z0-9_-]*)*$")


@dataclass
class Node:
    title: str
    status: str = "pending"
    deps: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class Graph:
    version: int
    title: str
    nodes: dict[str, Node]

    @classmethod
    def new(cls, title: str) -> Graph:
        return cls(version=FORMAT_VERSION, title=title, nodes={})


def check_id(node_id: str) -> None:
    if not ID_RE.match(node_id):
        raise DgError(
            f"invalid id '{node_id}': dot-separated segments of [a-z0-9][a-z0-9_-]* "
            "(e.g. 'auth', 'auth.2', '3.1.4')"
        )


def parent_of(node_id: str) -> str | None:
    if "." not in node_id:
        return None
    return node_id.rsplit(".", 1)[0]


def depth_of(node_id: str) -> int:
    return node_id.count(".") + 1


def is_container(g: Graph, node_id: str) -> bool:
    prefix = node_id + "."
    return any(i.startswith(prefix) for i in g.nodes)


def children_of(g: Graph, node_id: str) -> list[str]:
    prefix = node_id + "."
    out = [
        i for i in g.nodes
        if i.startswith(prefix) and "." not in i[len(prefix):]
    ]
    return sorted(out, key=id_sort_key)


def descendants(g: Graph, node_id: str) -> list[str]:
    prefix = node_id + "."
    return [i for i in g.nodes if i.startswith(prefix)]


def ancestors_of(node_id: str) -> list[str]:
    parts = node_id.split(".")
    return [".".join(parts[:k]) for k in range(1, len(parts))]


def require_node(g: Graph, node_id: str) -> Node:
    check_id(node_id)
    if node_id not in g.nodes:
        raise DgError(f"no such task: '{node_id}'")
    return g.nodes[node_id]


def sibling_ids(g: Graph, node_id: str) -> set[str]:
    parent = parent_of(node_id)
    prefix = "" if parent is None else parent + "."
    return {
        i for i in g.nodes
        if i != node_id and parent_of(i) == parent
        and (parent is None or i.startswith(prefix))
    }


def id_sort_key(node_id: str):
    key = []
    for seg in node_id.split("."):
        key.append((0, int(seg), "") if seg.isdigit() else (1, 0, seg))
    return tuple(key)


def to_dict(g: Graph) -> dict:
    return {
        "version": g.version,
        "title": g.title,
        "nodes": {
            i: {"title": n.title, "status": n.status, "deps": n.deps, "note": n.note}
            for i, n in g.nodes.items()
        },
    }


def from_dict(d: dict) -> Graph:
    try:
        version = d["version"]
        title = d["title"]
        raw_nodes = d["nodes"]
    except (KeyError, TypeError) as e:
        raise DgError(f"not a dg graph file: missing field {e}") from e
    nodes: dict[str, Node] = {}
    for i, raw in raw_nodes.items():
        try:
            nodes[i] = Node(
                title=raw["title"],
                status=raw.get("status", "pending"),
                deps=list(raw.get("deps", [])),
                note=raw.get("note"),
            )
        except (KeyError, TypeError) as e:
            raise DgError(f"bad node '{i}': {e}") from e
    return Graph(version=version, title=title, nodes=nodes)
