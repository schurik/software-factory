#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW PR Review — answer the review feedback on a run's own pull request.

Usage:
    uv run adws/adw_pr_review.py 17 [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: pr(fetch) -> builder(address) -> code(test) [-> builder(fix) -> code(test) ... bounded]
        -> git(commit) -> pr(report)

THE STEP THAT CLOSES THE LOOP. Every other chain ends when its branch is
proposed; this one starts there. A reviewer leaves comments, and the run that
wrote the branch comes back to them — in the SAME session, on the SAME branch,
so the pull request is updated rather than replaced.

Almost none of that needed new machinery, which is why this file is short:

  * `session.ensure(cfg, adw_id)` JOINS the session that opened the pull request,
    and `worktree.ensure()` re-creates its worktree from the branch if an
    accepted run already pruned it. The branch is the record.
  * `integration.keep_published()` after the commit pushes onto the branch that
    is already on the remote. THAT is the delivery: no integration phase, no
    second pull request, nothing that could move the base branch.
  * `runner.adopt_provenance()` restores the session's trigger, so an
    issue-triggered branch stays issue-triggered and integration keeps refusing
    to merge it.

WHICH SESSION TO JOIN IS READ FROM THE BRANCH. `sssf/<adw_id>` is the head ref of
any pull request this factory opened, so the pull request names its own session.
A branch without that prefix is refused rather than adopted: it has no pinned
base, no trace, no worktree this factory created, and nothing here would be true
of it.

THE REVIEW TEXT IS UNTRUSTED, in the sense `adw_issue_sdlc` spells out. It is
written by whoever can review, and it reaches an agent holding `bash`, `write`
and a checkout. The same four things stand between those facts: the branch
prefix is the authorization, `trusted_reviewers` narrows it, the threads arrive
as an ARTIFACT framed as requests rather than as instructions, and `writes:` plus
`protected_files` are enforced after every call. The base branch is unreachable
by construction — there is no integration phase in this chain at all.
"""

import argparse
import sys

from adw_modules import (agents, gates, git_helper, integration, pull_requests,
                         quality, session)
from adw_modules.data_types import (AgentCall, BuildOutput, PhaseParams,
                                    PullRequestRef, PullRequestUpdate)

REQUIRED_AGENTS = ["builder"]
MAX_FIX_LOOPS = 3


def _session_of(branch: str, prefix: str) -> str:
    """The adw_id a branch names, or "" when the branch is not this factory's."""
    return branch.removeprefix(prefix) if branch.startswith(prefix) else ""


def main(number: int, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)

    # Everything below happens BEFORE a session exists, and that is the point:
    # the pull request decides which session this is. A refusal here costs one
    # graphql call and leaves no trace rows, no worktree and no agent behind.
    main_root = git_helper.main_root()
    context = pull_requests.describe(main_root, cfg.pull_requests,
                                     PullRequestRef(number=number))
    if not context.open:
        print(f"pull request #{number} is {context.state.lower() or 'not open'} — "
              f"there is nothing to answer on a branch that is no longer under "
              f"review.", file=sys.stderr)
        return 2

    resolved = _session_of(context.branch, cfg.worktree.branch_prefix)
    if not resolved:
        print(f"#{number} is on `{context.branch}`, which does not start with "
              f"`{cfg.worktree.branch_prefix}` — no run of this factory produced "
              f"it. There is no session to join, no pinned base to measure "
              f"against and no worktree to re-create; work it by hand, or run a "
              f"chain against a fresh branch.", file=sys.stderr)
        return 2
    if adw_id and adw_id != resolved:
        print(f"--adw-id {adw_id} was passed, but #{number} is on "
              f"`{context.branch}`, which belongs to {resolved}. Refusing to "
              f"commit one session's review feedback into another's branch.",
              file=sys.stderr)
        return 2

    run = session.ensure(cfg, resolved)

    with run.phase(PhaseParams(name="pr", kind="code", owner="review",
                               description="Read the reviewers' own words and where "
                                           "each one hangs, before anyone paraphrases "
                                           "them into a task")) as ph:
        pull_requests.attach(run, cfg.pull_requests, context)
        run.record_pull_request(context)
        threads = pull_requests.actionable(cfg.pull_requests, context)
        ph.log(url=context.url, branch=context.branch, base=context.base_ref,
               decision=context.review_decision or "none",
               threads=f"{len(threads)} open of {len(context.threads)}",
               feedback=context.threads_path)
        strangers = pull_requests.trusted(cfg.pull_requests, context, threads)
        if strangers:
            raise RuntimeError(
                f"{', '.join(strangers)} left review feedback, and this repository "
                f"has said whose review may start a run "
                f"(pull_requests.trusted_reviewers). Nothing was changed.")

    # Nothing to answer is a SUCCESS, and saying so costs no agent. A watcher
    # polls this chain, so "already handled" is the common case, not the odd one.
    if not threads:
        run.console.note(f"#{context.number}: no open review threads — nothing to do")
        return run.finish(accepted=True)

    # The prompt slot is the OPERATOR's instruction and nothing else. The
    # reviewers' words travel through the envelope, which frames them as
    # requests to weigh — interpolating even a thread's file path here would
    # walk untrusted text past that framing into every agent's {{prompt}}.
    prompt = (f"Address the open review threads on pull request #{context.number}. "
              f"They are in the envelope you were handed; the file that envelope "
              f"names is the full text of each one, with the file and line it "
              f"hangs on. Change only what they ask for.")

    with run.phase(PhaseParams(name="address", kind="agent", owner="builder",
                               description="Make the changes the reviewers asked for, "
                                           "and only those")) as ph:
        build = ph.call(AgentCall(
            output_type=BuildOutput, prompt=prompt,
            previous=pull_requests.as_envelope(context, threads),
            gates=[gates.diff_matches_claims]))

    test = None
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(PhaseParams(name=f"test_{i}", kind="code", owner="quality",
                                   description="Run the suite — answering a reviewer "
                                               "with a red branch answers nothing")) as ph:
            test = quality.run_tests(run)
            passed = sum(1 for check in test.checks if check.passed)
            ph.log(passed=test.passed, checks=f"{passed}/{len(test.checks)}",
                   artifacts=", ".join(test.artifacts))

        if test.passed:
            break

        with run.phase(PhaseParams(name=f"fix_{i}", kind="agent", owner="builder", retries=1,
                                   description="Repair what the suite reported, from its "
                                               "verbatim output")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                      previous=quality.as_envelope(test, "tests"),
                                      gates=[gates.diff_matches_claims]))

    verified = test is not None and test.passed
    synced = None
    if verified:
        with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                                   description="Push the answer to where the reviewer is "
                                               "already looking, on the branch they "
                                               "are reviewing")) as ph:
            message = build.commit_message or f"sssf({run.adw_id}): {build.summary}"
            sha = git_helper.commit_all(run.repo_root, message)
            # The whole delivery. This branch has been pushed before — that is
            # what made it a pull request — so keep_published carries the new
            # commits to it. Nothing here opens a second one or touches the base.
            synced = integration.keep_published(run)
            ph.log(sha=sha, message=message, pushed=synced.pushed,
                   notes=" · ".join(synced.notes))

    # The reviewers hear back either way, as the tracker does in the issue
    # chain. A run that could not finish is exactly the one whose reviewer most
    # needs to know their comment was picked up and where it stopped.
    with run.phase(PhaseParams(name="report", kind="code", owner="review",
                               description="Answer each thread that was addressed, and "
                                           "say once what the run as a whole did")) as ph:
        answered = _write_back(run, context, threads, verified, synced)
        ph.log(threads=len(threads), replied=answered["replied"],
               resolved=answered["resolved"], notes=" · ".join(answered["notes"]))

    return run.finish(accepted=verified,
                      reason="the suite never came back clean, so nothing was "
                             "committed onto the branch under review")


