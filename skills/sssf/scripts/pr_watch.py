#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/pr_watch — answer review feedback on the factory's open pull requests. Run from a repo root.

Usage:
    uv run <skill>/scripts/pr_watch.py once
    uv run <skill>/scripts/pr_watch.py loop [--interval 120] [--pr 17]
    uv run <skill>/scripts/pr_watch.py status

The sibling of `issue_watch.py` at the other end of a branch's life, and
deliberately OUTSIDE the factory for the same reason: a poller is a worker above
the control plane, not a phase inside it.

THE UNRESOLVED THREAD IS THE QUEUE. There is no label to claim and no state file.
A thread a reviewer opened is outstanding until something resolves it, and the
run that answers it resolves it — so the queue empties itself, in a place both
the reviewer and the watcher already read. That is the same trick `issue_watch`
plays with a label, minus the flip: the forge maintains this state for us.

Two things that state cannot do on its own, and this file exists for both:

  * A RUN THAT ENDS RED LEAVES ITS THREADS OPEN — which is exactly the condition
    that launched it. Nothing marks the difference between "not yet attempted"
    and "attempted and failed", so without a mark the next poll relaunches the
    same failing run, forever. `states.failed` is that mark, and a human
    removing it is the restart. It is the ONLY label this path uses.
  * A MERGED PULL REQUEST ENDS ITS SESSION, and nothing inside the factory can
    notice. `_reap()` does: it kills a review run still working a branch that
    has already landed, and releases the worktree that run was keeping alive.

Exclusion is a file lock per pull request, held across the run, as in
`issue_watch` — and with the same limit: it covers one watcher per repository on
one machine, which is the deployment this is built for. Two watchers on two
machines would both launch; run one.

Thin for the reason ADWs are (SKILL.md rule 6): every decision below lives in
`adws/adw_modules/pull_requests.py` and `adws/adw_modules/worktree.py`, which
this imports from the repo it is run in.
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
ADW = "adws/adw_pr_review.py"


def _load(config_path: str):
    from adw_modules import agents, git_helper, pull_requests
    cfg = agents.load_config(config_path)
    main_root = git_helper.main_root()
    project = pull_requests.resolve_project(cfg.pull_requests, main_root)
    return cfg, main_root, project


def _list_open(cfg, main_root, project: str) -> list[dict]:
    """Open pull requests on this factory's own branches. Never raises."""
    from adw_modules.utils import operator_env
    argv = [*cfg.pull_requests.list_command, "--state", "open",
            "--json", "number,headRefName,labels,isDraft", "--limit", "50"]
    if project:
        argv += ["--repo", project]
    completed = subprocess.run(argv, cwd=str(main_root), env=operator_env(),
                               capture_output=True, text=True)
    if completed.returncode != 0:
        print(f"  ! could not list pull requests: "
              f"{(completed.stderr or completed.stdout).strip()[-300:]}")
        return []
    try:
        entries = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        print("  ! the list command did not return JSON")
        return []
    prefix = cfg.worktree.branch_prefix
    return [entry for entry in entries
            if str(entry.get("headRefName", "")).startswith(prefix)]


def _labels_of(entry: dict) -> list[str]:
    return [item.get("name", "") if isinstance(item, dict) else str(item)
            for item in entry.get("labels") or []]


def _running_count(cfg, main_root) -> int:
    """How many runs are ACTUALLY in flight — the pid check, not the db's belief.

    Identical in spirit to `issue_watch._running_count`: a session row saying
    `running` survives a SIGKILL, and counting those would wedge the watcher at
    max_concurrent permanently while looking like a busy factory.
    """
    return len(_live(cfg, main_root))


def _live(cfg, main_root) -> dict:
    """{adw_id: pid} for sessions whose process still exists."""
    from adw_modules.tracer import running_adw_pids
    from adw_modules.utils import anchor
    alive = {}
    for adw_id, pid in running_adw_pids(anchor(main_root, cfg.observability.db)).items():
        try:
            os.kill(pid, 0)
            alive[adw_id] = pid
        except ProcessLookupError:
            continue                    # the row lies; the process is gone
        except PermissionError:
            alive[adw_id] = pid         # exists, owned by someone else
    return alive


