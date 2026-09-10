"""The review chain's third outcome: a thread answered by changing nothing.

`pull_requests.HANDOFF_NOTES` tells the builder, in those words, that "an
unaddressed thread with a reason is a better outcome than a change nobody asked
for" — and then the chain used to kill the process when a builder did exactly
that. `commit_all` raised on the clean tree, the exception escaped the commit
phase, and `main()` never reached the report phase, so:

  * the reviewer heard nothing, on the one run they most needed to hear from;
  * the thread kept the reviewer's comment as its last word, which is what
    `pull_requests.actionable` reads as outstanding work — so the watcher
    launched the whole review again on the next poll, and every poll after it;
  * the run died on an unhandled traceback instead of its own exit code.

It was found on a real pull request whose sole open thread asked an agent to
delete a vendored component and install an unfamiliar single-maintainer package.
The builder refused it three times, correctly, and the factory recorded three
failures and paid for all of them.

These tests drive the real `main()`. Only the four things that reach the network
are stood in for — reading the pull request, running the suite, replying, and
commenting — and the builder is the fake harness, scripted to change nothing.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

import pytest
import yaml

import adw_pr_review
from adw_modules import pull_requests, quality
from adw_modules.data_types import (PullRequestContext, PullRequestResult,
                                    QualityCheckResult, QualityResult,
                                    ReviewComment, ReviewThread)

from conftest import git, write_prompts

CONFIG_PATH = "adws/adw_sssf_config/sssf.config.yaml"
ADW_ID = "testpr01"                       # the branch below names this session
THREAD = "PRRT_thread_1"


def _context() -> PullRequestContext:
    """One open pull request on this factory's own branch, with one open thread."""
    return PullRequestContext(
        number=72, project="acme/widgets", url="https://forge/acme/widgets/pull/72",
        title="Do the thing", state="OPEN", author="engineer",
        branch=f"sssf/{ADW_ID}", base_ref="main",
        threads=[ReviewThread(
            thread_id=THREAD, path="app.py", line=12,
            comments=[ReviewComment(comment_id=1, author="reviewer",
                                    body="please use the border-beam package instead",
                                    created_at="2026-09-10T22:00:00Z")])])


def _suite(passed: bool) -> QualityResult:
    """What `quality.run_tests` returns, without a suite to run."""
    check = QualityCheckResult(name="test", area="backend", operation="build",
                               command="true" if passed else "false",
                               returncode=0 if passed else 1, passed=passed,
                               duration_seconds=0.0, output_artifact="",
                               output_tail="" if passed else "1 failed")
    return QualityResult(passed=passed, checks=[check], artifacts=[],
                         failures=[] if passed else ["test: `false` exited 1"])


@pytest.fixture
def review(repo: Path, monkeypatch):
    """A stamped repo with a scripted builder, and a fake forge that records.

    Returns `(build, written)`: `build(replies=..., suite=...)` stamps the roster
    and installs the stand-ins, `written()` reads back every reply, resolve and
    comment the run sent.
    """
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "argv", ["adw_pr_review.py", "72"])
    # `session.ensure` installs SIGTERM/SIGINT handlers on the process it runs
    # in, which here is pytest's. Restored so one test cannot leave the next
    # one unable to be interrupted.
    original = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    sent: dict[str, list] = {"replies": [], "resolved": [], "comments": []}

    def build(*, replies: list[dict], suite: QualityResult | None = None,
              writes: list[str] | None = None):
        raw = {
            "defaults": {"harness": "fake", "model": "fake", "data_dir": "adws/adw_data",
                         "timeout_seconds": 60,
                         "protected_files": ["adws/adw_modules/", "adws/adw_*.py"]},
            "worktree": {"enabled": True, "keep_on_success": False},
            "observability": {"db": "adws/adw_data/sssf.db"},
            "pull_requests": {"enabled": True, "project": "acme/widgets"},
            "agents": [{
                "name": "builder", "harness": "fake", "model": "fake",
                "purpose": "builder, scripted",
                "writes": writes or [],
                "prompt_engineering": {
                    "system": "adws/adw_data/prompt_engineering/builder/system.md",
                    "user": "adws/adw_data/prompt_engineering/builder/user.md"},
                "harness_options": {"replies": replies},
            }],
        }
        write_prompts(repo, "builder")
        path = repo / CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(raw))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "stamp the factory")

        monkeypatch.setattr(pull_requests, "describe", lambda *a, **k: _context())
        monkeypatch.setattr(quality, "run_tests", lambda run: suite or _suite(True))

        def answer_thread(tree, config, update) -> PullRequestResult:
            sent["replies"].append(update.reply)
            if update.resolve:
                sent["resolved"].append(update.thread_id)
            return PullRequestResult(ok=True, number=update.number, replied=True,
                                     resolved=[update.thread_id] if update.resolve else [])

        def comment(tree, config, update) -> PullRequestResult:
            sent["comments"].append(update.comment)
            return PullRequestResult(ok=True, number=update.number, commented=True)

        monkeypatch.setattr(pull_requests, "answer_thread", answer_thread)
        monkeypatch.setattr(pull_requests, "comment", comment)

    yield build, (lambda: sent)
    for sig, handler in original.items():
        signal.signal(sig, handler)


