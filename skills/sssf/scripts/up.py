#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/up — start the whole factory: the trace UI and both watchers, in one process.

Usage:
    uv run <skill>/scripts/up.py [--only issues,prs,obs] [--interval 120]
    uv run <skill>/scripts/up.py status

Run from a repo root. Ctrl-C stops everything it started.

WHY THIS EXISTS. The factory's three long-running parts are the trace UI, the
issue watcher and the review watcher, and until this file they were three
commands in three terminals. Two things went wrong with that, every week:

  * You forget one. Labelling an issue and waiting for a run that nothing was
    polling for is the single most expensive mistake this system invites,
    because it is silent — a watcher that is not running and a watcher with
    nothing to do print exactly the same amount of nothing.
  * `just obs` backgrounded its API server in a subshell, so Ctrl-C killed the
    vite process and left the API holding :4600. The next `just obs` then died
    on EADDRINUSE with no hint about who was squatting.

Both are supervision problems, so this is a supervisor: one foreground process
that owns every child, prefixes their output, restarts what dies, and takes the
whole tree down with it. Deliberately OUTSIDE the factory, like `issue_watch.py`
and `worktrees.py` — a process manager sits above the control plane, not inside
it, and nothing here knows what a phase is.

IT IS NOT A DAEMON. There is no pidfile, no detach, no `up.py stop`. The
terminal it runs in is the handle, which is the same bargain `bun run` and
`vite` already make, and it means a stopped supervisor cannot be a mystery.
For an unattended deployment, the cron form is still the one in the justfile
(`just issues-watch`, `just prs-watch`) — that is what those recipes are for.

WHAT `status` ANSWERS. The other half of the same problem: is anything running
right now, and did it poll recently. It reads the watcher heartbeats out of the
watcher heartbeat files (`adw_data/watchers/<kind>.json`, written by both
watchers on every poll) and
probes each recorded pid, so a watcher killed with SIGKILL reads as gone rather
than as whatever its last row happened to say.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path.cwd() / "adws"))      # the stamped factory in this repo

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
API_PORT = int(os.environ.get("PORT", "4600"))
UI_PORT = 4601

# Prefix colors, one per service, so four interleaved streams stay readable.
# Plain ANSI rather than rich: this process only ever prints prefixed lines, and
# a Console here would fight the children for the same terminal.
COLORS = {"obs": "\033[36m", "ui": "\033[35m", "issues": "\033[33m", "prs": "\033[32m"}
DIM, RESET = "\033[2m", "\033[0m"


def paint(color: str, text: str) -> str:
    return f"{color}{text}{RESET}" if sys.stdout.isatty() else text


# ── services ─────────────────────────────────────────────────────────────────

@dataclass
class Service:
    """One supervised child process, and what to do when it dies."""
    name: str
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    proc: subprocess.Popen | None = None
    restarts: int = 0
    started_at: float = 0.0
    give_up: bool = False