@contextmanager
def _claim(cfg, main_root, project: str, number: int):
    """Hold an exclusive claim on one pull request, or yield False.

    `flock`, non-blocking, held for the whole run and released by the OS even if
    this process is killed — a lock file left behind is not a stuck lock.
    """
    from adw_modules.utils import anchor, ensure_dir
    lock_dir = ensure_dir(anchor(main_root, f"{cfg.defaults.data_dir}/pr-locks"))
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


def _mark(cfg, main_root, project: str, number: int, add: str = "", remove: str = "") -> None:
    """Add or remove the one label this path uses. Thin over pull_requests.set_state."""
    from adw_modules.data_types import PullRequestUpdate
    from adw_modules.pull_requests import set_state
    result = set_state(main_root, cfg.pull_requests, PullRequestUpdate(
        number=number, project=project,
        add_labels=[add] if add else [], remove_labels=[remove] if remove else []))
    if not result.ok:
        print(f"  #{number}: {' · '.join(result.notes)}")


def _launch(config_path: str, number: int, main_root) -> int:
    """Run one review ADW to completion and return its exit code.

    Serial, as in `issue_watch`: `max_concurrent` bounds how many runs exist at
    once, and waiting for the one just started is the honest way to hold that.
    """
    argv = ["uv", "run", ADW, str(number), "--config", config_path]
    print(f"  #{number}: {' '.join(argv)}")
    return subprocess.run(argv, cwd=str(main_root)).returncode


# ── merged pull requests end their sessions ──────────────────────────────────

def _reap(cfg, main_root, project: str) -> int:
    """Close out sessions whose pull request has been merged or closed.

    WHAT IS EXAMINED is the factory's own open state, never the repository's
    history: the worktrees still on disk, plus the sessions that believe they
    are running. Both shrink as this does its job, so the pass costs less the
    longer the factory has been tidy — where walking merged pull requests would
    cost more every week.

    For each such session whose pull request is no longer open, in this order:

      1. KILL A RUN THAT IS STILL WORKING IT. SIGTERM, which `session.py` turns
         into a clean finish: the session row closes, the process rows close,
         and the worktree is deliberately left standing. Letting it continue
         would be worse than pointless — `keep_published` would push onto a
         branch that has already landed, and may already be deleted.
      2. RELEASE THE WORKTREE, by the same conservative rule `just
         worktrees-prune` uses: only an ENDED session's tree, only when it is
         CLEAN. Uncommitted work stays put even here; a merged pull request is
         not a reason to throw away the only copy of something. The BRANCH is
         never touched, locally or on the remote — deleting it belongs to
         whoever merged.
      3. DROP THE LOOP-STOP LABEL, so a merged pull request does not keep a
         `sssf:pr-failed` badge forever, and remove the lock file.

    Idempotent by construction: a second pass finds no process, no worktree and
    no label, and does nothing. Never raises — a failed cleanup is a message,
    not a reason to skip the poll that follows it.
    """
    from adw_modules import pull_requests, worktree
    from adw_modules.data_types import PullRequestRef
    from adw_modules.tracer import session_pr_urls
    from adw_modules.utils import anchor

    db = str(anchor(main_root, cfg.observability.db))
    live = _live(cfg, main_root)
    trees = {info.adw_id for info in worktree.inventory(main_root, cfg.worktree, db)}
    candidates = sorted(trees | set(live))
    if not candidates:
        return 0

    urls = session_pr_urls(db)
    reaped = 0
    for adw_id in candidates:
        number = _pr_number(urls.get(adw_id, ""))
        if not number:
            continue                    # no pull request, nothing this pass decides
        try:
            context = pull_requests.describe(main_root, cfg.pull_requests,
                                             PullRequestRef(number=number,
                                                            project=project))
        except RuntimeError as error:
            print(f"  ~ {adw_id}: could not read #{number} ({error}) — left alone")
            continue
        if context.open:
            continue

        print(f"  ~ {adw_id}: #{number} is {context.state.lower()}")
        if adw_id in live:
            _terminate(adw_id, live[adw_id])
        _release(cfg, main_root, adw_id, db)
        _mark(cfg, main_root, project, number,
              remove=cfg.pull_requests.states.failed)
        _drop_lock(cfg, main_root, project, number)
        reaped += 1
    return reaped


