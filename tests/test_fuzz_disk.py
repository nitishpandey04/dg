"""Differential e2e fuzz: CLI argv sequences on disk must agree with the
pure ops model, and persistence must be exact.

For every generated command we predict the outcome on an in-memory shadow
graph using the ops API through fs.try_op, then assert:
  - success => exit 0, message on captured stdout, load() equals ops result
  - typed rejection => exit 1, stderr starts with 'error: ', graph.json
    byte-identical to beforehand (rejected ops never persist)
Read-model commands (next/validate --json) must keep their frozen shapes on
any reachable state.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import random
import tempfile

import fuzz_support as fs
from hypothesis import given, settings
from hypothesis import strategies as st

from dg import cli, ops
from dg.model import Graph, id_sort_key
from dg.storage import graph_path, load

ROOTS = ("a", "b", "c")


@st.composite
def _commands(draw):
    """Grow-ish head, lifecycle middle; interleaved wildcards."""
    n = draw(st.integers(3, 18))
    out = []
    for idx in range(n):
        grow_verbs = ("add-root", "add-child", "link")
        run_verbs = ("start", "finish", "cancel", "next-json", "validate-json")
        verb = draw(st.sampled_from(grow_verbs if idx < n // 2 else run_verbs))
        out.append(
            {
                "verb": verb,
                "i": draw(st.integers(0, 7)),
                "wild": draw(st.booleans()),
                "note": draw(
                    st.one_of(
                        st.none(),
                        st.text(min_size=1, max_size=12).filter(lambda s: not s.startswith("-")),
                    )
                ),
            }
        )
    return out


def _capture_main(argv):
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = cli.main(argv)
    return rc, out_buf.getvalue(), err_buf.getvalue()


class _DiskSession:
    """One temporary repo running real CLI calls against a shadow model."""

    def __init__(self, tmp: str):
        self.prev_cwd = os.getcwd()
        os.chdir(tmp)
        self.g = Graph.new("fuzz-disk")  # shadow start-state (pre-init-disk)
        rc, _, err = _capture_main(["init", "--title", "fuzz-disk"])
        assert rc == 0 and err == "", (rc, err)

    def close(self):
        os.chdir(self.prev_cwd)

    @staticmethod
    def bytes_now(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    # ------------------------------------------------------------- checks

    def expect_mutation(self, argv, opfn, *args):
        gp_before = self.bytes_now(graph_path("."))
        ok, new_g = fs.try_op(opfn, self.g, *args)
        rc, out, err = _capture_main(argv)
        if not ok:
            assert rc == 1, (argv, rc, out)
            assert err.startswith("error: "), (argv, err)
            assert self.bytes_now(graph_path(".")) == gp_before, f"graph persisted a rejection: {argv}"
            return
        assert rc == 0, (argv, rc, err)
        assert out.strip(), f"success without feedback line: {argv}"
        loaded = load(".")
        assert loaded == new_g, f"disk diverged from ops model after {argv}"
        self.g = new_g

    def expect_next_shape(self):
        rc, out, err = _capture_main(["next", "--json"])
        assert rc == 0, err
        payload = json.loads(out)
        assert set(payload) == {"tasks"}
        wanted = [{"id": i, "title": self.g.nodes[i].title} for i in ops.frontier(self.g)]
        assert payload["tasks"] == wanted

    def expect_validate_shape(self):
        rc, out, err = _capture_main(["validate", "--json"])
        assert rc == 0, (out, err)
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["tasks"] == len(self.g.nodes)
        assert payload["ready"] == len(ops.frontier(self.g))

    # ----------------------------------------------------------- plan run

    def _leaf_pred(self, nid):
        return not any(k.startswith(nid + ".") for k in self.g.nodes)

    def run_plan(self, plan):
        for step in plan:
            verb, i, wild, note = step["verb"], step["i"], step["wild"], step["note"]
            rng = random.Random(i * 31 + len(plan))
            live = sorted(self.g.nodes, key=id_sort_key)

            if verb == "add-root":
                missing = [r for r in ROOTS if r not in live]
                if not missing and not wild:
                    continue
                name = rng.choice(missing or ROOTS)
                use = [] if not wild or not live else [rng.choice(live)]
                argv = ["add", name, f"title {name}"]
                if use:
                    argv += ["--after", ",".join(use)]
                self.expect_mutation(argv, ops.add_task, name, f"title {name}", use)
            elif verb == "add-child":
                if not live:
                    continue
                p = rng.choice(live)
                kids = [k for k in live
                        if k.startswith(p + ".") and "." not in k[len(p) + 1:]]
                n = 1
                while f"{p}.{n}" in self.g.nodes:
                    n += 1
                use = [rng.choice(kids)] if kids else []
                argv = ["sub", p, f"t-{p}.{n}"]
                if use:
                    argv += ["--after", ",".join(use)]
                self.expect_mutation(argv, ops.sub_task, p, f"t-{p}.{n}", use)
            elif verb == "link":
                if len(live) < 2:
                    continue
                src = rng.choice(live)
                sibs = [s for s in live
                        if s != src and s.rsplit(".", 1)[0] == src.rsplit(".", 1)[0]]
                dst = rng.choice(sibs if sibs and not wild else live)
                self.expect_mutation(["link", src, dst], ops.link, src, dst)
            elif verb == "start":
                if not live:
                    continue
                ready_leaves = [
                    k for k in live
                    if self._leaf_pred(k)
                    and self.g.nodes[k].status == "pending"
                    and not ops.blocked_by(self.g, k)
                ]
                target = (
                    rng.choice(ready_leaves) if ready_leaves and not wild
                    else rng.choice(live)
                )
                self.expect_mutation(["start", target], ops.start_task, target)
            elif verb == "finish":
                if not live:
                    continue
                target = rng.choice(live)
                argv = ["done", target]
                if note:
                    argv += ["--note", note]
                self.expect_mutation(argv, ops.finish_task, target, note)
            elif verb == "cancel":
                if not live:
                    continue
                target = rng.choice(live)
                self.expect_mutation(["cancel", target], ops.cancel_task, target)
            elif verb == "next-json":
                self.expect_next_shape()
            elif verb == "validate-json":
                self.expect_validate_shape()


@settings(deadline=None, max_examples=50)
@given(_commands())
def test_cli_disk_matches_ops_model(plan):
    with tempfile.TemporaryDirectory(prefix="dg-fuzz-") as tmp:
        sess = _DiskSession(tmp)
        try:
            sess.run_plan(plan)
        finally:
            sess.close()
