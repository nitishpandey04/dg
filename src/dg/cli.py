from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from . import ops
from .errors import DgError
from .model import FORMAT_VERSION, Graph
from .storage import (
    discover_root,
    ensure_gitignore,
    graph_path,
    init_root,
    journal,
    load,
    pop_journal,
    save,
)
from .validate import structural_errors


def _parse_after(csv: str | None) -> list[str]:
    if not csv:
        return []
    return [s.strip() for s in csv.split(",") if s.strip()]


def _banner() -> str:
    try:
        return f"dg {_pkg_version('dg')} (format v{FORMAT_VERSION})"
    except PackageNotFoundError:
        return f"dg 0.0.0+dev (format v{FORMAT_VERSION})"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dg", description="dependency-graph task tracker for AI agents")
    p.add_argument("--version", action="version", version=_banner())
    sp = p.add_subparsers(dest="cmd", required=True)

    q = sp.add_parser("init", help="create an empty graph here (.dg/graph.json)")
    q.add_argument("--title", "-t", default="Untitled plan")
    q.add_argument("--force", action="store_true", help="overwrite existing graph")

    q = sp.add_parser("add", help="add task with explicit id: dg add <ID> <TITLE> [--after IDS]")
    q.add_argument("id", help="task id, e.g. setup or auth.2 (parent must exist)")
    q.add_argument("title")
    q.add_argument("--after", metavar="IDS", help="comma-separated sibling predecessors")
    q.add_argument("--note", "-n")

    q = sp.add_parser("sub", help="decompose: add numbered child: dg sub <PARENT> <TITLE> [--after IDS]")
    q.add_argument("parent")
    q.add_argument("title")
    q.add_argument("--after", metavar="IDS")
    q.add_argument("--note", "-n")

    q = sp.add_parser("link", help="B depends on A: dg link A B")
    q.add_argument("a")
    q.add_argument("b")

    q = sp.add_parser("unlink", help="remove dependency A -> B")
    q.add_argument("a")
    q.add_argument("b")

    q = sp.add_parser("edit", help="change title/note of a task")
    q.add_argument("id")
    q.add_argument("--title")
    q.add_argument("--note")
    q.add_argument("--clear-note", action="store_true")

    q = sp.add_parser("remove", help="delete a leaf entirely (draft cleanup; it must be unreferenced)")
    q.add_argument("id")

    for name, help_ in (("start", "claim a leaf: pending -> in_progress"),
                        ("done", "finish a leaf (--note records handoff for successors)"),
                        ("cancel", "cancel a leaf")):
        q = sp.add_parser(name, help=help_)
        q.add_argument("id")
        if name == "done":
            q.add_argument("--note", "-n")

    q = sp.add_parser("next", help="the frontier: ready, unstarted tasks")
    q.add_argument("--json", action="store_true")

    q = sp.add_parser("show", help="print subtree [default: whole graph]")
    q.add_argument("root", nargs="?")
    q.add_argument("--json", action="store_true")

    q = sp.add_parser("render", help="emit mermaid of subtree")
    q.add_argument("--at", dest="at", default=None)

    q = sp.add_parser("validate", help="run all invariant checks")
    q.add_argument("--json", action="store_true")

    sp.add_parser("undo", help="revert last mutation")
    return p


def _mutating(root: str, argv: list[str], prev: Graph, new: Graph, msg: str) -> int:
    journal(root, argv[0], argv, prev)
    save(root, new)
    print(msg)
    return 0


def command_names() -> list[str]:
    """Subcommand list, used by the docs-drift test."""
    out = []
    for a in build_parser()._actions:
        if isinstance(a, argparse._SubParsersAction):
            out.extend(a.choices.keys())
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    ns = build_parser().parse_args(argv)
    cmd = ns.cmd
    root = "."
    try:
        if cmd == "init":
            existing = discover_root()
            if existing is not None:
                if existing == os.getcwd():
                    if not ns.force:
                        raise DgError(
                            f"graph already exists at {graph_path(existing)} (use --force to overwrite)"
                        )
                else:
                    raise DgError(
                        f"a graph already exists at {graph_path(existing)}; "
                        "initializing here would fork the plan - run commands from that root instead"
                    )
            init_root(root)
            save(root, Graph.new(ns.title))
            if ensure_gitignore(root):
                print("wrote .gitignore entry: .dg/journal.jsonl")
            print(f"initialized graph '{ns.title}' at {graph_path(root)}")
            return 0

        found = discover_root()
        if found is None:
            raise DgError("no dg graph in this directory tree; run 'dg init' at the project root")
        root = found
        g = load(root)

        if cmd == "add":
            new = ops.add_task(g, ns.id, ns.title, _parse_after(ns.after), ns.note)
            return _mutating(root, argv, g, new, f"added {ns.id}")
        if cmd == "sub":
            new, new_id = ops.sub_task(g, ns.parent, ns.title, _parse_after(ns.after), ns.note)
            return _mutating(root, argv, g, new, f"added {new_id}")
        if cmd == "link":
            new = ops.link(g, ns.a, ns.b)
            return _mutating(root, argv, g, new, f"linked {ns.a} -> {ns.b}")
        if cmd == "unlink":
            new = ops.unlink(g, ns.a, ns.b)
            return _mutating(root, argv, g, new, f"unlinked {ns.a} -> {ns.b}")
        if cmd == "edit":
            new = ops.edit_task(g, ns.id, ns.title, ns.note, ns.clear_note)
            return _mutating(root, argv, g, new, f"edited {ns.id}")
        if cmd == "remove":
            new = ops.remove_task(g, ns.id)
            return _mutating(root, argv, g, new, f"removed {ns.id}")

        if cmd == "start":
            new = ops.start_task(g, ns.id)
            return _mutating(root, argv, g, new, f"{ns.id}: in_progress")
        if cmd == "done":
            new = ops.finish_task(g, ns.id, ns.note)
            return _mutating(root, argv, g, new, f"{ns.id}: done")
        if cmd == "cancel":
            new = ops.cancel_task(g, ns.id)
            return _mutating(root, argv, g, new, f"{ns.id}: cancelled")

        if cmd == "undo":
            prev = pop_journal(root)
            if prev is None:
                raise DgError("nothing to undo")
            save(root, prev)
            print("undid last mutation")
            return 0

        if cmd == "next":
            items = [{"id": i, "title": g.nodes[i].title} for i in ops.frontier(g)]
            if ns.json:
                print(json.dumps({"tasks": items}))
            else:
                for t in items:
                    print(f"{t['id']:<14} {t['title']}")
            return 0

        if cmd == "show":
            if ns.json:
                print(json.dumps({"title": g.title, "nodes": ops.inspect_graph(g, ns.root)}))
            else:
                print(g.title)
                print("-" * max(len(g.title), 8))
                print(ops.ascii_tree(g, ns.root) or "(empty)")
            return 0

        if cmd == "render":
            print(ops.mermaid(g, ns.at))
            return 0

        if cmd == "validate":
            errs = structural_errors(g)
            if ns.json:
                out: dict = {"ok": not errs, "tasks": len(g.nodes)}
                if errs:
                    out["errors"] = errs
                else:
                    out["ready"] = len(ops.frontier(g))
                print(json.dumps(out))
            elif errs:
                for e in errs:
                    print(f"error: {e}", file=sys.stderr)
            else:
                print(f"ok — {len(g.nodes)} tasks, {len(ops.frontier(g))} ready")
            return 1 if errs else 0

    except DgError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command {cmd}")  # pragma: no cover
