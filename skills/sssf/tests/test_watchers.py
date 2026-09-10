"""The watchers' reading of a run's exit code — three outcomes, not two.

`issue_watch.py` and `pr_watch.py` are the only two places in the factory that
see a chain's exit STATUS rather than its session record, and both used to sort
that status into pass or fail. A run stopped at a human gate is neither: it
exits 75 with its worktree intact and a person's name on it, and calling that a
failure tells the reporter the opposite of what happened while `just pending`
says the truth at the same moment.

These tests drive the real `once()` against a fake forge — the four commands
are config, so a python script standing in for `gh` is a supported deployment,
not a mock — with `_launch` replaced by a chosen exit code. Everything between
listing an issue and moving its label is the code that ships.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
CONFIG_PATH = "adws/adw_sssf_config/sssf.config.yaml"


def _load(name: str):
    """Import a watcher by path. They are `uv run` scripts, not a package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ── the fake forge ───────────────────────────────────────────────────────────

FORGE = '''\
import json, sys
from pathlib import Path
log = Path(sys.argv[1])
verb, argv = sys.argv[2], sys.argv[3:]
if verb == "list":
    print(json.dumps(json.loads(Path(sys.argv[1]).with_name("queued.json").read_text())))
else:
    entries = json.loads(log.read_text()) if log.exists() else []
    entries.append([verb, *argv])
    log.write_text(json.dumps(entries))
'''


@pytest.fixture
def forge(repo: Path, monkeypatch):
    """A repo with the factory stamped and a scriptable `gh` standing in for it.

    Returns (calls, queued) — `calls()` reads back every label edit the watcher
    made, `queued(...)` sets what the next poll will list.
    """
    monkeypatch.chdir(repo)
    adws = repo / "adws" / "adw_sssf_config"
    adws.mkdir(parents=True)
    (repo / "adws" / "adw_modules").mkdir(parents=True, exist_ok=True)

    forge_py = repo / "forge.py"
    forge_py.write_text(FORGE)
    log = repo / "edits.json"
    base = [sys.executable, str(forge_py), str(log)]

    (repo / "adws" / "adw_sssf_config" / "sssf.config.yaml").write_text(yaml.safe_dump({
        "defaults": {"harness": "fake", "model": "fake", "data_dir": "adws/adw_data"},
        "worktree": {"enabled": False},
        "observability": {"db": "adws/adw_data/sssf.db"},
        "agents": [],
        "issues": {
            "enabled": True, "project": "acme/widgets",
            "list_command": [*base, "list"],
            "state_command": [*base, "edit"],
            "comment_command": [*base, "comment"],
            "fetch_command": [*base, "view"],
            "route": {"sssf:build": "adws/adw_issue_sdlc.py"},
        },
    }))

    def queued(*numbers: int) -> None:
        (repo / "queued.json").write_text(json.dumps(
            [{"number": n, "title": f"#{n}", "author": {"login": "someone"},
              "labels": [{"name": "sssf:queued"}, {"name": "sssf:build"}]}
             for n in numbers]))

    def calls() -> list[list[str]]:
        return json.loads(log.read_text()) if log.exists() else []

    queued()
    return calls, queued


def _labels(calls, number: int) -> tuple[list[str], list[str]]:
    """(added, removed) across every edit the watcher made to one issue."""
    added, removed = [], []
    for call in calls():
        if call[0] != "edit" or str(number) not in call:
            continue
        for flag, value in zip(call, call[1:]):
            (added if flag == "--add-label" else
             removed if flag == "--remove-label" else []).append(value)
    return added, removed


# ── the third outcome ────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected", [
    (0, "sssf:done"),
    (1, "sssf:failed"),
])
def test_a_finished_run_moves_the_issue_to_its_outcome(forge, monkeypatch, code, expected):
    """The two outcomes that were always handled, unchanged by the third."""
    calls, queued = forge
    queued(42)
    watch = _load("issue_watch")
    monkeypatch.setattr(watch, "_launch", lambda *a: code)

    assert watch.once(CONFIG_PATH) == 0
    added, removed = _labels(calls, 42)
    assert added == ["sssf:running", expected]
    assert removed == ["sssf:queued", "sssf:running"]


def test_a_run_stopped_at_a_gate_is_not_called_failed(forge, monkeypatch, capsys):
    """Exit 75 is EX_TEMPFAIL — a person has not answered yet, which is not a
    verdict on the work. The issue keeps `running`: it is true, and only
    `queued` is dequeued, so nothing claims the issue twice while she decides."""
    calls, queued = forge
    queued(42)
    watch = _load("issue_watch")
    assert watch.EXIT_WAITING == 75
    monkeypatch.setattr(watch, "_launch", lambda *a: watch.EXIT_WAITING)

    assert watch.once(CONFIG_PATH) == 0
    added, removed = _labels(calls, 42)
    assert added == ["sssf:running"]          # the claim, and nothing after it
    assert removed == ["sssf:queued"]
    assert "sssf:failed" not in added

    said = capsys.readouterr().out
    assert "stopped for a human at a gate" in said
    assert "just pending" in said             # where the truth lives meanwhile


def test_a_waiting_issue_is_not_relisted_because_it_left_queued(forge, monkeypatch):
    """The claim is what keeps a suspended run from being started twice: the
    next poll lists `queued`, and this issue is not on it any more."""
    calls, queued = forge
    queued(42)
    watch = _load("issue_watch")
    monkeypatch.setattr(watch, "_launch", lambda *a: watch.EXIT_WAITING)
    watch.once(CONFIG_PATH)

    launches = []
    monkeypatch.setattr(watch, "_launch", lambda *a: launches.append(a) or 0)
    queued()                                   # the forge no longer lists #42
    assert watch.once(CONFIG_PATH) == 0
    assert launches == []


