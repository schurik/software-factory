"""review — confirm that what was built is what was asked for, and send the
builder back a bounded number of times when it is not.

An agent stage that owns a loop, and asks a different question from verify:
the suite asks "does it run", the reviewer asks "is this what was asked",
against the plan when there is one and the prompt otherwise. Neither answers
the other's question. A revision that closes a finding re-enters the checks
named in `retest:`, so the tree that gets committed is the tree that was both
tested and approved. A reviewer that still withholds approval after
`max_rounds` stops the run here: nothing after a rejected review may land.

Shape: review_1, then revise_1 only if not approved, then review_2, … then
`retest` once, only if a revision changed code.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from engine import gates, quality
from engine.data_types import AgentCall, BuildOutput, PhaseParams, ReviewOutput
from engine.stage import StageStop

NAME = "review"
KIND = "agent"
OUTPUT = BuildOutput                # the build as it stands after the last revision
NEEDS = (BuildOutput,)
TASKS = {"review": ("task.md", ReviewOutput), "revise": ("revise.md", BuildOutput)}


class Revise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = "builder"
    retries: int = 1


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = "reviewer"
    max_rounds: int = Field(default=2, ge=1)
    revise: Revise = Field(default_factory=Revise)
    # Blocks to re-run after a revision changed code; [] skips the retest.
    retest: list[str] = Field(default_factory=lambda: ["test"])


def run(ctx, opts: Options):
    build = ctx.previous
    revised = False
    for round_ in range(1, opts.max_rounds + 1):
        with ctx.run.phase(PhaseParams(name=f"review_{round_}", kind="agent", owner=opts.agent,
                                       description="Confirm the build matches what was asked, "
                                                   "against the plan or the prompt")) as ph:
            review = ph.call(AgentCall(output_type=ReviewOutput, prompt=ctx.prompt,
                                       previous=build,
                                       gates=[gates.artifacts_exist, gates.verdict_consistent],
                                       task=ctx.task("review")))
        if review.approved:
            break
        if round_ == opts.max_rounds:
            blocking = "; ".join(review.blocking)[:300]
            raise StageStop(f"the reviewer withheld approval after {opts.max_rounds} "
                            f"round(s): {blocking or review.summary}")
        with ctx.run.phase(PhaseParams(name=f"revise_{round_}", kind="agent",
                                       owner=opts.revise.agent, retries=opts.revise.retries,
                                       description="Close the reviewer's blocking findings, "
                                                   "in the same session that built it")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=ctx.prompt,
                                      previous=review, gates=[gates.diff_matches_claims],
                                      task=ctx.task("revise")))
            revised = True
    if revised and opts.retest:
        what = ", ".join(opts.retest)
        with ctx.run.phase(PhaseParams(name="retest", kind="code", owner="quality",
                                       description=f"Re-run {what} — the revision changed code "
                                                   f"after the last green result")) as ph:
            result = quality.run_blocks(ctx.run, opts.retest)
            passed = sum(1 for check in result.checks if check.passed)
            ph.log(passed=result.passed, checks=f"{passed}/{len(result.checks)}",
                   artifacts=", ".join(result.artifacts))
        if not result.passed:
            raise StageStop(f"the revision the reviewer asked for broke {what}")
    return build
