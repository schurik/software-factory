"""Operating a factory from outside a run: gates, resume, doctor.

None of this is a workflow. Choosing a verdict takes no agent, re-launching a
run takes no prompt, and asking whether the repository is ready to run spawns
nothing — so it is code, reached through `asf.py`'s subcommands, and thin for
the reason stages are: the decision record lives in `engine.hitl`, the
session record in `engine.artifacts`, the checks in `engine.preflight`.

THE SESSION DIRECTORY IS THE RECORD, never the trace db. `run.json` says
which workflow ran, the argv that started it, and how it ended — so a
suspended run can be answered and brought back with the db deleted.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import artifacts, git_helper, hitl, preflight
from .data_types import SSSFConfig
from .utils import engineer_name

SHOW_LINES = 400        # a plan is a page or two; a diff can be anything
RUNNER = "asf/asf.py"

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
MARKS = {"ok": (GREEN, "✓"), "warn": (YELLOW, "~"), "fatal": (RED, "✗")}


def sessions_dir(cfg: SSSFConfig) -> Path:
    return artifacts.sessions_root(git_helper.main_root(), cfg.defaults.data_dir)


def _answer_hint(adw_id: str) -> str:
    return (f"asf approve {adw_id} [-m remarks] · asf reject {adw_id} -m \"...\" · "
            f"asf abort {adw_id}")


# ── gates ────────────────────────────────────────────────────────────────────

def pending(cfg: SSSFConfig) -> int:
    waiting = artifacts.waiting_sessions(sessions_dir(cfg))
    if not waiting:
        print("no run is waiting at a gate")
        return 0
    width = max(len(adw_id) for adw_id in waiting)
    for adw_id, what in sorted(waiting.items(), key=lambda item: item[1].since):
        print(f"{adw_id:<{width}}  {what.gate} · round {what.round}  since {what.since}")
        if what.summary:
            print(f"{'':<{width}}  {what.summary}")
        print(f"{'':<{width}}  asf show {adw_id} · {_answer_hint(adw_id)}")
    return 0


def show(cfg: SSSFConfig, adw_id: str) -> int:
    state = artifacts.read_run(sessions_dir(cfg) / adw_id)
    if state is None:
        print(f"{adw_id}: no such session — `asf sessions`")
        return 1
    if state.waiting_for is None:
        print(f"{adw_id}: not waiting at a gate (status {state.status})")
        return 1
    what = state.waiting_for
    print(f"{adw_id} · gate {what.gate} · round {what.round} · since {what.since}")
    print(f"  subject: {what.summary}")
    if what.notes:
        print(f"  the agent's notes: {what.notes}")
    print(f"  tree:    {state.repo_root} ({state.branch})")
    for path in what.paths:
        print(f"\n─── {path} ───")
        try:
            lines = Path(path).read_text().splitlines()
        except OSError as error:
            print(f"  (unreadable: {error})")
            continue
        print("\n".join(lines[:SHOW_LINES]))
        if len(lines) > SHOW_LINES:
            print(f"… {len(lines) - SHOW_LINES} more line(s) — open the file for the rest")
    print(f"\nanswer: {_answer_hint(adw_id)}")
    return 0


def decide(cfg: SSSFConfig, config_path: str, verdict: str, adw_id: str, notes: str,
           no_resume: bool = False) -> int:
    """Record one verdict, then bring the run back unless told not to.

    A run that is still ATTENDED — asking at its own terminal — is polling
    for this file and needs no relaunch. A suspended one is re-launched with
    `--resume`, replays every recorded agent phase for free, and reaches the
    gate with its answer in hand. A reject goes back to the agent that made
    the artifact, in the same session, and the run stops at the gate again.
    """
    session_dir = sessions_dir(cfg) / adw_id
    try:
        decision = hitl.answer(session_dir, verdict, notes, by=engineer_name())
    except RuntimeError as error:
        print(f"{adw_id}: {error}")
        return 1
    print(f"{adw_id}: {decision.verdict} recorded at "
          f"{hitl.decision_path(session_dir, decision.gate, decision.round)}")
    state = artifacts.read_run(session_dir)
    if state is not None and state.status == "running":
        print("  the run is attended and polling — it picks this up on its own")
        return 0
    if no_resume:
        print(f"  not relaunched (--no-resume): `asf resume {adw_id}` when ready")
        return 0
    code = relaunch(cfg, config_path, adw_id)
    after = artifacts.read_run(session_dir)
    if code == hitl.EXIT_WAITING and after is not None and after.waiting_for is not None:
        what = after.waiting_for
        print(f"{adw_id}: waiting again — gate {what.gate}, round {what.round}. "
              f"`asf show {adw_id}`")
    return code


# ── resume ───────────────────────────────────────────────────────────────────

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def rebuild(command: list[str], adw_id: str, config_path: str) -> list[str]:
    """The recorded argv, re-pointed at this session and told to replay.

    The record's first word is the runner's NAME, so it is re-anchored at
    `asf/asf.py`; the interpreter is this process's own, which is the one
    known to have the engine's dependencies. `--config` is a runner-level
    option and goes before the subcommand; a recorded one is replaced by the
    path this process resolved. `--adw-id` and `--resume` are dropped and
    re-added, so a run launched without an id is still pinned to the one it
    was given.
    """
    if not command:
        return []
    rest, skip = [], False
    for token in command[1:]:
        if skip:
            skip = False
            continue
        if token in ("--adw-id", "--config"):
            skip = True
            continue
        if token.startswith(("--adw-id=", "--config=")) or token == "--resume":
            continue
        rest.append(token)
    return [sys.executable, RUNNER, "--config", config_path, *rest, "--adw-id", adw_id, "--resume"]


def relaunch(cfg: SSSFConfig, config_path: str, adw_id: str, dry_run: bool = False,
             passthrough: tuple[str, ...] = ()) -> int:
    """Re-launch the workflow that recorded `adw_id`, with `--resume`."""
    session_dir = sessions_dir(cfg) / adw_id
    state = artifacts.read_run(session_dir)
    if state is None:
        print(f"{adw_id}: no session recorded at {session_dir} — `asf sessions` lists "
              f"what this repo has run")
        return 1
    if state.status == "running" and state.pid and _alive(state.pid):
        print(f"{adw_id}: still running as pid {state.pid} — nothing to resume")
        return 1
    if state.status == "waiting" and state.waiting_for is not None:
        waiting = state.waiting_for
        if hitl.read_decision(session_dir, waiting.gate, waiting.round) is None:
            print(f"{adw_id}: waiting at gate {waiting.gate} (round {waiting.round}) with no "
                  f"decision recorded — {_answer_hint(adw_id)} first; resuming now would "
                  f"stop at the same gate")
            return 1
    if not state.command:
        print(f"{adw_id}: the session recorded no invocation, so there is nothing to "
              f"repeat. Re-run the workflow by hand with --adw-id {adw_id} --resume")
        return 1
    argv = rebuild(state.command, adw_id, config_path) + list(passthrough)
    if state.status == "success":
        print(f"note: {adw_id} ended in success — resuming replays it and re-runs what "
              f"code owns")
    print(f"{adw_id}: {state.adw_name} · {state.status}")
    print(f"  {' '.join(shlex.quote(part) for part in argv)}")
    if dry_run:
        return 0
    return subprocess.run(argv).returncode


# ── doctor ───────────────────────────────────────────────────────────────────

def _paint(color: str, text: str) -> str:
    return f"{color}{text}{RESET}" if sys.stdout.isatty() else text


def doctor(cfg: SSSFConfig) -> int:
    """Everything that would fail later, in one screen. 0 unless something is
    fatal, so it works as a first CI step too. The checks live in
    `engine.preflight`, stamped and yours to edit; this is the screen."""
    main_root = git_helper.main_root()
    print(f"asf doctor — {main_root}\n")
    findings = preflight.everything(cfg, main_root)
    width = max((len(finding.check) for finding in findings), default=0)
    for finding in findings:
        color, mark = MARKS[finding.level]
        print(f"  {_paint(color, mark)} {finding.check.ljust(width)}  {finding.detail}")
        if finding.level != "ok" and finding.fix:
            print(f"    {_paint(DIM, '→ ' + finding.fix)}")
    fatal = [f for f in findings if f.level == "fatal"]
    warn = [f for f in findings if f.level == "warn"]
    print()
    if fatal:
        print(_paint(RED, f"  {len(fatal)} fatal, {len(warn)} warning(s) — a run would not "
                          f"get past this"))
        return 1
    if warn:
        print(_paint(YELLOW, f"  {len(warn)} warning(s) — runs work, with the caveats above"))
        return 0
    print(_paint(GREEN, "  all clear"))
    return 0
