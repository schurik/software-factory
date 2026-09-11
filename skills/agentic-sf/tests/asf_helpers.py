"""What the tests do to a stamped repo: put the roster on the fake harness,
script the agents, wire a check, run the runner as a subprocess, read the
record back."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

SKILL_ROOT = Path(__file__).resolve().parent.parent
INSTALL = SKILL_ROOT / "scripts" / "install.py"
DB = "adws/adw_data/sssf.db"


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def install(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """The installer as a user runs it: a subprocess, from the target repo."""
    return subprocess.run([sys.executable, str(INSTALL), *args], cwd=cwd,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def asf(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """`asf/asf.py …` as a user runs it, with no terminal on stdin — so a gate
    that fires suspends instead of prompting, exactly as under a watcher."""
    return subprocess.run([sys.executable, "asf/asf.py", *args], cwd=cwd,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, **(env or {})})


def envelope(**fields) -> dict:
    return {"status": "success", "summary": "scripted", **fields}


def fake_roster(repo: Path, **agents: list[dict]) -> None:
    """Put factory.yaml on the fake harness and script each named agent.

    `agents` is {name: replies}; a name the starter roster lacks becomes a new
    agent directory with a one-line identity, so a test can bind whatever it
    needs.
    """
    config = repo / "asf" / "factory.yaml"
    raw = yaml.safe_load(config.read_text())
    raw["defaults"].update({"harness": "fake", "model": "fake", "tools": None,
                            "harness_options": {"fake": {}}})
    config.write_text(yaml.safe_dump(raw))
    for name, replies in agents.items():
        directory = repo / "asf" / "agents" / name
        directory.mkdir(parents=True, exist_ok=True)
        spec = directory / "agent.yaml"
        entry = yaml.safe_load(spec.read_text()) if spec.is_file() else {"purpose": f"{name}, scripted"}
        entry.update({"harness": "fake", "harness_options": {"replies": replies}})
        spec.write_text(yaml.safe_dump(entry))
        identity = directory / "system.md"
        if not identity.is_file():
            identity.write_text(f"You are {name}.\n")


def wire(repo: Path, block: str, argv: list[str]) -> None:
    """Replace one quality block's argv in the stamped quality.py."""
    quality = repo / "asf" / "engine" / "quality.py"
    source = quality.read_text()
    pattern = re.compile(rf'argv=(_placeholder\("{block}"\)|\[[^\n]*?\]),[^\n]*')
    replaced, count = pattern.subn(f"argv={json.dumps(argv)},", source, count=1)
    assert count == 1, f"no {block} block to wire in {quality}"
    quality.write_text(replaced)


PY_CHECK = [sys.executable, "-c", "import runpy; runpy.run_path('app.py')"]


def commit_all(repo: Path, message: str = "prepare") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def write_workflow(repo: Path, name: str, spec: dict, tasks: dict[str, str] | None = None,
                   appends: dict[str, str] | None = None) -> Path:
    """A workflow directory the way an engineer would lay one out."""
    directory = repo / "asf" / "workflows" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "workflow.yaml").write_text(yaml.safe_dump({"name": name, **spec}, sort_keys=False))
    for key, text in (tasks or {}).items():
        (directory / "tasks").mkdir(exist_ok=True)
        (directory / "tasks" / f"{key}.md").write_text(text)
    for filename, text in (appends or {}).items():
        (directory / "agents").mkdir(exist_ok=True)
        (directory / "agents" / filename).write_text(text)
    return directory


TASK = """# {title}

{marker}

### prompt

{{{{prompt}}}}

### previous_envelope

{{{{previous_envelope}}}}

### context_handoff_dir

{{{{context_handoff_dir}}}}

## Report

```json
{report}
```
"""


def task_text(title: str, marker: str, report: dict) -> str:
    return TASK.format(title=title, marker=marker, report=json.dumps(report, indent=2))


BUILD_REPORT = {"status": "success", "summary": "<s>", "changed_files": ["app.py"],
                "artifacts": [], "commit_message": "<m>", "notes_for_next_agent": "<n>"}


# ── reading the record back ───────────────────────────────────────────────────

def db_rows(repo: Path, sql: str) -> list[tuple]:
    """The trace db — an ASSERTION source, never a production path."""
    connection = sqlite3.connect(f"file:{repo / DB}?mode=ro", uri=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def adw_id_of(result: subprocess.CompletedProcess) -> str:
    """From the workspace line every run prints first — a suspended run never
    reaches the closing banner that also carries it."""
    match = re.search(r" on asf/(\w{8}) ", result.stdout)
    assert match, result.stdout + result.stderr
    return match.group(1)


def session_dir(repo: Path, adw_id: str) -> Path:
    return repo / "asf" / "data" / "sessions" / adw_id


def run_state(repo: Path, adw_id: str) -> dict:
    return json.loads((session_dir(repo, adw_id) / "run.json").read_text())


def phase_names(repo: Path, adw_id: str) -> list[str]:
    return [row[0] for row in db_rows(
        repo, f"select name from phases where adw_id='{adw_id}' order by seq")]
