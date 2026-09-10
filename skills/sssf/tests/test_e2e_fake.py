"""A whole ADW chain, end to end, on the fake harness. No agent, no network.

This is the test the factory could not have before: every one of the bugs in
its recent history was found by paying an agent to walk a real chain, slowly
and non-deterministically. Everything around the agent — `session.ensure`, the
worktree, phases, gates, gate corrections, JSON re-prompts, envelopes,
permissions, budgets, the trace db, `run.finish` — is deterministic machinery,
and a scripted harness walks all of it in milliseconds for free.

Nothing is mocked. A real `git init` with a real worktree, the real `Tracer`
writing a real sqlite file, the real session directory. Only the coding agent
is scripted, because it is the only part that costs money and does not repeat.

`test_a_run_works_with_the_trace_db_deleted` is the invariant `1a099f0`
established and this suite must not be allowed to erode: the factory WRITES the
db and never reads it. These tests may read it to assert — that is not a
production path — and `test_no_db_reads.py` guards the other direction.
"""

from __future__ import annotations

import json
import signal
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

from adw_modules import agents, gates, session
from adw_modules.data_types import (AgentCall, BuildOutput, PhaseParams,
                                    PlanOutput, ScoutOutput)
from adw_modules.limits import AgentTimeout
from adw_modules.permissions import PermissionBreach

from conftest import git, write_prompts

CONFIG_PATH = "adws/adw_sssf_config/sssf.config.yaml"


def roster(**agent_scripts) -> dict:
    """A config whose agents are all fake, each with the script it was given.

    `agent_scripts` is {name: {"replies": [...], "writes": [...], ...}} — the
    per-agent entry minus the boilerplate every entry repeats.
    """
    hitl = agent_scripts.pop("hitl", None)
    entries = []
    for name, spec in agent_scripts.items():
        replies = spec.pop("replies", [])
        strict = spec.pop("strict", False)
        entries.append({
            "name": name,
            "harness": "fake",
            "model": "fake",
            "purpose": f"{name}, scripted",
            "prompt_engineering": {
                "system": f"adws/adw_data/prompt_engineering/{name}/system.md",
                "user": f"adws/adw_data/prompt_engineering/{name}/user.md"},
            "harness_options": {"replies": replies, "strict": strict},
            **spec,
        })
    return {
        "defaults": {"harness": "fake", "model": "fake", "data_dir": "adws/adw_data",
                     "timeout_seconds": 60,
                     "protected_files": ["adws/adw_modules/", "adws/adw_*.py"]},
        "worktree": {"enabled": True, "keep_on_success": False},
        "observability": {"db": "adws/adw_data/sssf.db"},
        "agents": entries,
        **({"hitl": hitl} if hitl else {}),
    }


@pytest.fixture
def factory(repo: Path, monkeypatch):
    """A repository with the factory's config stamped, ready to run a chain.

    Returns a callable: `factory(**agent_scripts) -> SSSFConfig`.
    """
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "argv", ["adw_test.py", "do the thing"])
    # `session.ensure` installs SIGTERM/SIGINT handlers on the process it runs
    # in — which here is pytest's. Restored afterwards so one test cannot leave
    # the next one unable to be interrupted.
    original = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def build(**agent_scripts):
        raw = roster(**agent_scripts)
        write_prompts(repo, *[a["name"] for a in raw["agents"]])
        path = repo / CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(raw))
        # Committed, like a real stamped repo: the roster and its prompts are
        # tracked files, and leaving them untracked would make every assertion
        # about a clean checkout read as dirty.
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "stamp the factory")
        return agents.load_config(str(path))

    yield build
    for sig, handler in original.items():
        signal.signal(sig, handler)


def envelope(status: str = "success", **fields) -> dict:
    return {"status": status, "summary": "scripted", **fields}


def plan_phase(run, gate_list=None, prompt="do the thing"):
    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner", retries=1,
                               description="Turn the request into a spec the "
                                           "builder can implement")) as ph:
        return ph.call(AgentCall(output_type=PlanOutput, prompt=prompt,
                                 gates=gate_list if gate_list is not None
                                 else [gates.artifacts_exist, gates.files_non_empty]))


