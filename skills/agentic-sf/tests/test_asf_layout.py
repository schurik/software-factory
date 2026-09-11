"""Everything a workflow directory can get wrong is found at load time.

These run `engine.workflow.load` in-process against a stamped repo, and each
asserts on the message as well as the refusal: the message is the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine import factory, workflow
from engine.data_types import BuildOutput

from .asf_helpers import BUILD_REPORT, fake_roster, task_text, write_workflow

QUICK = [{"build": {"agent": "builder"}},
         {"verify": {"blocks": ["test"]}},
         {"commit": {"of": "build"}}]


@pytest.fixture
def factory_repo(stamped: Path, monkeypatch) -> Path:
    fake_roster(stamped, planner=[{"envelope": {"status": "success"}}],
                builder=[{"envelope": {"status": "success"}}])
    monkeypatch.chdir(stamped)
    return stamped


def refused(name: str) -> str:
    with pytest.raises(SystemExit) as stop:
        workflow.load(name)
    return str(stop.value)


def test_the_shipped_workflows_load_and_name_their_agents(factory_repo):
    sdlc = workflow.load("sdlc")
    assert [s.stage.name for s in sdlc.steps] == ["plan", "build", "verify", "commit"]
    assert sdlc.required_agents == ["builder", "planner"]
    assert sdlc.steps[0].tasks["plan"].endswith("asf/stages/plan/task.md")
    quick = workflow.load("quick")
    assert quick.required_agents == ["builder"]


def test_the_roster_is_directories_and_factory_yaml_may_not_carry_agents(factory_repo):
    cfg = factory.load()
    assert sorted(a.name for a in cfg.agents) == ["builder", "planner"]
    planner = next(a for a in cfg.agents if a.name == "planner")
    assert planner.writes == ["specs/"]
    assert planner.prompt_engineering.user == ""          # tasks belong to stages
    assert planner.prompt_engineering.system.endswith("asf/agents/planner/system.md")

    config = factory_repo / "asf" / "factory.yaml"
    raw = yaml.safe_load(config.read_text())
    raw["agents"] = [{"name": "x"}]
    config.write_text(yaml.safe_dump(raw))
    with pytest.raises(SystemExit, match="does not belong in factory.yaml"):
        factory.load()


def test_a_stage_outside_the_vocabulary_is_refused_with_the_vocabulary(factory_repo):
    write_workflow(factory_repo, "bad", {"description": "x",
                                         "stages": [{"deploy": {}}]})
    message = refused("bad")
    assert "'deploy' is not a stage" in message
    assert "build, commit, plan, verify" in message


def test_an_option_no_stage_takes_is_refused(factory_repo):
    write_workflow(factory_repo, "bad", {"description": "x",
                                         "stages": [{"build": {"agent": "builder", "loops": 3}}]})
    assert "loops" in refused("bad")


def test_a_verify_with_nothing_to_verify_is_refused(factory_repo):
    write_workflow(factory_repo, "bad", {"description": "x",
                                         "stages": [{"verify": {}}, {"build": {}}]})
    message = refused("bad")
    assert "verify: needs a BuildOutput" in message and "hands on nothing" in message


def test_a_commit_of_a_stage_that_wrote_no_message_is_refused(factory_repo):
    write_workflow(factory_repo, "bad", {"description": "x",
                                         "stages": [{"build": {}}, {"commit": {"of": "review"}}]})
    assert "of: 'review' is not a stage before this one" in refused("bad")


def test_an_agent_the_roster_lacks_is_refused(factory_repo):
    write_workflow(factory_repo, "bad", {"description": "x",
                                         "stages": [{"build": {"agent": "coder"}}]})
    assert "agent 'coder' is neither in the roster nor bound" in refused("bad")


def test_a_task_whose_report_drifted_from_the_type_is_refused(factory_repo):
    drifted = {**BUILD_REPORT, "changed": ["app.py"]}
    del drifted["changed_files"]
    write_workflow(factory_repo, "bad", {"description": "x", "stages": QUICK},
                   tasks={"build": task_text("Build", "m", drifted)})
    message = refused("bad")
    assert "['changed']" in message and "BuildOutput has no field" in message


def test_a_task_that_omits_a_placeholder_is_refused(factory_repo):
    text = task_text("Build", "m", BUILD_REPORT).replace("{{context_handoff_dir}}", "")
    write_workflow(factory_repo, "bad", {"description": "x", "stages": QUICK},
                   tasks={"build": text})
    assert "does not mention {{context_handoff_dir}}" in refused("bad")


def test_a_binding_may_narrow_writes_but_never_widen_them(factory_repo):
    write_workflow(factory_repo, "narrow", {
        "description": "x",
        "agents": {"planner": {"from": "planner", "writes": ["specs/api/"]}},
        "stages": [{"plan": {"agent": "planner"}}]})
    loaded = workflow.load("narrow")
    assert next(a for a in loaded.cfg.agents if a.name == "planner").writes == ["specs/api/"]

    write_workflow(factory_repo, "wide", {
        "description": "x",
        "agents": {"planner": {"from": "planner", "writes": ["specs/", "src/"]}},
        "stages": [{"plan": {"agent": "planner"}}]})
    assert "writes ['src/'] are not covered" in refused("wide")


def test_a_binding_may_append_to_an_identity_but_never_replace_it(factory_repo):
    write_workflow(factory_repo, "bad", {
        "description": "x",
        "agents": {"builder": {"from": "builder", "system": "agents/other.md"}},
        "stages": QUICK})
    assert "system" in refused("bad") and "extra" in refused("bad").lower()

    write_workflow(factory_repo, "missing", {
        "description": "x",
        "agents": {"builder": {"from": "builder", "system_append": ["agents/nope.md"]}},
        "stages": QUICK})
    assert "system_append agents/nope.md not found" in refused("missing")


def test_an_alias_binds_a_roster_agent_under_a_new_name(factory_repo):
    write_workflow(factory_repo, "aliased", {
        "description": "x",
        "agents": {"fixer": {"from": "builder", "thinking": "high"}},
        "stages": [{"build": {"agent": "builder"}},
                   {"verify": {"fix": {"agent": "fixer"}}},
                   {"commit": {"of": "build"}}]})
    loaded = workflow.load("aliased")
    assert loaded.required_agents == ["builder", "fixer"]
    fixer = next(a for a in loaded.cfg.agents if a.name == "fixer")
    assert fixer.thinking == "high" and fixer.harness == "fake"

    write_workflow(factory_repo, "orphan", {
        "description": "x",
        "agents": {"fixer": {"from": "nobody"}},
        "stages": QUICK})
    assert "from 'nobody' is not in the roster" in refused("orphan")


def test_a_stage_s_hitl_option_lands_in_the_gate_policy(factory_repo):
    write_workflow(factory_repo, "gated", {
        "description": "x",
        "stages": [{"plan": {"hitl": True}}, {"build": {}}, {"commit": {"of": "build"}}]})
    assert workflow.load("gated").cfg.hitl.gates == {"plan": "on"}
    assert workflow.load("sdlc").cfg.hitl.gates == {}           # no opinion: factory.yaml's


def test_the_stage_output_is_what_the_next_stage_sees(factory_repo):
    loaded = workflow.load("quick")
    assert loaded.steps[1].stage.needs == (BuildOutput,)
    assert loaded.steps[2].stage.output is None                 # commit passes through


def test_a_stage_module_that_breaks_the_contract_is_refused(factory_repo):
    broken = factory_repo / "asf" / "stages" / "half"
    broken.mkdir()
    (broken / "stage.py").write_text("NAME = 'half'\n")
    with pytest.raises(SystemExit, match="does not meet the contract"):
        workflow.load("quick")
