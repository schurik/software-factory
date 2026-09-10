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

A RUN HAS THREE OUTCOMES, NOT TWO. It can succeed, it can fail, and it can stop
for a person at a HITL gate — exit 75, worktree intact, `just pending` naming
it. The third is neither of the first two, and reading it as failure tells the
reporter the opposite of what happened. See `_flip`'s call site in `once()`.

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
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "adws"))      # the stamped factory in this repo

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
# `adw_modules.hitl.EXIT_WAITING`, deliberately restated rather than imported:
# this is a value read off ANOTHER process's exit status, and that process is
# whatever version of the factory is stamped into the repo being watched. A
# constant shared across a process boundary is a protocol, not a dependency —
# and 75 is EX_TEMPFAIL, which is where both halves get it from anyway.
EXIT_WAITING = 75


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
    from adw_modules import artifacts
    alive = 0
    sessions = artifacts.sessions_root(main_root, cfg.defaults.data_dir)
    for adw_id, pid in artifacts.running_pids(sessions).items():
        try:
            os.kill(pid, 0)
            alive += 1
        except ProcessLookupError:
            print(f"  (session {adw_id} says running, pid {pid} is gone — not counted)")
        except PermissionError:
            alive += 1          # exists, owned by someone else
    return alive


def _beat(cfg, main_root, status: str, *, project: str = "",
          interval: int = 0, note: str = "") -> None:
    """Say that this watcher exists and what it just did — in a file, and in the db.

    The whole point is that "nothing is happening" stops being ambiguous: a
    watcher nobody started reads as absent rather than as a quiet one. The FILE
    is what `just status` reads, so the answer holds on a machine with no db;
    the db row is what the trace UI's badge renders, and writing it costs
    nothing. Tolerant of a stamped factory older than either — an out-of-date
    repo loses the badge, not the watcher.
    """
    try:
        from adw_modules import artifacts
        from adw_modules.tracer import watcher_beat
        from adw_modules.utils import anchor, now_iso
    except ImportError:
        return
    artifacts.watcher_beat(
        artifacts.watchers_dir(main_root, cfg.defaults.data_dir), "issues",
        {"status": status, "pid": os.getpid(), "project": project,
         "interval_s": interval, "note": note,
         "started_at": now_iso(), "last_poll_at": now_iso()})
    watcher_beat(anchor(main_root, cfg.observability.db), "issues", status,
                 pid=os.getpid(), project=project, interval_s=interval, note=note)


def _names(labels: list) -> list[str]:
    """The label names on one issue, however the forge spelled them."""
    return [entry.get("name", "") if isinstance(entry, dict) else str(entry)
            for entry in labels]


def _route(cfg, labels: list) -> str:
    """Which ADW this issue's labels ask for. Empty when none of them do.

    The ROUTING label is not part of the state machine below and is never
    removed: it is the authorization a human applied, and the record of which
    chain was asked for. It is expected to sit alongside whatever state label
    the issue currently carries.
    """
    names = _names(labels)
    for label, script in cfg.issues.route.items():
        if label in names:
            return script
    return ""


def _stale_states(cfg, labels: list, keep: str) -> list[str]:
    """State labels this issue still carries from an EARLIER run.

    The four states are meant to be mutually exclusive, and nothing enforced
    that: each flip removed exactly the one label it had just put on, so a
    `failed` left by one run survived the next one and an issue re-queued by
    hand — the documented recovery — ended up carrying `done` AND `failed`,
    with no way to tell which run either belonged to.

    Only labels the issue ACTUALLY CARRIES are named, and that is not
    tidiness: `gh issue edit --remove-label` on a name the repository never
    defined is an error, and a speculative clean-up would fail the claim it is
    attached to. A label the listing shows is a label the forge has.
    """
    states = cfg.issues.states
    known = {states.queued, states.running, states.done, states.failed}
    return sorted({name for name in _names(labels) if name in known and name != keep})


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
          add: str, remove: str | list[str]) -> bool:
    """Move an issue's labels. NOT exclusion — see _claim and the module docstring.

    Thin over `adw_modules.issues.set_state`, which is where the argv shape and
    the forge's quirks already live. Reimplementing it here is what the first
    version did, and the two copies would have drifted the moment either grew a
    flag.

    `remove` takes several because the CLAIM clears every state an earlier run
    left behind, in the one edit that takes the issue — see `_stale_states`.
    """
    from adw_modules.data_types import IssueUpdate
    from adw_modules.issues import set_state
    result = set_state(main_root, cfg.issues, IssueUpdate(
        number=number, project=project, add_labels=[add],
        remove_labels=[remove] if isinstance(remove, str) else list(remove)))
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
    # THIS WATCHER'S TERMINAL IS NOT THE RUN'S. `subprocess.run` hands the child
    # our stdin, so a watcher someone started by hand from a window gives every
    # run it launches a TTY indistinguishable from the engineer's — and a gate
    # would then prompt whoever is watching the queue, about a plan they never
    # asked to read, holding this deliberately serial loop for
    # `hitl.wait_seconds` before suspending anyway. Only the launcher knows the
    # difference, so the launcher says so. See adw_modules/hitl.py `attended`.
    env = {**os.environ, "SSSF_UNATTENDED": "1"}
    return subprocess.run(argv, cwd=str(main_root), env=env).returncode