# ── the same defect in the sibling watcher ───────────────────────────────────

def test_both_watchers_read_the_same_exit_status_for_waiting():
    """A constant crossing a process boundary is a protocol, and both halves of
    it live in different files on purpose — the chain that exits is whatever
    version of the factory is stamped into the watched repo. This is what keeps
    the two halves from drifting apart silently."""
    from adw_modules import hitl
    assert _load("issue_watch").EXIT_WAITING == hitl.EXIT_WAITING
    assert _load("pr_watch").EXIT_WAITING == hitl.EXIT_WAITING


def test_a_watcher_tells_the_run_its_terminal_is_not_the_runs(repo, monkeypatch):
    """`subprocess.run` hands the child our stdin, so a watcher started by hand
    from a window would give every run it launches a TTY indistinguishable from
    the engineer's — and a gate would prompt whoever is watching the queue.
    Only the launcher knows the difference, so the launcher says so."""
    monkeypatch.chdir(repo)
    for name in ("issue_watch", "pr_watch"):
        watch = _load(name)
        seen = {}

        def fake_run(argv, cwd=None, env=None, **kwargs):
            seen["env"] = env or {}
            return type("Completed", (), {"returncode": 0})()

        monkeypatch.setattr(watch.subprocess, "run", fake_run)
        args = (("adws/adw_issue_sdlc.py", CONFIG_PATH, 42, repo) if name == "issue_watch"
                else (CONFIG_PATH, 42, repo))
        watch._launch(*args)
        assert seen["env"].get("SSSF_UNATTENDED") == "1", name


# ── a chain places the gates it advertises ───────────────────────────────────

ADWS = SKILL_ROOT / "templates" / "adws"

# Which gates each chain must PLACE, and how it must place them. This table is
# the other half of `hitl.gates:` in the config: policy decides whether a placed
# gate fires, and a gate that was never placed makes the config setting a silent
# no-op — `hitl.gates: {plan: on}` was exactly that on `adw_issue_sdlc` while its
# usage line advertised `--hitl all|none|every|plan`, and `{integrate: on}` was
# exactly that on `adw_simple_sdlc` while its docstring drew the phase. Both
# failures are invisible until an operator turns a gate on and nothing stops.
PLACED = {
    "adw_plan_build":             {"plan": "revisable"},
    "adw_plan_build_test":        {"plan": "revisable"},
    "adw_plan_build_test_quality": {"plan": "revisable"},
    "adw_simple_sdlc":            {"plan": "revisable", "integrate": "approve_or_abort"},
    "adw_issue_sdlc":             {"plan": "revisable"},
}


def _gates_in(source: str) -> dict[str, str]:
    """{gate name: "revisable" | "approve_or_abort"} for every `hitl.gated` call."""
    import ast
    found = {}
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "gated"):
            continue
        gate = next((a for a in node.args
                     if isinstance(a, ast.Call) and getattr(a.func, "id", "") == "Gate"), None)
        assert gate is not None, "hitl.gated was handed something that is not a Gate(...)"
        keywords = {k.arg: k.value for k in gate.keywords}
        name = keywords["name"].value
        found[name] = "revisable" if "call" in keywords else "approve_or_abort"
    return found


@pytest.mark.parametrize("chain,expected", sorted(PLACED.items()))
def test_a_chain_places_every_gate_it_advertises(chain, expected):
    source = (ADWS / f"{chain}.py").read_text()
    assert _gates_in(source) == expected

    # ...and says so where an operator looks for it, which is the usage line
    # they copy and the phase list they read.
    docstring = source.split('"""')[1]
    assert "--hitl" in docstring
    for gate in expected:
        assert f"approve_{gate}" in docstring or f"`{gate}` gate" in docstring, gate


# ── pr_watch: a suspended review is skipped, not re-bought ───────────────────

def test_pr_watch_skips_a_pull_request_whose_run_is_waiting(repo, monkeypatch):
    """A suspended review left its threads unresolved — the exact condition that
    launched it — so without this guard `_has_work` says yes on the very next
    poll and the whole review is re-run and re-paid for, every interval, until a
    person answers. The `failed` label cannot be the guard here: the run did not
    fail, and badging the pull request would make clearing a label the price of
    approving a plan."""
    monkeypatch.chdir(repo)
    from adw_modules import artifacts
    from adw_modules.data_types import RunState, WaitingFor

    sessions = artifacts.sessions_root(repo, "adws/adw_data")
    watch = _load("pr_watch")

    class Cfg:                          # the two attributes `_waiting_on` reads
        class defaults:
            data_dir = "adws/adw_data"
    assert watch._waiting_on(Cfg, repo, 17) == ""      # no sessions at all yet

    for adw_id, status, url in [("a1b2c3d4", "waiting", "https://forge/acme/w/pull/17"),
                                ("deadbeef", "success", "https://forge/acme/w/pull/18"),
                                ("cafed00d", "waiting", "https://forge/acme/w/pull/19")]:
        directory = sessions / adw_id
        directory.mkdir(parents=True)
        artifacts.write_run(directory, RunState(
            adw_id=adw_id, status=status, pr_url=url,
            waiting_for=WaitingFor(gate="plan", round=1) if status == "waiting" else None))

    assert watch._waiting_on(Cfg, repo, 17) == "a1b2c3d4"
    assert watch._waiting_on(Cfg, repo, 18) == ""      # finished, not waiting
    assert watch._waiting_on(Cfg, repo, 20) == ""      # nothing waiting on it