def _write_back(run, context, threads, verified: bool, synced) -> dict:
    """Reply in each addressed thread, resolve it, and comment the outcome once.

    NOTHING IS RESOLVED ON A FAILED RUN. A resolved thread tells a reviewer their
    ask is handled; a run that never got the suite green has not handled it, and
    resolving anyway would hide outstanding work behind a green checkmark. The
    reply still goes out — being told "this was attempted and here is where it
    stopped" is the useful half.
    """
    config = run.cfg.pull_requests
    replied = resolved = 0
    notes: list[str] = []

    for thread in threads:
        where = thread.path or "this pull request"
        body = (f"{pull_requests.SSSF_MARKER} · `{run.adw_id}` — addressed in the "
                f"commit above; see the diff on `{where}`."
                if verified else
                f"{pull_requests.SSSF_MARKER} · `{run.adw_id}` — picked this up and "
                f"could not finish it. The suite never came back clean, so nothing "
                f"was committed; this thread stays open.")
        result = pull_requests.answer_thread(run.main_root, config, PullRequestUpdate(
            number=context.number, project=context.project,
            thread_id=thread.thread_id, reply=body, resolve=verified))
        replied += 1 if result.replied else 0
        resolved += len(result.resolved)
        notes += result.notes

    summary = pull_requests.comment(run.main_root, config, PullRequestUpdate(
        number=context.number, project=context.project,
        comment=_summary(run, threads, verified, synced)))
    notes += summary.notes
    return {"replied": replied, "resolved": resolved, "notes": notes}


def _summary(run, threads, verified: bool, synced) -> str:
    """The one comment on the pull request itself. The adw_id is the resume handle."""
    head = (f"answered {len(threads)} review thread(s)" if verified else
            f"picked up {len(threads)} review thread(s) and could not finish")
    lines = [f"{pull_requests.SSSF_MARKER} · `{run.adw_id}` — {head}", ""]
    if verified and synced is not None:
        lines += [("Pushed onto the branch under review." if synced.pushed else
                   f"Committed, but not pushed: {' · '.join(synced.notes) or 'no remote'}"),
                  ""]
    if not verified:
        lines += ["The suite never came back clean, so nothing was committed and no "
                  "thread was resolved. The threads stay open.", ""]
    lines += [f"<sub>Resume or inspect with `--adw-id {run.adw_id}`; "
              f"phases: `just phases {run.adw_id}`</sub>"]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", type=int, help="the pull request to answer")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None,
                        help="the session to join; must match the pull request's branch")
    args = parser.parse_args()
    sys.exit(main(args.number, args.config, args.adw_id))
