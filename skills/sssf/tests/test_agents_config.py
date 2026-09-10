"""`load_config` merges defaults key by key; `validate` collects every problem.

The merge is where a silent misconfiguration lives: an agent that overrides one
`harness_options` key must not lose the rest of the block, and the block it
inherits is the one for the harness IT runs on — not the roster's default
harness. Both are one line in `agents.load_config` and both were bugs waiting
to be written.

`validate` is checked for the shape every ADW depends on: collect all problems,
raise ONE SystemExit. A validator that raised on the first would tell an
engineer about one typo per run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from adw_modules import agents

from conftest import write_prompts

ROSTER = {
    "defaults": {
        "harness": "fake",
        "model": "fake",
        "thinking": "medium",
        "timeout_seconds": 900,
        "tools": ["Read", "Bash"],
        "harness_engineering": ["shared.ts"],
        "writes": ["src/"],
        "harness_options": {
            "fake": {"strict": True, "replies": [{"envelope": {"status": "success"}}]},
            "claude_code": {"safe_mode": True, "permission_mode": "acceptEdits"},
        },
        "data_dir": "adws/adw_data",
    },
    "agents": [
        {"name": "planner",
         "prompt_engineering": {"system": "adws/adw_data/prompt_engineering/planner/system.md",
                                "user": "adws/adw_data/prompt_engineering/planner/user.md"}},
    ],
}


def roster_file(tmp_path: Path, raw: dict) -> str:
    path = tmp_path / "sssf.config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return str(path)


def load(tmp_path: Path, **patch):
    raw = yaml.safe_load(yaml.safe_dump(ROSTER))        # deep copy
    for key, value in patch.items():
        raw["agents"][0][key] = value
    return agents.load_config(roster_file(tmp_path, raw))


# ── load_config: the defaults merge ──────────────────────────────────────────

def test_an_agent_inherits_every_scalar_default(tmp_path):
    planner = load(tmp_path).agents[0]
    assert (planner.harness, planner.model, planner.thinking) == ("fake", "fake", "medium")
    assert planner.timeout_seconds == 900
    assert planner.tools == ["Read", "Bash"]
    assert planner.writes == ["src/"]
    assert planner.harness_engineering == ["shared.ts"]


def test_an_agent_s_own_value_wins_over_the_default(tmp_path):
    planner = load(tmp_path, model="other", timeout_seconds=60).agents[0]
    assert planner.model == "other" and planner.timeout_seconds == 60


def test_writes_empty_list_survives_the_merge(tmp_path):
    """`setdefault` must not read `[]` as absent — that would hand a read-only
    agent the roster-wide write grant."""
    assert load(tmp_path, writes=[]).agents[0].writes == []


def test_tools_empty_list_survives_the_merge(tmp_path):
    assert load(tmp_path, tools=[]).agents[0].tools == []


def test_harness_options_are_inherited_from_the_block_for_this_harness(tmp_path):
    planner = load(tmp_path).agents[0]
    assert planner.harness_options == {"strict": True,
                                       "replies": [{"envelope": {"status": "success"}}]}


def test_an_agent_on_another_harness_inherits_that_harness_s_block(tmp_path):
    """The keying is by the AGENT's harness, not the roster's default."""
    planner = load(tmp_path, harness="claude_code").agents[0]
    assert planner.harness_options == {"safe_mode": True,
                                       "permission_mode": "acceptEdits"}


def test_overriding_one_harness_option_keeps_the_rest(tmp_path):
    """Overriding `permission_mode` must not silently drop `safe_mode`."""
    planner = load(tmp_path, harness="claude_code",
                   harness_options={"permission_mode": "bypassPermissions"}).agents[0]
    assert planner.harness_options == {"safe_mode": True,
                                       "permission_mode": "bypassPermissions"}


def test_an_agent_on_a_harness_with_no_defaults_block_gets_an_empty_one(tmp_path):
    planner = load(tmp_path, harness="pi").agents[0]
    assert planner.harness_options == {}


def test_a_config_with_no_defaults_block_still_loads(tmp_path):
    raw = {"agents": ROSTER["agents"]}
    cfg = agents.load_config(roster_file(tmp_path, raw))
    assert cfg.agents[0].name == "planner"


# ── resolve ──────────────────────────────────────────────────────────────────

def test_resolve_finds_an_agent_by_name(tmp_path):
    cfg = load(tmp_path)
    assert agents.resolve(cfg, "planner").name == "planner"


def test_resolve_lists_what_exists_when_it_cannot(tmp_path):
    cfg = load(tmp_path)
    with pytest.raises(SystemExit) as caught:
        agents.resolve(cfg, "typo")
    assert "['planner']" in str(caught.value)


# ── validate: every problem, one exit ────────────────────────────────────────

@pytest.fixture
def in_repo(repo, monkeypatch):
    """`validate` resolves prompt paths against the MAIN checkout."""
    monkeypatch.chdir(repo)
    return repo


def test_a_valid_roster_passes(in_repo, tmp_path):
    write_prompts(in_repo, "planner")
    agents.validate(load(tmp_path), ["planner"])


def test_a_missing_agent_is_named(in_repo, tmp_path):
    write_prompts(in_repo, "planner")
    with pytest.raises(SystemExit) as caught:
        agents.validate(load(tmp_path), ["builder"])
    assert "builder" in str(caught.value)


def test_a_missing_prompt_file_is_named(in_repo, tmp_path):
    with pytest.raises(SystemExit) as caught:
        agents.validate(load(tmp_path), ["planner"])
    message = str(caught.value)
    assert "system prompt not found" in message and "user prompt not found" in message


def test_an_unknown_harness_is_named_with_what_exists(in_repo, tmp_path):
    write_prompts(in_repo, "planner")
    with pytest.raises(SystemExit) as caught:
        agents.validate(load(tmp_path, harness="codex"), ["planner"])
    assert "is not one of" in str(caught.value) and "fake" in str(caught.value)


def test_a_negative_timeout_is_refused(in_repo, tmp_path):
    """`0` disables the clock; a negative is a typo that would read as `0`."""
    write_prompts(in_repo, "planner")
    with pytest.raises(SystemExit) as caught:
        agents.validate(load(tmp_path, timeout_seconds=-1), ["planner"])
    assert "timeout_seconds must be >= 0" in str(caught.value)


def test_every_problem_is_collected_into_one_exit(in_repo, tmp_path):
    """One run, one report. A validator that stopped at the first problem
    would cost an engineer a run per typo."""
    with pytest.raises(SystemExit) as caught:
        agents.validate(load(tmp_path, timeout_seconds=-5, model=""), ["planner", "ghost"])
    message = str(caught.value)
    assert "prompt not found" in message
    assert "timeout_seconds must be >= 0" in message
    assert "model is empty" in message
    assert "ghost" in message


def test_a_harness_specific_model_rule_is_asked_of_that_harness(in_repo, tmp_path):
    """A pi `provider/model-id` pattern on a claude_code agent fails HERE,
    not deep inside the chain."""
    write_prompts(in_repo, "planner")
    with pytest.raises(SystemExit) as caught:
        agents.validate(load(tmp_path, harness="claude_code",
                             model="google/gemini-3.6-flash"), ["planner"])
    assert "pi provider/model-id pattern" in str(caught.value)


def test_a_fake_agent_with_no_script_is_refused(in_repo, tmp_path):
    """The fake harness's own validate_agent, reached through the same seam."""
    write_prompts(in_repo, "planner")
    raw = yaml.safe_load(yaml.safe_dump(ROSTER))
    raw["defaults"]["harness_options"].pop("fake")
    with pytest.raises(SystemExit) as caught:
        agents.validate(agents.load_config(roster_file(tmp_path, raw)), ["planner"])
    assert "needs `replies:` or `script:`" in str(caught.value)


def test_an_unknown_harness_option_key_is_refused(in_repo, tmp_path):
    """`extra="forbid"`: a key that silently does nothing is a lie in the roster."""
    write_prompts(in_repo, "planner")
    with pytest.raises(SystemExit) as caught:
        agents.validate(load(tmp_path, harness_options={"safe_mode": True}), ["planner"])
    assert "harness_options" in str(caught.value)
