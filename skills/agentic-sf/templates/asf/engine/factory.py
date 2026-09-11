"""The roster as files and folders: `asf/factory.yaml` plus `asf/agents/<name>/`.

factory.yaml is the manifest — defaults, budget, gates, where the trace goes,
how a run's worktree is cut and landed. It holds NO agents. An agent is a
directory: `agent.yaml` for what the machinery needs (model, thinking, tools,
writes, purpose) and `system.md` for who the agent is. Its name is the
directory's name, so it cannot be misspelt in two places, and its task is not
here at all — that belongs to the stage that calls it (`engine.tasks`).

What this module produces is the same `SSSFConfig` the rest of the engine has
always run on, so nothing downstream — session, worktree, permissions, the
trace — knows the roster changed shape.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import agents
from .data_types import SSSFConfig

DEFAULT_CONFIG = "asf/factory.yaml"
AGENT_RESERVED = ("name", "prompt_engineering")


def root_of(config_path: str | Path) -> Path:
    """The `asf/` directory a config lives in — where agents, stages and
    workflows are found relative to it."""
    return Path(config_path).parent


def load(config_path: str | Path = DEFAULT_CONFIG) -> SSSFConfig:
    """factory.yaml + every agents/<name>/agent.yaml, merged over defaults."""
    path = Path(config_path)
    if not path.is_file():
        raise SystemExit(f"no config at {path} — is the factory installed here?")
    raw = yaml.safe_load(path.read_text()) or {}
    if "agents" in raw:
        raise SystemExit(f"{path}: `agents:` does not belong in factory.yaml — an agent is "
                         f"a directory under {root_of(path) / 'agents'}, with agent.yaml "
                         f"and system.md in it")
    default_harness = (raw.get("defaults") or {}).get("harness", "")
    raw["agents"] = [_agent_entry(directory, default_harness)
                     for directory in agent_dirs(root_of(path))]
    return agents.merge_defaults(raw)


def agent_dirs(root: Path) -> list[Path]:
    agents_dir = root / "agents"
    if not agents_dir.is_dir():
        raise SystemExit(f"no agents directory at {agents_dir} — is the factory installed?")
    found = sorted(p for p in agents_dir.iterdir() if p.is_dir())
    if not found:
        raise SystemExit(f"{agents_dir} holds no agent — every agent is a directory with "
                         f"agent.yaml and system.md in it")
    return found


def _agent_entry(directory: Path, default_harness: str) -> dict:
    spec = directory / "agent.yaml"
    if not spec.is_file():
        raise SystemExit(f"agent {directory.name!r}: {spec} is missing")
    entry = yaml.safe_load(spec.read_text()) or {}
    if not isinstance(entry, dict):
        raise SystemExit(f"agent {directory.name!r}: {spec} must be a mapping")
    reserved = [key for key in AGENT_RESERVED if key in entry]
    if reserved:
        raise SystemExit(f"agent {directory.name!r}: {spec} sets {reserved} — the name is "
                         f"the directory's and the prompts are the files beside it")
    harness = entry.get("harness", default_harness)
    # A harness-specific identity wins over the neutral one, because the two
    # differ in what they may tell the agent to do (pi's planner fans out to
    # subagent tools; Claude Code has none). The task files never carry that
    # difference — they describe the job, not the tools.
    system = directory / f"system.{harness}.md"
    if not system.is_file():
        system = directory / "system.md"
    if not system.is_file():
        raise SystemExit(f"agent {directory.name!r}: no system.md beside {spec.name}")
    entry["name"] = directory.name
    entry["prompt_engineering"] = {"system": str(system), "user": ""}
    return entry