def _pr_number(pr_url: str) -> int:
    """The number a pull request url ends in, or 0. No forge API involved."""
    tail = (pr_url or "").rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _terminate(adw_id: str, pid: int) -> None:
    """SIGTERM one run, and let its own handler close the trace it owns.

    Not SIGKILL: `session._finalize_when_killed` turns SIGTERM into a clean
    finish, so the session ends as `fail` with its process rows closed instead
    of reading `running` forever. A run that is already gone is not an error —
    it finished between the listing and here, which is the common case.
    """
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"    stopped the run still working it (pid {pid})")
    except ProcessLookupError:
        pass
    except PermissionError:
        print(f"    a run is working it (pid {pid}) but belongs to another user "
              f"— not stopping it")


def _release(cfg, main_root, adw_id: str, db: str) -> None:
    """Give up the worktree, if the same rule `just worktrees-prune` uses allows.

    Re-read rather than reused from the caller's inventory: the SIGTERM above
    just changed the session's status, and `reclaimable()` asks about exactly
    that. Deciding on the pre-kill snapshot would refuse every tree it just
    freed.
    """
    from adw_modules import worktree
    for info in worktree.inventory(main_root, cfg.worktree, db):
        if info.adw_id != adw_id:
            continue
        if not worktree.reclaimable(info):
            print(f"    kept {info.path} — "
                  f"{'uncommitted work' if info.dirty else f'run is {info.status}'}")
            return
        try:
            worktree.remove(main_root, info.path)
            print(f"    removed {info.path}; branch {info.branch} retained")
        except RuntimeError as error:
            print(f"    kept {info.path} — {error}")
        return


def _drop_lock(cfg, main_root, project: str, number: int) -> None:
    """Remove a finished pull request's lock file. Best effort, never fatal."""
    from adw_modules.utils import anchor
    slug = f"{project.replace('/', '-')}-{number}.lock" if project else f"{number}.lock"
    path = anchor(main_root, f"{cfg.defaults.data_dir}/pr-locks") / slug
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ── the poll ─────────────────────────────────────────────────────────────────

def once(config_path: str, only: int = 0) -> int:
    """One pass: reap what merged, then answer what is outstanding.

    Returns 0 normally; 3 means "the pull request this watcher was pinned to is
    finished", which is how `loop --pr` knows to stop.
    """
    cfg, main_root, project = _load(config_path)
    if not cfg.pull_requests.enabled:
        print("pull_requests.enabled is false — nothing to watch")
        return 0
    if not project:
        print("pull_requests.project is empty and no origin remote could be read.\n"
              "Set pull_requests.project in the config: a watcher that cannot name "
              "its project polls nothing, and polling nothing is indistinguishable "
              "from having nothing to do.", file=sys.stderr)
        return 2

    if cfg.pull_requests.reap_merged:
        reaped = _reap(cfg, main_root, project)
        if reaped:
            print(f"reaped {reaped} finished pull request(s)")

    entries = _list_open(cfg, main_root, project)
    if only:
        entries = [entry for entry in entries if entry.get("number") == only]
        if not entries:
            print(f"#{only} is no longer open — done watching it")
            return 3

    failed_label = cfg.pull_requests.states.failed
    print(f"{project}: {len(entries)} open pull request(s) on "
          f"{cfg.worktree.branch_prefix}*")
    launched = 0
    for entry in entries:
        number = entry.get("number")
        if entry.get("isDraft"):
            print(f"  #{number}: draft — not answering review feedback on it yet")
            continue
        if failed_label in _labels_of(entry):
            print(f"  #{number}: carries {failed_label} — a run already failed on "
                  f"it; remove the label to try again")
            continue
        if not _has_work(cfg, main_root, project, number):
            continue
        if _running_count(cfg, main_root) >= cfg.pull_requests.max_concurrent:
            print(f"  #{number}: max_concurrent ({cfg.pull_requests.max_concurrent}) "
                  f"reached — leaving it for the next poll")
            break

        with _claim(cfg, main_root, project, number) as mine:
            if not mine:
                continue
            code = _launch(config_path, number, main_root)
            launched += 1
            # The run's own report phase said WHAT happened in the threads; this
            # says whether it may be picked up again. Only the exit code knows,
            # and only this process ever sees it.
            if code != 0:
                _mark(cfg, main_root, project, number, add=failed_label)
    print(f"launched {launched} run(s)")
    return 0


