"""document — capture what the run changed, then write it up.

Two phases. `changes` is code: a diff of the run's tree against the baseline
pinned when the run started, written to context_handoff/ as the artifact the
documenter reads. Two git commands, not a judgement call. `document` is the
agent: it writes for the engineer who arrives next, from that diff and
nothing else. An empty diff fails the phase, because a documenter handed
nothing to describe would describe something anyway.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from engine import changes, gates
from engine.data_types import AgentCall, BuildOutput, ChangeCapture, DocumentOutput, PhaseParams

NAME = "document"
KIND = "agent"
OUTPUT = DocumentOutput
NEEDS = (BuildOutput,)
TASKS = {"document": ("task.md", DocumentOutput)}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = "documenter"
    retries: int = 1
    max_diff_lines: int = Field(default=2000, ge=100)


def run(ctx, opts: Options):
    with ctx.run.phase(PhaseParams(name="changes", kind="code", owner="git",
                                   description="Diff the whole run against its pinned "
                                               "baseline, for the documenter")) as ph:
        changeset = changes.capture(ctx.run, ChangeCapture(base=ctx.baseline,
                                                           max_diff_lines=opts.max_diff_lines))
        ph.log(base=f"{changeset.base.label} @ {changeset.base.commit[:7]}",
               reason=changeset.base.reason,
               files=len(changeset.files) + len(changeset.untracked),
               lines=f"+{changeset.insertions} -{changeset.deletions}",
               diff=changeset.diff_path)
        if changeset.empty:
            raise RuntimeError(f"nothing changed since {changeset.base.label} "
                               f"({changeset.base.reason}) — there is nothing to document")
    with ctx.run.phase(PhaseParams(name="document", kind="agent", owner=opts.agent,
                                   retries=opts.retries,
                                   description="Write up the completed change, from the diff, "
                                               "for the engineer who arrives next")) as ph:
        return ph.call(AgentCall(output_type=DocumentOutput, prompt=ctx.prompt,
                                 previous=changes.as_envelope(changeset),
                                 gates=[gates.artifacts_exist, gates.files_non_empty],
                                 task=ctx.task("document")))