def db_rows(repo: Path, sql: str) -> list[tuple]:
    """Read the trace db — as an ASSERTION source. Never a production path."""
    connection = sqlite3.connect(f"file:{repo / 'adws/adw_data/sssf.db'}?mode=ro", uri=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def events_of(run) -> list[dict]:
    """The session's own event log, which is what the factory itself reads."""
    path = run.session_dir / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_two_phase_chain_runs_green_end_to_end(factory, repo):
    cfg = factory(
        planner={"writes": ["specs/"], "replies": [
            {"writes": {"specs/plan.md": "# Plan\n\n1. write app.py\n"},
             "tokens": 1200, "cost": 0.02,
             "tool_calls": [{"tool": "Read", "args": {"file_path": "README.md"},
                             "ok": True, "result": "# fixture"}],
             "envelope": envelope(artifacts=["specs/plan.md"],
                                  commit_message="docs: plan")}]},
        builder={"replies": [
            {"writes": {"app.py": "print('hello')\n"}, "tokens": 3400, "cost": 0.06,
             "envelope": envelope(changed_files=["app.py"],
                                  commit_message="feat: app")}]})
    agents.validate(cfg, ["planner", "builder"])
    run = session.ensure(cfg)

    plan = plan_phase(run)
    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement the plan exactly as written")) as ph:
        build = ph.call(AgentCall(output_type=BuildOutput, prompt="build it",
                                  previous=plan, gates=[gates.diff_matches_claims]))

    assert plan.artifacts == ["specs/plan.md"]
    assert build.changed_files == ["app.py"]
    # The agents worked in the run's worktree, not the engineer's checkout.
    assert run.repo_root != repo
    assert (run.repo_root / "specs" / "plan.md").is_file()
    assert not (repo / "app.py").exists()

    exit_code = run.finish()
    assert exit_code == 0

    # The record, read the way the factory reads it: files.
    from adw_modules import artifacts
    state = artifacts.read_run(run.session_dir)
    assert state.status == "success"
    assert state.total_tokens == 4600
    assert state.total_cost == pytest.approx(0.08)
    assert [r.phase for r in artifacts.recorded_phases(run.session_dir)] == ["plan", "build"]

    # And the mirror the visualizer polls, which a run must still write.
    assert db_rows(repo, "select status from sessions")[0][0] == "success"
    assert {row[0] for row in db_rows(repo, "select name from phases")} == {"plan", "build"}
    assert db_rows(repo, "select count(*) from events where type='tool_call'")[0][0] == 1


def test_the_run_s_branch_holds_the_work_and_the_checkout_is_untouched(factory, repo):
    cfg = factory(builder={"replies": [
        {"writes": {"app.py": "x\n"}, "envelope": envelope(changed_files=["app.py"])}]})
    run = session.ensure(cfg)
    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement the change in this run's "
                                           "own tree")) as ph:
        ph.call(AgentCall(output_type=BuildOutput, prompt="build",
                          gates=[gates.diff_matches_claims]))
    assert run.workspace.branch == f"sssf/{run.adw_id}"
    assert git(repo, "status", "--porcelain") == ""
    run.finish()


# ── the gate-correction loop ─────────────────────────────────────────────────

