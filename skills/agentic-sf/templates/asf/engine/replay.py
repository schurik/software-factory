"""Replay: hand back what an earlier process in this session already produced.

A chain that dies in its last phase has already paid for every phase before it.
The plan, the build and the review are in the session's own directory, and the
run's worktree still holds the tree they made — a failed run keeps it on
purpose. Restarting from the top buys none of that back and charges for all of
it a second time, which is what `--resume` exists to stop: an agent phase whose
envelope this session already recorded is answered FROM the record, and
everything code owns runs again for real.

That split is the factory's own line applied to recovery. An agent's output is
the expensive, non-repeatable half, and it is already written down. A suite, a
commit, a diff are cheap and deterministic, so re-running them is how a resumed
run VERIFIES the tree it inherited — and a replayed envelope whose gates no
longer hold is discarded, with the agent asked again, live. Nothing is trusted
because it is old; it is trusted because its gates still pass.

The record comes from `engine/artifacts.py` — the session directory, never
the trace db. A run must work with the db deleted, and the db is the mirror the
visualizer polls, not a dependency of the factory.

Matching is by phase NAME — which `PhaseParams` already requires to be unique
within a run — plus the agent that owned it and the output type it produced. Any
of the three differing means the ADW has changed shape since the recorded run,
and a changed chain is not one this can replay: the phase runs live. So does
every phase after the record runs out, which is the whole point — the resumed
run reaches the phase that failed and does the work from there.

Each name is replayed at most once per process, so a chain that re-enters a name
it has already replayed runs it live rather than answering twice from one record.
"""

from __future__ import annotations

from pathlib import Path

from . import artifacts
from .data_types import EnvelopeBase, Phase, RecordedPhase


class ReplayLog:
    """This session's recorded agent envelopes, keyed by the phase that made them.

    Inert unless the run was launched with `--resume`: `active` False makes
    `envelope_for` return None every time, so the replay path costs a dict
    lookup on a normal run and nothing else.
    """

    def __init__(self, records: dict[str, RecordedPhase], active: bool = False):
        self.records = records if active else {}
        self.active = active
        self.used: list[str] = []

    @property
    def available(self) -> int:
        """How many recorded phases are still on offer."""
        return len(self.records) - len(self.used)

    def summary(self) -> str:
        """The one line the console prints when a resumed run starts."""
        if not self.active:
            return ""
        if not self.records:
            return ("resume: nothing recorded for this session yet — every phase "
                    "runs live")
        return (f"resume: {len(self.records)} recorded phase(s) available to replay "
                f"({', '.join(sorted(self.records))})")

    def envelope_for(self, phase: Phase, output_type: type[EnvelopeBase]) -> EnvelopeBase | None:
        """The recorded envelope for this phase, or None to run it live.

        None covers every reason not to replay, and they are all ordinary: not
        resuming, nothing recorded under this name, this name already replayed,
        a different agent or output type than the record (the chain changed), or
        a payload that no longer parses against the type (the contract changed).
        The caller does not distinguish them — it just runs the agent.
        """
        record = self.records.get(phase.params.name)
        if record is None or record.phase in self.used:
            return None
        if record.agent != phase.params.owner or record.output_type != output_type.__name__:
            return None
        try:
            envelope = output_type.model_validate_json(record.payload_json)
        except ValueError:
            return None            # the type moved on; the record cannot be honoured
        self.used.append(record.phase)
        return envelope


def load(session_dir: Path, active: bool) -> ReplayLog:
    """Build this session's replay log from its own directory.

    The LAST successful record wins per phase name, because a session that has
    been resumed before holds two records for the same phase — the original and
    its replay — and they say the same thing.
    """
    if not active:
        return ReplayLog({}, active=False)
    return ReplayLog({record.phase: record
                      for record in artifacts.recorded_phases(session_dir)},
                     active=True)
