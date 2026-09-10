"""The session directory IS the record. This module reads and writes it.

`adws/adw_data/sessions/<adw_id>/` already holds everything a run produced —
`events.jsonl`, each agent's `envelope.json`, its compiled `prompts/`, the raw
harness stream, `agent_map.json`, `context_handoff/`. The trace db is the
queryable MIRROR of the same events, kept for the visualizer to poll; nothing
the factory does at runtime depends on it. So anything that needs to know what
a session has already done reads these files, never sqlite: a run works when
the db is missing, when it has been deleted to reclaim disk, or when the
visualizer is holding it — and the record travels with the session directory.

Two artifacts are written here that the rest of the record could not supply:

  * `run.json` — the session's own state: which workflow ran, the ARGV that
    started it, the pid, and how it ended. `events.jsonl` describes phases, not
    the process that opened them, and `just resume` has to know what to launch
    again.
  * `envelopes/<phase_id>.json` — one file per agent PHASE. The per-agent
    `envelope.json` beside it is last-wins, which is right for "what did the
    builder last say" and useless for a replay: a builder that built, fixed and
    revised leaves one file and three phases.

Both are small, both are rewritten rather than appended, and neither is read by
anything but the factory itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from .data_types import RecordedPhase, RunState
from .utils import ensure_dir

RUN_FILE = "run.json"
ENVELOPES_DIR = "envelopes"
EVENTS_FILE = "events.jsonl"


# ── run.json ─────────────────────────────────────────────────────────────────

def run_path(session_dir: Path) -> Path:
    return Path(session_dir) / RUN_FILE


def read_run(session_dir: Path) -> RunState | None:
    """The session's recorded state, or None when it has none yet."""
    path = run_path(session_dir)
    if not path.is_file():
        return None
    try:
        return RunState(**json.loads(path.read_text()))
    except (ValueError, OSError):
        return None            # a truncated write is a missing answer, not a crash


def write_run(session_dir: Path, state: RunState) -> None:
    ensure_dir(Path(session_dir))
    run_path(session_dir).write_text(state.model_dump_json(indent=2))


def start_run(session_dir: Path, state: RunState) -> RunState:
    """Record that a process has taken this session, keeping what came before.

    A joined session is worked by more than one ADW, and `workflows` is the list
    of them in order — the same story `sessions.adw_name` tells in the db. The
    command and the pid are the NEWEST process's, because they answer "what
    would running this again mean", and the newest process is the one that was
    working when the session stopped.
    """
    previous = read_run(session_dir)
    if previous:
        state.workflows = previous.workflows + [
            name for name in state.workflows if name not in previous.workflows]
        # Provenance is learned once and never unlearned — an issue-triggered
        # session that a later ADW re-enters is still issue-triggered.
        state.trigger = state.trigger or previous.trigger
        state.issue_url = state.issue_url or previous.issue_url
        state.pr_url = state.pr_url or previous.pr_url
    write_run(session_dir, state)
    return state


def finish_run(session_dir: Path, status: str) -> None:
    """Close the session's record. Never raises: a run must not die reporting.

    Called from `run.finish()`, from a failed phase, and from the SIGTERM
    handler, so the file agrees with the db about how the session ended even
    when the ending was not the happy one.
    """
    from .utils import now_iso
    state = read_run(session_dir)
    if state is None:
        return
    state.status = status
    state.ended_at = now_iso()
    try:
        write_run(session_dir, state)
    except OSError:
        pass


def update_run(session_dir: Path, **fields) -> None:
    """Patch the recorded state in place (provenance, a pull request url)."""
    state = read_run(session_dir)
    if state is None:
        return
    for key, value in fields.items():
        if value:
            setattr(state, key, value)
    try:
        write_run(session_dir, state)
    except OSError:
        pass


# ── envelopes/<phase_id>.json ────────────────────────────────────────────────

def write_envelope(session_dir: Path, record: RecordedPhase) -> None:
    """One agent phase's envelope, keyed by the phase that produced it."""
    directory = ensure_dir(Path(session_dir) / ENVELOPES_DIR)
    (directory / f"{record.phase_id}.json").write_text(record.model_dump_json(indent=2))


def recorded_phases(session_dir: Path) -> list[RecordedPhase]:
    """Every agent phase this session completed successfully, in run order.

    Two session artifacts answer this together, and both are needed. The
    envelope files say what each agent PRODUCED; `events.jsonl` says which
    phases actually PASSED — an envelope is written before the phase closes, so
    one whose phase then failed (a bad status, a permission breach) is on disk
    and must never be handed back. A phase with no `phase_end` recorded (the
    process was killed mid-phase) is unfinished, and therefore not offered.
    """
    directory = Path(session_dir) / ENVELOPES_DIR
    if not directory.is_dir():
        return []
    passed = _phase_outcomes(Path(session_dir))
    records = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = RecordedPhase(**json.loads(path.read_text()))
        except (ValueError, OSError):
            continue           # an unreadable record is one that is not offered
        if passed.get(record.phase_id) == "success":
            records.append(record)
    return sorted(records, key=lambda record: record.seq)


def max_phase_seq(session_dir: Path, adw_id: str) -> int:
    """The highest phase number this session has already used; 0 when it is new.

    A joined or resumed run continues the sequence instead of restarting at 1 —
    restarting collides with the first process's phases on both the ordering and
    the `phase_id`, which is the name of the envelope file this module writes.
    Read from `events.jsonl` rather than the db for the reason the rest of this
    module exists: a run whose db was deleted must still number its phases
    correctly, and the seq is right there in every phase id the session emitted
    (`<adw_id>_<seq>_<name>`, so the prefix comes off and the digits are next).
    """
    highest = 0
    for phase_id in _phase_outcomes(session_dir, every=True):
        tail = phase_id.removeprefix(f"{adw_id}_").split("_", 1)[0]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest


def _phase_outcomes(session_dir: Path, every: bool = False) -> dict[str, str]:
    """{phase_id: final status} from the session's own event log.

    `events.jsonl` is the raw record the tracer appends as things happen — the
    same lines the db mirrors. Read forwards, so a phase re-entered by a later
    process in the session ends on its LATEST outcome. `every` widens it to
    phases that only ever STARTED, which is what counting the phase numbers a
    session has used needs — see `max_phase_seq`.
    """
    path = session_dir / EVENTS_FILE
    if not path.is_file():
        return {}
    outcomes: dict[str, str] = {}
    try:
        with path.open() as stream:
            for line in stream:
                line = line.strip()
                wanted = ('"phase_' if every else '"phase_end"')
                if not line or wanted not in line:
                    continue   # cheap reject: most lines are logs and tool calls
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not event.get("phase_id"):
                    continue
                if event.get("type") == "phase_end":
                    outcomes[event["phase_id"]] = (event.get("payload") or {}).get("status", "")
                elif every and event.get("type") == "phase_start":
                    outcomes.setdefault(event["phase_id"], "")
    except OSError:
        return {}
    return outcomes
