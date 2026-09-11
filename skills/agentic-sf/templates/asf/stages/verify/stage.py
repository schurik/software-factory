"""verify — run the repository's own checks, and send failures back to the
builder a bounded number of times.

A code stage that owns a loop. It follows `implement`. It follows `implement`. Which blocks run is `blocks:` — names from
`asf/engine/quality.py`, where the commands are written down, so no agent
spends a context window rediscovering `bun test`. A red block does not fail
the phase: the runner did its job, the code is what failed. The output travels
back to the builder verbatim, as an envelope, and the builder's fix is checked
again. An exhausted loop stops the run here — nothing after a red verify may
land — and the run is finished as not accepted.

Shape: verify_1, then fix_1 only if red, then verify_2, … The last verify is
never followed by a fix nobody would check.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from engine import gates, quality
from engine.data_types import AgentCall, BuildOutput, PhaseParams
from engine.stage import StageStop

NAME = "verify"
KIND = "code"
OUTPUT = BuildOutput                # the build as it stands after the last fix
NEEDS = (BuildOutput,)
TASKS = {"fix": "fix.md"}


class Fix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = "builder"
    retries: int = 1


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[str] = Field(default_factory=lambda: ["test"])
    max_fix_loops: int = Field(default=3, ge=1)
    fix: Fix = Field(default_factory=Fix)


def _record(ph, result) -> None:
    passed = sum(1 for check in result.checks if check.passed)
    ph.log(passed=result.passed, checks=f"{passed}/{len(result.checks)}",
           artifacts=", ".join(result.artifacts))


def run(ctx, opts: Options):
    build = ctx.previous
    what = ", ".join(opts.blocks)
    for attempt in range(1, opts.max_fix_loops + 1):
        with ctx.run.phase(PhaseParams(name=f"verify_{attempt}", kind="code", owner="quality",
                                       description=f"Run {what} — known commands, so code "
                                                   f"runs them and no agent rediscovers "
                                                   f"them")) as ph:
            result = quality.run_blocks(ctx.run, opts.blocks)
            _record(ph, result)
        if result.passed:
            return build
        if attempt == opts.max_fix_loops:
            break
        with ctx.run.phase(PhaseParams(name=f"fix_{attempt}", kind="agent", owner=opts.fix.agent,
                                       retries=opts.fix.retries,
                                       description="Repair what the checks reported, from "
                                                   "their verbatim output")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=ctx.prompt,
                                      previous=quality.as_envelope(result, what),
                                      gates=[gates.diff_matches_claims],
                                      task=ctx.task("fix")))
    raise StageStop(f"{what} still failed after {opts.max_fix_loops} attempt(s)")
