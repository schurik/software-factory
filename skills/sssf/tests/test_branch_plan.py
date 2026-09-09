import subprocess

import pytest

from adw_modules import branches
from adw_modules.data_types import (BranchRequest, IssueBrief, IssueRef, LinkedBranch,
                                    SSSFConfig)


def _git(cwd, *args):
    completed = subprocess.run(["git", *args], cwd=str(cwd),
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture
def cfg():
    config = SSSFConfig()
    config.issues.project = "acme/widgets"
    return config


@pytest.fixture
def forge(monkeypatch):
    """No forge and no git: every outward call is stubbed and recorded."""
    # The title is deliberately short enough that `issue_slug` does not truncate
    # it: the locally computed name and the forge's answer are then the SAME
    # string, so a test can tell "the forge's name won" from "nothing happened"
    # only by what it asserts, not by accident of length.
    state = {"brief": IssueBrief(ok=True, number=42, title="floorEuro rounds down"),
             "linked": LinkedBranch(ok=True, created=True, head="abc1234",
                                    branch="sssf/a1b2c3d4-42-flooreuro-rounds-down"),
             "recorded": "", "remote": True, "develop_calls": []}

    monkeypatch.setattr(branches.issues, "peek",
                        lambda tree, config, ref: state["brief"])
    # `_base_ref_of` asks the checkout what it has checked out, and tmp_path is
    # not a git repository — without this every linking test dies in git_helper.
    monkeypatch.setattr(branches.git_helper, "current_branch",
                        lambda main_root: "main")

    def fake_develop(tree, config, request):
        state["develop_calls"].append(request)
        return state["linked"]

    monkeypatch.setattr(branches.issues, "develop", fake_develop)
    monkeypatch.setattr(branches.worktree, "recorded_branch",
                        lambda main_root, config, adw_id: state["recorded"])
    monkeypatch.setattr(branches.git_helper, "has_remote",
                        lambda cwd, remote: state["remote"])
    return state


def test_a_prompt_run_is_named_after_its_prompt(cfg, forge, tmp_path):
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="7b3c9a01",
        prompt="Add tenant export to CSV\n\nlonger body here"))

    assert plan.branch == "sssf/7b3c9a01-add-tenant-export-to-csv"
    assert plan.linked is False
    assert forge["develop_calls"] == []


def test_an_issue_run_is_named_and_linked(cfg, forge, tmp_path):
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4-42-flooreuro-rounds-down"
    assert plan.base_commit == "abc1234"
    assert plan.linked is True
    assert len(forge["develop_calls"]) == 1
    assert forge["develop_calls"][0].branch.startswith("sssf/a1b2c3d4-42-")


def test_link_branch_false_names_but_does_not_link(cfg, forge, tmp_path):
    cfg.issues.link_branch = False
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch.startswith("sssf/a1b2c3d4-42-")
    assert plan.linked is False
    assert plan.base_commit == ""
    assert forge["develop_calls"] == []


def test_a_failed_link_keeps_the_slugged_local_name(cfg, forge, tmp_path):
    forge["linked"] = LinkedBranch(ok=False, created=False,
                                   notes=["gh: not authenticated"])
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4-42-flooreuro-rounds-down"
    assert plan.linked is False
    assert "not authenticated" in " ".join(plan.notes)


def test_a_created_but_unfetchable_branch_falls_back_to_the_bare_name(cfg, forge,
                                                                     tmp_path):
    """The remote branch exists; reusing its name locally would diverge from it."""
    forge["linked"] = LinkedBranch(ok=False, created=True,
                                   branch="sssf/a1b2c3d4-42-flooreuro-rounds-down",
                                   notes=["could not be fetched"])
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4"
    assert plan.linked is False


def test_a_recorded_branch_wins_over_everything(cfg, forge, tmp_path):
    forge["recorded"] = "sssf/a1b2c3d4-42-the-original-name"
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", prompt="something else entirely"))

    assert plan.branch == "sssf/a1b2c3d4-42-the-original-name"
    assert forge["develop_calls"] == []


def test_no_remote_means_no_forge_call(cfg, forge, tmp_path):
    forge["remote"] = False
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.linked is False
    assert forge["develop_calls"] == []