def test_a_claim_the_tree_refutes_comes_back_as_a_correction(factory):
    """The scripted first turn claims an artifact it never wrote — which is
    exactly what a real agent does when it reports before it saves."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"envelope": envelope(artifacts=["specs/plan.md"])},              # claim only
        {"writes": {"specs/plan.md": "# Plan\n"},                         # after correction
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg)

    plan = plan_phase(run)

    assert plan.artifacts == ["specs/plan.md"]
    assert (run.repo_root / "specs" / "plan.md").is_file()
    kinds = [e["type"] for e in events_of(run)]
    assert "gate_fail" in kinds and "gate_pass" in kinds
    run.finish()


def test_the_correction_prompt_names_the_violation(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"envelope": envelope(artifacts=["specs/plan.md"])},
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg)
    plan_phase(run)
    prompt = (run.session_dir / "planner" / "prompts" / "user.md").read_text()
    assert "do the thing" in prompt          # the compiled prompt was saved
    raw = (run.session_dir / "planner" / "raw_output.jsonl").read_text()
    assert raw.count('"type": "result"') == 2      # two sends, two turns recorded
    run.finish()


def test_a_claim_that_is_never_made_true_fails_the_phase_and_the_run(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"envelope": envelope(artifacts=["specs/plan.md"])}]})   # repeated forever
    run = session.ensure(cfg)

    with pytest.raises(agents.GateFailure) as caught:
        plan_phase(run)

    assert "failed gates after" in str(caught.value)
    assert "specs/plan.md" in str(caught.value)
    from adw_modules import artifacts
    assert artifacts.read_run(run.session_dir).status == "fail"
    # A failed run keeps its worktree — that is where you go to see what happened.
    assert Path(run.repo_root).is_dir()


def test_an_escaped_artifact_path_fails_the_gate_even_though_the_file_is_real(
        factory, repo, tmp_path):
    """`c76a372` end to end: the agent really wrote the file — one level up,
    in the engineer's own checkout — and the gate must still refuse it."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"envelope": envelope(artifacts=[str(repo / "escaped.md")])}]})
    (repo / "escaped.md").write_text("# written outside the run's tree\n")
    run = session.ensure(cfg)

    with pytest.raises(agents.GateFailure) as caught:
        plan_phase(run)
    assert "outside this run's own tree" in str(caught.value)


# ── the JSON re-prompt ───────────────────────────────────────────────────────

def test_a_response_that_is_not_envelope_json_is_re_prompted_in_the_same_session(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"text": "I had some trouble and here is some prose instead."},
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg)

    plan = plan_phase(run)

    assert plan.status == "success"
    # The invalid attempt is recorded, so the trace shows what was actually said.
    invalid = db_rows(Path.cwd(), "select valid from envelopes order by rowid")
    assert [row[0] for row in invalid] == [0, 1]
    run.finish()


def test_a_response_wrapped_in_a_code_fence_still_parses(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "text": "Here you go:\n```json\n"
                 + json.dumps(envelope(artifacts=["specs/plan.md"])) + "\n```\n"}]})
    run = session.ensure(cfg)
    assert plan_phase(run).artifacts == ["specs/plan.md"]
    run.finish()


def test_an_agent_that_never_produces_valid_json_fails_the_phase(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [{"text": "prose, forever"}]})
    run = session.ensure(cfg)
    with pytest.raises(RuntimeError, match="never produced valid PlanOutput JSON"):
        plan_phase(run)


