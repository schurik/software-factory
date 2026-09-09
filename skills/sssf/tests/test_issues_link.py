import json
import subprocess

import pytest

from adw_modules import issues
from adw_modules.data_types import IssueRef, IssuesConfig


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


@pytest.fixture
def calls(monkeypatch):
    """Record every forge invocation instead of running one."""
    recorded = []
    replies = {}

    def fake_run(argv, cwd):
        recorded.append(argv)
        return replies.get("next", _completed())

    monkeypatch.setattr(issues, "_run", fake_run)
    return recorded, replies


def test_peek_reads_number_title_and_url(monkeypatch, tmp_path, calls):
    recorded, replies = calls
    replies["next"] = _completed(stdout=json.dumps({
        "number": 42, "title": "floorEuro rounds down on negative amounts",
        "url": "https://github.com/acme/widgets/issues/42",
        "author": {"login": "alex"}}))

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is True
    assert brief.number == 42
    assert brief.title == "floorEuro rounds down on negative amounts"
    assert brief.author == "alex"
    assert recorded[0] == ["gh", "issue", "view", "42", "--repo", "acme/widgets",
                           "--json", "number,title,url,author"]


def test_peek_never_raises_when_the_forge_refuses(tmp_path, calls):
    recorded, replies = calls
    replies["next"] = _completed(stderr="gh: not authenticated", returncode=1)

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is False
    assert brief.title == ""
    assert brief.number == 42          # what we asked for is still worth knowing


def test_peek_never_raises_on_junk_output(tmp_path, calls):
    recorded, replies = calls
    replies["next"] = _completed(stdout="not json at all")

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is False


@pytest.mark.parametrize("stdout", ["null", "[]"])
def test_peek_never_raises_when_json_is_not_an_object(tmp_path, calls, stdout):
    recorded, replies = calls
    replies["next"] = _completed(stdout=stdout)

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is False


def test_peek_falls_back_to_the_asked_for_number_when_it_is_not_numeric(tmp_path, calls):
    recorded, replies = calls
    replies["next"] = _completed(stdout=json.dumps({
        "number": "", "title": "some title",
        "url": "https://github.com/acme/widgets/issues/42",
        "author": {"login": "alex"}}))

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is False
    assert brief.number == 42


from adw_modules import git_helper
from adw_modules.data_types import LinkedBranchRequest


@pytest.fixture
def forge(monkeypatch):
    """Record `gh` calls and `git fetch`es without running either."""
    # `local_branches` is what `branch_exists` answers from — it has to
    # distinguish the BASE branch (which exists, so `--base` is passed) from the
    # RUN's branch (which usually does not, so the fetch happens).
    state = {"gh": _completed(), "fetch": _completed(),
             "local_branches": {"main"},
             "head": "0" * 40, "calls": [], "fetched": []}

    def fake_run(argv, cwd):
        state["calls"].append(argv)
        return state["gh"]

    def fake_fetch(cwd, remote, branch):
        state["fetched"].append((remote, branch))
        return state["fetch"]

    monkeypatch.setattr(issues, "_run", fake_run)
    monkeypatch.setattr(issues.git_helper, "fetch_branch", fake_fetch)
    monkeypatch.setattr(issues.git_helper, "branch_exists",
                        lambda cwd, name: name in state["local_branches"])
    monkeypatch.setattr(issues.git_helper, "rev", lambda cwd, ref="HEAD": state["head"])
    return state


def _request(branch="sssf/a1b2c3d4-42-fix-rounding", base_ref="main"):
    return LinkedBranchRequest(ref=IssueRef(number=42, project="acme/widgets"),
                               branch=branch, base_ref=base_ref, remote="origin")


def test_develop_creates_fetches_and_reports_the_head(tmp_path, forge):
    forge["gh"] = _completed(
        stdout="https://github.com/acme/widgets/tree/sssf/a1b2c3d4-42-fix-rounding\n")
    forge["head"] = "abc123" + "0" * 34

    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())

    assert result.ok is True
    assert result.created is True
    assert result.branch == "sssf/a1b2c3d4-42-fix-rounding"
    assert result.head == "abc123" + "0" * 34
    assert forge["calls"][0] == [
        "gh", "issue", "develop", "42", "--repo", "acme/widgets",
        "--name", "sssf/a1b2c3d4-42-fix-rounding", "--base", "main"]
    assert forge["fetched"] == [("origin", "sssf/a1b2c3d4-42-fix-rounding")]


def test_develop_omits_base_when_it_is_not_a_branch_name(tmp_path, forge):
    issues.develop(tmp_path, IssuesConfig(project="acme/widgets"),
                   _request(base_ref="3f9a1c2d"))
    assert "--base" not in forge["calls"][0]


def test_develop_skips_the_fetch_when_the_branch_is_already_local(tmp_path, forge):
    """A rerun: gh reuses the linked branch, and a fetch would be rejected as a
    non-fast-forward the moment the earlier run committed anything."""
    forge["local_branches"].add("sssf/a1b2c3d4-42-fix-rounding")
    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())
    assert result.ok is True
    assert forge["fetched"] == []


def test_develop_failing_reports_nothing_created(tmp_path, forge):
    forge["gh"] = _completed(stderr="could not create linked branch", returncode=1)
    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())
    assert result.ok is False
    assert result.created is False
    assert "could not create linked branch" in " ".join(result.notes)


def test_develop_reports_a_created_branch_it_could_not_fetch(tmp_path, forge):
    """The one state a caller must treat differently: the remote branch EXISTS.
    Reusing its name locally would diverge from it and be rejected at push."""
    forge["gh"] = _completed(
        stdout="https://github.com/acme/widgets/tree/sssf/a1b2c3d4-42-fix-rounding\n")
    forge["fetch"] = _completed(stderr="could not read from remote", returncode=1)

    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())

    assert result.ok is False
    assert result.created is True
    assert result.branch == "sssf/a1b2c3d4-42-fix-rounding"


def test_develop_never_raises_when_the_forge_cli_is_not_installed(tmp_path):
    """The spec's own acceptance criterion (`docs/phase-8-linked-branches.md`,
    Verification): a run with `develop_command` pointed at a binary that does
    not exist must finish accepted and say why the branch is not linked — not
    die with a traceback. This deliberately does NOT monkeypatch `issues._run`
    — that would only prove the mock behaves, not that the real function does.
    A binary that genuinely is not on PATH is fast and hermetic to invoke for
    real.
    """
    config = IssuesConfig(project="acme/widgets",
                          develop_command=["definitely-not-a-real-binary-xyz",
                                          "issue", "develop"])

    result = issues.develop(tmp_path, config, _request())

    assert result.ok is False
    assert result.created is False
    assert result.notes          # the reason travels with the result, not a traceback


def test_peek_never_raises_when_the_forge_cli_is_not_installed(tmp_path):
    """Same acceptance criterion, for the OTHER caller of `_run` that runs
    before a run exists: `peek()` is called from `branches.plan()` ahead of the
    Tracer's construction in `session.py`, so a raise here has no trace row, no
    session row and no console line to explain it — the least debuggable
    failure surface in the system, over a branch name.
    """
    config = IssuesConfig(project="acme/widgets",
                          fetch_command=["definitely-not-a-real-binary-xyz",
                                        "issue", "view"])

    brief = issues.peek(tmp_path, config, IssueRef(number=42))

    assert brief.ok is False
    assert brief.number == 42
