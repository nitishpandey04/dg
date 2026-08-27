# AGENTS.md — working on dg

## What this is

`dg` is a dependency-graph task tracker for AI agents: hierarchical DAG of
tasks, validated mutations, frontier-based work selection. The specs live in
`README.md` (format, semantics) and `SKILL.md` (agent protocol). Read both
before changing behavior.

## Dev loop

Package manager is **uv**. Before handing back any change:

```
uv run pytest            # full suite must pass
uv run ruff check .      # lint clean
```

## Architecture map

| Module | Owns |
|---|---|
| `src/dg/model.py` | Node/Graph dataclasses, id algebra (paths, parents, children), serialization |
| `src/dg/validate.py` | the invariant gate: refs, layer rules, cycles with path |
| `src/dg/ops.py` | mutations + read model (effective status, frontier, gates), rendering |
| `src/dg/storage.py` | atomic writes, journal/undo, root discovery, gitignore |
| `src/dg/cli.py` | argparse wiring, output contract (`error:` prefix, exit codes, --json) |
| `SKILL.md` | agent-facing protocol; harness-agnostic |

## Non-negotiables

1. **Every mutation goes through `_apply()`'s validation gate** in ops.py.
   Never write a mutation that saves without validation. Ops are
   copy-on-write: they return a fresh Graph and never mutate in place —
   reassign the result (`g = ops.link(g, ...)`) or the change is silently
   dropped (this bit even the fuzz-harness author; see SKILL.md guarantees).
2. **CLI surface is frozen public API**: command names, flags, exit codes,
   `error:` stderr prefix, `--json` shapes. Agents hardcode all of it;
   changes must be additive. Bump `FORMAT_VERSION` + write a migration for
   any graph-format change.
3. **New subcommand ⇒ document it in SKILL.md** (tests/test_skill_doc.py
   enforces this) and add CLI e2e tests via `main([...])`, not mocks.
4. **Disk mutations are atomic** (temp+rename) and journaled before apply.

## Semantics reference

If touching statuses, frontiers, or containers, these are load-bearing:
encapsulated layers (deps connect siblings only), container gates inherited
by children, derived container status (all-settled → done, partial →
in_progress), cancelled releases successors, leaf-only execution transitions.
Tests in tests/test_ops.py are executable documentation of exactly these.

## Dogfooding

Changes with 3+ distinct steps get planned in dg itself:
`dg init` at repo root, plan first, execute via the frontier loop. The
resulting `.dg/graph.json` is committed alongside the work.
