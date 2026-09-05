#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/kill — stop a running ADW: its agent children first, then the workflow itself.

Usage:
    uv run <skill>/scripts/kill_run.py <adw_id> [--force] [--config ...]

A hung agent emits nothing — no events, no tokens, no output to read — which is
exactly when you need its pid. `processes` is the only table that can answer
"what is this run running, and how do I stop it": one row per live process, the
adw written before the first phase opens and each agent child as it spawns.

CHILDREN BEFORE THE PARENT, on purpose. Kill the workflow first and its coding
agent keeps running, detached, still burning tokens against an API — with
nothing left to record what it did.

EVERY PID IS VERIFIED BEFORE IT IS SIGNALLED. Pids get recycled, and a stale
`running` row can name one that now belongs to something else entirely; the
`command` column exists for exactly this check. A pid whose command no longer
matches is reported and skipped, never signalled — killing a stranger's process
because a trace row was out of date is the one outcome worth this whole file.

Deliberately not an ADW. Stopping work takes no prompt and is not
agents-plus-code work; it is thin over adw_modules for the same reason ADWs are.
"""

import argparse
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "adws"))      # the stamped factory in this repo

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
GRACE_SECONDS = 5.0


def _live_rows(db_path: Path, adw_id: str) -> list[tuple]:
    """Believed-alive processes for this run: children first, adw last."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        return conn.execute(
            "SELECT kind, name, pid, command FROM processes "
            " WHERE adw_id = ? AND ended_at IS NULL AND pid IS NOT NULL"
            # 'agent' sorts before 'adw' only by accident; order explicitly.
            " ORDER BY CASE kind WHEN 'agent' THEN 0 ELSE 1 END, id",
            (adw_id,),
        ).fetchall()
    finally:
        conn.close()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True             # exists, owned by someone else


def _matches(pid: int, recorded: str) -> bool:
    """Whether pid is still the process the trace recorded.

    /proc on Linux, `ps` elsewhere. Neither readable → NOT a match: the whole
    point of this check is to refuse when it cannot be made.
    """
    if not recorded:
        return False
    actual = ""
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():
        actual = cmdline.read_bytes().replace(b"\\x00", b" ").decode(errors="replace")
    else:
        import subprocess
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True)
        actual = out.stdout.strip() if out.returncode == 0 else ""
    if not actual:
        return False
    # The recorded command is the argv the tracer saw; a prefix match survives
    # the shell rewriting or truncating the tail without loosening the check
    # to "any process at all".
    head = recorded.split()[0] if recorded.split() else recorded
    return head in actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("adw_id")
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--force", action="store_true",
                        help="SIGKILL after the grace period, and signal pids whose "
                             "command no longer matches (say it out loud, or do not)")
    args = parser.parse_args()

    from adw_modules import agents, git_helper
    from adw_modules.utils import anchor

    cfg = agents.load_config(args.config)
    db_path = anchor(git_helper.main_root(), cfg.observability.db)

    rows = _live_rows(db_path, args.adw_id)
    if not rows:
        print(f"{args.adw_id}: nothing believed alive — already finished, or never started")
        return 0

    signalled: list[int] = []
    for kind, name, pid, command in rows:
        label = f"{kind}{'/' + name if name else ''} pid {pid}"
        if not _alive(pid):
            print(f"  {label}: already gone")
            continue
        if not _matches(pid, command) and not args.force:
            print(f"  {label}: SKIPPED — no longer the recorded command "
                  f"({command[:60]!r}); the pid was recycled. --force overrides")
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            signalled.append(pid)
            print(f"  {label}: SIGTERM")
        except OSError as error:
            print(f"  {label}: could not signal ({error})")

    if not signalled:
        return 0

    # The run's own SIGTERM handler finalizes its trace — the session lands on
    # `fail` with its process rows closed, instead of reading `running` forever.
    # That is worth waiting for; SIGKILL would skip it.
    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        signalled = [pid for pid in signalled if _alive(pid)]
        if not signalled:
            print(f"{args.adw_id}: stopped, trace finalized by the run itself")
            return 0
        time.sleep(0.2)

    if not args.force:
        print(f"{args.adw_id}: still alive after {GRACE_SECONDS:.0f}s: "
              f"{signalled} — re-run with --force to SIGKILL")
        return 1

    for pid in signalled:
        try:
            os.kill(pid, signal.SIGKILL)
            print(f"  pid {pid}: SIGKILL")
        except OSError as error:
            print(f"  pid {pid}: {error}")
    print(f"{args.adw_id}: killed. The session row may still read `running` — "
          f"SIGKILL leaves no chance to finalize; that is what --force costs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
