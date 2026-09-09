#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/resume — re-launch a run where it died, replaying what it already produced.

Usage:
    uv run <skill>/scripts/resume.py <adw_id> [--dry-run] [--config ...]

A failed chain has already paid for every phase before the one that broke, and
the trace holds all of them: the plan, the build, the review, each with its
envelope. Re-running the ADW from the top buys none of that back and charges
for it again. So this re-launches the SAME workflow against the SAME session
with `--resume`, and the run answers those phases from the record instead of
from an agent — see `adw_modules/replay.py` for what is replayed and what is
deliberately re-run.

WHAT IT RE-LAUNCHES IS WHAT RAN. The command line is not reconstructed from the
session's request text — that column is truncated, and a prompt that came from
a file is not in it at all. `processes` recorded the adw process's argv when it
started, which is the only place the real invocation exists; the newest one for
this session is the process that was working when it ended.

Deliberately not an ADW. Choosing what to launch takes no prompt and no
judgement, so it is code — the same reason `kill_run.py` is not one either.
"""

import argparse
import os
import shlex
import sqlite3
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


def _last_adw_command(db_path: Path, adw_id: str) -> str:
    """The argv of the newest adw process recorded for this session."""
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        row = conn.execute(
            "SELECT command FROM processes WHERE adw_id = ? AND kind = 'adw'"
            " ORDER BY id DESC LIMIT 1", (adw_id,)).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    return row[0] if row and row[0] else ""


def _session_row(db_path: Path, adw_id: str) -> tuple[str, str]:
    """(status, adw_name) for the session, or ("", "") when it is unknown here."""
    if not db_path.exists():
        return "", ""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        row = conn.execute("SELECT status, adw_name FROM sessions WHERE adw_id = ?",
                           (adw_id,)).fetchone()
    except sqlite3.Error:
        return "", ""
    finally:
        conn.close()
    return (row[0] or "", row[1] or "") if row else ("", "")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True             # exists, owned by someone else


def _rebuild(command: str, adw_id: str, config: str) -> list[str]:
    """The recorded argv, re-pointed at this session and told to replay.

    The recorded first word is the script's NAME (that is what the tracer
    writes), so it is re-anchored under `adws/`. An `--adw-id` already in the
    argv is dropped and re-added — a run launched without one was still given
    one, and the resumed process has to be pinned to it. A run that took the
    default config carries no `--config`, so the one this script resolved is
    passed on rather than left to a default that may since have moved.
    """
    argv = shlex.split(command)
    if not argv:
        return []
    script = Path(argv[0]).name
    rest, skip = [], False
    for token in argv[1:]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("adw_id")
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the command this would run, and run nothing")
    args, passthrough = parser.parse_known_args()

    from adw_modules import agents, git_helper, tracer
    from adw_modules.utils import anchor

    cfg = agents.load_config(args.config)
    db_path = anchor(git_helper.main_root(), cfg.observability.db)

    status, adw_name = _session_row(db_path, args.adw_id)
    if not status:
        print(f"{args.adw_id}: no such session in {db_path} — `just sessions` lists them")
        return 1

    # Two processes working one session would fight over its worktree, its
    # branch and its agent sessions. The pid is what tells a run that is still
    # alive from one whose row was never closed (a SIGKILL, an OOM, a reboot).
    pid = tracer.running_adw_pids(db_path).get(args.adw_id, 0)
    if pid and _alive(pid):
        print(f"{args.adw_id}: still running as pid {pid} — nothing to resume. "
              f"`just kill {args.adw_id}` first if it is stuck")
        return 1

    command = _last_adw_command(db_path, args.adw_id)
    if not command:
        print(f"{args.adw_id}: no adw process recorded, so there is no invocation to "
              f"repeat. Re-run the workflow by hand with --adw-id {args.adw_id} --resume")
        return 1

    # `processes.command` is stored clipped at 500 characters, which only a long
    # INLINE prompt reaches — and a clipped argv would be re-run as a different
    # request. Printed, never silently repaired: the command is echoed below, so
    # the engineer can see what would run and pass the prompt themselves instead.
    if len(command) >= 500:
        print("warning: the recorded invocation was clipped at 500 characters — "
              "check the command below before trusting it")
    argv = _rebuild(command, args.adw_id, args.config)
    script = Path(argv[2]).stem if len(argv) > 2 else ""
    if script in NO_RESUME:
        print(f"{args.adw_id}: {script} is a single-agent workflow — there is nothing "
              f"to resume, only to run again. `just {script.removeprefix('adw_')}` does that")
        return 1
    if passthrough:
        argv += passthrough

    if status == "success":
        print(f"note: {args.adw_id} ended in success — resuming replays it and re-runs "
              f"what code owns")
    print(f"{args.adw_id}: {adw_name or script} · {status}")
    print(f"  {' '.join(shlex.quote(part) for part in argv)}")
    if args.dry_run:
        return 0
    return subprocess.run(argv).returncode


if __name__ == "__main__":
    sys.exit(main())
