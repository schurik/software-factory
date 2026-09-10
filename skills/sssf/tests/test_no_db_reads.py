"""The factory writes the trace db and never reads it. A test, not a promise.

`1a099f0` established the rule and every module since has been written to it:
`tracer.py` is the one place sqlite is opened, everything else answers from the
session directory through `adw_modules/artifacts.py`. It buys three things —
a run works with the db deleted, a run works while the visualizer holds it, and
the day the events go to a hosted API nothing but `tracer.py` has to change —
and all three are lost the first time somebody adds `SELECT` to a module
because it was the shortest path.

That is exactly the kind of regression a code review misses and a structural
test catches, so it is asserted over the shipped source.

The db is still a legitimate READ for two audiences and neither is the factory:
the visualizer (`apps/visualizer/`), and the orchestrator observing a run — see
`references/observability.md`. Tests read it too, to assert. None of those is a
path a run depends on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import ADWS, SKILL_ROOT

TRACER = ADWS / "adw_modules" / "tracer.py"


def factory_sources() -> list[Path]:
    """Everything the factory stamps and runs, minus the one module that may."""
    paths = sorted(ADWS.rglob("*.py")) + sorted((SKILL_ROOT / "scripts").glob("*.py"))
    return [p for p in paths if p != TRACER and "__pycache__" not in p.parts]


def imported_names(source: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_there_are_sources_to_check():
    """A guard that silently checks nothing is worse than no guard."""
    assert len(factory_sources()) > 20
    assert TRACER.is_file()


@pytest.mark.parametrize("source", factory_sources(), ids=lambda p: p.name)
def test_only_the_tracer_opens_sqlite(source: Path):
    assert "sqlite3" not in imported_names(source), (
        f"{source.relative_to(SKILL_ROOT)} imports sqlite3. The factory answers "
        f"every question about a session from that session's own directory "
        f"(adw_modules/artifacts.py) — a read here means a run stops working "
        f"when the db is deleted.")


def test_the_tracer_is_the_one_that_does():
    """The rule is 'only here', not 'nowhere' — prove the exemption is real."""
    assert "sqlite3" in imported_names(TRACER)


def test_artifacts_is_the_module_that_answers_instead():
    """The replacement has to exist, or the rule above is just an absence."""
    from adw_modules import artifacts
    for name in ("read_run", "recorded_phases", "max_phase_seq", "scan",
                 "running_pids", "statuses", "pr_urls", "adw_names"):
        assert callable(getattr(artifacts, name))
