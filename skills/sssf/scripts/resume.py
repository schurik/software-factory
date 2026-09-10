#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/resume — re-launch a run where it died, replaying what it already produced.

Usage:
    uv run <skill>/scripts/resume.py <adw_id> [--dry-run] [--config ...]

A failed chain has already paid for every phase before the one that broke, and
the session directory holds all of them: the plan, the build, the review, each
with its envelope. Re-running the ADW from the top buys none of that back and
charges for it again. So this re-launches the SAME workflow against the SAME
session with `--resume`, and the run answers those phases from its own record
instead of from an agent — see `adw_modules/replay.py` for what is replayed and
what is deliberately re-run.

THE SESSION DIRECTORY IS THE RECORD, not the trace db. `adws/adw_data/sessions/
<adw_id>/run.json` is written when a process takes the session and closed when
it ends: which workflow ran, the argv that started it, the pid, the outcome.
That is what makes this work with the db deleted, and it is why the argv comes
back exactly as it went in — a list, never a joined string clipped to fit a
column. The db stays the queryable mirror the visualizer polls.

Deliberately not an ADW. Choosing what to launch takes no prompt and no
judgement, so it is code — the same reason `kill_run.py` is not one either.
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "adws"))      # the stamped factory in this repo

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
# Chains only. A single-agent ADW has nothing to resume — replaying its one
# phase would leave the run with no work left to do — so those never took the
# flag, and saying that is more use than an argparse error.
NO_RESUME = {"adw_prompt", "adw_scout", "adw_issue_scout", "adw_plan",
             "adw_document", "adw_quality", "adw_integrate"}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True             # exists, owned by someone else


def _rebuild(command: list[str], adw_id: str, config: str) -> list[str]:
    """The recorded argv, re-pointed at this session and told to replay.

    The recorded first word is the script's NAME (that is what the session
    records), so it is re-anchored under `adws/`. An `--adw-id` already in the
    argv is dropped and re-added — a run launched without one was still given
    one, and the resumed process has to be pinned to it. A run that took the
    default config carries no `--config`, so the one this script resolved is
    passed on rather than left to a default that may since have moved.
    """
    if not command:
        return []
    script = Path(command[0]).name
    rest, skip = [], False
    for token in command[1:]:
        if skip:
            skip = False
            continue
        if token == "--adw-id":
            skip = True
            continue
        if token.startswith("--adw-id=") or token == "--resume":
            continue
        rest.append(token)
    if not any(token == "--config" or token.startswith("--config=") for token in rest):
        rest += ["--config", config]
    return ["uv", "run", f"adws/{script}", *rest, "--adw-id", adw_id, "--resume"]


def relaunch(adw_id: str, config: str, dry_run: bool = False,
             passthrough: tuple[str, ...] = ()) -> int:
    """Re-launch the workflow that recorded `adw_id`, with `--resume`.

    Everything `main()` does after parsing, so `hitl.py` can answer a gate and
    bring the run back in one step. Returns the exit code to hand on — the
    relaunched run's own, or 1 with a printed reason when nothing was launched.
    """
    from adw_modules import agents, artifacts, git_helper, hitl
    from adw_modules.utils import anchor

    cfg = agents.load_config(config)
    sessions = anchor(git_helper.main_root(), f"{cfg.defaults.data_dir}/sessions")
    session_dir = sessions / adw_id

    state = artifacts.read_run(session_dir)
    if state is None:
        print(f"{adw_id}: no session recorded at {session_dir} — "
              f"`just sessions` lists what this repo has run")
        return 1

    # Two processes working one session would fight over its worktree, its
    # branch and its agent sessions. The pid is what tells a run that is still
    # alive from one whose record was never closed (a SIGKILL, an OOM, a reboot).
    if state.status == "running" and state.pid and _alive(state.pid):
        print(f"{adw_id}: still running as pid {state.pid} — nothing to resume. "
              f"`just kill {adw_id}` first if it is stuck")
        return 1

    # A run stopped at a gate resumes INTO that gate. Without a decision it would
    # stop there again — cheaply, but pointlessly — so say what it needs instead.
    if state.status == "waiting" and state.waiting_for is not None:
        waiting = state.waiting_for
        if hitl.read_decision(session_dir, waiting.gate, waiting.round) is None:
            print(f"{adw_id}: waiting at gate {waiting.gate} (round {waiting.round}) with "
                  f"no decision recorded — `just approve {adw_id}`, `just reject {adw_id} "
                  f"-m \"...\"` or `just abort {adw_id}` first; resuming now would stop "
                  f"at the same gate")
            return 1

    if not state.command:
        print(f"{adw_id}: the session recorded no invocation, so there is nothing "
              f"to repeat. Re-run the workflow by hand with --adw-id {adw_id} --resume")
        return 1

    argv = _rebuild(state.command, adw_id, config)
    script = Path(argv[2]).stem if len(argv) > 2 else ""
    if script in NO_RESUME:
        print(f"{adw_id}: {script} is a single-agent workflow — there is nothing "
              f"to resume, only to run again. `just {script.removeprefix('adw_')}` does that")
        return 1
    if passthrough:
        argv += list(passthrough)

    if state.status == "success":
        print(f"note: {adw_id} ended in success — resuming replays it and re-runs "
              f"what code owns")
    print(f"{adw_id}: {state.adw_name or script} · {state.status}")
    print(f"  {' '.join(shlex.quote(part) for part in argv)}")
    if dry_run:
        return 0
    return subprocess.run(argv).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("adw_id")
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the command this would run, and run nothing")
    args, passthrough = parser.parse_known_args()
    return relaunch(args.adw_id, args.config, args.dry_run, tuple(passthrough))


if __name__ == "__main__":
    sys.exit(main())
