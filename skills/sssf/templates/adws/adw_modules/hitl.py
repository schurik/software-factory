"""Human-in-the-loop gates: stop after a phase, ask a person, continue or revise.

A gate is its own `kind="engineer"` phase — the lane that until now only ever
logged the request — plus a revise loop that is the reviewer loop in
`adw_build_review.py` with a person where the reviewer is. `gated()` owns both.
An ADW spends one call per gate and never sees the loop, the wait, or the file.

THE WAIT IS A SUSPEND. `decide()` looks for a decision this session already
recorded for the gate and round; finding none, it records what it is waiting
for in `run.json`, exits the process with status 75, and leaves the worktree
where it is. `just approve <adw_id>` writes the decision and re-launches the
same workflow with `--resume`: replay answers every recorded agent phase from
the record, the chain reaches the gate again, and this time the decision is
there. A terminal prompt is a convenience over that — while stdin is a TTY the
run asks in place and polls the same file, and `d` or `wait_seconds` turns the
block into the suspend it would have been anyway.

THE DECISION NAMES WHAT IT DECIDED. `subject_digest` hashes the artifact files
at the moment the human was asked; a decision whose digest does not match the
subject in front of the run now is refused and the run waits again. That is
the one rule everything here rests on, and it is what makes a decision file
safe to write from anywhere.

TRUST IS RECORDED. A gate the policy skips writes `verdict=approve,
by="policy", channel="auto"` to the same directory, so the record of a run
shows every gate it passed and who passed it.

Files only. The trace db mirrors the events; nothing here reads it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from . import artifacts
from .data_types import Decision

EXIT_WAITING = 75          # EX_TEMPFAIL: "try again later", which is exactly it


# ── the record ───────────────────────────────────────────────────────────────

def digest(paths: list[Path]) -> str:
    """One hash over the subject's files, independent of listing order.

    A missing file hashes as its name plus a marker, so "the plan is gone" is a
    different subject from "the plan is here" and from "there was no plan".
    """
    hasher = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        hasher.update(str(path).encode())
        hasher.update(b"\0")
        try:
            hasher.update(path.read_bytes())
        except OSError:
            hasher.update(b"<missing>")
        hasher.update(b"\0")
    return hasher.hexdigest()


def decision_path(session_dir: Path, gate: str, round: int) -> Path:
    return artifacts.decisions_dir(session_dir) / f"{gate}_{round}.json"


def record(session_dir: Path, decision: Decision) -> Path:
    """Write one decision. Keyed by gate and round; a later write replaces."""
    path = decision_path(session_dir, decision.gate, decision.round)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(decision.model_dump_json(indent=2))
    return path


def read_decision(session_dir: Path, gate: str, round: int) -> Optional[Decision]:
    path = decision_path(session_dir, gate, round)
    if not path.is_file():
        return None
    try:
        return Decision(**json.loads(path.read_text()))
    except (ValueError, OSError):
        return None            # a half-written file is a missing answer, not a crash
