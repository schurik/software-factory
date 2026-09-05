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

THE LABEL FLIP IS THE CLAIM, NOT THE LOCK — an earlier version of this file
said otherwise and was wrong. The forge has no conditional label change:
`gh issue edit --remove-label queued` succeeds whether or not the issue still
carries it, so two watchers that listed concurrently BOTH come back ok and both
launch, which costs two worktrees, two pull requests and two runs' worth of
tokens on one issue.

Exclusion therefore comes from a file lock, taken per issue before the claim and
held for the whole run. That covers the deployment this is built for — one
watcher per repository, from cron, on one machine — and it does NOT cover two
watchers on two machines. Nothing available at the forge would; if you need
that, run one watcher.

What the labels still give you is STATE a human can read and reset, in the place
they already look. A crashed watcher leaves an issue on `running`, and moving it
back to `queued` by hand is the whole recovery.

`project` is resolved ONCE at startup and this refuses to run without it. A
watcher that polls nothing looks exactly like a watcher with nothing to do —
which is what makes cron the place this silently breaks, and why it is checked
before the first poll rather than discovered on the tenth empty one.

Thin for the reason ADWs are (SKILL.md rule 6): every decision below lives in
`adws/adw_modules/issues.py`, which this imports from the repo it is run in.
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
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
    """How many runs are ACTUALLY in flight. WAL: reading never blocks a writer.

    A session row saying `running` is a belief, not a fact — a SIGKILL, an OOM
    or a reboot leaves one behind forever, and nothing reaps it. Counting those
    would wedge the watcher at max_concurrent permanently, looking exactly like
    a busy factory. So each candidate is checked for a live pid; signal 0 is the
    standard "does this process exist" probe and delivers nothing.

    A recycled pid can make a dead run look alive. That errs toward launching
    too FEW runs, which the next poll fixes — the opposite mistake spends money.
    """
    from adw_modules.tracer import running_adw_pids
    from adw_modules.utils import anchor
    alive = 0
    for adw_id, pid in running_adw_pids(anchor(main_root, cfg.observability.db)).items():
        try:
            os.kill(pid, 0)
            alive += 1
        except ProcessLookupError:
            print(f"  (session {adw_id} says running, pid {pid} is gone — not counted)")
        except PermissionError:
            alive += 1          # exists, owned by someone else
    return alive


def _route(cfg, labels: list[str]) -> str:
    """Which ADW this issue's labels ask for. Empty when none of them do."""
    names = [entry.get("name", "") if isinstance(entry, dict) else str(entry)
             for entry in labels]
    for label, script in cfg.issues.route.items():
        if label in names:
            return script
    return ""


@contextmanager
def _claim(cfg, main_root, project: str, number: int):
    """Hold an exclusive claim on one issue, or yield False.

    `flock` on a file per (project, issue), non-blocking: a second watcher on
    this machine fails instantly and moves on rather than launching a duplicate
    run. Held for the whole run, released by the OS even if this process is
    killed — a lock file left behind is not a stuck lock.

    Two watchers on two machines are still both able to claim. That is a real
    limit, stated in the module docstring rather than papered over.
    """
    from adw_modules.utils import anchor, ensure_dir
    lock_dir = ensure_dir(anchor(main_root, f"{cfg.defaults.data_dir}/issue-locks"))
    slug = f"{project.replace('/', '-')}-{number}.lock" if project else f"{number}.lock"
    handle = open(lock_dir / slug, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"  #{number}: another watcher on this machine holds it")
            yield False
            return
        yield True
    finally:
        handle.close()          # releases the flock


def _flip(cfg, main_root, project: str, number: int,
          add: str, remove: str) -> bool:
    """Move an issue's labels. NOT exclusion — see _claim and the module docstring.

    Thin over `adw_modules.issues.set_state`, which is where the argv shape and
    the forge's quirks already live. Reimplementing it here is what the first
    version did, and the two copies would have drifted the moment either grew a
    flag.
    """
    from adw_modules.data_types import IssueUpdate
    from adw_modules.issues import set_state
    result = set_state(main_root, cfg.issues, IssueUpdate(
        number=number, project=project, add_labels=[add], remove_labels=[remove]))
    if not result.ok:
        print(f"  #{number}: {' · '.join(result.notes)}")
    return result.ok


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
        # The lock is held across the claim, the run and the release — a second
        # watcher that listed the same issue a moment ago finds it taken and
        # moves on instead of launching a duplicate.
        with _claim(cfg, main_root, project, number) as mine:
            if not mine:
                continue
            if not _flip(cfg, main_root, project, number,
                         cfg.issues.states.running, cfg.issues.states.queued):
                continue

            code = _launch(script, config_path, number, main_root)
            launched += 1
            # The run's own report phase said WHAT happened on the issue; this
            # says whether it may be picked up again. Only the exit code knows
            # that, and only this process ever sees it.
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
