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
