"""Fixtures for the factory's own tests.

Two things every test here needs and nothing else provides:

  * `adw_modules` on the path. The modules under test are TEMPLATES — they are
    stamped into a target repo as `adws/adw_modules/`, so they are not an
    installed package and there is nothing to `pip install -e`. `templates/adws`
    is the directory they are stamped FROM, and importing them from there is
    importing exactly what a stamped repo will run.
  * A real git repository. `session.ensure` cuts a worktree, `permissions.py`
    fingerprints `git diff`, and `worktree.py` is git end to end. Mocking git
    would test the mock; a `git init` in a tmp_path costs milliseconds and tests
    the thing.

The trace db is written by every run and read by NONE of the factory — see
`adw_modules/artifacts.py`. Tests may read it as an ASSERTION source, which is
not a production path, and `test_no_db_reads.py` is what keeps that asymmetry
honest.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = TESTS_DIR.parent
ADWS = SKILL_ROOT / "templates" / "adws"

if str(ADWS) not in sys.path:
    sys.path.insert(0, str(ADWS))

from adw_modules.data_types import (AgentConfig, ConfigDefaults, PromptEngineering,  # noqa: E402
                                    RunSpec, SSSFConfig, Workspace, WorktreeConfig)
from adw_modules.runner import Run  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository with one commit — the least a worktree can branch from."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "sssf tests")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("# fixture\n")
    (root / ".gitignore").write_text("adws/adw_data/\n.sssf-worktrees/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def agent(name: str, **overrides) -> AgentConfig:
    """A roster entry on the fake harness, scripted by the caller."""
    fields = {
        "name": name,
        "harness": "fake",
        "model": "fake",
        "prompt_engineering": PromptEngineering(
            system=f"adws/adw_data/prompt_engineering/{name}/system.md",
            user=f"adws/adw_data/prompt_engineering/{name}/user.md"),
    }
    fields.update(overrides)
    return AgentConfig(**fields)


def write_prompts(root: Path, *names: str) -> None:
    """The system.md/user.md pair `agents.validate` insists on, per agent."""
    for name in names:
        directory = root / "adws" / "adw_data" / "prompt_engineering" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "system.md").write_text(f"You are {name}.\n")
        (directory / "user.md").write_text("{{prompt}}\n\nHandoff: {{context_handoff_dir}}\n")


def config(*agents: AgentConfig, **overrides) -> SSSFConfig:
    fields = {
        "agents": list(agents),
        "defaults": ConfigDefaults(harness="fake", model="fake"),
        "worktree": WorktreeConfig(enabled=False),
    }
    fields.update(overrides)
    return SSSFConfig(**fields)


class FakeTracer:
    """Records what a run traced, without a database.

    The tracer is write-only by design, so a test double that only remembers is
    a complete stand-in for it — and the unit tests that use it are then testing
    gates, permissions and limits rather than sqlite. `test_e2e_fake.py` uses
    the REAL tracer, because an end-to-end run must prove the db writes work.
    """

    def __init__(self) -> None:
        self.events: list = []
        self.calls: list[tuple] = []

    def event(self, record) -> None:
        self.events.append(record)

    def types(self, kind: str) -> list:
        return [e for e in self.events if e.type == kind]

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


@pytest.fixture
def make_run(repo: Path):
    """Build a real `Run` against the fixture repo, with a recording tracer.

    Everything the run touches is real — the session directory, the envelopes,
    `events.jsonl` — except the db. That is the split the factory itself makes:
    the record is files, the db is a mirror.
    """
    def build(cfg: SSSFConfig, adw_id: str = "testrun0", **spec) -> Run:
        workspace = Workspace(main_root=repo, repo_root=repo)
        return Run(RunSpec(cfg=cfg, adw_id=adw_id, engineer="tester",
                           workspace=workspace, **spec), FakeTracer())
    return build
