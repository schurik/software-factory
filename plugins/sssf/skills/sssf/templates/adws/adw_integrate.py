#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Integrate — land a finished run's branch, or push it for review.

Usage:
    uv run adws/adw_integrate.py --adw-id a1b2c3d4 [--mode merge|pr|none] [--config adws/adw_sssf_config/sssf.config.yaml]

Phases: engineer(request) -> code(integrate)

A run leaves its work on `sssf/<adw_id>` and nowhere else. This is the step that
brings it back, and it is deliberately a separate ADW: whether a machine may
move the base branch is a repository's decision, so the chains that build things
stop at a committed branch and this one — run when you are ready — lands it.

`--adw-id` is required and names the run whose branch to land. Passing it joins
that session, so the integration is recorded as part of the run it belongs to
rather than as an unrelated event, and it re-attaches to the run's worktree
(re-creating it from its branch if an earlier accepted run already pruned it).

No agents. Merging is a known command, so it is a `kind="code"` phase over
adw_modules/integration.py — SKILL.md rule 8. What that command IS comes from
`worktree.integration` in the config, because repositories disagree about merge
vs. rebase and about who may move the base branch at all.
"""

import argparse
import sys

from adw_modules import agents, integration, session
from adw_modules.data_types import IntegrationRequest, PhaseParams

REQUIRED_AGENTS: list[str] = []      # deterministic end to end — rule 1 still applies


def main(adw_id: str, mode: str = "",
         config: str = "adws/adw_sssf_config/sssf.config.yaml") -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Name the run whose branch is being landed")) as ph:
        ph.log(input=f"integrate {run.workspace.branch or '(no worktree)'}",
               base=run.workspace.base_ref, mode=mode or cfg.worktree.integration.mode)

    with run.phase(PhaseParams(name="integrate", kind="code", owner="git",
                               description="Land the run's branch on its base branch, "
                                           "the way this repository has said it wants it "
                                           "landed")) as ph:
        landed = integration.integrate(run, IntegrationRequest(mode=mode))
        ph.log(mode=landed.mode, landed=landed.ok, head=landed.head[:7],
               merged_into=landed.merged_into, pushed=landed.pushed,
               pr_url=landed.pr_url, notes=" · ".join(landed.notes))

    # The phase ran and reported either way — the RUN is only accepted when the
    # branch actually went somewhere. Rule 10: those are different questions.
    return run.finish(accepted=landed.ok,
                      reason="; ".join(landed.notes) or "the branch was not landed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adw-id", required=True, help="the run whose branch to land")
    parser.add_argument("--mode", default="", choices=["", "none", "merge", "pr"],
                        help="override worktree.integration.mode for this run")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    args = parser.parse_args()
    sys.exit(main(args.adw_id, args.mode, args.config))