def once(config_path: str, interval: int = 0) -> int:
    cfg, main_root, project = _load(config_path)
    if not cfg.issues.enabled:
        print("issues.enabled is false — nothing to watch")
        _beat(cfg, main_root, "disabled", note="issues.enabled is false")
        return 0
    if not project:
        print("issues.project is empty and no origin remote could be read.\n"
              "Set issues.project in the config: a watcher that cannot name its "
              "project polls nothing, and polling nothing is indistinguishable "
              "from having nothing to do.", file=sys.stderr)
        _beat(cfg, main_root, "error", note="issues.project is unresolved")
        return 2

    queued = _list_queued(cfg, main_root, project)
    print(f"{project}: {len(queued)} issue(s) labelled {cfg.issues.states.queued}")
    _beat(cfg, main_root, "polling", project=project, interval=interval,
          note=f"{len(queued)} queued")
    launched = waiting = 0
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
            # The claim, and the same edit clears whatever an earlier run left
            # on this issue — `queued` always, plus a stale `done` or `failed`
            # from before a human re-queued it. Doing it here rather than at
            # the outcome is what keeps the flip below removing only a label it
            # put on itself, which is the one kind of removal that cannot fail.
            if not _flip(cfg, main_root, project, number, cfg.issues.states.running,
                         _stale_states(cfg, entry.get("labels") or [],
                                       keep=cfg.issues.states.running)):
                continue

            # A run blocks this watcher for as long as it takes, so the row
            # has to say that: an idle watcher is recognised by a fresh
            # `last_poll_at`, and without this a busy one would look stale.
            _beat(cfg, main_root, "working", project=project, interval=interval,
                  note=f"#{number} {Path(script).stem}")
            code = _launch(script, config_path, number, main_root)
            launched += 1
            # A run that stopped at a gate is the THIRD outcome, and the label
            # it must not get is `failed` — the reporter would read that the
            # run was rejected when a person has simply not answered yet, and
            # `just pending` would say the opposite at the same moment.
            #
            # It is left on `running`, which is true and is also the safe
            # thing: only `queued` is dequeued, so the issue cannot be claimed
            # a second time while a person is still deciding.
            #
            # THE LABEL DOES NOT COME BACK ON ITS OWN. `just approve` re-launches
            # the run with --resume in a process this watcher never sees, so
            # `running` is the last word said here and it outlives the run that
            # earned it. `just pending` is where the truth lives meanwhile. A
            # terminal label for an answered run wants the answering path to own
            # the flip, which is a wider change than this one.
            if code == EXIT_WAITING:
                waiting += 1
                print(f"  #{number}: stopped for a human at a gate — left on "
                      f"{cfg.issues.states.running}, NOT {cfg.issues.states.failed}. "
                      f"`just pending` names the run; `just approve` / "
                      f"`just reject -m \"...\"` / `just abort` answer it")
                continue
            # The run's own report phase said WHAT happened on the issue; this
            # says whether it may be picked up again. Only the exit code knows
            # that, and only this process ever sees it.
            _flip(cfg, main_root, project, number,
                  cfg.issues.states.done if code == 0 else cfg.issues.states.failed,
                  cfg.issues.states.running)
    stalled = f", {waiting} waiting for a human" if waiting else ""
    print(f"launched {launched} run(s){stalled}")
    _beat(cfg, main_root, "polling", project=project, interval=interval,
          note=f"{len(queued)} queued, launched {launched}{stalled}")
    return 0


def loop(config_path: str, interval: int) -> int:
    print(f"polling every {interval}s — ctrl-c to stop")
    home = _home(config_path)
    try:
        while True:
            try:
                once(config_path, interval)
            except KeyboardInterrupt:
                raise
            except Exception as error:              # an outage is not a crash
                print(f"! poll failed: {error}")
                _beat_error(home, error)
            time.sleep(interval)
    finally:
        # The row outlives the process, so leaving it on `polling` would make a
        # watcher that was stopped on purpose look like one that died. Written
        # from the config resolved at STARTUP, never re-read here: this runs
        # while the process is being torn down, and a git subprocess plus a
        # config parse is long enough for the shutdown to win the race.
        if home:
            _beat(*home, "stopped", note="watcher exited")


def _home(config_path: str) -> tuple | None:
    """(cfg, main_root) resolved once, for beats written on the way out."""
    try:
        cfg, main_root, _ = _load(config_path)
        return cfg, main_root
    except Exception:
        return None


def _beat_error(home: tuple | None, error: Exception) -> None:
    """Record a failed poll. A poll that failed is exactly the state the badge
    is for — the forge unreachable, the token expired — so it must survive
    whatever went wrong, including the config having become unreadable."""
    if home:
        _beat(*home, "error", note=str(error)[:200])


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


def _exit_on_sigterm() -> None:
    """Turn the FIRST SIGTERM into an ordinary exit, and ignore the rest.

    Without this, `just up` stopping this watcher kills it where it stands and
    the heartbeat row keeps whatever the last poll wrote — so a watcher stopped
    on purpose is indistinguishable from one that died, which is the single
    distinction the row exists to draw. 143 is the conventional code for it.

    Ignoring the second one is not defensive coding, it is the actual bug: up.py
    signals the whole process group, so `uv` gets SIGTERM alongside this process
    and forwards its own to us. The second delivery lands while the first is
    still unwinding, and raises SystemExit again from inside the `finally` that
    was writing the goodbye beat — which is exactly how that beat went missing.
    """
    def leave(*_) -> None:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        sys.exit(143)
    signal.signal(signal.SIGTERM, leave)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["once", "loop", "status"])
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--interval", type=int, default=120,
                        help="loop: seconds between polls")
    args = parser.parse_args()

    _exit_on_sigterm()
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