def test_worktrees_disabled_skips_the_whole_thing(cfg, forge, tmp_path):
    cfg.worktree.enabled = False
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == ""
    assert forge["develop_calls"] == []


def test_a_joining_run_recovers_an_existing_slugged_branch_via_git(cfg, forge, tmp_path):
    """The recompute bug re-entered through a different door: the metadata file
    is gitignored and local-only, so a fresh clone, a `git clean -fdx`, or a
    second checkout of the same repository loses it while the branch — a real
    git ref — survives. A joining run (no issue, no prompt: `adw_integrate`,
    `adw_pr_review`) must land on that branch, not fall through to the bare
    <prefix><adw_id> and cut a second one from the base, orphaning the run's
    commits on the first.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    _git(repo, "branch", "sssf/a1b2c3d4-42-the-original-name")

    plan = branches.plan(cfg, BranchRequest(main_root=repo, adw_id="a1b2c3d4"))

    assert plan.branch == "sssf/a1b2c3d4-42-the-original-name"
    assert forge["develop_calls"] == []


def test_a_joining_run_with_two_matching_branches_falls_through(cfg, forge, tmp_path):
    """Two matches is ambiguous — "which one?" has no good answer — so this
    stays conservative and falls through to today's behaviour (the bare name)
    rather than guessing.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    _git(repo, "branch", "sssf/a1b2c3d4-42-first-name")
    _git(repo, "branch", "sssf/a1b2c3d4-99-second-name")

    plan = branches.plan(cfg, BranchRequest(main_root=repo, adw_id="a1b2c3d4"))

    assert plan.branch == "sssf/a1b2c3d4"


def test_the_forges_branch_name_wins_when_it_still_decodes(cfg, forge, tmp_path):
    """The assertion the existing fixture cannot make: `forge`'s stub branch is
    IDENTICAL to the locally computed name by construction (the fixture's own
    comment says so), so `result.branch = linked.branch` passes even with that
    line deleted. Make the forge return something DIFFERENT but still
    decodable, and assert its answer — not the locally computed one — wins.
    """
    forge["linked"] = LinkedBranch(ok=True, created=True, head="abc1234",
                                   branch="sssf/a1b2c3d4-42-a-sanitised-title")

    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4-42-a-sanitised-title"
    assert plan.linked is True
    assert plan.base_commit == "abc1234"


def test_an_undecodable_forge_branch_falls_back_instead_of_being_adopted(cfg, forge,
                                                                         tmp_path):
    """If the forge ever answered with a name that does not decode to THIS
    adw_id, adopting it blindly would make `session_of` return "" for it
    forever — and `adw_pr_review` refuses to run on a pull request whose head
    branch it cannot decode, on the one path where a human already asked for a
    review. The guard must route this down the same fallback as a fetch
    failure, not adopt it.
    """
    forge["linked"] = LinkedBranch(ok=True, created=True, head="abc1234",
                                   branch="sssf/deadbeef-42-somebody-elses-run")

    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4"
    assert plan.linked is False


def test_an_unborn_head_still_produces_a_usable_plan(cfg, forge, tmp_path, monkeypatch):
    """A repo with a remote configured but zero commits (a fresh `git init`) has
    an unborn HEAD. `has_remote` only reads `git remote` output, so it does not
    rule this out — and the real `current_branch` RAISES on an unborn HEAD
    (`rev-parse --abbrev-ref HEAD` exits 128). `_base_ref_of` must check
    `ref_exists` first and never reach `current_branch` here, so the whole
    chain does not die over a branch name before a single phase opens.
    """
    monkeypatch.setattr(branches.git_helper, "ref_exists",
                        lambda main_root, ref: False)

    def _current_branch_raises_like_the_real_one(main_root):
        raise RuntimeError("fatal: ambiguous argument 'HEAD': unknown revision")

    monkeypatch.setattr(branches.git_helper, "current_branch",
                        _current_branch_raises_like_the_real_one)

    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4-42-flooreuro-rounds-down"
    assert plan.linked is True
    assert forge["develop_calls"][0].base_ref == ""
