# dg — dependency-graph task tracker

A CLI for representing work as a **hierarchical DAG of tasks**. Designed as the
planning / execution / tracking substrate for AI coding agents: before starting
work, an agent drafts the task graph; while working, it consumes the frontier
and marks nodes done; humans review plans via `show` / `render`.

Zero runtime dependencies. Python >= 3.11.

## Concepts

- **Node** = a task: `{id, title, status, deps[], note}`.
- **Edge** = `deps[]`: finish-to-start only. `b.deps = ["a"]` means *a must
  finish before b starts*.
- **Layers**: any node may contain subtasks. `dg sub <parent> <title>` creates
  `parent.N` (auto-numbered). A node with children is a **container**.
- **IDs** are hierarchical dotted paths (`auth`, `auth.2`, `auth.2.1`);
  segments match `[a-z0-9][a-z0-9_-]*`.

### Semantics (the rules everything else inherits)

1. **Encapsulated layers** — deps connect siblings within the same container
   only. Cross-layer ordering attaches to the *container*: an edge `p -> box`
   gates every top-level child of `box` (inherited gate), even though those
   children declare no edge themselves. Outward, `box` is ready when all its
   descendant leaves are settled.
2. **Derived status** — containers are not directly start/done/cancel-able;
   their status derives from descendant leaves: all settled → `done`;
   any activity or partial completion → `in_progress`; else `pending`.
3. **Settled** means `done` or `cancelled`; either releases successors.
4. **Leaf-only execution** — only leaves get `start/done/cancel`.
5. Every mutation is validated (ids, references, layer rules, cycles with the
   offending path printed) and journaled; rejected mutations change nothing.

## Storage

Created per-directory by `dg init` under `<project-root>/.dg/`:

- `graph.json` — `{version, title, nodes: {id: {...}}}`; versioned for future
  migrations; **commit it** — it is the durable plan + audit trail. Writes are
  atomic (temp + rename).
- `journal.jsonl` — snapshot-per-mutation undo log behind `dg undo`;
  session-scoped scratch, auto-added to `.gitignore` at init.

Every command resolves the graph like git resolves the repo root: it walks up
from the CWD to the nearest `.dg/`. Commands work from any subdirectory and
always hit the same graph — one graph per directory tree, guaranteed (`init`
refuses to create a second one below an existing root).

## Commands

| Command | Effect |
|---|---|
| `dg init [--title T] [--force]` | create `.dg/graph.json` here |
| `dg add ID TITLE [--after IDS] [--note N]` | explicit id; parent must exist |
| `dg sub PARENT TITLE [--after IDS]` | decompose: next numbered child |
| `dg link A B` / `dg unlink A B` | B depends on A / remove that dep |
| `dg edit ID [--title T --note N --clear-note]` | retitle / annotate |
| `dg remove ID` | delete an unreferenced leaf ("never existed"); linked/container leaves refused |
| `dg start ID` | leaf: pending -> in_progress (rejects blocked/containers) |
| `dg done ID [--note N]` | leaf -> done; note is the handoff for successors |
| `dg cancel ID` | leaf -> cancelled (releases successors) |
| `dg next [--json]` | **frontier**: ready, unstarted tasks |
| `dg show [ROOT] [--json]` | indented subtree with statuses + unmet blockers |
| `dg render [--at ROOT]` | mermaid `flowchart TD` |
| `dg validate [--json]` | run all invariant checks |
| `dg undo` | revert last mutation |

Exit codes: `0` ok, `1` operational error (stderr line begins `error:`),
`2` usage error. Read commands accept `--json` for machine consumption.
Agents should treat `next --json` as their per-turn work queue and put
handoff context into `done --note`.

## The agent loop

```
draft    add/sub/link until `validate` passes && `render` looks right
execute  next -> read note(s) of deps -> start -> do the work -> done --note ...
replan   unexpected? edit/unlink/add + cancel obsolete leaves; keep ids stable
```

## Development

```
uv sync            # install with dev tools
uv run pytest      # 24 tests: invariants, semantics, storage, cli e2e
uv run ruff check .
```
