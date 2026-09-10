"""The write boundary: `tools:` is a capability list, `writes:` is enforced.

Two halves, tested separately because they fail separately: `permitted()` is
the policy (globs, protected files, the session runtime), and `enforce()` is
the consequence — roll back what the agent introduced, name what cannot be
undone, raise.

Every test here runs against a real git repository, because the mechanism IS
`git diff --numstat` plus `git ls-files --others`: a mocked git would test the
mock, and the `git checkout adws/` case in the module's own docstring is only
visible when git is real.
"""

from __future__ import annotations

import pytest

from adw_modules import permissions
from adw_modules.data_types import ConfigDefaults

from conftest import agent, config, git


@pytest.fixture
def run(make_run):
    return make_run(config(agent("builder")))


def commit(run, message: str = "work") -> None:
    git(run.repo_root, "add", "-A")
    git(run.repo_root, "commit", "-q", "-m", message)


# ── permitted(): the policy ──────────────────────────────────────────────────

def test_writes_none_is_unrestricted():
    cfg = config()
    assert permissions.permitted("src/app.py", agent("a", writes=None), cfg)


def test_writes_empty_list_is_read_only():
    cfg = config()
    assert not permissions.permitted("src/app.py", agent("scout", writes=[]), cfg)


def test_a_directory_prefix_grants_everything_under_it():
    cfg = config()
    planner = agent("planner", writes=["specs/"])
    assert permissions.permitted("specs/plan.md", planner, cfg)
    assert permissions.permitted("specs/nested/deep.md", planner, cfg)
    assert not permissions.permitted("src/app.py", planner, cfg)


def test_a_star_does_not_cross_a_path_separator():
    """`adws/adw_*.py` means the ADW scripts, not everything under adws/.

    fnmatch would let `*` cross `/` and quietly widen every pattern in the
    roster — a `writes:` entry that grants more than it reads as.
    """
    assert permissions._matches("adws/adw_plan.py", "adws/adw_*.py")
    assert not permissions._matches("adws/adw_data/sessions/x/y.py", "adws/adw_*.py")


def test_double_star_is_how_you_say_across_directories():
    assert permissions._matches("src/deep/nested/app.py", "src/**/*.py")
    cfg = config()
    a = agent("a", writes=["src/**/*.py"])
    assert permissions.permitted("src/deep/nested/app.py", a, cfg)


def test_a_glob_write_grant_does_not_reach_a_nested_path():
    cfg = config()
    a = agent("a", writes=["adws/adw_*.py"])
    assert permissions.permitted("adws/adw_plan.py", a, cfg)
    assert not permissions.permitted("adws/nested/adw_plan.py", a, cfg)


def test_protected_files_are_off_limits_to_an_unrestricted_agent():
    cfg = config()
    assert not permissions.permitted("adws/adw_modules/gates.py", agent("a"), cfg)


def test_naming_a_protected_path_is_what_unlocks_it():
    """The maintainer agent that is SUPPOSED to edit the factory says so."""
    cfg = config()
    maintainer = agent("maintainer", writes=["adws/adw_modules/"])
    assert permissions.permitted("adws/adw_modules/gates.py", maintainer, cfg)


def test_the_session_runtime_is_writable_even_for_a_read_only_agent():
    """A `writes: []` agent is read-only w.r.t. the REPO, never mute."""
    cfg = config()
    assert permissions.permitted("adws/adw_data/sessions/x/report.md",
                                 agent("scout", writes=[]), cfg)


def test_the_runtime_grant_follows_a_moved_data_dir():
    cfg = config(defaults=ConfigDefaults(harness="fake", model="fake",
                                         data_dir="var/sssf"))
    scout = agent("scout", writes=[])
    assert permissions.permitted("var/sssf/sessions/x/report.md", scout, cfg)
    assert not permissions.permitted("adws/adw_data/sessions/x/report.md", scout, cfg)


# ── snapshot()/changed_paths(): what counts as a change ──────────────────────

def test_an_untracked_file_registers(run):
    before = permissions.snapshot(run)
    (run.repo_root / "new.txt").write_text("x")
    assert permissions.changed_paths(before, permissions.snapshot(run)) == ["new.txt"]


def test_editing_an_already_dirty_file_registers(run):
    """numstat counts, not a boolean — an edit on top of an edit is a change."""
    (run.repo_root / "README.md").write_text("# fixture\nline\n")
    before = permissions.snapshot(run)
    (run.repo_root / "README.md").write_text("# fixture\nline\nanother\n")
    assert permissions.changed_paths(before, permissions.snapshot(run)) == ["README.md"]


