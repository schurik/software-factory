"""The session directory IS the record. These tests read it the way the
factory does — and never the trace db, which nothing in the factory reads.

`start_run` carries two things across processes and both are load-bearing:
provenance (an issue-triggered session that a later ADW re-enters is still
issue-triggered, and `integration.py` refuses to merge on that) and SPEND
(dropping it hands every `--adw-id` re-entry a fresh wallet, which turns
`budget:` into a ceiling on whoever happens to be running rather than on the
work).
"""

from __future__ import annotations

import json
from pathlib import Path

from adw_modules import artifacts
from adw_modules.data_types import RecordedPhase, RunState


def state(**fields) -> RunState:
    base = {"adw_id": "abc123", "workflows": ["adw_plan"], "command": ["adw_plan.py", "x"],
            "pid": 111, "engineer": "tester", "status": "running"}
    base.update(fields)
    return RunState(**base)


def events(session: Path, *rows: dict) -> None:
    session.mkdir(parents=True, exist_ok=True)
    with (session / artifacts.EVENTS_FILE).open("a") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def phase_end(phase_id: str, status: str = "success") -> dict:
    return {"type": "phase_end", "phase_id": phase_id, "payload": {"status": status}}


# ── run.json: a fresh session ────────────────────────────────────────────────

def test_a_session_with_no_record_reads_as_none(tmp_path):
    assert artifacts.read_run(tmp_path / "nothing") is None


def test_a_truncated_record_is_a_missing_answer_not_a_crash(tmp_path):
    artifacts.run_path(tmp_path).write_text('{"adw_id": "abc12')
    assert artifacts.read_run(tmp_path) is None


def test_start_run_writes_the_record(tmp_path):
    artifacts.start_run(tmp_path, state())
    assert artifacts.read_run(tmp_path).adw_id == "abc123"


# ── start_run: what a second process inherits ────────────────────────────────

def test_workflows_accumulate_in_order(tmp_path):
    artifacts.start_run(tmp_path, state(workflows=["adw_plan"]))
    artifacts.start_run(tmp_path, state(workflows=["adw_build_test"]))
    assert artifacts.read_run(tmp_path).workflows == ["adw_plan", "adw_build_test"]


def test_a_workflow_that_runs_twice_is_listed_once(tmp_path):
    artifacts.start_run(tmp_path, state(workflows=["adw_plan"]))
    artifacts.start_run(tmp_path, state(workflows=["adw_plan"]))
    assert artifacts.read_run(tmp_path).workflows == ["adw_plan"]


def test_the_newest_process_owns_the_command_and_the_pid(tmp_path):
    """They answer "what would running this again mean" — `just resume` reads it."""
    artifacts.start_run(tmp_path, state(pid=111, command=["adw_plan.py"]))
    artifacts.start_run(tmp_path, state(pid=222, command=["adw_build_test.py", "--resume"]))
    recorded = artifacts.read_run(tmp_path)
    assert recorded.pid == 222
    assert recorded.command == ["adw_build_test.py", "--resume"]


def test_provenance_is_learned_once_and_never_unlearned(tmp_path):
    """A later ADW re-entering an issue-triggered session is still issue-triggered."""
    artifacts.start_run(tmp_path, state(trigger="issue",
                                        issue_url="https://forge/issues/7"))
    artifacts.start_run(tmp_path, state(trigger="", issue_url=""))
    recorded = artifacts.read_run(tmp_path)
    assert recorded.trigger == "issue"
    assert recorded.issue_url == "https://forge/issues/7"


def test_a_pr_url_survives_a_re_entry(tmp_path):
    """A branch already under review is pushed to, never proposed a second time."""
    artifacts.start_run(tmp_path, state(pr_url="https://forge/pull/9"))
    artifacts.start_run(tmp_path, state())
    assert artifacts.read_run(tmp_path).pr_url == "https://forge/pull/9"


def test_spend_carries_across_processes(tmp_path):
    """Drop this and `--adw-id` re-entry hands the session a fresh wallet."""
    artifacts.start_run(tmp_path, state(total_tokens=12_000, total_cost=3.5))
    fresh = artifacts.start_run(tmp_path, state(total_tokens=0, total_cost=0.0))
    assert (fresh.total_tokens, fresh.total_cost) == (12_000, 3.5)
    assert artifacts.read_run(tmp_path).total_tokens == 12_000


def test_a_first_process_starts_at_zero(tmp_path):
    fresh = artifacts.start_run(tmp_path, state())
    assert (fresh.total_tokens, fresh.total_cost) == (0, 0.0)


