"""implement — turn the plan, or the prompt itself, into code.

Named `implement` and not `build` because `build/` is in the Python gitignore
template most repositories start from, and a stage directory the target
repo silently refuses to track is a stage that vanishes on the next clone.

An agent stage that may start from nothing: a `quick` workflow builds straight
from the request, `sdlc` hands it a plan. The gate is the tree, not the
report — every file the builder claims to have changed must exist.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from engine import gates
from engine.data_types import AgentCall, BuildOutput, PhaseParams

NAME = "implement"
KIND = "agent"
OUTPUT = BuildOutput
NEEDS = ()
TASKS = {"implement": ("task.md", BuildOutput)}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = "builder"
    retries: int = 1


def run(ctx, opts: Options):
    with ctx.run.phase(PhaseParams(name="implement", kind="agent", owner=opts.agent,
                                   retries=opts.retries,
                                   description="Implement the plan exactly, or the "
                                               "request itself when nothing planned it")) as ph:
        return ph.call(AgentCall(output_type=BuildOutput, prompt=ctx.prompt,
                                 previous=ctx.previous, gates=[gates.diff_matches_claims],
                                 task=ctx.task("implement")))
