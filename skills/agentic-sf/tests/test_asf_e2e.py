"""Whole workflows, end to end, on the fake harness. No agent, no network.

A real install into a real git repository, the real runner as a subprocess,
the real worktree, the real tracer writing the real db — only the coding
agent is scripted, because it is the only part that costs money and does not
repeat. What these prove is the part no unit test reaches: that a workflow
directory, its stages, its tasks and its bindings actually drive a run.
"""

from __future__ import annotations

from pathlib import Path

from .asf_helpers import (BUILD_REPORT, PY_CHECK, adw_id_of, asf, commit_all, db_rows,
                         envelope, fake_roster, git, phase_names, run_state, session_dir,
                         task_text, wire, write_workflow)


def plan_reply() -> dict:
    return {"writes": {"specs/plan.md": "# Plan\n"}, "tokens": 100, "cost": 0.01,
            "envelope": envelope(artifacts=["specs/plan.md"], commit_message="docs: plan")}


def build_reply(content: str, message: str) -> dict:
    return {"writes": {"app.py": content},
            "envelope": envelope(changed_files=["app.py"], commit_message=message)}


def test_sdlc_runs_green_end_to_end_and_lands_a_commit(stamped: Path):
    fake_roster(stamped, planner=[plan_reply()], builder=[build_reply("ok = 1\n", "feat: app")])
    wire(stamped, "test", PY_CHECK)
    commit_all(stamped)

    result = asf(stamped, "run", "sdlc", "add app.py")
    assert result.returncode == 0, result.stdout + result.stderr
    adw_id = adw_id_of(result)

    assert phase_names(stamped, adw_id) == ["request", "plan", "implement", "verify_1",
                                            "commit_implement"]
    # The run's branch holds the work, in the builder's own words; the checkout
    # never saw it.
    assert git(stamped, "log", "-1", "--format=%s", f"asf/{adw_id}") == "feat: app"
    assert git(stamped, "status", "--porcelain") == ""
    assert not (stamped / "app.py").exists()
    # The record knows which workflow ran, and how to run it again.
    state = run_state(stamped, adw_id)
    assert state["status"] == "success"
    assert state["workflows"] == ["sdlc"]
    assert state["command"][:3] == ["asf.py", "run", "sdlc"]
    # The mirror the visualizer polls calls it by the workflow's name too.
    assert db_rows(stamped, f"select adw_name from sessions where adw_id='{adw_id}'") == [("sdlc",)]


def test_a_red_check_goes_back_to_the_builder_and_the_green_retry_lands(stamped: Path):
    fake_roster(stamped, planner=[plan_reply()],
                builder=[build_reply("1/0\n", "feat: app"),
                         build_reply("ok = 1\n", "feat: app, division fixed")])
    wire(stamped, "test", PY_CHECK)
    commit_all(stamped)

    result = asf(stamped, "run", "sdlc", "add app.py")
    assert result.returncode == 0, result.stdout + result.stderr
    adw_id = adw_id_of(result)

    assert phase_names(stamped, adw_id) == ["request", "plan", "implement", "verify_1", "fix_1",
                                            "verify_2", "commit_implement"]
    # The commit is the build as it stands after the fix, in the fix's words.
    assert git(stamped, "log", "-1", "--format=%s", f"asf/{adw_id}") == "feat: app, division fixed"
    # The fix was asked with the fix task, not the build task.
    user_prompt = (session_dir(stamped, adw_id) / "builder" / "prompts" / "user.md").read_text()
    assert user_prompt.startswith("# Fix")


def test_an_exhausted_fix_loop_stops_the_run_before_anything_lands(stamped: Path):
    fake_roster(stamped, builder=[build_reply("1/0\n", "feat: broken"),
                                  build_reply("2/0\n", "feat: still broken")])
    wire(stamped, "test", PY_CHECK)
    commit_all(stamped)

    result = asf(stamped, "run", "quick", "add app.py")      # quick: max_fix_loops 2
    assert result.returncode == 1, result.stdout + result.stderr
    adw_id = adw_id_of(result)

    names = phase_names(stamped, adw_id)
    assert names == ["request", "implement", "verify_1", "fix_1", "verify_2"]
    assert "commit_implement" not in names
    assert run_state(stamped, adw_id)["status"] == "fail"
    assert "still failed after 2 attempt(s)" in result.stdout
    # Nothing landed, and the worktree was kept for whoever wants to look.
    assert git(stamped, "log", "-1", "--format=%s", f"asf/{adw_id}") == "prepare"
    assert (stamped / ".asf-worktrees" / adw_id).is_dir()


def test_a_workflow_s_own_task_and_appended_identity_reach_the_agent(stamped: Path):
    fake_roster(stamped, builder=[build_reply("ok = 1\n", "feat: app")])
    wire(stamped, "test", PY_CHECK)
    write_workflow(stamped, "custom", {
        "description": "quick, with this repo's own words for the builder",
        "agents": {"builder": {"from": "builder", "system_append": ["agents/builder.md"],
                               "writes": ["app.py"]}},
        "stages": [{"implement": {"agent": "builder"}},
                   {"verify": {"blocks": ["test"], "max_fix_loops": 1}},
                   {"commit": {"of": "implement"}}],
    }, tasks={"implement": task_text("Implement, our way", "TASK-MARKER-7", BUILD_REPORT)},
       appends={"builder.md": "IDENTITY-MARKER-9: never touch the Makefile.\n"})
    commit_all(stamped)

    result = asf(stamped, "run", "custom", "add app.py")
    assert result.returncode == 0, result.stdout + result.stderr
    adw_id = adw_id_of(result)

    prompts = session_dir(stamped, adw_id) / "builder" / "prompts"
    assert "TASK-MARKER-7" in (prompts / "user.md").read_text()
    system = (prompts / "system.md").read_text()
    assert system.startswith("# Builder")                 # the roster's identity, first
    assert "IDENTITY-MARKER-9" in system                  # the workflow's addition, after


def test_a_stage_s_hitl_option_stops_an_unattended_run_at_the_gate(stamped: Path):
    fake_roster(stamped, planner=[plan_reply()], builder=[build_reply("ok = 1\n", "feat: app")])
    wire(stamped, "test", PY_CHECK)
    write_workflow(stamped, "gated", {
        "description": "sdlc with a person between the plan and the build",
        "stages": [{"plan": {"agent": "planner", "hitl": True}},
                   {"implement": {"agent": "builder"}},
                   {"commit": {"of": "implement"}}],
    })
    commit_all(stamped)

    result = asf(stamped, "run", "gated", "add app.py")
    assert result.returncode == 75, result.stdout + result.stderr   # suspended, not failed
    adw_id = adw_id_of(result)
    state = run_state(stamped, adw_id)
    assert state["status"] == "waiting"
    assert state["waiting_for"]["gate"] == "plan"
    assert phase_names(stamped, adw_id) == ["request", "plan", "approve_plan"]
    # `--hitl none` on the command line beats the workflow's say.
    again = asf(stamped, "run", "gated", "add app.py", "--hitl", "none")
    assert again.returncode == 0, again.stdout + again.stderr
