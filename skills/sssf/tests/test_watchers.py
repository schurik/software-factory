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


# ── the justfile and the scripts it calls are a contract ────────────────────

JUSTFILE = SKILL_ROOT / "templates" / "justfile"


def _recipe_lines(script: str) -> list[list[str]]:
    """Every `templates/justfile` invocation of one script, as argv.

    The recipe's `{{...}}` placeholders are substituted with values of the shape
    `just` would pass. `{{ARGS}}` is the operator's own tail and is dropped —
    it is whatever they typed, not something the template promises.
    """
    argv = []
    for line in JUSTFILE.read_text().splitlines():
        if f"scripts/{script}.py" not in line or line.lstrip().startswith("#"):
            continue
        tail = line.split(f"scripts/{script}.py", 1)[1]
        tail = (tail.replace("{{config}}", "adws/adw_sssf_config/sssf.config.yaml")
                    .replace("{{ADW_ID}}", "a6d783e4")
                    .replace("{{NUMBER}}", "42")
                    .replace("{{ARGS}}", ""))
        assert "{{" not in tail, f"unsubstituted placeholder in: {line}"
        argv.append(tail.split())
    return argv


def test_every_hitl_recipe_in_the_justfile_actually_parses():
    """The justfile writes `--config` AFTER the subcommand on all five recipes,
    and argparse binds a parent-level option only BEFORE one — so every verdict
    exited 2 with `unrecognized arguments` on stderr before reaching
    `hitl.answer`. `just approve` printed nothing and the run stayed waiting,
    which is a worse failure than a traceback: a gate that cannot be answered
    through the documented interface cannot be answered at all.

    The template is generated into every stamped repo and the script is vendored
    beside it, so the two are a contract with nothing else checking it."""
    hitl_cli = _load("hitl")
    recipes = _recipe_lines("hitl")
    assert len(recipes) == 5, f"expected pending/show/approve/reject/abort, got {recipes}"

    for argv in recipes:
        parsed = hitl_cli._parser().parse_args(argv)      # SystemExit(2) if it cannot
        assert parsed.config == "adws/adw_sssf_config/sssf.config.yaml", argv


@pytest.mark.parametrize("argv,expected", [
    (["pending"], "adws/adw_sssf_config/sssf.config.yaml"),        # neither side
    (["pending", "--config", "after.yaml"], "after.yaml"),         # the justfile's order
    (["--config", "before.yaml", "pending"], "before.yaml"),       # the documented order
    (["approve", "a6d783e4", "--config", "after.yaml"], "after.yaml"),
    (["--config", "before.yaml", "approve", "a6d783e4"], "before.yaml"),
])
def test_config_is_accepted_on_both_sides_of_the_subcommand(argv, expected):
    """Both orders, because both are written: the justfile puts the flag after
    the subcommand and the module docstring and `--help` put it before.

    The `--config BEFORE <verb>` cases are the ones a `parents=[common]` fix
    silently breaks — argparse copies a subcommand's namespace back over the
    outer one, so a shared or defaulted copy overwrites what the operator typed
    with the default. That is a wrong config read without a word said, which is
    worse than the exit 2 this replaces."""
    assert _load("hitl")._parser().parse_args(argv).config == expected


def test_no_other_script_grew_subcommands_without_the_same_treatment():
    """A canary, not a style rule. Every other script under `scripts/` takes its
    options on one flat parser, where argparse accepts them in any position and
    the justfile's ordering cannot bite. The moment one grows `add_subparsers`
    it inherits the bug fixed above, and it needs `_config_on` plus a line in
    `test_every_hitl_recipe_in_the_justfile_actually_parses`."""
    with_subcommands = sorted(path.stem for path in SCRIPTS.glob("*.py")
                              if "add_subparsers" in path.read_text())
    assert with_subcommands == ["hitl"]


# ── the four states are mutually exclusive ──────────────────────────────────

