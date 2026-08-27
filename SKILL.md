---
name: dg
description: Plan, execute, and track work as a validated dependency graph using the dg CLI. Use before starting any multi-step engineering task.
---

# dg: dependency-graph task protocol

You represent work as a hierarchical DAG of tasks, not a mental todo list.
`dg` enforces the structure; your job is the sequencing decisions and honest
status. Run everything from inside the target project — commands resolve the
graph upward like git resolves the repo root.

## 0. Read the current state

```
dg show            # full graph with statuses + blockers
dg next --json     # ready tasks — your work queue
dg validate        # structural sanity
```

If no graph exists yet, plan first (step 1) — at the project root run
`dg init --title "<job name>"`. If one exists and matches this session's job,
resume from `next`.

## 1. Planning phase (before any implementation)

Draft top-down through validated mutations — never one big plan from thin air:

1. **Milestones**: 3–9 coarse root-level tasks via
   `dg add <id> "<imperative title>" [--after <ids>]`
2. **Decompose** each milestone until leaves are atomic — roughly one commit /
   one focused working session:
   `dg sub <milestone> "<subtask>" [--after <sibling ids>]` (auto-numbers `<milestone>.N`)
3. **Order only what truly orders.** An edge means *finish-to-start*. Two
   tasks touching independent files can usually run in parallel — leave them
   unordered rather than serializing by accident. Within one milestone,
   subtasks are siblings and may depend on each other via `--after`.
4. **Containers carry external gates.** To make an entire subtree wait on
   outside work, link the predecessor to the *container*; its children inherit
   the gate automatically. You rarely need edges reaching across layers.
5. **Self-review**: `dg validate` must pass, then `dg render` and reread the
   mermaid output asking: does every edge reflect a real dependency? any
   missing gate? anything serial that could be parallel?

Only after validate passes do you touch code.

### Sizing heuristics

- Leaf too big if it needs "and" twice in its title.
- Fan-out >7 children or nesting deeper than ~4 levels: rethink grouping.
- Prefer fewer, well-named milestones over exhaustive upfront detail —
  decompose a milestone just-in-time when its turn approaches.

## 2. Execution loop

Per turn, exactly:

```
dg next --json                 # 1. what's ready?
#    pick ONE task             # 2. read notes of its deps (`dg show`) for handoff context
dg start <id>                  # 3. claim it
#    ...do the actual work...  # 4. implement, test
dg done <id> --note "<what>"   # 5. record completion
```

The **done-note is a contract**: whoever owns the successor tasks must be able
to proceed cold from your note alone. Always include artifact pointers —
commit hash, branch, created/changed paths, verification results:

```
dg done auth.1 --note "token refresh in src/auth/token.py @ c3f9a12; tests in tests/test_token.py all pass"
```

## 3. Rep planning rules (plan ≠ prophecy)

- Discovered missing work → `dg add` / `dg sub` it, link it in. Cheap repairs
  beat pretending the plan was right.
- Task obsolete → `dg cancel <id>` (stays visible, releases successors).
  `dg remove <id>` only for never-referenced draft mistakes — it refuses
  linked nodes and non-empty containers.
- Wrong dependency → `dg unlink A B`, then relink correctly.
- Title drifted from reality → `dg edit <id> --title "..."; keep ids frozen
  once referenced anywhere (notes, commits) — only titles/notes are mutable.
- Started a task and the approach died → fix forward and `done` honestly, or
  `cancel` it and `add` a corrective successor whose deps point at reality.
- Never mark a task done that isn't actually finished on disk. Status lies
  poison every downstream decision; when uncertain, look with `show`.

## 4. Committing

`graph.json` is project memory — commit it alongside the work it tracks.
(Don't commit `journal.jsonl`; already gitignored.) Your plan diffs then form
a reviewable history of how the work evolved.

## Interface guarantees you may rely on

- Success exit `0`; failures print one actionable line starting `error:` on
  stderr, change nothing on disk, exit `1`.
- `--json` on reads (`next`, `validate`, `show`) for machine parsing.
- Every mutation is journaled: `dg undo` reverts the last one, stepwise.
- Structural errors (cycles, bad refs, layer violations) are impossible to
  commit — if rejected, read the message and fix the sequence, not the tool.
