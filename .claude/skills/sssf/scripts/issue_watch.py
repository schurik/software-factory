#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/issue_watch — poll the tracker and launch a run per labelled issue. Run from a repo root.

Usage:
    uv run <skill>/scripts/issue_watch.py once
    uv run <skill>/scripts/issue_watch.py loop [--interval 120]
    uv run <skill>/scripts/issue_watch.py status

Deliberately OUTSIDE the factory, like `worktrees.py`. A queue and a worker sit
above the control plane, not inside it — and this is the smallest thing that
does the job: the LABEL IS THE QUEUE and the poll is the dequeue.

THE LABEL FLIP IS THE LOCK. Claiming an issue means moving it off `queued` at
the forge, which is atomic there and visible to humans in the place they already
look. Two watchers racing the same issue: one wins the flip, the other's `gh`
call is a no-op on an issue that no longer matches and it moves on. No state
file, nothing to corrupt, and a crashed watcher leaves a `running` label a
person can read and reset.

`project` is resolved ONCE at startup and this refuses to run without it. A
watcher that polls nothing looks exactly like a watcher with nothing to do —
which is what makes cron the place this silently breaks, and why it is checked
before the first poll rather than discovered on the tenth empty one.

Thin for the reason ADWs are (SKILL.md rule 6): every decision below lives in
`adws/adw_modules/issues.py`, which this imports from the repo it is run in.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "adws"))      # the stamped factory in this repo

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"


def _load(config_path: str):
    from adw_modules import agents, git_helper, issues
    cfg = agents.load_config(config_path)
    main_root = git_helper.main_root()
    project = issues.resolve_project(cfg.issues, main_root)
    return cfg, main_root, project


def _list_queued(cfg, main_root, project: str) -> list[dict]:
    """Issues carrying the queued label. Never raises — an outage is not a crash."""
    from adw_modules.utils import operator_env
    argv = [*cfg.issues.list_command, "--label", cfg.issues.states.queued,
            "--state", "open", "--json", "number,title,labels,author",
            "--limit", "50"]
    if project:
        argv += ["--repo", project]
    completed = subprocess.run(argv, cwd=str(main_root), env=operator_env(),
                               capture_output=True, text=True)
    if completed.returncode != 0:
        print(f"  ! could not list issues: "
              f"{(completed.stderr or completed.stdout).strip()[-300:]}")
        return []
    try:
        return json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        print("  ! the list command did not return JSON")
        return []


def _running_count(cfg, main_root) -> int:
    """How many runs are in flight, straight from the trace. WAL: never blocks."""
    from adw_modules.tracer import session_statuses
    from adw_modules.utils import anchor
    statuses = session_statuses(anchor(main_root, cfg.observability.db))
    return sum(1 for status in statuses.values() if status == "running")


def _route(cfg, labels: list[str]) -> str:
    """Which ADW this issue's labels ask for. Empty when none of them do."""
    names = [entry.get("name", "") if isinstance(entry, dict) else str(entry)
             for entry in labels]
    for label, script in cfg.issues.route.items():
        if label in names:
            return script
    return ""


def _flip(cfg, main_root, project: str, number: int,
          add: str, remove: str) -> bool:
    """Move an issue's label. The return value is the lock: False = someone else."""
    from adw_modules.utils import operator_env
    argv = [*cfg.issues.state_command, str(number)]
    if project:
        argv += ["--repo", project]
    argv += ["--add-label", add, "--remove-label", remove]
    completed = subprocess.run(argv, cwd=str(main_root), env=operator_env(),
                               capture_output=True, text=True)
    if completed.returncode != 0:
        print(f"  #{number}: not claimed "
              f"({(completed.stderr or completed.stdout).strip()[-200:]})")
        return False
    return True


def _launch(script: str, config_path: str, number: int, main_root) -> int:
    """Run one ADW to completion and return its exit code.

    Serial on purpose. `max_concurrent` bounds how many runs exist at once, and
    the honest way to hold that bound is to wait for the one just started rather
    than to fire and hope. A watcher that must not block is a watcher that
    wanted a queue, and a queue is Phase 3's problem, not this file's.
    """
    argv = ["uv", "run", script, str(number), "--config", config_path]
    print(f"  #{number}: {' '.join(argv)}")
    return subprocess.run(argv, cwd=str(main_root)).returncode


def once(config_path: str) -> int:
    cfg, main_root, project = _load(config_path)
    if not cfg.issues.enabled:
        print("issues.enabled is false — nothing to watch")
        return 0
    if not project:
        print("issues.project is empty and no origin remote could be read.\n"
              "Set issues.project in the config: a watcher that cannot name its "
              "project polls nothing, and polling nothing is indistinguishable "
              "from having nothing to do.", file=sys.stderr)
        return 2

    queued = _list_queued(cfg, main_root, project)
    print(f"{project}: {len(queued)} issue(s) labelled {cfg.issues.states.queued}")
    launched = 0
    for entry in queued:
        number = entry.get("number")
        script = _route(cfg, entry.get("labels") or [])
        if not script:
            print(f"  #{number}: no routing label — leaving it queued")
            continue
        if _running_count(cfg, main_root) >= cfg.issues.max_concurrent:
            print(f"  #{number}: max_concurrent ({cfg.issues.max_concurrent}) "
                  f"reached — leaving it queued for the next poll")
            break
        if not _flip(cfg, main_root, project, number,
                     cfg.issues.states.running, cfg.issues.states.queued):
            continue

        code = _launch(script, config_path, number, main_root)
        launched += 1
        # The run's own report phase said WHAT happened on the issue; this says
        # whether it may be picked up again. Only the exit code knows that, and
        # only this process ever sees it.
        _flip(cfg, main_root, project, number,
              cfg.issues.states.done if code == 0 else cfg.issues.states.failed,
              cfg.issues.states.running)
    print(f"launched {launched} run(s)")
    return 0


def loop(config_path: str, interval: int) -> int:
    print(f"polling every {interval}s — ctrl-c to stop")
    while True:
        try:
            once(config_path)
        except KeyboardInterrupt:
            raise
        except Exception as error:                  # an outage is not a crash
            print(f"! poll failed: {error}")
        time.sleep(interval)


def status(config_path: str) -> int:
    cfg, main_root, project = _load(config_path)
    print(f"enabled:        {cfg.issues.enabled}")
    print(f"project:        {project or '(unresolved — set issues.project)'}"
          f"{'  (from origin)' if project and not cfg.issues.project else ''}")
    print(f"queued label:   {cfg.issues.states.queued}")
    print(f"max_concurrent: {cfg.issues.max_concurrent}"
          f"  (running now: {_running_count(cfg, main_root)})")
    print(f"force_pr:       {cfg.issues.force_pr}")
    print(f"trusted:        {', '.join(cfg.issues.trusted_authors) or '(anyone who gets labelled)'}")
    print("routes:")
    for label, script in cfg.issues.route.items() or {}.items():
        print(f"  {label:<16} -> {script}")
    if not cfg.issues.route:
        print("  (none — no label routes to an ADW, so nothing would ever launch)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["once", "loop", "status"])
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--interval", type=int, default=120,
                        help="loop: seconds between polls")
    args = parser.parse_args()

    if args.action == "once":
        return once(args.config)
    if args.action == "loop":
        return loop(args.config, args.interval)
    return status(args.config)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(130)