DECLINED = {"envelope": {
    "status": "success",
    "summary": "Declined: the thread asks for an unfamiliar single-maintainer package "
               "and reverses this branch's own no-new-dependency decision, so no code "
               "was changed.",
    "changed_files": [], "commit_message": ""}}

ADDRESSED = {"writes": {"app.py": "print('reviewed')\n"},
             "envelope": {"status": "success", "summary": "did what the reviewer asked",
                          "changed_files": ["app.py"],
                          "commit_message": "answer the reviewer"}}


# ── the third outcome ────────────────────────────────────────────────────────

def test_a_builder_that_changes_nothing_on_purpose_does_not_kill_the_run(review):
    """The bug, at the only level that would have caught it: `main()` returns."""
    build, written = review
    build(replies=[DECLINED])

    assert adw_pr_review.main(72, CONFIG_PATH) == 0


def test_the_reviewer_is_answered_even_though_nothing_was_committed(review):
    """The report phase is the whole point of the chain's last third. A run that
    changed nothing is the one whose reviewer most needs to be told why."""
    build, written = review
    build(replies=[DECLINED])

    adw_pr_review.main(72, CONFIG_PATH)
    sent = written()
    assert len(sent["replies"]) == 1
    assert "no code was changed" in sent["replies"][0]
    assert len(sent["comments"]) == 1


def test_a_thread_nothing_was_done_about_is_never_resolved(review):
    """A resolved thread tells a reviewer their ask is handled. A refusal is not
    handled — it is a decision a person has to agree with, so the thread stays
    open and carries the factory's reply as its last word."""
    build, written = review
    build(replies=[DECLINED])

    adw_pr_review.main(72, CONFIG_PATH)
    assert written()["resolved"] == []


def test_the_reason_the_builder_gave_reaches_the_thread(review):
    """"Nothing was changed" without the why sends the reviewer to a terminal."""
    build, written = review
    build(replies=[DECLINED])

    adw_pr_review.main(72, CONFIG_PATH)
    assert "no-new-dependency" in written()["replies"][0]


def test_nothing_is_committed_and_the_branch_is_left_alone(review, repo):
    """The clean tree is the outcome, not a step on the way to a commit."""
    build, written = review
    build(replies=[DECLINED])
    before = git(repo, "rev-parse", "HEAD")

    adw_pr_review.main(72, CONFIG_PATH)
    assert git(repo, "rev-parse", f"sssf/{ADW_ID}") == before


# ── the two outcomes that already worked, still working ──────────────────────

def test_a_thread_that_was_addressed_is_committed_replied_to_and_resolved(review, repo):
    build, written = review
    build(replies=[ADDRESSED], writes=["app.py"])

    assert adw_pr_review.main(72, CONFIG_PATH) == 0
    sent = written()
    assert sent["resolved"] == [THREAD]
    assert "addressed in the commit above" in sent["replies"][0]
    assert git(repo, "log", "-1", "--format=%s", f"sssf/{ADW_ID}") == "answer the reviewer"


def test_a_red_suite_is_still_a_failed_run_that_resolves_nothing(review):
    build, written = review
    build(replies=[ADDRESSED, ADDRESSED, ADDRESSED, ADDRESSED],
          suite=_suite(False), writes=["app.py"])

    assert adw_pr_review.main(72, CONFIG_PATH) == 1
    sent = written()
    assert sent["resolved"] == []
    assert "could not finish" in sent["replies"][0]


# ── the reason, as a comment can carry it ────────────────────────────────────

def test_a_reason_is_quoted_line_by_line_so_its_own_markdown_cannot_escape():
    """A summary containing a heading or a list would otherwise break out of the
    quote and read as the factory's voice rather than as something reported."""
    quoted = adw_pr_review._quoted("# not a heading\n- not a list")
    assert quoted == "> # not a heading\n> - not a list"


def test_a_long_reason_is_clipped_and_says_so():
    quoted = adw_pr_review._quoted("x" * 900)
    assert quoted.endswith("[…]") and len(quoted) < 900


def test_a_builder_that_gave_no_reason_is_reported_as_having_given_none():
    """Quoting an empty summary would print an empty blockquote, which reads as
    a reason the reviewer cannot see rather than one that was never written."""
    assert "no reason" in adw_pr_review._quoted("   ")
