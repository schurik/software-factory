#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Plan Build — two-agent chain: planner -> envelope -> builder.

Usage:
    uv run adws/adw_plan_build.py "<prompt or path/to/prompt.md>" [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4] [--resume] [--hitl all|none|every|plan]

Phases: engineer(request) -> planner [-> engineer(approve_plan) -> planner(revise) ... until approved] -> builder -> git(commit)
"""

import argparse
import sys

from adw_modules import agents, gates, git_helper, hitl, integration, session, utils
from adw_modules.data_types import (AgentCall, BuildOutput, Gate, PhaseParams,
                                    PlanOutput)

REQUIRED_AGENTS = ["planner", "builder"]


def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None, resume: bool = False, hitl_mode: str = "") -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id, resume, hitl_mode)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the incoming ask")) as ph:
        ph.log(input=prompt)

    plan_call = AgentCall(output_type=PlanOutput, prompt=prompt,
                          gates=[gates.artifacts_exist, gates.files_non_empty])
    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                               description="Turn the request into an implementable plan")) as ph:
        plan = ph.call(plan_call)
    # A human may stop here. Off unless `hitl:` in the config or --hitl says
    # otherwise; on reject the planner reworks its plan in the same session and
    # the run asks again. See adw_modules/hitl.py.
    plan = hitl.gated(run, Gate(name="plan", owner="planner", call=plan_call), plan)

    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement the plan exactly")) as ph:
        build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=plan,
                                  gates=[gates.diff_matches_claims]))

    with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                               description="Land the builder's changes, using the message it wrote")) as ph:
        message = build.commit_message or f"sssf({run.adw_id}): {build.summary}"
        sha = git_helper.commit_all(run.repo_root, message, allow_clean=run.resuming)
        # A session whose branch is already pushed keeps its pull request
        # current — on a branch nobody published this is a no-op.
        synced = integration.keep_published(run)
        ph.log(sha=sha or "unchanged — this session already committed it",
               message=message, pushed=synced.pushed,
               notes=" · ".join(synced.notes))

    return run.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    parser.add_argument("--resume", action="store_true",
                        help="replay this session's recorded agent phases instead "
                             "of paying for them again; needs --adw-id")
    parser.add_argument("--hitl", default="",
                        help="which gates stop for you: all | none | every | gate,names "
                             "— over the config's hitl: block and SSSF_HITL")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id, args.resume,
                  args.hitl))