def test_a_reversion_registers_as_a_change(run):
    """The `git checkout adws/` case: clean afterwards is still a modification."""
    (run.repo_root / "README.md").write_text("# fixture\nengineer's work\n")
    before = permissions.snapshot(run)
    git(run.repo_root, "checkout", "--", "README.md")
    assert permissions.changed_paths(before, permissions.snapshot(run)) == ["README.md"]


def test_gitignored_paths_never_appear(run):
    before = permissions.snapshot(run)
    (run.repo_root / "adws" / "adw_data").mkdir(parents=True, exist_ok=True)
    (run.repo_root / "adws" / "adw_data" / "noise.json").write_text("{}")
    assert permissions.changed_paths(before, permissions.snapshot(run)) == []


# ── enforce(): the consequence ───────────────────────────────────────────────

def test_a_permitted_change_is_returned_not_raised(run):
    builder = run.cfg.agents[0]
    before = permissions.snapshot(run)
    (run.repo_root / "src.py").write_text("print('hi')\n")
    assert permissions.enforce(run, None, builder, before) == ["src.py"]


def test_an_unauthorized_new_file_is_deleted_and_the_phase_dies(run):
    scout = agent("scout", writes=[])
    before = permissions.snapshot(run)
    (run.repo_root / "sneaky.py").write_text("print('hi')\n")

    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.enforce(run, None, scout, before)

    assert "sneaky.py" in str(caught.value)
    assert "deleted" in str(caught.value)
    assert not (run.repo_root / "sneaky.py").exists()


def test_an_unauthorized_edit_to_a_clean_file_is_rolled_back(run):
    scout = agent("scout", writes=[])
    before = permissions.snapshot(run)
    (run.repo_root / "README.md").write_text("rewritten by an agent\n")

    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.enforce(run, None, scout, before)

    assert "rolled back" in str(caught.value)
    assert (run.repo_root / "README.md").read_text() == "# fixture\n"


def test_uncommitted_engineer_work_is_never_discarded_to_tidy_up(run):
    """A path already dirty when the agent started is left exactly as it is.

    Rolling it back would commit the harm this module exists to prevent, using
    the cleanup as the weapon.
    """
    (run.repo_root / "README.md").write_text("# fixture\nENGINEER'S UNSAVED WORK\n")
    before = permissions.snapshot(run)
    (run.repo_root / "README.md").write_text("# fixture\nENGINEER'S UNSAVED WORK\nagent\n")

    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.enforce(run, None, agent("scout", writes=[]), before)

    assert "left as-is (was already modified)" in str(caught.value)
    assert "ENGINEER'S UNSAVED WORK" in (run.repo_root / "README.md").read_text()


def test_an_agent_that_reverted_engineer_work_says_so_loudly(run):
    """Content that was never committed cannot be reconstructed — say it."""
    (run.repo_root / "README.md").write_text("# fixture\nENGINEER'S UNSAVED WORK\n")
    before = permissions.snapshot(run)
    git(run.repo_root, "checkout", "--", "README.md")

    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.enforce(run, None, agent("scout", writes=[]), before)

    assert "REVERTED-BY-AGENT" in str(caught.value)
    assert "cannot restore" in str(caught.value)


def test_the_breach_message_names_the_scope_that_was_violated(run):
    before = permissions.snapshot(run)
    (run.repo_root / "src.py").write_text("x")
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.enforce(run, None, agent("planner", writes=["specs/"]), before)
    assert "limited to ['specs/']" in str(caught.value)


def test_a_protected_path_breach_names_what_is_protected(run):
    before = permissions.snapshot(run)
    (run.repo_root / "adws" / "adw_modules").mkdir(parents=True)
    (run.repo_root / "adws" / "adw_modules" / "gates.py").write_text("# rewritten\n")
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.enforce(run, None, agent("builder"), before)
    assert "barred from" in str(caught.value)


def test_an_agent_writing_only_where_it_may_passes(run):
    planner = agent("planner", writes=["specs/"])
    before = permissions.snapshot(run)
    (run.repo_root / "specs").mkdir()
    (run.repo_root / "specs" / "plan.md").write_text("# plan\n")
    assert permissions.enforce(run, None, planner, before) == ["specs/plan.md"]
    assert (run.repo_root / "specs" / "plan.md").exists()
