"""The decision record: what a human said, about exactly which artifact.

Files only. `hitl.py` never touches the db — `test_no_db_reads.py` keeps it so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adw_modules import artifacts, hitl
from adw_modules.data_types import Decision, RunState, WaitingFor


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "sessions" / "a1b2c3d4"
    directory.mkdir(parents=True)
    artifacts.write_run(directory, RunState(adw_id="a1b2c3d4", status="running", pid=0))
    return directory


# ── the digest ───────────────────────────────────────────────────────────────

def test_digest_covers_content_and_is_independent_of_listing_order(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("plan\n")
    b.write_text("diff\n")
    assert hitl.digest([a, b]) == hitl.digest([b, a])
    before = hitl.digest([a, b])
    a.write_text("plan, revised\n")
    assert hitl.digest([a, b]) != before


def test_digest_of_a_missing_file_is_stable_and_distinct(tmp_path):
    present = tmp_path / "a.md"
    present.write_text("x")
    missing = tmp_path / "gone.md"
    assert hitl.digest([present, missing]) == hitl.digest([present, missing])
    assert hitl.digest([present, missing]) != hitl.digest([present])


# ── the decision ─────────────────────────────────────────────────────────────

def test_a_decision_is_an_envelope_the_next_agent_can_read():
    decision = Decision(gate="plan", round=2, verdict="reject",
                        notes="split the migration", by="alice", channel="cli")
    assert decision.status == "success"           # the DECISION happened
    assert decision.approved is False
    assert "alice" in decision.summary and "reject" in decision.summary
    assert decision.notes_for_next_agent == "split the migration"


def test_record_and_read_round_trip(session_dir):
    decision = Decision(gate="plan", round=1, verdict="approve", by="alice",
                        channel="cli", subject_digest="abc")
    path = hitl.record(session_dir, decision)
    assert path == session_dir / "decisions" / "plan_1.json"
    assert hitl.read_decision(session_dir, "plan", 1) == decision
    assert hitl.read_decision(session_dir, "plan", 2) is None


# ── the waiting run ──────────────────────────────────────────────────────────

def test_suspend_and_clear_write_run_json(session_dir):
    waiting = WaitingFor(gate="plan", round=1, phase_id="a1b2c3d4_03_approve_plan",
                         subject_digest="abc", paths=["specs/plan.md"],
                         summary="a plan", since="2026-09-10T00:00:00Z")
    artifacts.suspend_run(session_dir, waiting)
    state = artifacts.read_run(session_dir)
    assert state.status == "waiting"
    assert state.waiting_for == waiting
    assert state.ended_at == ""                    # waiting is not ended
    assert state.pid == 0                          # nothing of it is alive

    artifacts.clear_waiting(session_dir)
    state = artifacts.read_run(session_dir)
    assert state.waiting_for is None
    assert state.status == "waiting"               # status is the RUN's to change


def test_waiting_sessions_lists_only_what_waits(tmp_path, session_dir):
    other = tmp_path / "sessions" / "deadbeef"
    other.mkdir()
    artifacts.write_run(other, RunState(adw_id="deadbeef", status="success"))
    artifacts.suspend_run(session_dir, WaitingFor(gate="plan", round=1))
    assert list(artifacts.waiting_sessions(tmp_path / "sessions")) == ["a1b2c3d4"]


def test_a_new_process_inherits_what_the_session_waits_for(session_dir):
    """`start_run` carries `waiting_for` forward the way it carries provenance:
    the resumed process must reach the gate knowing what it asked about."""
    artifacts.suspend_run(session_dir, WaitingFor(gate="plan", round=1, subject_digest="abc"))
    artifacts.start_run(session_dir, RunState(adw_id="a1b2c3d4", status="running", pid=42))
    state = artifacts.read_run(session_dir)
    assert state.status == "running" and state.pid == 42
    assert state.waiting_for.subject_digest == "abc"


# ── the policy ───────────────────────────────────────────────────────────────

from adw_modules.data_types import HitlConfig                            # noqa: E402
from adw_modules.hitl import HitlPolicy                                  # noqa: E402


def test_policy_default_off_means_every_gate_is_auto():
    policy = HitlPolicy(HitlConfig())
    assert policy.mode("plan", "engineer") == "auto"
    assert policy.every is False


def test_policy_gate_entries_win_over_default():
    policy = HitlPolicy(HitlConfig(default="off", gates={"plan": "on"}))
    assert policy.mode("plan", "engineer") == "on"
    assert policy.mode("build", "engineer") == "auto"
    policy = HitlPolicy(HitlConfig(default="on", gates={"build": "off"}))
    assert policy.mode("plan", "engineer") == "on"
    assert policy.mode("build", "engineer") == "auto"


@pytest.mark.parametrize("override,plan,build,every", [
    ("all", "on", "on", False),
    ("none", "auto", "auto", False),
    ("plan", "on", "auto", False),
    ("plan,build", "on", "on", False),
    ("every", "on", "on", True),
    ("", "on", "auto", False),           # no override: the config decides
])
def test_policy_override_forms(override, plan, build, every):
    policy = HitlPolicy(HitlConfig(gates={"plan": "on"}), override)
    assert policy.mode("plan", "engineer") == plan
    assert policy.mode("build", "engineer") == build
    assert policy.every is every


def test_policy_override_rejects_nonsense():
    with pytest.raises(ValueError, match="--hitl"):
        HitlPolicy(HitlConfig(), "some times")


# ── who may be asked in place ────────────────────────────────────────────────

def test_a_launcher_can_say_its_terminal_is_not_the_runs(monkeypatch):
    """A TTY can be INHERITED. `issue_watch` and `pr_watch` run a chain with a
    blocking `subprocess.run` that passes their own stdin through, so a watcher
    someone started from a window hands every run it launches a keyboard that
    looks exactly like the engineer's — and a gate would then prompt whoever is
    watching the queue, holding a deliberately serial loop for `wait_seconds`.
    Only the launcher knows the difference, so the launcher says so."""
    class TTY:
        def isatty(self): return True
    monkeypatch.setattr(hitl.sys, "stdin", TTY())
    monkeypatch.setattr(hitl.sys, "stdout", TTY())
    monkeypatch.delenv(hitl.UNATTENDED_ENV, raising=False)
    assert hitl.attended() is True

    monkeypatch.setenv(hitl.UNATTENDED_ENV, "1")
    assert hitl.attended() is False
    # ...and a policy built under it has nobody to ask, so gates suspend at once.
    assert HitlPolicy(HitlConfig(default="on")).ask is None


def test_an_empty_unattended_flag_is_not_a_declaration(monkeypatch):
    """An env var set to "" is what an unset var looks like to a shell that
    exported it anyway; it must not silently mute an engineer's own terminal."""
    class TTY:
        def isatty(self): return True
    monkeypatch.setattr(hitl.sys, "stdin", TTY())
    monkeypatch.setattr(hitl.sys, "stdout", TTY())
    monkeypatch.setenv(hitl.UNATTENDED_ENV, "  ")
    assert hitl.attended() is True