def _has_work(cfg, main_root, project: str, number: int) -> bool:
    """Whether this pull request has threads worth spawning an agent for.

    The full read, not the listing: `gh pr list` cannot see review threads at
    all, so a watcher that trusted it would launch a run per open pull request
    per poll and pay an agent to discover there was nothing to do.
    """
    from adw_modules import pull_requests
    from adw_modules.data_types import PullRequestRef
    try:
        context = pull_requests.describe(main_root, cfg.pull_requests,
                                         PullRequestRef(number=number, project=project))
    except RuntimeError as error:
        print(f"  #{number}: could not read it ({error})")
        return False
    threads = pull_requests.actionable(cfg.pull_requests, context)
    if not threads:
        print(f"  #{number}: no open review threads")
        return False
    print(f"  #{number}: {len(threads)} open review thread(s)")
    return True


def loop(config_path: str, interval: int, only: int = 0) -> int:
    target = f" on #{only}" if only else ""
    print(f"polling every {interval}s{target} — ctrl-c to stop")
    while True:
        try:
            code = once(config_path, only)
            if code == 3:               # the pinned pull request is finished
                return 0
        except KeyboardInterrupt:
            raise
        except Exception as error:                  # an outage is not a crash
            print(f"! poll failed: {error}")
        time.sleep(interval)


def status(config_path: str) -> int:
    cfg, main_root, project = _load(config_path)
    pr = cfg.pull_requests
    print(f"enabled:        {pr.enabled}")
    print(f"project:        {project or '(unresolved — set pull_requests.project)'}"
          f"{'  (from origin)' if project and not pr.project else ''}")
    print(f"branches:       {cfg.worktree.branch_prefix}*")
    print(f"queue:          unresolved review threads (max {pr.max_threads} per run)")
    print(f"failed label:   {pr.states.failed}")
    print(f"max_concurrent: {pr.max_concurrent}"
          f"  (running now: {_running_count(cfg, main_root)})")
    print(f"writes back:    reply={pr.reply_to_threads} resolve={pr.resolve_threads}")
    print(f"reap_merged:    {pr.reap_merged}")
    print(f"trusted:        {', '.join(pr.trusted_reviewers) or '(anyone who can review)'}")
    print(f"ignored:        {', '.join(pr.ignore_authors) or '(no bots ignored)'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["once", "loop", "status"])
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--interval", type=int, default=120,
                        help="loop: seconds between polls")
    parser.add_argument("--pr", type=int, default=0,
                        help="watch one pull request; loop exits when it is merged or closed")
    args = parser.parse_args()

    if args.action == "once":
        # 3 means "the pinned pull request is finished" — a signal for `loop`,
        # not a failure for a single pass.
        code = once(args.config, args.pr)
        return 0 if code == 3 else code
    if args.action == "loop":
        return loop(args.config, args.interval, args.pr)
    return status(args.config)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(130)