def test_an_envelope_reporting_failure_fails_the_phase(factory):
    """Rule: a parsed envelope is not the same as a successful phase."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"envelope": {"status": "fail", "summary": "I cannot do this"}}]})
    run = session.ensure(cfg)
    with pytest.raises(RuntimeError, match="reported status='fail'"):
        plan_phase(run, gate_list=[])


# ── the write boundary, inside a real chain ──────────────────────────────────

def test_a_read_only_agent_that_writes_the_repo_is_rolled_back_and_the_phase_dies(factory):
    cfg = factory(scout={"writes": [], "replies": [
        {"writes": {"src/sneaky.py": "print('I was not asked to')\n"},
         "envelope": envelope()}]})
    run = session.ensure(cfg)

    with pytest.raises(PermissionBreach) as caught:
        with run.phase(PhaseParams(name="scout", kind="agent", owner="scout",
                                   description="Find where things live and change "
                                               "nothing")) as ph:
            ph.call(AgentCall(output_type=ScoutOutput, prompt="look around"))

    assert "scout is read-only" in str(caught.value)
    assert not (run.repo_root / "src" / "sneaky.py").exists()
    kinds = [e["name"] for e in events_of(run) if e["type"] == "error"]
    assert "permission_breach" in kinds


def test_an_agent_writing_inside_its_allowlist_is_recorded_not_refused(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg)
    plan_phase(run)
    touched = [e for e in events_of(run) if e.get("name") == "paths_touched"]
    assert touched and touched[0]["payload"]["paths"] == ["specs/plan.md"]
    run.finish()


def test_a_protected_path_is_refused_even_for_an_unrestricted_agent(factory):
    cfg = factory(builder={"replies": [
        {"writes": {"adws/adw_modules/gates.py": "# rewritten by the agent it judges\n"},
         "envelope": envelope(changed_files=["adws/adw_modules/gates.py"])}]})
    run = session.ensure(cfg)
    with pytest.raises(PermissionBreach, match="barred from"):
        with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                                   description="Implement the change the plan "
                                               "describes")) as ph:
            ph.call(AgentCall(output_type=BuildOutput, prompt="build"))


# ── the two bounds ───────────────────────────────────────────────────────────

def test_a_ceiling_stops_the_next_turn_and_keeps_the_one_already_paid_for(factory):
    """`budget:` refuses the NEXT send, never the one in flight — a turn that
    has been bought keeps its envelope."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"}, "cost": 9.0,
         "envelope": envelope(artifacts=["specs/plan.md"])}]},
        builder={"replies": [{"envelope": envelope()}]})
    cfg.budget.max_cost_usd = 5.0
    run = session.ensure(cfg)

    plan = plan_phase(run)                       # bought before the ceiling was hit
    assert plan.artifacts == ["specs/plan.md"]

    from adw_modules.limits import BudgetExceeded
    with pytest.raises(BudgetExceeded) as caught:
        with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                                   description="Implement what the plan asked for")) as ph:
            ph.call(AgentCall(output_type=BuildOutput, prompt="build"))

    assert "budget.max_cost_usd" in str(caught.value)
    assert "$9.0000" in str(caught.value)


def test_the_ceiling_counts_what_an_earlier_process_in_the_session_spent(factory):
    """`--adw-id` re-entry must not hand the session a fresh wallet."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"}, "cost": 9.0,
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    first = session.ensure(cfg)
    plan_phase(first)
    first.finish()

    cfg.budget.max_cost_usd = 5.0
    second = session.ensure(cfg, adw_id=first.adw_id)     # joined, not new
    assert "budget.max_cost_usd" in second.overrun()


def test_a_turn_that_runs_past_its_clock_fails_the_phase_and_banks_what_it_cost(factory):
    cfg = factory(planner={"timeout_seconds": 1, "writes": ["specs/"], "replies": [
        {"sleep": 5, "tokens": 700, "cost": 0.4, "envelope": envelope()}]})
    run = session.ensure(cfg)

    with pytest.raises(AgentTimeout):
        plan_phase(run)

    from adw_modules import artifacts
    state = artifacts.read_run(run.session_dir)
    assert state.status == "fail"
    # A killed turn was still paid for; a ceiling that ignored it undercounts.
    assert state.total_tokens == 700 and state.total_cost == pytest.approx(0.4)
    limits_recorded = [e for e in events_of(run) if e.get("name") == "agent_timeout"]
    assert limits_recorded


def test_timeout_zero_disables_the_clock(factory):
    cfg = factory(planner={"timeout_seconds": 0, "writes": ["specs/"], "replies": [
        {"sleep": 0.05, "writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg)
    assert plan_phase(run).status == "success"
    run.finish()


# ── joining and resuming ─────────────────────────────────────────────────────

def test_a_joined_process_continues_the_phase_numbering(factory):
    """Restarting at 1 collides with the first process's envelope FILE names."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]},
        builder={"replies": [{"envelope": envelope()}]})
    first = session.ensure(cfg)
    plan_phase(first)
    first.finish()

    second = session.ensure(cfg, adw_id=first.adw_id)
    with second.phase(PhaseParams(name="build", kind="agent", owner="builder",
                                  description="Implement what the plan asked for")) as ph:
        ph.call(AgentCall(output_type=BuildOutput, prompt="build"))
    assert second.phases[0].phase_id == f"{first.adw_id}_02_build"
    second.finish()


