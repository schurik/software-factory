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
import select
import sys
from pathlib import Path
from typing import Optional

from . import artifacts
from .data_types import Decision, HitlConfig, WaitingFor

EXIT_WAITING = 75          # EX_TEMPFAIL: "try again later", which is exactly it
POLL_SECONDS = 2.0         # attended: how long one look at the keyboard waits


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


# ── the keyboard ─────────────────────────────────────────────────────────────

def attended() -> bool:
    """Whether anyone can answer at this terminal. False under cron, a watcher, a test."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def read_keypress(waiting: WaitingFor) -> tuple[str, str]:
    """The real `ask`: one line from a TTY, or ("", "") after POLL_SECONDS so the
    caller can look at the decision record again. Notes are required on a reject
    — they are what the agent revises from."""
    ready, _, _ = select.select([sys.stdin], [], [], POLL_SECONDS)
    if not ready:
        return "", ""
    key = sys.stdin.readline().strip().lower()[:1]
    if key == "a":
        return "approve", input("notes for the next agent (optional): ").strip()
    if key == "r":
        notes = ""
        while not notes:
            notes = input("what should change: ").strip()
        return "reject", notes
    if key == "x":
        return "abort", input("reason (optional): ").strip()
    if key == "d":
        return "detach", ""
    return "", ""


# ── the policy ───────────────────────────────────────────────────────────────

OVERRIDES = ("all", "none", "every")


class HitlPolicy:
    """Whether a named gate fires, resolved most-specific-first.

    `override` is the `--hitl` flag (or `SSSF_HITL`): `all`, `none`, `every`,
    or a comma-separated list of gate names. It is a person saying so at the
    keyboard, and it wins over everything in the config — including
    `when_unattended`, because a flag on an issue run's argv was put there by
    the operator who launched the watcher.

    `ask` is how an attended run asks in place: a callable taking the
    `WaitingFor` and returning `(verdict | "detach" | "", notes)`. None means
    nobody is at the keyboard and the gate suspends at once. It lives here
    because "is anyone attending" is a policy input, and because this is the
    run-scoped object a test can hand a scripted answerer to.
    """

    def __init__(self, config: HitlConfig, override: str = ""):
        self.config = config
        self.override = (override or "").strip().lower()
        self.every = self.override == "every"
        self._named: set[str] = set()
        if self.override and self.override not in OVERRIDES:
            names = {part.strip() for part in self.override.split(",") if part.strip()}
            if not names or any(not part.replace("_", "").isalnum() for part in names):
                raise ValueError(f"--hitl {override!r}: expected all | none | every | "
                                 f"a comma-separated list of gate names")
            self._named = names
        self.ask = read_keypress if attended() else None

    def mode(self, gate: str, trigger: str) -> str:
        """`on` — stop and ask. `auto` — record a policy approval and go on."""
        if self.override in ("all", "every"):
            return "on"
        if self.override == "none":
            return "auto"
        if self._named:
            return "on" if gate in self._named else "auto"
        wanted = self.config.gates.get(gate, self.config.default) == "on"
        if not wanted:
            return "auto"
        if trigger != "engineer" and self.config.when_unattended == "auto":
            return "auto"
        return "on"

    def summary(self) -> str:
        """The one line the console prints when any gate may fire."""
        source = f"--hitl {self.override}" if self.override else "config"
        on = sorted(gate for gate, value in self.config.gates.items() if value == "on")
        line = f"hitl: {source}"
        if self.every:
            line += " · a checkpoint after every agent phase"
        elif not self.override:
            line += f" · default {self.config.default}"
            if on:
                line += f" · on: {', '.join(on)}"
        return line + (" · attended" if self.ask else " · unattended, gates suspend")