def test_the_trigger_does_not_decide_who_may_be_asked(monkeypatch):
    """An engineer who types `uv run adws/adw_issue_sdlc.py 42` at their own
    keyboard is on the `issue` trigger too, and is still there to answer. The
    trigger decides whether a gate FIRES (`when_unattended`); it does not decide
    whether there is a terminal to fire into."""
    class TTY:
        def isatty(self): return True
    monkeypatch.setattr(hitl.sys, "stdin", TTY())
    monkeypatch.setattr(hitl.sys, "stdout", TTY())
    monkeypatch.delenv(hitl.UNATTENDED_ENV, raising=False)
    policy = HitlPolicy(HitlConfig(default="on"))
    assert policy.ask is not None
    assert policy.mode("plan", "issue") == "on"


def test_policy_unattended_runs_read_when_unattended():
    on = HitlConfig(default="on")
    assert HitlPolicy(on).mode("plan", "issue") == "on"          # suspend and wait
    auto = HitlConfig(default="on", when_unattended="auto")
    assert HitlPolicy(auto).mode("plan", "issue") == "auto"
    # A flag is a person at a keyboard saying so, and it wins even for an issue run.
    assert HitlPolicy(auto, "all").mode("plan", "issue") == "on"


# ── answering from outside the run (the CLI) ─────────────────────────────────

def test_answer_copies_the_digest_the_run_asked_about(session_dir):
    artifacts.suspend_run(session_dir, WaitingFor(gate="plan", round=2, subject_digest="abc"))
    decision = hitl.answer(session_dir, "reject", "split it", by="alice")
    assert decision.subject_digest == "abc" and decision.round == 2
    assert decision.channel == "cli" and decision.decided_at
    assert hitl.read_decision(session_dir, "plan", 2) == decision


def test_answer_refuses_a_session_that_is_not_waiting(session_dir):
    with pytest.raises(RuntimeError, match="not waiting"):
        hitl.answer(session_dir, "approve", "", by="alice")


def test_answer_requires_notes_on_a_reject(session_dir):
    artifacts.suspend_run(session_dir, WaitingFor(gate="plan", round=1, subject_digest="abc"))
    with pytest.raises(RuntimeError, match="notes"):
        hitl.answer(session_dir, "reject", "   ", by="alice")


def test_answer_reaches_a_blocked_run_that_is_still_polling(session_dir):
    """An attended run records `waiting_for` while it polls; the CLI answers it
    the same way, and the running process picks the file up."""
    artifacts.update_run(session_dir, waiting_for=WaitingFor(gate="plan", subject_digest="x"))
    assert artifacts.read_run(session_dir).status == "running"
    assert hitl.answer(session_dir, "approve", "", by="alice").approved


def test_the_config_reads_on_and_off_as_yaml_writes_them():
    """PyYAML reads `on`/`off` as booleans. The stamped config says `default: off`,
    and a roster that could not be loaded would break every install."""
    import yaml
    raw = yaml.safe_load("hitl:\n  default: off\n  gates: {plan: on, build: off}\n")
    config = HitlConfig(**raw["hitl"])
    assert config.default is False and config.gates == {"plan": True, "build": False}
    policy = HitlPolicy(config)
    assert policy.mode("plan", "engineer") == "on"
    assert policy.mode("build", "engineer") == "auto"
    assert HitlConfig(default="on").default is True           # the word, quoted or in Python
    with pytest.raises(ValueError):
        HitlConfig(default="sometimes")
    with pytest.raises(ValueError):
        HitlConfig(when_unattended="ask")