def test_a_requeued_issue_does_not_keep_the_previous_run_s_verdict(forge, monkeypatch):
    """`queued` is put back BY HAND — it is the documented recovery for a failed
    or crashed run. Each flip used to remove exactly the one label it had just
    put on, so the `failed` from the first run survived the second and the issue
    ended up carrying `done` and `failed` at once, with nothing saying which run
    either belonged to. Seen in the wild on a real tracker."""
    calls, queued = forge
    watch = _load("issue_watch")

    queued(68)
    monkeypatch.setattr(watch, "_launch", lambda *a: 1)          # red
    watch.once(CONFIG_PATH)
    assert _labels(calls, 68)[0] == ["sssf:running", "sssf:failed"]

    # A human re-queues it; the forge now lists it carrying the stale verdict.
    Path("queued.json").write_text(json.dumps(
        [{"number": 68, "title": "#68", "author": {"login": "someone"},
          "labels": [{"name": "sssf:queued"}, {"name": "sssf:build"},
                     {"name": "sssf:failed"}]}]))
    monkeypatch.setattr(watch, "_launch", lambda *a: 0)          # green
    watch.once(CONFIG_PATH)

    added, removed = _labels(calls, 68)
    assert added == ["sssf:running", "sssf:failed", "sssf:running", "sssf:done"]
    # The claim cleared the stale verdict in the same edit that took the issue.
    assert removed == ["sssf:queued", "sssf:running",
                       "sssf:failed", "sssf:queued", "sssf:running"]
    net = {label for label in added if added.count(label) > removed.count(label)}
    assert net == {"sssf:done"}, f"the issue is left carrying {sorted(net)}"


def test_the_routing_label_is_never_removed(forge, monkeypatch):
    """It is the authorization a human applied and the record of which chain was
    asked for — not a state, and deliberately co-resident with one."""
    calls, queued = forge
    queued(42)
    watch = _load("issue_watch")
    monkeypatch.setattr(watch, "_launch", lambda *a: 0)
    watch.once(CONFIG_PATH)
    assert "sssf:build" not in _labels(calls, 42)[1]


def test_the_claim_only_removes_labels_the_issue_actually_carries(forge, monkeypatch):
    """A speculative `--remove-label` is not free: `gh issue edit` errors on a
    name the repository never defined, and the claim would fail with it. Only
    what the listing showed may be removed."""
    calls, queued = forge
    queued(42)                       # carries queued + build, no terminal state
    watch = _load("issue_watch")
    monkeypatch.setattr(watch, "_launch", lambda *a: 0)
    watch.once(CONFIG_PATH)

    claim = [call for call in calls() if call[0] == "edit"][0]
    assert claim.count("--remove-label") == 1        # `queued`, and nothing invented
    assert "sssf:done" not in claim and "sssf:failed" not in claim


# ── the process that ends a run owns its label ──────────────────────────────

def _run_state(**fields):
    from adw_modules.data_types import RunState
    return RunState(**{"adw_id": "a6d783e4", "status": "waiting", **fields})


@pytest.fixture
def resumable(forge, monkeypatch):
    """A session that a watcher claimed and that then stopped at a gate.

    Returns (calls, land) — `land(code)` is `resume._land_label` for a run that
    ended with that exit status, against the same fake forge the watcher used.
    """
    calls, _ = forge
    resume = _load("resume")
    from adw_modules import agents
    cfg = agents.load_config(CONFIG_PATH)

    def land(code: int, **overrides):
        fields = {"trigger": "issue", "issue_number": 68,
                  "issue_project": "acme/widgets", **overrides}
        resume._land_label(cfg, Path.cwd(), _run_state(**fields), code)
    return calls, land


@pytest.mark.parametrize("code,expected", [(0, "sssf:done"), (1, "sssf:failed")])
def test_an_answered_run_lands_its_label_where_it_actually_ended(resumable, code, expected):
    """The watcher launched this run, saw exit 75 and correctly left the issue
    on `running` — but `just approve` brings the run back in a process the
    watcher never sees, and that is where it finishes. Before this, nothing
    moved the label afterwards and an answered issue sat on `running` forever."""
    calls, land = resumable
    land(code)
    added, removed = _labels(calls, 68)
    assert added == [expected]
    assert removed == ["sssf:running"]      # only what the claim put there


def test_a_run_that_stopped_at_the_NEXT_gate_keeps_its_claim(resumable):
    """A reject sends the artifact back and the run asks again, so exit 75 the
    second time means the same thing it meant the first: still claimed, still
    waiting, nothing to land."""
    calls, land = resumable
    land(75)
    assert _labels(calls, 68) == ([], [])


@pytest.mark.parametrize("overrides", [
    {"trigger": "engineer", "issue_number": 0, "issue_project": ""},   # not an issue run
    {"trigger": "pr_review", "issue_number": 0, "issue_project": ""},  # a review run
    {"trigger": "issue", "issue_number": 0},                           # too old to know
])
def test_only_an_issue_run_that_knows_its_number_touches_the_tracker(resumable, overrides):
    """`issue_number` is 0 for a session recorded before this field existed, and
    a resume of one must be a no-op rather than a guess — the url is not a
    number, and a tracker that is not the forge does not spell it the same way."""
    calls, land = resumable
    land(0, **overrides)
    assert calls() == []


