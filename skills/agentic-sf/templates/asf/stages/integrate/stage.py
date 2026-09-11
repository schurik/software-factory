"""integrate — land the run's branch on its base branch, the way this
repository has said it wants it landed.

A code stage, and the one that leaves the run's own branch. `worktree.
integration` in factory.yaml decides how (merge, pr, none); `mode:` here
overrides it for one workflow. The `integrate` gate, when a workflow turns it
on, hands the engineer this run's whole diff against its baseline before the
branch moves. A branch that does not land is not a failed run: the work is
committed and the branch is kept, so landing stays something a person can
finish by hand.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from engine import changes, hitl, integration
from engine.data_types import ChangeCapture, Gate, IntegrationRequest, PhaseParams

NAME = "integrate"
KIND = "code"
OUTPUT = None
NEEDS = ()
TASKS = {}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hitl: Optional[bool] = None                       # None: factory.yaml decides
    mode: Literal["", "none", "merge", "pr"] = ""     # "": worktree.integration.mode


def run(ctx, opts: Options):
    run = ctx.run
    changeset = changes.capture(run, ChangeCapture(base=ctx.baseline))
    hitl.gated(run, Gate(name="integrate", paths=[changeset.diff_path],
                         description="Hand the engineer this run's diff against its "
                                     "baseline, before the branch moves"),
               changes.as_envelope(changeset))
    with run.phase(PhaseParams(name="integrate", kind="code", owner="git",
                               description="Land the run's branch on its base branch, the "
                                           "way this repository has said it wants it "
                                           "landed")) as ph:
        landed = integration.integrate(run, IntegrationRequest(mode=opts.mode))
        ph.log(mode=landed.mode, landed=landed.ok, merged_into=landed.merged_into,
               pushed=landed.pushed, pr_url=landed.pr_url, notes=" · ".join(landed.notes))
    return None