def test_a_resumed_run_replays_its_recorded_phase_instead_of_paying_again(factory):
    """The scripted planner has ONE reply. A resume that re-ran it would consume
    a second and fail — so a green second run IS the proof it replayed."""
    cfg = factory(planner={"writes": ["specs/"], "strict": True, "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]},
        builder={"replies": [{"envelope": envelope()}]})

    first = session.ensure(cfg)
    plan_phase(first)
    first.finish()

    second = session.ensure(cfg, adw_id=first.adw_id, resume=True)
    plan = plan_phase(second)

    assert plan.artifacts == ["specs/plan.md"]
    replays = [e for e in events_of(second) if e["type"] == "replay"]
    assert replays and replays[0]["payload"]["source_phase"] == "plan"
    second.finish()


def test_a_replay_whose_gates_no_longer_hold_runs_the_agent_for_real(factory):
    """Nothing is trusted because it is old — it is trusted because its gates
    still pass."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])},
        {"writes": {"specs/plan.md": "# Plan, written again\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    first = session.ensure(cfg)
    plan_phase(first)
    first.finish()

    # The worktree was released on success and re-created from the branch, which
    # never carried the uncommitted plan — the exact situation a resume meets.
    second = session.ensure(cfg, adw_id=first.adw_id, resume=True)
    (Path(second.repo_root) / "specs" / "plan.md").unlink(missing_ok=True)

    plan_phase(second)

    assert (Path(second.repo_root) / "specs" / "plan.md").read_text() == \
        "# Plan, written again\n"
    assert [e["type"] for e in events_of(second)].count("replay") == 0
    second.finish()


# ── the invariant: the factory never reads the db ────────────────────────────

def test_a_run_works_with_the_trace_db_deleted(factory, repo):
    """`1a099f0`: every question is answered from the session directory, so a
    repository whose db was deleted to reclaim disk still runs, and still
    resumes."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]},
        builder={"replies": [{"envelope": envelope()}]})
    first = session.ensure(cfg)
    plan_phase(first)
    first.finish()

    for db_file in (repo / "adws" / "adw_data").glob("sssf.db*"):
        db_file.unlink()

    second = session.ensure(cfg, adw_id=first.adw_id)
    with second.phase(PhaseParams(name="build", kind="agent", owner="builder",
                                  description="Implement what the plan asked for")) as ph:
        ph.call(AgentCall(output_type=BuildOutput, prompt="build"))

    assert second.phases[0].phase_id == f"{first.adw_id}_02_build"   # numbering survived
    assert second.finish() == 0


# ── run.finish: the exit code, the banner and the db agree ───────────────────

