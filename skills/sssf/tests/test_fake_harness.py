"""The fake harness itself: does it meet the contract, and is it honest?

`harnesses/__init__.py` documents the names a module must expose. A harness
that is missing one fails deep inside `agents.execute`, so the contract is
asserted directly — and asserted for the two real harnesses too, because the
point of testing the fake one is that it stands in for them.

The rest is the fake's own behaviour: the cursor that makes a second send a
different reply, the tree changes that let a scripted run drive gates, and the
things it deliberately refuses to pretend about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adw_modules import harnesses
from adw_modules.data_types import AgentRequest
from adw_modules.harnesses import fake
from adw_modules.limits import AgentTimeout

from conftest import agent

CONTRACT = ("NAME", "Options", "resolve_model", "reachable", "validate_agent",
            "new_session_id", "ToolCallTracker", "run")


@pytest.mark.parametrize("name", sorted(harnesses.HARNESSES))
@pytest.mark.parametrize("member", CONTRACT)
def test_every_registered_harness_meets_the_contract(name, member):
    assert hasattr(harnesses.HARNESSES[name], member), \
        f"harness {name!r} is missing {member!r} — see harnesses/__init__.py"


def test_the_fake_is_registered_and_selectable():
    assert harnesses.get("fake") is fake
    assert "fake" in harnesses.NAMES


def test_an_unknown_harness_name_is_a_clean_exit():
    with pytest.raises(SystemExit, match="is not one of"):
        harnesses.get("codex")


def test_the_installer_does_not_offer_the_fake_as_a_repository_s_harness():
    """A factory whose whole roster is fake produces nothing, so `install.py`
    must never present it as a choice. The gate is the absence of a template
    directory, which is what `scripts/_harness.names()` scans."""
    from conftest import SKILL_ROOT
    offered = sorted(d.name for d in (SKILL_ROOT / "templates" / "harnesses").iterdir()
                     if d.is_dir() and (d / "defaults.yaml").is_file())
    assert "fake" not in offered
    assert offered == ["claude_code", "pi"]


def test_it_says_out_loud_that_it_is_not_a_real_agent():
    findings = fake.credentials(agent("planner", harness="fake"))
    assert findings and findings[0].level == "warn"
    assert "FAKE harness" in findings[0].detail


# ── running a turn ───────────────────────────────────────────────────────────

def request(tmp_path: Path, options: dict, **overrides) -> AgentRequest:
    fields = {
        "prompt": "do it", "system_prompt": "you are a test", "model": "fake",
        "session_id": "s1", "session_dir": str(tmp_path / "sessions"),
        "raw_output_path": str(tmp_path / "raw_output.jsonl"),
        "cwd": str(tmp_path / "tree"), "options": options,
    }
    fields.update(overrides)
    (tmp_path / "tree").mkdir(exist_ok=True)
    return AgentRequest(**fields)


def test_a_scripted_envelope_comes_back_as_the_turn_s_text(tmp_path):
    result = fake.run(request(tmp_path, {"replies": [
        {"envelope": {"status": "success", "summary": "done"}}]}))
    assert json.loads(result.text) == {"status": "success", "summary": "done"}
    assert result.returncode == 0


def test_a_reply_writes_and_deletes_files_in_the_run_s_tree(tmp_path):
    """This is what lets a scripted run drive gates that measure the TREE."""
    (tmp_path / "tree" / "old.txt").parent.mkdir(exist_ok=True)
    (tmp_path / "tree" / "old.txt").write_text("goes away")
    fake.run(request(tmp_path, {"replies": [
        {"writes": {"specs/plan.md": "# Plan\n"}, "deletes": ["old.txt"],
         "envelope": {"status": "success"}}]}))
    assert (tmp_path / "tree" / "specs" / "plan.md").read_text() == "# Plan\n"
    assert not (tmp_path / "tree" / "old.txt").exists()


def test_each_send_consumes_the_next_reply(tmp_path):
    options = {"replies": [{"envelope": {"status": "fail"}},
                           {"envelope": {"status": "success"}}]}
    first = fake.run(request(tmp_path, options))
    second = fake.run(request(tmp_path, options))
    assert json.loads(first.text)["status"] == "fail"
    assert json.loads(second.text)["status"] == "success"


def test_the_cursor_is_per_session_not_per_process(tmp_path):
    """Two sessions in one process do not share a place in the script."""
    options = {"replies": [{"envelope": {"status": "fail"}},
                           {"envelope": {"status": "success"}}]}
    fake.run(request(tmp_path, options, session_id="a"))
    other = fake.run(request(tmp_path, options, session_id="b"))
    assert json.loads(other.text)["status"] == "fail"


def test_running_past_the_end_repeats_the_last_reply(tmp_path):
    options = {"replies": [{"envelope": {"status": "success", "summary": "only one"}}]}
    fake.run(request(tmp_path, options))
    again = fake.run(request(tmp_path, options))
    assert json.loads(again.text)["summary"] == "only one"


def test_strict_turns_running_out_of_script_into_a_loud_failure(tmp_path):
    options = {"strict": True, "replies": [{"envelope": {"status": "success"}}]}
    fake.run(request(tmp_path, options))
    with pytest.raises(RuntimeError, match="has no scripted reply"):
        fake.run(request(tmp_path, options))


def test_a_script_file_is_read_relative_to_the_run_s_tree(tmp_path):
    (tmp_path / "tree").mkdir(exist_ok=True)
    (tmp_path / "tree" / "script.json").write_text(json.dumps(
        {"replies": [{"envelope": {"status": "success", "summary": "from a file"}}]}))
    result = fake.run(request(tmp_path, {"script": "script.json"}))
    assert json.loads(result.text)["summary"] == "from a file"


def test_a_script_file_may_be_a_bare_list(tmp_path):
    (tmp_path / "tree").mkdir(exist_ok=True)
    (tmp_path / "tree" / "script.json").write_text(json.dumps(
        [{"envelope": {"status": "success", "summary": "bare list"}}]))
    result = fake.run(request(tmp_path, {"script": "script.json"}))
    assert json.loads(result.text)["summary"] == "bare list"


def test_raw_text_overrides_the_envelope(tmp_path):
    """How a reply that is NOT valid envelope JSON gets scripted."""
    result = fake.run(request(tmp_path, {"replies": [{"text": "just prose"}]}))
    assert result.text == "just prose"


def test_the_turn_is_written_to_raw_output_like_a_real_harness(tmp_path):
    fake.run(request(tmp_path, {"replies": [
        {"tool_calls": [{"tool": "Read", "args": {"file_path": "x"}, "result": "y"}],
         "envelope": {"status": "success"}}]}))
    lines = [json.loads(line) for line in
             (tmp_path / "raw_output.jsonl").read_text().splitlines()]
    assert [row["type"] for row in lines] == ["tool_call", "result"]


def test_events_are_forwarded_to_the_caller(tmp_path):
    seen = []
    fake.run(request(tmp_path, {"replies": [
        {"tool_calls": [{"tool": "Bash", "args": {"command": "ls"}, "result": "a b"}],
         "envelope": {"status": "success"}}]}), on_event=seen.append)
    assert [event["type"] for event in seen] == ["tool_call", "result"]


def test_usage_is_reported_so_a_budget_can_be_exercised(tmp_path):
    result = fake.run(request(tmp_path, {"replies": [
        {"tokens": 1500, "cost": 0.25, "envelope": {"status": "success"}}]}))
    assert (result.tokens, result.cost) == (1500, 0.25)
    assert result.usage.total_tokens == 1500


def test_a_turn_past_its_clock_raises_the_same_exception_a_real_harness_does(tmp_path):
    with pytest.raises(AgentTimeout) as caught:
        fake.run(request(tmp_path, {"replies": [
            {"sleep": 30, "tokens": 400, "cost": 0.1, "envelope": {"status": "success"}}]},
            timeout_seconds=1))
    # The partial result is what the killed turn had already been paid for.
    assert caught.value.result.tokens == 400


def test_a_sleep_inside_the_clock_is_not_a_timeout(tmp_path):
    result = fake.run(request(tmp_path, {"replies": [
        {"sleep": 0.01, "envelope": {"status": "success"}}]}, timeout_seconds=30))
    assert json.loads(result.text)["status"] == "success"


def test_the_script_is_not_mutated_by_running_it(tmp_path):
    """The same options dict is reused across every send in a phase."""
    options = {"replies": [{"tool_calls": [{"tool": "Read", "args": {}}],
                            "envelope": {"status": "success"}}]}
    before = json.dumps(options, sort_keys=True)
    fake.run(request(tmp_path, options))
    fake.run(request(tmp_path, options))
    assert json.dumps(options, sort_keys=True) == before


def test_no_child_process_is_ever_recorded_as_killable(tmp_path):
    """There is no child here; recording the ADW's own pid would make
    `just kill` shoot the run itself."""
    spawned = []
    fake.run(request(tmp_path, {"replies": [{"envelope": {"status": "success"}}]}),
             on_spawn=spawned.append, on_exit=spawned.append)
    assert spawned == []


# ── the tracker ──────────────────────────────────────────────────────────────

def test_the_tracker_produces_the_shared_record_shape():
    tracker = fake.ToolCallTracker()
    records = tracker.observe({"type": "tool_call", "id": "1", "tool": "Bash",
                               "args": {"command": "ls -la"}, "ok": True,
                               "result": "a\nb"})
    assert len(records) == 1
    record = records[0]
    assert record["tool"] == "Bash"
    assert record["label"] == "Bash: ls -la"
    assert record["ok"] is True
    assert record["result_snippet"] == "a\nb"
    assert record["started_at"] and record["ended_at"]


def test_the_tracker_ignores_everything_that_is_not_a_tool_call():
    assert fake.ToolCallTracker().observe({"type": "result", "text": "{}"}) == []


def test_a_failed_tool_call_is_recorded_as_failed():
    records = fake.ToolCallTracker().observe(
        {"type": "tool_call", "id": "1", "tool": "Read", "ok": False,
         "args": {"file_path": "gone"}, "result": "no such file"})
    assert records[0]["ok"] is False


# ── validation and small contract details ────────────────────────────────────

def test_an_empty_model_is_still_a_typo():
    with pytest.raises(ValueError, match="model is empty"):
        fake.resolve_model("  ")


def test_any_non_empty_model_resolves():
    assert fake.resolve_model(" fake ") == "fake"


def test_reachable_never_raises():
    fake.reachable()


def test_session_ids_are_deterministic():
    a = agent("planner", harness="fake")
    assert fake.new_session_id("abc123", a) == fake.new_session_id("abc123", a)
    assert fake.new_session_id("abc123", a) != fake.new_session_id("other", a)


def test_a_scriptless_agent_is_a_validation_problem():
    assert fake.validate_agent(agent("planner", harness="fake")) == [
        "harness_options: the fake harness answers from a script, so it needs "
        "`replies:` or `script:` — with neither, every send fails"]


def test_an_unknown_option_key_is_a_validation_problem():
    problems = fake.validate_agent(agent(
        "planner", harness="fake", harness_options={"replies": [], "safe_mode": True}))
    assert problems and "harness_options" in problems[0]


def test_a_scripted_agent_validates_clean():
    assert fake.validate_agent(agent("planner", harness="fake", harness_options={
        "replies": [{"envelope": {"status": "success"}}]})) == []