def _spawn(service: Service, on_line) -> None:
    """Start one child in its OWN process group, with its output piped here.

    `start_new_session` is the load-bearing part. `bun run vite` and `uv run`
    both spawn children of their own, and killing only the pid we hold leaves
    those orphaned — which is exactly the :4600 squatter this file exists to
    stop creating. A process group can be signalled whole.
    """
    env = {**os.environ, **service.env}
    service.proc = subprocess.Popen(
        service.argv, cwd=str(service.cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, start_new_session=True)
    service.started_at = time.monotonic()
    threading.Thread(target=_pump, args=(service, on_line), daemon=True).start()


def _pump(service: Service, on_line) -> None:
    """Forward a child's output, one prefixed line at a time."""
    assert service.proc and service.proc.stdout
    for line in service.proc.stdout:
        on_line(service.name, line.rstrip("\n"))


def _stop(service: Service, grace: float = 8.0) -> None:
    """SIGTERM the child's whole group, then SIGKILL whatever is left.

    SIGTERM first because the watchers turn it into a clean exit: a stopped
    watcher writes a `stopped` heartbeat, so `up.py status` afterwards says it
    was stopped rather than leaving the last poll's row to imply it is still up.
    """
    proc = service.proc
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# ── preflight ────────────────────────────────────────────────────────────────

def _port_free(port: int) -> bool:
    """Whether :port can be bound. One implementation, in the stamped checks, so
    `just up` and `just doctor` cannot disagree about who is holding it."""
    from adw_modules import preflight
    return preflight.port_free(port)


def _harness_names(cfg) -> list[str]:
    return sorted({agent.harness for agent in cfg.agents})


def preflight(cfg, want: set[str]) -> list[str]:
    """Everything that would stop THESE SERVICES, checked now. Fatal problems.

    Non-fatal findings are printed and the service they concern is dropped —
    no bun is a reason to run without the UI, not a reason to refuse to watch
    anything. A fatal problem is one that makes the whole command pointless.

    Deliberately narrower than `adws/adw_modules/preflight.py`, which asks
    whether a RUN would work (credentials, base_ref, data_dir, the quality
    blocks) and is what `just doctor` prints. This one asks only about the
    three processes it is about to supervise.
    """
    fatal: list[str] = []

    if "obs" in want:
        if not shutil.which("bun"):
            print(paint(DIM, "  ~ bun is not on PATH — starting without the trace UI "
                             "(https://bun.sh)"))
            want.discard("obs")
        elif not _port_free(API_PORT):
            print(paint(DIM, f"  ~ something already listens on :{API_PORT} — starting "
                             f"without the trace UI. Another `just up`, or an API server "
                             f"orphaned by an older `just obs`: "
                             f"`lsof -ti :{API_PORT} | xargs kill`"))
            want.discard("obs")

    # The forge CLI is what both watchers shell out to. Missing, they would poll
    # forever and report an unreadable list on every pass — which is precisely
    # the silent nothing this command exists to remove.
    forge = (cfg.issues.list_command or ["gh"])[0]
    if want & {"issues", "prs"} and not shutil.which(forge):
        print(paint(DIM, f"  ~ {forge!r} is not on PATH — the watchers can start, but "
                         f"every poll will fail to list anything"))

    # A missing agent CLI does not break the watchers, it breaks the runs they
    # launch — an hour later, mid-chain. Worth one line now.
    try:
        from adw_modules import harnesses
        for name in _harness_names(cfg):
            try:
                harnesses.get(name).reachable()
            except (RuntimeError, SystemExit) as error:
                print(paint(DIM, f"  ~ {error}"))
    except ImportError:
        pass

    if not want:
        fatal.append("nothing to start — see the messages above")
    return fatal


def _wanted(cfg, only: str) -> set[str]:
    """Which services this invocation should run, and why the others are out."""
    if only:
        chosen = {part.strip() for part in only.split(",") if part.strip()}
        unknown = chosen - {"obs", "issues", "prs"}
        if unknown:
            sys.exit(f"--only: unknown service(s) {', '.join(sorted(unknown))} "
                     f"— pick from obs, issues, prs")
        return chosen
    want = {"obs", "issues", "prs"}
    # A disabled watcher is not started rather than started and left idling:
    # an `issues` prefix that never says anything is the ambiguity again.
    if not cfg.issues.enabled:
        print(paint(DIM, "  ~ issues.enabled is false — not starting the issue watcher"))
        want.discard("issues")
    if not cfg.pull_requests.enabled:
        print(paint(DIM, "  ~ pull_requests.enabled is false — not starting the "
                         "review watcher"))
        want.discard("prs")
    return want


# ── start ────────────────────────────────────────────────────────────────────

def _services(want: set[str], config_path: str, interval: int,
              main_root: Path, db: Path) -> list[Service]:
    visualizer = SKILL_ROOT / "apps" / "visualizer"
    services: list[Service] = []
    if "obs" in want:
        services.append(Service("obs", ["bun", "run", "server/index.ts"], visualizer,
                                {"SSSF_DB": str(db), "PORT": str(API_PORT)}))
        services.append(Service("ui", ["bunx", "vite"], visualizer,
                                {"PORT": str(API_PORT)}))
    # PYTHONUNBUFFERED, because their stdout is a pipe here rather than a
    # terminal: block buffering would hold a watcher's lines back until 4KB of
    # them existed, which for a poll every two minutes is most of an hour.
    for name, script in (("issues", "issue_watch.py"), ("prs", "pr_watch.py")):
        if name in want:
            services.append(Service(name,
                                    ["uv", "run", str(SKILL_ROOT / "scripts" / script),
                                     "loop", "--config", config_path,
                                     "--interval", str(interval)],
                                    main_root, {"PYTHONUNBUFFERED": "1"}))
    return services


def start(config_path: str, interval: int, only: str) -> int:
    cfg, main_root, db = _load(config_path)
    print(f"sssf up — {main_root}")
    want = _wanted(cfg, only)
    problems = preflight(cfg, want)
    if problems:
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        return 2

    if "obs" in want:
        # The api refuses to start without a db, by design — it would otherwise
        # create an empty one next to itself and show an empty factory. On a
        # repo that has never run anything there is none yet, and a supervisor
        # whose answer to that is three restarts and a shrug is worse than one
        # that just makes the file. The schema is the tracer's own.
        if not db.exists():
            from adw_modules.tracer import ensure_db
            ensure_db(db).close()
            print(f"  {paint(DIM, 'no trace db yet — created an empty one')}")
        # Once, and only when it is actually missing: `bun install` on every
        # start adds seconds to a command meant to be reflexive.
        if not (SKILL_ROOT / "apps" / "visualizer" / "node_modules").is_dir():
            print("  installing the visualizer's dependencies (first run only)…")
            subprocess.run(["bun", "install"], cwd=SKILL_ROOT / "apps" / "visualizer",
                           check=False)

    width = max(len(name) for name in COLORS)
    lock = threading.Lock()

    def on_line(name: str, line: str) -> None:
        with lock:                      # four threads, one terminal
            print(f"{paint(COLORS.get(name, ''), name.rjust(width))} "
                  f"{paint(DIM, '│')} {line}", flush=True)

    services = _services(want, config_path, interval, main_root, db)
    for service in services:
        _spawn(service, on_line)

    print()
    if "obs" in want:
        print(f"  trace UI   http://localhost:{UI_PORT}   (api on :{API_PORT})")
    for name in ("issues", "prs"):
        if name in want:
            print(f"  {name:<9}  polling every {interval}s")
    print(f"\n{paint(DIM, '  ctrl-c stops all of it')}\n")

    stopping = threading.Event()

    def shutdown(signum, _frame) -> None:
        if stopping.is_set():
            return                      # a second ctrl-c while we are already stopping
        stopping.set()
        print(f"\n{paint(DIM, 'stopping…')}", flush=True)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        collapsed = _supervise(services, stopping, on_line)
    finally:
        for service in services:
            _stop(service)
        print(paint(DIM, "stopped"))
    # Exiting 0 after every service gave up would tell a wrapper — a shell loop,
    # a tmux respawn — that this ran fine.
    return 1 if collapsed else 0


def _supervise(services: list[Service], stopping: threading.Event, on_line) -> bool:
    """Restart what dies, until the engineer stops us or nothing is left alive.

    A crash loop is capped rather than run forever: three restarts inside a
    minute means the thing is broken in a way another restart will not fix, and
    a supervisor that hides that behind an endless respawn is worse than one
    that says so and leaves the rest running.

    True when everything gave up, which is a failed run rather than a finished
    one.
    """
    while not stopping.is_set():
        time.sleep(0.4)
        alive = False
        for service in services:
            if service.give_up:
                continue
            if service.proc and service.proc.poll() is None:
                alive = True
                continue
            code = service.proc.returncode if service.proc else -1
            ran_for = time.monotonic() - service.started_at
            if ran_for < 60:
                service.restarts += 1
            else:
                service.restarts = 0    # it stayed up; this is not a crash loop
            if service.restarts > 3:
                on_line(service.name, f"exited ({code}) and keeps exiting — giving up "
                                      f"on it; the rest keeps running")
                service.give_up = True
                continue
            on_line(service.name, f"exited ({code}) — restarting")
            time.sleep(min(2 ** service.restarts, 15))
            if stopping.is_set():
                return False
            _spawn(service, on_line)
            alive = True
        if not alive:
            on_line("up", "every service has given up — nothing left to supervise")
            return True
    return False


# ── status ───────────────────────────────────────────────────────────────────

def _age(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    seconds = int((datetime.now(timezone.utc) - then).total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def _pid_alive(pid: int) -> bool:
    """Whether that process still exists. Only meaningful on this machine."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                     # exists, owned by someone else


def status(config_path: str) -> int:
    """One screen: what is watching, what is running, what is left behind."""
    from adw_modules import artifacts
    cfg, main_root, db = _load(config_path)
    # Both from files, never from the db: `just status` has to answer on a
    # machine where the trace db was deleted, or where the events go to a
    # hosted API and there is no db at all. See adw_modules/artifacts.py.
    sessions = artifacts.sessions_root(main_root, cfg.defaults.data_dir)
    rows = artifacts.watcher_states(artifacts.watchers_dir(main_root, cfg.defaults.data_dir))

    print(f"repo:      {main_root}")
    print(f"db:        {db}{'' if db.exists() else '  (no runs yet)'}\n")

    print("watchers")
    for kind, enabled in (("issues", cfg.issues.enabled),
                          ("prs", cfg.pull_requests.enabled)):
        row = rows.get(kind)
        if not row:
            state = "never started here" if enabled else "off (enabled: false)"
            print(f"  {kind:<7} {state}"
                  + ("        — `just up`" if enabled else ""))
            continue
        live = _pid_alive(int(row.get("pid") or 0))
        if not live:
            # The row is what the watcher last wrote, not what is true now. A
            # dead pid outranks it, whatever it says — but WHICH thing it last
            # wrote is the difference between "you stopped it" and "it died",
            # so the two do not get the same sentence.
            age = _age(row.get("last_poll_at"))
            print(f"  {kind:<7} stopped {age}" if row.get("status") == "stopped"
                  else f"  {kind:<7} not running (last {row.get('status')}, {age})")
            continue
        note = row.get("note") or ""
        print(f"  {kind:<7} {row.get('status'):<8} pid {row.get('pid')}  "
              f"last poll {_age(row.get('last_poll_at'))}"
              f"{'  · ' + note if note else ''}")

    live_runs = {adw_id: pid for adw_id, pid in artifacts.running_pids(sessions).items()
                 if _pid_alive(pid)}
    print(f"\nruns in flight: {len(live_runs)}")
    for adw_id, pid in sorted(live_runs.items()):
        print(f"  {adw_id}  pid {pid}")

    try:
        from adw_modules import worktree
        trees = worktree.inventory(main_root, cfg.worktree, str(sessions))
        if trees:
            print(f"\nworktrees: {len(trees)}   (`just worktrees` for detail)")
    except Exception:
        pass
    return 0


# ── plumbing ─────────────────────────────────────────────────────────────────

def _load(config_path: str):
    """The config, the main checkout, and the trace db — or a usable refusal."""
    if not Path(config_path).is_file():
        sys.exit(f"no {config_path} here — run this from a repo the factory is "
                 f"installed into, or install it first (`/sssf install`)")
    from adw_modules import agents, git_helper
    from adw_modules.utils import anchor
    cfg = agents.load_config(config_path)
    main_root = git_helper.main_root()
    return cfg, main_root, anchor(main_root, cfg.observability.db)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", default="start", choices=["start", "status"])
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--interval", type=int, default=120,
                        help="seconds between polls, for both watchers")
    parser.add_argument("--only", default="",
                        help="comma-separated subset of obs,issues,prs")
    args = parser.parse_args()

    if args.action == "status":
        return status(args.config)
    return start(args.config, args.interval, args.only)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
