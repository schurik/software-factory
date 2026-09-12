"""scout — find where the request lives before anyone plans against a guess.

An agent stage, read-only in the roster sense: the scout's `writes: []` means
it may change nothing tracked, and its findings go to the run's
context_handoff/, which is runtime, not the repo. It needs nothing before it
and hands a `ScoutOutput` on; `plan` accepts one as its `previous` and reads
the findings as recon, not as a plan.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from engine import gates
from engine.data_types import AgentCall, PhaseParams, ScoutOutput

NAME = "scout"
KIND = "agent"
OUTPUT = ScoutOutput
NEEDS = ()
TASKS = {"scout": ("task.md", ScoutOutput)}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = "scout"
    retries: int = 1                # gate-correction rounds into the same session


def run(ctx, opts: Options):
    call = AgentCall(output_type=ScoutOutput, prompt=ctx.prompt, previous=ctx.previous,
                     gates=[gates.artifacts_exist, gates.files_non_empty],
                     task=ctx.task("scout"))
    with ctx.run.phase(PhaseParams(name="scout", kind="agent", owner=opts.agent,
                                   retries=opts.retries,
                                   description="Find the code the request actually "
                                               "touches, so the plan is written against "
                                               "the repository and not against a guess")) as ph:
        return ph.call(call)