def test_a_green_chain_that_was_not_accepted_exits_non_zero(factory):
    """A test phase that ran a red suite did its job; the RUN must still fail."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg)
    plan_phase(run)

    assert run.finish(accepted=False, reason="the suite came back red") == 1

    from adw_modules import artifacts
    assert artifacts.read_run(run.session_dir).status == "fail"
    assert db_rows(Path.cwd(), "select status from sessions")[0][0] == "fail"
    not_accepted = [e for e in events_of(run) if e.get("name") == "not_accepted"]
    assert not_accepted[0]["payload"]["reason"] == "the suite came back red"


def test_a_run_with_no_phases_at_all_is_not_a_success(factory):
    cfg = factory(planner={"replies": [{"envelope": envelope()}]})
    run = session.ensure(cfg)
    assert run.finish() == 1


# ── human-in-the-loop: the wait ──────────────────────────────────────────────

from adw_modules import artifacts, hitl                                    # noqa: E402
from adw_modules.data_types import Decision, Subject                       # noqa: E402


def approve_phase(run, envelope, gate="plan", round=1):
    name = f"approve_{gate}" if round == 1 else f"approve_{gate}_{round}"
    with run.phase(PhaseParams(name=name, kind="engineer", owner=run.engineer,
                               description="Hand the plan to the engineer and wait "
                                           "for a verdict")) as ph:
        return ph.decide(Subject(gate=gate, round=round, summary=envelope.summary,
                                 paths=envelope.artifacts))


def one_plan(**extra):
    return {"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}], **extra}


def test_a_gate_with_no_decision_suspends_the_run_with_exit_75(factory, repo):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="all")
    plan = plan_phase(run)

    with pytest.raises(SystemExit) as stop:
        approve_phase(run, plan)
    assert stop.value.code == hitl.EXIT_WAITING

    state = artifacts.read_run(run.session_dir)
    assert state.status == "waiting"
    assert state.waiting_for.gate == "plan"
    assert state.waiting_for.round == 1
    subject = Path(run.repo_root) / "specs" / "plan.md"
    assert state.waiting_for.paths == [str(subject)]
    assert state.waiting_for.subject_digest == hitl.digest([subject])
    assert state.pid == 0
    # The phase closed as WAITING — neither running forever nor failed.
    assert run.phases[-1].status == "waiting"
    assert db_rows(repo, "select status from sessions") == [("waiting",)]
    assert db_rows(repo, "select status from phases where name='approve_plan'") == [("waiting",)]
    assert db_rows(repo, "select count(*) from processes where ended_at is null") == [(0,)]
    # Its worktree is kept: the uncommitted plan is the subject.
    assert Path(run.repo_root).exists()


def test_a_resumed_run_reaches_the_gate_and_reads_its_decision(factory, repo):
    cfg = factory(planner=one_plan(strict=True))
    first = session.ensure(cfg, hitl="all")
    plan = plan_phase(first)
    with pytest.raises(SystemExit):
        approve_phase(first, plan)

    waiting = artifacts.read_run(first.session_dir).waiting_for
    hitl.record(first.session_dir, Decision(
        gate="plan", round=1, verdict="approve", notes="ship it", by="alice",
        channel="cli", subject_digest=waiting.subject_digest))

    second = session.ensure(cfg, adw_id=first.adw_id, resume=True, hitl="all")
    plan = plan_phase(second)                      # replayed: the strict planner has ONE reply
    decision = approve_phase(second, plan)

    assert decision.approved and decision.by == "alice"
    assert artifacts.read_run(second.session_dir).waiting_for is None
    assert "decision" in [e["type"] for e in events_of(second)]
    assert second.finish() == 0
    assert db_rows(repo, "select status from sessions") == [("success",)]


def test_a_decision_for_a_changed_subject_is_refused(factory):
    cfg = factory(planner=one_plan())
    first = session.ensure(cfg, hitl="all")
    plan = plan_phase(first)
    with pytest.raises(SystemExit):
        approve_phase(first, plan)
    hitl.record(first.session_dir, Decision(
        gate="plan", round=1, verdict="approve", by="alice", channel="cli",
        subject_digest="not-the-plan-that-was-shown"))

    second = session.ensure(cfg, adw_id=first.adw_id, resume=True, hitl="all")
    plan = plan_phase(second)
    with pytest.raises(SystemExit) as stop:             # waits again, and says why
        approve_phase(second, plan)
    assert stop.value.code == hitl.EXIT_WAITING
    notes = [e["payload"].get("message", "") for e in events_of(second) if e["type"] == "log"]
    assert any("stale" in note for note in notes)


def test_an_attended_run_asks_in_place(factory):
    """The terminal path, with the prompt scripted instead of a TTY."""
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("approve", "looks right")     # what a keypress returns
    plan = plan_phase(run)
    decision = approve_phase(run, plan)
    assert decision.approved and decision.channel == "terminal"
    assert decision.by == run.engineer and decision.notes == "looks right"
    assert hitl.read_decision(run.session_dir, "plan", 1) == decision
    assert artifacts.read_run(run.session_dir).waiting_for is None
    assert run.finish() == 0


def test_an_attended_run_honours_a_decision_written_from_another_terminal(factory):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="all")

    def someone_else_answers(waiting):
        hitl.record(run.session_dir, Decision(gate="plan", round=1, verdict="approve",
                                              by="bob", channel="cli",
                                              subject_digest=waiting.subject_digest))
        return "", ""                                    # this keypress poll saw nothing
    run.hitl.ask = someone_else_answers
    decision = approve_phase(run, plan_phase(run))
    assert decision.by == "bob"


def test_an_attended_detach_suspends(factory):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("detach", "")
    plan = plan_phase(run)
    with pytest.raises(SystemExit) as stop:
        approve_phase(run, plan)
    assert stop.value.code == hitl.EXIT_WAITING
    assert artifacts.read_run(run.session_dir).status == "waiting"


def test_notify_command_runs_on_suspend_with_the_subject_on_stdin(factory, repo):
    log = repo / "notified.json"
    cfg = factory(planner=one_plan(),
                  hitl={"notify_command": [sys.executable, "-c",
                                           f"import sys; open({str(log)!r}, 'w').write(sys.stdin.read())"]})
    run = session.ensure(cfg, hitl="all")
    with pytest.raises(SystemExit):
        approve_phase(run, plan_phase(run))
    assert json.loads(log.read_text())["gate"] == "plan"


# ── human-in-the-loop: the loop ──────────────────────────────────────────────

from adw_modules.data_types import Gate                                    # noqa: E402


def plan_gate(prompt="do the thing"):
    return Gate(name="plan", owner="planner",
                call=AgentCall(output_type=PlanOutput, prompt=prompt,
                               gates=[gates.artifacts_exist, gates.files_non_empty]))


def test_a_trusted_gate_records_a_policy_approval_and_opens_no_phase(factory):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg)                          # config default: off
    plan = hitl.gated(run, plan_gate(), plan_phase(run))
    assert plan.artifacts == ["specs/plan.md"]
    assert [p.params.name for p in run.phases] == ["plan"]
    recorded = hitl.read_decision(run.session_dir, "plan", 1)
    assert recorded.by == "policy" and recorded.channel == "auto" and recorded.approved
    assert recorded.subject_digest == hitl.digest([Path(run.repo_root) / "specs/plan.md"])
    assert run.finish() == 0


def test_a_rejection_revises_in_the_same_session_and_asks_again(factory):
    """The planner has TWO scripted replies in ONE session: the plan, then the
    revision. A revise that started a fresh session would find no second reply."""
    cfg = factory(planner={"writes": ["specs/"], "strict": True, "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"], summary="v1")},
        {"writes": {"specs/plan.md": "# Plan, split migration\n"},
         "envelope": envelope(artifacts=["specs/plan.md"], summary="v2")}]})
    run = session.ensure(cfg, hitl="all")
    answers = iter([("reject", "split the migration"), ("approve", "")])
    run.hitl.ask = lambda waiting: next(answers)

    plan = hitl.gated(run, plan_gate(), plan_phase(run))

    assert plan.summary == "v2"
    assert [p.params.name for p in run.phases] == [
        "plan", "approve_plan", "plan_revise_1", "approve_plan_2"]
    assert all(p.status == "success" for p in run.phases)
    # The revise prompt carried the human's words, through previous_envelope.
    sent = (run.session_dir / "planner" / "prompts" / "user.md").read_text()
    assert "split the migration" in sent and '"verdict": "reject"' in sent
    assert hitl.read_decision(run.session_dir, "plan", 2).approved
    assert run.finish() == 0


def test_approve_with_remarks_carries_them_to_the_next_agent(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"],
                              notes_for_next_agent="use the existing helper")}]})
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("approve", "keep the migration reversible")
    plan = hitl.gated(run, plan_gate(), plan_phase(run))
    assert "use the existing helper" in plan.notes_for_next_agent
    assert "keep the migration reversible" in plan.notes_for_next_agent


def test_an_abort_ends_the_run_as_not_accepted(factory, repo):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("abort", "wrong feature")
    with pytest.raises(SystemExit) as stop:
        hitl.gated(run, plan_gate(), plan_phase(run))
    assert stop.value.code not in (0, hitl.EXIT_WAITING)
    assert run.phases[-1].params.name == "approve_plan"
    assert run.phases[-1].status == "fail"
    assert "wrong feature" in run.phases[-1].error
    assert db_rows(repo, "select status from sessions") == [("fail",)]
    assert artifacts.read_run(run.session_dir).status == "fail"
    assert Path(run.repo_root).exists()                # a failed run keeps its tree


def test_a_gate_without_a_revise_target_cannot_be_rejected(factory):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("reject", "no")
    with pytest.raises(SystemExit):
        hitl.gated(run, Gate(name="integrate"), plan_phase(run))
    assert "approve/abort" in run.phases[-1].error


def test_max_rounds_bounds_the_loop_when_set(factory):
    cfg = factory(planner=one_plan(), hitl={"default": "on", "max_rounds": 1})
    run = session.ensure(cfg)
    run.hitl.ask = lambda waiting: ("reject", "again")
    with pytest.raises(SystemExit):
        hitl.gated(run, plan_gate(), plan_phase(run))
    assert "max_rounds" in run.phases[-1].error


def test_a_suspended_reject_round_resumes_into_the_revise_phase(factory):
    """The whole loop across two processes: suspend, reject from the CLI, resume —
    the plan replays, the revise phase runs live, the second gate suspends."""
    cfg = factory(planner={"writes": ["specs/"], "strict": True, "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"], summary="v1")},
        {"writes": {"specs/plan.md": "# Plan 2\n"},
         "envelope": envelope(artifacts=["specs/plan.md"], summary="v2")}]})
    first = session.ensure(cfg, hitl="all")
    with pytest.raises(SystemExit):
        hitl.gated(first, plan_gate(), plan_phase(first))
    waiting = artifacts.read_run(first.session_dir).waiting_for
    hitl.record(first.session_dir, Decision(gate="plan", round=1, verdict="reject",
                                            notes="more", by="alice", channel="cli",
                                            subject_digest=waiting.subject_digest))

    second = session.ensure(cfg, adw_id=first.adw_id, resume=True, hitl="all")
    with pytest.raises(SystemExit) as stop:
        hitl.gated(second, plan_gate(), plan_phase(second))
    assert stop.value.code == hitl.EXIT_WAITING
    assert [p.params.name for p in second.phases] == [
        "plan", "approve_plan", "plan_revise_1", "approve_plan_2"]
    assert [p.status for p in second.phases] == ["success", "success", "success", "waiting"]
    assert artifacts.read_run(second.session_dir).waiting_for.round == 2
    assert (Path(second.repo_root) / "specs/plan.md").read_text() == "# Plan 2\n"


# ── human-in-the-loop: --hitl every ──────────────────────────────────────────

def test_hitl_every_checkpoints_after_each_agent_phase(factory):
    cfg = factory(planner=one_plan(), builder={"replies": [{"envelope": envelope()}]})
    run = session.ensure(cfg, hitl="every")
    asked = []
    run.hitl.ask = lambda waiting: asked.append(waiting.gate) or ("approve", "")

    plan_phase(run)
    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement what the plan asked for")) as ph:
        ph.call(AgentCall(output_type=BuildOutput, prompt="build"))

    assert asked == ["plan", "build"]
    assert [p.params.name for p in run.phases] == [
        "plan", "approve_plan", "build", "approve_build"]
    assert run.finish() == 0


def test_hitl_every_cannot_revise_so_a_reject_at_a_checkpoint_aborts(factory):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="every")
    run.hitl.ask = lambda waiting: ("reject", "again")
    with pytest.raises(SystemExit):
        plan_phase(run)
    assert "approve/abort" in run.phases[-1].error


def test_hitl_every_shares_its_approval_with_a_gate_the_adw_placed(factory):
    """The checkpoint after `plan` and the gate `gated()` opens are the SAME gate,
    so one ask serves both: the placed gate finds the checkpoint's approval."""
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="every")
    asked = []
    run.hitl.ask = lambda waiting: asked.append(waiting.gate) or ("approve", "")
    plan = hitl.gated(run, plan_gate(), plan_phase(run))
    assert asked == ["plan"]
    assert [p.params.name for p in run.phases] == ["plan", "approve_plan"]
    assert plan.artifacts == ["specs/plan.md"]


def test_hitl_every_checkpoint_that_suspends_leaves_the_agent_phase_green(factory):
    cfg = factory(planner=one_plan())
    run = session.ensure(cfg, hitl="every")
    run.hitl.ask = lambda waiting: ("detach", "")
    with pytest.raises(SystemExit) as stop:
        plan_phase(run)
    assert stop.value.code == hitl.EXIT_WAITING
    assert [(p.params.name, p.status) for p in run.phases] == [
        ("plan", "success"), ("approve_plan", "waiting")]
