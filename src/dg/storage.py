from __future__ import annotations

import json
import os
import time

from .errors import DgError
from .model import FORMAT_VERSION, Graph, from_dict, to_dict


def dg_dir(root: str) -> str:
    return os.path.join(root, ".dg")


def graph_path(root: str) -> str:
    return os.path.join(dg_dir(root), "graph.json")


def journal_path(root: str) -> str:
    return os.path.join(dg_dir(root), "journal.jsonl")


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


GITIGNORE_LINE = ".dg/journal.jsonl"


def ensure_gitignore(root: str) -> bool:
    """Keep the undo journal out of version control; graph.json stays tracked.
    Idempotent. Returns True if the file was touched."""
    gi = os.path.join(root, ".gitignore")
    existing = ""
    if os.path.exists(gi):
        with open(gi, encoding="utf-8") as f:
            existing = f.read()
    if GITIGNORE_LINE in existing.splitlines():
        return False
    with open(gi, "a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(GITIGNORE_LINE + "\n")
    return True


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


def journal(root: str, op: str, argv: list[str], prev: Graph) -> None:
    """Append an undo record containing the full pre-mutation snapshot."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "op": op,
        "argv": argv,
        "prev": to_dict(prev),
    }
    with open(journal_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def pop_journal(root: str) -> Graph | None:
    """Remove and return the newest journal entry's snapshot."""
    jp = journal_path(root)
    if not os.path.exists(jp):
        return None
    with open(jp, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    if not lines:
        return None
    entry = json.loads(lines[-1])
    del lines[-1]
    tmp = jp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(ln + "\n" for ln in lines)
    os.replace(tmp, jp)
    return from_dict(entry["prev"])
