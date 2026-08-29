import json

import pytest

from dg.cli import main
from dg.errors import DgError
from dg.model import FORMAT_VERSION, to_dict
from dg.storage import graph_path, load, save


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--title", "Demo"]) == 0
    return tmp_path


def run(*args):
    return main(list(args))


def test_full_agent_loop(repo, capsys):
    assert run("add", "setup", "Project skeleton") == 0
    assert run("add", "auth", "Auth service") == 0
    assert run("sub", "auth", "Token logic") == 0
    assert run("link", "setup", "auth") == 0

    capsys.readouterr()  # drop confirmation lines
    capsys.readouterr()
    assert run("next", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in payload["tasks"]] == ["setup"]

    assert run("done", "setup", "--note", "venv + deps ready") == 0
    capsys.readouterr()
    run("next", "--json")
    payload = json.loads(capsys.readouterr().out)
    # 'setup' done releases the gate on subtree 'auth'; its leaf is what's actionable
    assert [t["id"] for t in payload["tasks"]] == ["auth.1"]

    # container has subtasks -> must go through its leaves
    assert run("start", "auth") != 0
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert run("start", "auth.1") == 0
    assert run("done", "auth.1") == 0

    capsys.readouterr()
    assert run("validate") == 0
    assert "error" not in capsys.readouterr().out


def test_cycle_via_cli_is_actionable(repo, capsys):
    run("add", "a", "A")
    run("add", "b", "B")
    run("link", "a", "b")
    rc = run("link", "b", "a")
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "cycle detected" in err


def test_rollback_on_invalid_mutation(repo, capsys):
    """Rejected mutations must leave the stored graph untouched."""
    run("add", "a", "A")
    before = to_dict(load(repo))
    rc = run("add", "bad id!", "Broken")
    assert rc == 1
    assert to_dict(load(repo)) == before


def test_roundtrip_and_version_guard(tmp_path):
    from dg.model import Graph, Node
    g = Graph.new("rt")
    g.nodes["a"] = Node("A", deps=[], note="n1")
    save(str(tmp_path), g)
    loaded = load(str(tmp_path))
    assert loaded.title == "rt"
    assert loaded.nodes["a"].note == "n1"

    broken = to_dict(loaded)
    broken["version"] = FORMAT_VERSION + 1
    import pathlib
    p = pathlib.Path(graph_path(str(tmp_path)))
    p.write_text(json.dumps(broken))
    with pytest.raises(DgError, match="newer than this tool"):
        load(str(tmp_path))


def test_no_graph_clear_message(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = run("next")
    assert rc == 1
    assert "dg init" in capsys.readouterr().err


def test_init_guards_against_forking(repo, tmp_path, monkeypatch, capsys):
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    rc = run("init")
    assert rc == 1
    assert "fork the plan" in capsys.readouterr().err
    assert not (nested / ".dg").exists()          # nothing was created here
    assert (tmp_path / ".dg" / "graph.json").exists()


def test_commands_resolve_graph_from_subdirectories(repo, tmp_path, monkeypatch, capsys):
    run("add", "solo", "Only task")
    deep = tmp_path / "pkg" / "sub"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert run("next", "--json") == 0             # found the root graph from here
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert [t["id"] for t in payload["tasks"]] == ["solo"]
    assert run("done", "solo") == 0               # mutations too


def test_remove_lifecycle(repo, capsys):
    run("add", "ghost", "Typo'd task")
    run("add", "real", "Real task")
    run("link", "ghost", "real")
    rc = run("remove", "ghost")                   # referenced -> refused
    assert rc == 1
    assert "depended on by: real" in capsys.readouterr().err
    assert run("unlink", "ghost", "real") == 0
    assert run("remove", "ghost") == 0            # clean delete once unlinked
    assert run("validate", "--json") == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["ok"] is True


def test_json_reads(repo, capsys):
    run("add", "a", "A")
    run("add", "b", "B")
    run("link", "a", "b")
    capsys.readouterr()
    assert run("show", "--json") == 0
    doc = json.loads(capsys.readouterr().out)
    by_id = {n["id"]: n for n in doc["nodes"]}
    assert by_id["b"]["blocked_by"] == ["a"]
    assert by_id["a"]["leaf"] is True
    assert run("validate", "--json") == 0
    v = json.loads(capsys.readouterr().out)
    assert v == {"ok": True, "tasks": 2, "ready": 1}   # only 'a' is on the frontier; b gated


def test_version_reports_package_metadata(repo, capsys):
    from importlib.metadata import version
    with pytest.raises(SystemExit) as e:
        run("--version")
    assert e.value.code == 0
    out = capsys.readouterr().out.strip()
    assert version("dg") in out and "format v1" in out


def test_repeated_after_accumulates(repo):
    run("add", "a", "A")
    run("add", "b", "B")
    assert run("add", "t", "T", "--after", "a", "--after", "b") == 0
    assert run("add", "u", "U", "--after", "a,b") == 0
    nodes = load(repo).nodes
    assert nodes["t"].deps == ["a", "b"]   # regression: last --after used to win
    assert nodes["u"].deps == ["a", "b"]   # comma-list form unchanged


def test_init_leaves_plan_tracked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run("init", "--title", "G") == 0
    assert not (tmp_path / ".gitignore").exists() or ".dg/graph.json" not in (
        tmp_path / ".gitignore"
    ).read_text()                                  # plan stays tracked