def test_update_run_patches_in_place(tmp_path):
    artifacts.start_run(tmp_path, state())
    artifacts.update_run(tmp_path, pr_url="https://forge/pull/1")
    assert artifacts.read_run(tmp_path).pr_url == "https://forge/pull/1"


def test_update_run_ignores_empty_values(tmp_path):
    """A caller passing "" means "I have nothing to add", not "erase it"."""
    artifacts.start_run(tmp_path, state(trigger="issue"))
    artifacts.update_run(tmp_path, trigger="")
    assert artifacts.read_run(tmp_path).trigger == "issue"


def test_finish_run_closes_the_record_and_never_raises_on_a_missing_one(tmp_path):
    artifacts.start_run(tmp_path, state())
    artifacts.finish_run(tmp_path, "success")
    recorded = artifacts.read_run(tmp_path)
    assert recorded.status == "success" and recorded.ended_at
    artifacts.finish_run(tmp_path / "nothing", "fail")     # must not raise


# ── envelopes + events.jsonl: what a resume may replay ───────────────────────

def record(phase: str, seq: int, phase_id: str) -> RecordedPhase:
    return RecordedPhase(phase_id=phase_id, seq=seq, phase=phase, agent="planner",
                         output_type="PlanOutput",
                         payload_json='{"status": "success"}')


def test_only_phases_that_actually_passed_are_offered(tmp_path):
    """An envelope is written BEFORE the phase closes, so one whose phase then
    failed is on disk and must never be handed back."""
    artifacts.write_envelope(tmp_path, record("plan", 1, "a_01_plan"))
    artifacts.write_envelope(tmp_path, record("build", 2, "a_02_build"))
    events(tmp_path, phase_end("a_01_plan", "success"), phase_end("a_02_build", "fail"))
    assert [r.phase for r in artifacts.recorded_phases(tmp_path)] == ["plan"]


def test_a_phase_that_only_started_is_not_offered(tmp_path):
    """Killed mid-phase: no phase_end, so the work is unfinished."""
    artifacts.write_envelope(tmp_path, record("plan", 1, "a_01_plan"))
    events(tmp_path, {"type": "phase_start", "phase_id": "a_01_plan", "payload": {}})
    assert artifacts.recorded_phases(tmp_path) == []


def test_records_come_back_in_run_order(tmp_path):
    artifacts.write_envelope(tmp_path, record("build", 2, "a_02_build"))
    artifacts.write_envelope(tmp_path, record("plan", 1, "a_01_plan"))
    events(tmp_path, phase_end("a_01_plan"), phase_end("a_02_build"))
    assert [r.seq for r in artifacts.recorded_phases(tmp_path)] == [1, 2]


def test_a_phase_re_entered_later_ends_on_its_latest_outcome(tmp_path):
    artifacts.write_envelope(tmp_path, record("plan", 1, "a_01_plan"))
    events(tmp_path, phase_end("a_01_plan", "fail"), phase_end("a_01_plan", "success"))
    assert [r.phase for r in artifacts.recorded_phases(tmp_path)] == ["plan"]


def test_an_unreadable_envelope_is_skipped_not_fatal(tmp_path):
    artifacts.write_envelope(tmp_path, record("plan", 1, "a_01_plan"))
    (tmp_path / artifacts.ENVELOPES_DIR / "a_02_build.json").write_text("{ truncated")
    events(tmp_path, phase_end("a_01_plan"), phase_end("a_02_build"))
    assert [r.phase for r in artifacts.recorded_phases(tmp_path)] == ["plan"]


def test_no_envelopes_directory_is_an_empty_answer(tmp_path):
    assert artifacts.recorded_phases(tmp_path) == []


# ── max_phase_seq: a joined run continues the numbering ──────────────────────

def test_a_new_session_starts_at_zero(tmp_path):
    assert artifacts.max_phase_seq(tmp_path, "abc123") == 0


def test_the_highest_phase_number_comes_off_the_event_log(tmp_path):
    """Restarting at 1 collides with the first process's phase_ids — which are
    the names of the envelope FILES."""
    events(tmp_path,
           {"type": "phase_start", "phase_id": "abc123_01_plan", "payload": {}},
           phase_end("abc123_01_plan"),
           {"type": "phase_start", "phase_id": "abc123_02_build", "payload": {}})
    assert artifacts.max_phase_seq(tmp_path, "abc123") == 2