def test_a_tracker_that_cannot_be_reached_does_not_change_the_run_s_outcome(forge, monkeypatch, capsys):
    """The run's exit code is a fact about the work; an unreachable forge is a
    fact about the network. Raising here would tell an engineer their build
    broke because a label did not move — so it says so and returns."""
    calls, _ = forge
    resume = _load("resume")
    from adw_modules import agents, issues as issues_module
    cfg = agents.load_config(CONFIG_PATH)

    def down(*_args, **_kwargs):
        raise OSError("forge unreachable")
    monkeypatch.setattr(issues_module, "set_state", down)

    resume._land_label(cfg, Path.cwd(), _run_state(trigger="issue", issue_number=68), 0)
    assert calls() == []                              # nothing reached the tracker
    assert "move it by hand" in capsys.readouterr().out


def test_a_forge_that_refuses_the_edit_is_reported_not_swallowed(resumable, capsys):
    """A `gh` that ran and said no is different from one that could not be run,
    and an operator has to know the label is now theirs to move."""
    calls, land = resumable
    Path("forge.py").write_text("import sys; sys.exit(1)")     # the forge refuses
    land(0)
    said = capsys.readouterr().out
    assert "label not moved to sssf:done" in said and "move it by hand" in said


def test_the_label_machine_is_left_alone_when_the_tracker_workflow_is_off(forge, monkeypatch):
    """`just issue 42` by hand on a repo that never turned the watcher on is a
    run against a tracker keeping no sssf state; inventing some would be this
    script deciding a workflow the config declined."""
    calls, _ = forge
    resume = _load("resume")
    from adw_modules import agents
    cfg = agents.load_config(CONFIG_PATH)
    cfg.issues.enabled = False
    resume._land_label(cfg, Path.cwd(), _run_state(trigger="issue", issue_number=68), 0)
    assert calls() == []


# ── the seam: a session recorded before run.json carried the issue number ────

def test_a_session_older_than_the_field_is_backfilled_by_its_own_resume(forge, monkeypatch):
    """The set of runs already suspended at a gate when this shipped is exactly
    the set whose `run.json` has no `issue_number` — so if the label could only
    be landed from the pre-run snapshot, the feature would decline the runs that
    needed it most.

    It does not, because the resumed chain rewrites the field on its way past:
    `adw_issue_sdlc`'s issue phase is `kind="code"`, `replay.py` replays only
    agent phases, so the fetch runs for real and `Run.record_issue` records the
    number and project again. `relaunch` therefore re-reads `run.json` AFTER the
    subprocess rather than deciding from the snapshot it took before it.
    """
    from adw_modules import artifacts
    from adw_modules.data_types import RunState, WaitingFor
    calls, _ = forge
    resume = _load("resume")

    sessions = artifacts.sessions_root(Path.cwd(), "adws/adw_data")
    session_dir = sessions / "a6d783e4"
    session_dir.mkdir(parents=True)
    # Exactly the keys such a run.json has: trigger and issue_url, no number.
    artifacts.write_run(session_dir, RunState(
        adw_id="a6d783e4", status="waiting", pid=0, trigger="issue",
        issue_url="https://forge/acme/widgets/issues/68",
        command=["adw_issue_sdlc.py", "68"],
        waiting_for=WaitingFor(gate="plan", round=1, subject_digest="abc")))
    assert artifacts.read_run(session_dir).issue_number == 0

    # The decision the human wrote, so relaunch does not refuse to resume.
    from adw_modules.data_types import Decision
    from adw_modules import hitl as hitl_module
    hitl_module.record(session_dir, Decision(gate="plan", round=1, verdict="approve",
                                             by="alice", channel="cli",
                                             subject_digest="abc"))

    # `subprocess` is one shared module object, so intercept only the chain's
    # own argv and hand everything else (git_helper's calls) to the real one.
    real_run = resume.subprocess.run

    def resumed_chain(argv, **kwargs):
        """What the re-run `issue` code phase does: record the issue again."""
        if "adws/adw_issue_sdlc.py" not in argv:
            return real_run(argv, **kwargs)
        artifacts.update_run(session_dir, issue_number=68, issue_project="acme/widgets")
        return type("Completed", (), {"returncode": 0})()
    monkeypatch.setattr(resume.subprocess, "run", resumed_chain)

    assert resume.relaunch("a6d783e4", CONFIG_PATH) == 0
    added, removed = _labels(calls, 68)
    assert added == ["sssf:done"], "the pre-field session was declined"
    assert removed == ["sssf:running"]
