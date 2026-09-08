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
