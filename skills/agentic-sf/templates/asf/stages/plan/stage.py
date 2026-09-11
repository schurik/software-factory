"""plan — turn the request into a plan the builder can implement.

An agent stage. The plan is the first thing a run puts on record and the one
a human is most often asked to read, so it carries the `plan` gate: a
workflow turns it on with `hitl: true`, factory.yaml's `hitl:` block decides
otherwise, and `--hitl` on the command line beats both. A reject at the gate
goes back to the same planner, in the same session, with the engineer's notes.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from engine import gates, hitl
from engine.data_types import AgentCall, Gate, PhaseParams, PlanOutput

NAME = "plan"
KIND = "agent"
OUTPUT = PlanOutput
NEEDS = ()
TASKS = {"plan": "task.md"}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = "planner"
    hitl: Optional[bool] = None     # None: factory.yaml decides
    retries: int = 1                # gate-correction rounds into the same session


def run(ctx, opts: Options):
    call = AgentCall(output_type=PlanOutput, prompt=ctx.prompt, previous=ctx.previous,
                     gates=[gates.artifacts_exist, gates.files_non_empty],
                     task=ctx.task("plan"))
    with ctx.run.phase(PhaseParams(name="plan", kind="agent", owner=opts.agent,
                                   retries=opts.retries,
                                   description="Turn the request into an implementable "
                                               "plan, before any code exists to blur "
                                               "what was asked")) as ph:
        plan = ph.call(call)
    return hitl.gated(ctx.run, Gate(name="plan", owner=opts.agent, call=call), plan)