def test_a_phase_that_only_started_still_counts_for_the_numbering(tmp_path):
    events(tmp_path, {"type": "phase_start", "phase_id": "abc123_07_build", "payload": {}})
    assert artifacts.max_phase_seq(tmp_path, "abc123") == 7


def test_another_session_s_phase_ids_do_not_count(tmp_path):
    events(tmp_path, {"type": "phase_start", "phase_id": "other_09_plan", "payload": {}})
    assert artifacts.max_phase_seq(tmp_path, "abc123") == 0


# ── scanning many sessions ───────────────────────────────────────────────────

def sessions(root: Path, **by_id: RunState) -> Path:
    for adw_id, run_state in by_id.items():
        artifacts.start_run(root / adw_id, run_state)
    return root


def test_scan_finds_every_session_with_a_record(tmp_path):
    sessions(tmp_path, aaa=state(adw_id="aaa"), bbb=state(adw_id="bbb"))
    (tmp_path / "ccc").mkdir()                       # recorded before run.json existed
    assert sorted(artifacts.scan(tmp_path)) == ["aaa", "bbb"]


def test_scan_of_a_missing_directory_is_empty(tmp_path):
    assert artifacts.scan(tmp_path / "nothing") == {}


def test_running_pids_lists_only_sessions_that_believe_they_are_running(tmp_path):
    sessions(tmp_path,
             aaa=state(adw_id="aaa", status="running", pid=101),
             bbb=state(adw_id="bbb", status="success", pid=102))
    assert artifacts.running_pids(tmp_path) == {"aaa": 101}


def test_pr_urls_lists_only_sessions_that_became_a_pull_request(tmp_path):
    sessions(tmp_path,
             aaa=state(adw_id="aaa", pr_url="https://forge/pull/1"),
             bbb=state(adw_id="bbb"))
    assert artifacts.pr_urls(tmp_path) == {"aaa": "https://forge/pull/1"}


def test_statuses_reads_an_unclosed_record_as_running(tmp_path):
    sessions(tmp_path, aaa=state(adw_id="aaa", status="running"))
    assert artifacts.statuses(tmp_path) == {"aaa": "running"}


# ── processes.jsonl: what a run has alive ────────────────────────────────────

def test_a_started_process_is_live_until_it_ends(tmp_path):
    artifacts.record_process(tmp_path, "agent", "builder", 500, "claude -p")
    assert [row["pid"] for row in artifacts.live_processes(tmp_path)] == [500]
    artifacts.end_process(tmp_path, 500)
    assert artifacts.live_processes(tmp_path) == []


def test_children_are_listed_before_the_parent(tmp_path):
    """Kill the workflow first and its coding agent keeps burning tokens."""
    artifacts.record_process(tmp_path, "adw", "", 400, "adw_plan.py")
    artifacts.record_process(tmp_path, "agent", "builder", 500, "claude -p")
    assert [row["kind"] for row in artifacts.live_processes(tmp_path)] == ["agent", "adw"]


def test_ending_the_session_closes_every_process_it_still_believes_alive(tmp_path):
    artifacts.start_run(tmp_path, state())
    artifacts.record_process(tmp_path, "agent", "builder", 500, "claude -p")
    artifacts.finish_run(tmp_path, "fail")
    assert artifacts.live_processes(tmp_path) == []


def test_a_corrupt_process_line_is_skipped(tmp_path):
    artifacts.record_process(tmp_path, "agent", "builder", 500, "claude -p")
    with (tmp_path / artifacts.PROCESSES_FILE).open("a") as stream:
        stream.write("{ truncated\n\n")
    assert [row["pid"] for row in artifacts.live_processes(tmp_path)] == [500]


# ── watchers: liveness, not a session ────────────────────────────────────────

def test_a_watcher_that_never_ran_is_absent_not_stopped(tmp_path):
    assert artifacts.watcher_states(tmp_path) == {}


def test_started_at_is_preserved_across_beats(tmp_path):
    """The file also says how long the watcher has been up."""
    artifacts.watcher_beat(tmp_path, "issues", {"started_at": "T0", "status": "polling"})
    artifacts.watcher_beat(tmp_path, "issues", {"started_at": "T9", "status": "idle"})
    row = artifacts.watcher_states(tmp_path)["issues"]
    assert row["started_at"] == "T0" and row["status"] == "idle"


def test_a_watcher_beat_never_raises_on_an_unwritable_directory(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    artifacts.watcher_beat(blocker, "issues", {"status": "polling"})    # must not raise
