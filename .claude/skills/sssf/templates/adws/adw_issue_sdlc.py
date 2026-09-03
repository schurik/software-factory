#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Issue SDLC — a tracked work item, planned, built, tested, reviewed, landed.

Usage:
    uv run adws/adw_issue_sdlc.py 42 [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: issue(fetch) -> planner -> git(commit_plan)
        -> builder -> code(test) [-> builder(fix) -> code(test) ... bounded]
        -> reviewer [-> builder(revise) -> reviewer ... bounded]
        -> code(retest, only if a revision changed code)
        -> git(commit_build) -> code(changes) -> documenter -> git(commit_docs)
        -> git(integrate) -> issue(report)

`adw_simple_sdlc` with the tracker at both ends, and the same three commits in
between. What is new is only where the ask comes from and where the outcome
goes; everything between the first and last phase is unchanged, deliberately,
because the chain is not the part that becomes riskier.

WHAT DOES BECOME RISKIER IS THE PROMPT. Every other ADW is handed text the
engineer typed in their own terminal. This one is handed text written by
whoever can file an issue, and hands it to agents holding `bash`, `write` and a
checkout. Four things stand between those two facts, none sufficient alone:

  * The ROUTING LABEL is the authorization. A human applied it; the watcher only
    ever looks at issues that carry it, and `trusted_authors` narrows it further
    where anyone can label.
  * The BODY IS AN ARTIFACT, not a field, and it arrives framed as a user's
    description of a problem rather than as instructions. See `issues.py`.
  * `writes:` and `protected_files` are unchanged but now load-bearing: an agent
    that edits the machinery judging its own work is rolled back by
    `permissions.py` and the phase dies.
  * The BASE BRANCH IS NOT REACHABLE. `issues.force_pr` downgrades a configured
    merge to a pull request for this run, in `integration.integrate()` rather
    than in config, so the work arrives somewhere a human looks at it.

The issue phase runs FIRST, before the worktree has been touched and before any
agent is spawned, so an untrusted author or an unreadable issue costs nothing.
"""

import argparse
import sys

from adw_modules import (agents, changes, gates, git_helper, integration, issues,
                         quality, session)
from adw_modules.data_types import (AgentCall, BuildOutput, ChangeCapture,
                                    DocumentOutput, IntegrationRequest, IssueRef,
                                    IssueUpdate, PhaseParams, PlanOutput, ReviewOutput)

REQUIRED_AGENTS = ["planner", "builder", "reviewer", "documenter"]
MAX_FIX_LOOPS = 3
MAX_REVISION_LOOPS = 2

DOCUMENT_NOTES = ("Read diff_path in full before writing. Document only what the "
                  "diff shows, then copy the write-up into app_docs/ as your task "
                  "describes.")


def main(number: int, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)
    baseline = git_helper.rev(run.repo_root, "HEAD")   # pinned before this run commits anything

    def commit(ph, envelope) -> None:
        """Commit what the preceding phase produced, in that agent's own words."""
        message = envelope.commit_message or f"sssf({run.adw_id}): {envelope.summary}"
        ph.log(sha=git_helper.commit_all(run.repo_root, message), message=message)

    def record(ph, result) -> None:
        """Log a deterministic block's verdict — the same shape every ADW uses."""
        passed = sum(1 for check in result.checks if check.passed)
        ph.log(passed=result.passed, checks=f"{passed}/{len(result.checks)}",
               artifacts=", ".join(result.artifacts))

    with run.phase(PhaseParams(name="issue", kind="code", owner="tracker",
                               description="Read the reporter's own words and labels, "
                                           "before anyone paraphrases them into a task")) as ph:
        issue = issues.fetch(run, cfg.issues, IssueRef(number=number))
        run.record_issue(issue)
        ph.log(url=issue.url, title=issue.title, author=issue.author,
               labels=", ".join(issue.labels), body=issue.body_path,
               baseline=git_helper.short_sha(run.repo_root, baseline))
        if not issues.trusted(cfg.issues, issue):
            raise RuntimeError(
                f"{issue.author} is not in issues.trusted_authors — this repository "
                f"has said whose work items may start a run, and this is not one")

    prompt = f"Resolve issue #{issue.number}: {issue.title}"

    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                               description="Turn the reported problem into an "
                                           "implementable plan")) as ph:
        plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt,
                                 previous=issues.as_envelope(issue),
                                 gates=[gates.artifacts_exist, gates.files_non_empty]))

    with run.phase(PhaseParams(name="commit_plan", kind="code", owner="git",
                               description="Put the spec on record before any code exists to blur it")) as ph:
        commit(ph, plan)

    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement the plan exactly")) as ph:
        build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=plan,
                                  gates=[gates.diff_matches_claims]))

    test = None
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(PhaseParams(name=f"test_{i}", kind="code", owner="quality",
                                   description="Run the suite — a known command, so code runs "
                                               "it and no agent has to rediscover it")) as ph:
            test = quality.run_tests(run)
            record(ph, test)

        if test.passed:
            break

        with run.phase(PhaseParams(name=f"fix_{i}", kind="agent", owner="builder", retries=1,
                                   description="Repair what the suite reported, from its "
                                               "verbatim output")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                      previous=quality.as_envelope(test, "tests"),
                                      gates=[gates.diff_matches_claims]))

    review = None
    revised = False
    for i in range(1, MAX_REVISION_LOOPS + 1):
        with run.phase(PhaseParams(name=f"review_{i}", kind="agent", owner="reviewer",
                                   description="Confirm the build matches the plan")) as ph:
            review = ph.call(AgentCall(output_type=ReviewOutput, prompt=prompt, previous=build,
                                       gates=[gates.artifacts_exist, gates.verdict_consistent]))

        if review.approved or i == MAX_REVISION_LOOPS:
            break

        with run.phase(PhaseParams(name=f"revise_{i}", kind="agent", owner="builder", retries=1,
                                   description="Close the reviewer's blocking findings")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=review,
                                      gates=[gates.diff_matches_claims]))
            revised = True

    # A revision edited code after the suite last ran, so the green light is
    # stale. Re-run it rather than commit on a result that predates the change.
    if revised and review is not None and review.approved:
        with run.phase(PhaseParams(name="retest", kind="code", owner="quality",
                                   description="Re-run the suite — the revision changed code "
                                               "after the last green result")) as ph:
            test = quality.run_tests(run)
            record(ph, test)

    verified = (test is not None and test.passed
                and review is not None and review.approved)
    landed = None
    if verified:
        with run.phase(PhaseParams(name="commit_build", kind="code", owner="git",
                                   description="Land the code only now: green suite, approved review")) as ph:
            commit(ph, build)

        with run.phase(PhaseParams(name="changes", kind="code", owner="git",
                                   description="Diff the whole run against its pinned baseline, for the documenter")) as ph:
            changeset = changes.capture(run, ChangeCapture(base=baseline))
            ph.log(base=f"{changeset.base.label} @ {changeset.base.commit[:7]}",
                   reason=changeset.base.reason,
                   files=len(changeset.files) + len(changeset.untracked),
                   lines=f"+{changeset.insertions} -{changeset.deletions}",
                   diff=changeset.diff_path)
            if changeset.empty:
                raise RuntimeError(
                    f"nothing changed since {changeset.base.label} "
                    f"({changeset.base.reason}) — there is nothing to document.")

        with run.phase(PhaseParams(name="document", kind="agent", owner="documenter", retries=1,
                                   description="Write up the completed change")) as ph:
            document = ph.call(AgentCall(output_type=DocumentOutput, prompt=prompt,
                                         previous=changes.as_envelope(changeset, DOCUMENT_NOTES),
                                         gates=[gates.artifacts_exist, gates.files_non_empty]))

        with run.phase(PhaseParams(name="commit_docs", kind="code", owner="git",
                                   description="Ship the write-up in its own commit, beside the code it describes")) as ph:
            commit(ph, document)

        with run.phase(PhaseParams(name="integrate", kind="code", owner="git",
                                   description="Open the pull request this issue's work "
                                               "belongs in — a stranger's prompt does not "
                                               "move the base branch")) as ph:
            landed = integration.integrate(run, IntegrationRequest(
                title=f"{issue.title} (#{issue.number})"))
            ph.log(mode=landed.mode, landed=landed.ok, merged_into=landed.merged_into,
                   pushed=landed.pushed, pr_url=landed.pr_url,
                   notes=" · ".join(landed.notes))

    # The tracker hears about the run either way. A failed run is exactly the
    # one whose reporter most needs to know it was picked up and where it
    # stopped, and the label the watcher moves next is decided by the exit code.
    with run.phase(PhaseParams(name="report", kind="code", owner="tracker",
                               description="Tell the reporter what happened and where "
                                           "the work went")) as ph:
        posted = issues.comment(run, cfg.issues, IssueUpdate(
            number=issue.number, project=issue.project,
            comment=_comment(run, verified, landed)))
        ph.log(ok=posted.ok, notes=" · ".join(posted.notes))

    return run.finish(accepted=verified,
                      reason="the suite or the review never came back clean")


def _comment(run, verified: bool, landed) -> str:
    """What the reporter reads. The adw_id is also the resume handle."""
    head = "picked this up and it is ready for review" if verified else \
           "picked this up and could not finish it"
    lines = [f"**sssf** · `{run.adw_id}` — {head}", ""]
    if landed is not None and landed.pr_url:
        lines += [f"Pull request: {landed.pr_url}", ""]
    elif landed is not None:
        lines += [f"Branch `{landed.branch}`: {' · '.join(landed.notes) or 'not landed'}", ""]
    if not verified:
        lines += ["The suite or the review never came back clean, so no code was "
                  "committed. The plan commit stands as a record of what was asked.", ""]
    lines += [f"<sub>Resume or inspect with `--adw-id {run.adw_id}`; "
              f"phases: `just phases {run.adw_id}`</sub>"]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", type=int, help="the issue number to work")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(args.number, args.config, args.adw_id))
