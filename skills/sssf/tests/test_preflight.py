"""What `just doctor` asks about the forge CLI, and why presence is not enough.

An authenticated `gh` on PATH used to pass every check here and still be unable
to move a single label: releases before 2.98 ask for Projects (classic) on every
`pr edit` and `issue edit`, and GitHub answers that field with an error. Both
watchers mark a run's outcome with a label, so on an old CLI a failed review is
never marked and the next poll buys it again — which is how one refused thread
came to cost three runs of a real chain.

Only `subprocess.run` and `shutil.which` are stood in for, so the version is
read by the expression that ships, off the line `gh` actually prints.
"""

from __future__ import annotations

import pytest

from adw_modules import preflight
from adw_modules.data_types import SSSFConfig

WATCHED = {"issues": {"enabled": True, "project": "acme/widgets"}}


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


@pytest.fixture
def gh(monkeypatch):
    """`gh` on PATH, answering `--version` with whatever a test hands it."""
    monkeypatch.setattr(preflight.shutil, "which", lambda binary: f"/usr/bin/{binary}")

    def says(line: str) -> None:
        monkeypatch.setattr(preflight.subprocess, "run",
                            lambda *a, **k: _Completed(line))
    return says


def _too_old(findings) -> list:
    return [f for f in findings if "cannot move a label" in f.detail]


def test_a_gh_too_old_to_label_is_a_warning_with_the_upgrade_in_it(gh):
    gh("gh version 2.70.0 (2025-04-11)\n")
    found = _too_old(preflight.forge(SSSFConfig(**WATCHED)))

    assert len(found) == 1
    assert found[0].level == "warn"               # never fatal: it is their toolchain
    assert "upgrade gh" in found[0].fix
    assert "2.70" in found[0].detail              # which one they have, not just "old"


def test_a_gh_new_enough_says_nothing_about_versions(gh):
    gh("gh version 2.98.0 (2026-08-20)\n")
    assert _too_old(preflight.forge(SSSFConfig(**WATCHED))) == []


def test_a_version_that_cannot_be_read_is_not_a_finding(gh):
    """A check that cannot be sure says nothing: refusing on a guess is worse
    than the failure it was guessing about."""
    gh("some fork of gh that does not say\n")
    assert _too_old(preflight.forge(SSSFConfig(**WATCHED))) == []


def test_a_cli_that_cannot_be_run_at_all_is_not_a_finding_either(gh, monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no such file")))
    assert _too_old(preflight.forge(SSSFConfig(**WATCHED))) == []


def test_the_version_is_not_asked_of_a_repository_that_moves_no_labels(gh):
    """Only the paths that edit labels care. A repo with both watchers off is
    told about `gh` for what it did turn on, and nothing about a subcommand it
    never runs."""
    gh("gh version 2.70.0 (2025-04-11)\n")
    cfg = SSSFConfig(worktree={"integration": {"mode": "pr", "open_pr": True}})
    assert _too_old(preflight.forge(cfg)) == []


def test_a_forge_that_is_not_gh_is_never_version_checked(gh):
    """`glab`, or a script standing in for one, versions itself its own way. A
    number read out of the wrong CLI would be a confident wrong answer."""
    gh("glab version 1.2.3\n")
    cfg = SSSFConfig(issues={"enabled": True, "project": "acme/widgets",
                             "list_command": ["glab", "issue", "list"],
                             "state_command": ["glab", "issue", "update"]})
    assert _too_old(preflight.forge(cfg)) == []


def test_the_expression_reads_the_line_gh_actually_prints(monkeypatch):
    lines = {"gh version 2.70.0 (2025-04-11)": (2, 70),
             "gh version 2.100.1-ubuntu (2026-09-03)": (2, 100),
             "no version in here at all": ()}
    for line, expected in lines.items():
        monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _Completed(line))
        assert preflight._gh_version("gh") == expected, line
