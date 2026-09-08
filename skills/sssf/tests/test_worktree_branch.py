import subprocess
from pathlib import Path

import pytest

from adw_modules import worktree
from adw_modules.data_types import WorktreeConfig, WorktreeRequest


def _git(cwd, *args):
    completed = subprocess.run(["git", *args], cwd=str(cwd),
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A one-commit git repository — the smallest thing worktree.ensure accepts."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "first")
    return root


def test_default_branch_is_unchanged(repo):
    workspace = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=WorktreeConfig()))
    assert workspace.branch == "sssf/a1b2c3d4"


def test_a_given_branch_and_base_commit_are_used(repo):
    head = _git(repo, "rev-parse", "HEAD")
    workspace = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=WorktreeConfig(),
        branch="sssf/a1b2c3d4-42-fix-rounding", base_commit=head))
    assert workspace.branch == "sssf/a1b2c3d4-42-fix-rounding"
    assert workspace.base_commit == head
    assert _git(workspace.repo_root, "rev-parse", "--abbrev-ref", "HEAD") == \
        "sssf/a1b2c3d4-42-fix-rounding"


def test_a_pruned_worktree_reattaches_to_the_recorded_branch(repo):
    """The bug this fixes: a second process must not cut a SECOND branch.

    A successful run's worktree is pruned by design, so `just integrate <id>`
    enters with the directory gone. Recomputing the name would produce a bare
    sssf/<adw_id>, which does not exist — and the run's commits would be
    orphaned on the branch nobody looked for.
    """
    config = WorktreeConfig()
    first = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=config,
        branch="sssf/a1b2c3d4-42-fix-rounding"))
    assert worktree.release(first).startswith("removed")
    assert not Path(first.repo_root).exists()

    # Re-entry knows only the adw_id — exactly what adw_integrate.py has.
    second = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=config))
    assert second.branch == "sssf/a1b2c3d4-42-fix-rounding"


def test_recorded_branch_is_empty_when_nothing_was_recorded(repo):
    assert worktree.recorded_branch(repo, WorktreeConfig(), "deadbeef") == ""


def test_inventory_reports_the_adw_id_of_a_slugged_branch(repo):
    worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=WorktreeConfig(),
        branch="sssf/a1b2c3d4-42-fix-rounding"))
    found = worktree.inventory(repo, WorktreeConfig())
    assert [(info.adw_id, info.branch) for info in found] == [
        ("a1b2c3d4", "sssf/a1b2c3d4-42-fix-rounding")]
