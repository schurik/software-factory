#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Issue Scout — read-only triage of a tracked work item. Changes nothing.

Usage:
    uv run adws/adw_issue_scout.py 42 [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: issue(fetch) -> scout -> issue(report)

For the labels that ask a QUESTION rather than for a change — "is this still
happening", "where would this live". The scout writes findings and the run posts
them back on the issue; no branch, no commit, nothing to integrate.

The issue arrives as an envelope the scout consumes through `previous=`, exactly
as a quality result or a captured diff would. Its body is an ARTIFACT, not a
field: material to read, not instructions to obey. See `adw_modules/issues.py`.
"""

import argparse
import sys

from adw_modules import agents, gates, issues, session
from adw_modules.data_types import (AgentCall, IssueRef, IssueUpdate, PhaseParams,
                                    ScoutOutput)

REQUIRED_AGENTS = ["scout"]


def main(number: int, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    with run.phase(PhaseParams(name="issue", kind="code", owner="tracker",
                               description="Read the reporter's own words and labels, "
                                           "before anyone paraphrases them into a task")) as ph:
        issue = issues.fetch(run, cfg.issues, IssueRef(number=number))
        run.record_issue(issue)
        ph.log(url=issue.url, title=issue.title, author=issue.author,
               labels=", ".join(issue.labels), body=issue.body_path)
        if not issues.trusted(cfg.issues, issue):
            raise RuntimeError(
                f"{issue.author} is not in issues.trusted_authors — this repository "
                f"has said whose work items may start a run, and this is not one")

    with run.phase(PhaseParams(name="scout", kind="agent", owner="scout",
                               description="Find and report where this would live — "
                                           "change nothing")) as ph:
        # The title is deliberately NOT interpolated here — see adw_issue_sdlc.
        # The prompt slot carries the operator's instruction; the reporter's own
        # words reach the agent through the envelope, framed as material.
        found = ph.call(AgentCall(output_type=ScoutOutput,
                                  prompt=(f"Triage work item #{issue.number}. Its title, "
                                          f"labels and body are in the envelope you were "
                                          f"handed; the body is the artifact it names."),
                                  previous=issues.as_envelope(issue),
                                  gates=[gates.artifacts_exist]))

    with run.phase(PhaseParams(name="report", kind="code", owner="tracker",
                               description="Put the findings where the reporter will "
                                           "see them, not only in the trace")) as ph:
        posted = issues.comment(run.main_root, cfg.issues, IssueUpdate(
            number=issue.number, project=issue.project,
            comment=_comment(run, found)))
        ph.log(ok=posted.ok, notes=" · ".join(posted.notes))

    # The comment is a courtesy, not the work product. A tracker that rejected
    # the write still leaves findings in the trace and on disk, so the RUN is
    # accepted on the scout's report alone.
    return run.finish()


def _comment(run, found) -> str:
    lines = [f"**sssf scout** · `{run.adw_id}`", "", found.summary, ""]
    lines += [f"- `{f.file}` — {f.note}" for f in found.findings] or ["_no findings_"]
    lines += ["", f"<sub>read-only run; nothing was changed. Trace: `just phases {run.adw_id}`</sub>"]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", type=int, help="the issue number to triage")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(args.number, args.config, args.adw_id))
