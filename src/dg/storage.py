from __future__ import annotations

import json
import os

from .errors import DgError
from .model import FORMAT_VERSION, Graph, from_dict, to_dict


def dg_dir(root: str) -> str:
    return os.path.join(root, ".dg")


def graph_path(root: str) -> str:
    return os.path.join(dg_dir(root), "graph.json")


def discover_root(start: str | None = None) -> str | None:
    """Walk up from `start` to find the nearest directory owning .dg/graph.json.
    Guarantees one graph per directory tree, regardless of invocation depth."""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(graph_path(cur)):
            return cur
        nxt = os.path.dirname(cur)
        if nxt == cur:
            return None
        cur = nxt


def init_root(root: str) -> None:
    os.makedirs(dg_dir(root), exist_ok=True)


def load(root: str) -> Graph:
    gp = graph_path(root)
    if not os.path.exists(gp):
        raise DgError(f"no graph in this directory ({gp}); run 'dg init' first")
    try:
        with open(gp, encoding="utf-8") as f:
            g = from_dict(json.load(f))
    except json.JSONDecodeError as e:
        raise DgError(f"corrupt graph file {gp}: {e}") from e
    if g.version > FORMAT_VERSION:
        raise DgError(
            f"graph format v{g.version} is newer than this tool (v{FORMAT_VERSION}); upgrade dg"
        )
    return g


def save(root: str, g: Graph) -> None:
    """Atomic write: temp file + rename, so a crash never tears the file."""
    os.makedirs(dg_dir(root), exist_ok=True)
    gp = graph_path(root)
    tmp = gp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(to_dict(g), f, indent=2)
        f.write("\n")
    os.replace(tmp, gp)
