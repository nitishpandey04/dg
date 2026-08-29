# dg-ui NOTES — lessons from the cytoscape attempt (v1, replaced by React Flow)

## Data / server (re-land these verbatim)
- Edges = structural `n.deps`, NEVER `ops.blocked_by()` (reports unmet only
  → settled graphs lose every arrow; execution history erased).
- /state payload: id, title, note, parent, effective status, blocked_by
  (for ⚠ unmet badges), frontier, child_count, chain (internal dep count
  for the folded badge). ETag = graph.json mtime_ns → 304; poll 1s.
- Server: stdlib, 127.0.0.1 default, --host/--port/--root, asset whitelist,
  never writes. HTML no-store; assets content-hashed.
- Build stamp in the header = stale-client detection at a glance.
- Watch for zombie servers holding the port when smoke-testing (silent
  bind failure).

## dg semantics the UI must honor
- Two structures, drawn differently: containment tree = dotted rails;
  dep DAG = solid arrows (execution flow).
- Roots are NOT all independent — order them topologically along cross-root
  dep edges (tiebreak: active > pending > settled).
- Folded containers must advertise hidden flow ("→N inside") or the view lies.
- Folded card color = derived container status; ▶ + thick border = frontier.
- Node label: ▶? + title + "N tasks" + "→N inside" (folded only) + "⚠ k unmet".

## Client architecture
- Every node is a CARD — compound containment boxes erase the cascade
  visually (v1's core lesson).
- Layout: tidy tree, LR cascade; depth = column; roots in a horizontal
  pipeline row. Layered-DAG engines (dagre) stack same-rank siblings
  vertically → always reads top-down. Don't reach for them.
- One source of truth for collapse state; poll = diff (structural vs
  data-only), NEVER teardown+rebuild of surviving nodes ("everything
  expanded itself" bug class).
- No empty catch in the poll loop — a silent render abort looks like a
  blank page.
- React Flow: nodes are HTML components (ends canvas-text bugs); no layout
  engine — our tree ports as a positions array.

## Process
- Verify against rendered pixels (headless Chrome), not code reading.
- Cytoscape traps (historical): data selectors take no booleans;
  classes() returns array; read ext source before guessing APIs.
