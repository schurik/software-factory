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

THE PROCESS THAT ENDS A RUN OWNS ITS LABEL. A run that stopped for a human was
started by the watcher but FINISHES here, once `just approve` brings it back —
so an issue-triggered run lands its `done` or `failed` from this file, and the
watcher's flip covers only the runs that never suspended. See `_land_label`.

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


def _land_label(cfg, main_root, state, code: int) -> None:
    """Move an issue-triggered run's label now that this process has ended it.

    THE LABEL BELONGS TO WHOEVER ENDS THE RUN, and until now that was assumed to
    be the watcher. It stopped being true the moment a run could suspend: the
    watcher launched it, saw exit 75, and correctly left the issue on `running`
    — but `just approve` brings the run back HERE, in a process the watcher
    never sees, and this is where it actually finishes. Nothing moved the label
    afterwards, so an answered issue sat on `running` forever, which is a
    quieter lie than the `failed` it replaced but a lie all the same.

    This is also the reason it is in `relaunch` rather than in `hitl.py`: the
    three verdicts and a bare `just resume` all funnel through here, so this is
    the one place that sees the end of every re-entered run. A `just resume` on
    a run that failed and now succeeds moves it off `failed` for the same
    reason.

    Only `running` is removed, never a terminal label — that keeps this to the
    one removal that cannot fail, since `gh issue edit --remove-label` errors on
    a name the repository never defined, and the claim in `issue_watch.py`
    already cleared every other state before the run began.

    NEVER RAISES and never changes the exit code. The run's outcome is a fact
    about the work; a tracker that could not be reached is a fact about the
    network, and the second must not be reported as the first.
    """
    from adw_modules.data_types import IssueUpdate
    from adw_modules.hitl import EXIT_WAITING
    from adw_modules.issues import set_state
    if state.trigger != "issue" or not state.issue_number:
        return
    if not cfg.issues.enabled:
        # The label state machine is the watcher's, and this repo has not turned
        # it on — so `just issue 42` by hand is a run against a tracker that
        # keeps no sssf state, and inventing some for it would be this script
        # deciding a workflow the config declined.
        return
    if code == EXIT_WAITING:
        return              # it stopped at the NEXT gate; still running, still claimed
    landed = cfg.issues.states.done if code == 0 else cfg.issues.states.failed
    try:
        result = set_state(main_root, cfg.issues, IssueUpdate(
            number=state.issue_number, project=state.issue_project,
            add_labels=[landed], remove_labels=[cfg.issues.states.running]))
    except Exception as error:                      # noqa: BLE001 — see the docstring
        print(f"  #{state.issue_number}: label not moved to {landed} ({error}); "
              f"move it by hand")
        return
    if result.ok:
        print(f"  #{state.issue_number}: {landed}")
    else:
        print(f"  #{state.issue_number}: label not moved to {landed} — "
              f"{' · '.join(result.notes)}; move it by hand")


def relaunch(adw_id: str, config: str, dry_run: bool = False,
             passthrough: tuple[str, ...] = ()) -> int:
    """Re-launch the workflow that recorded `adw_id`, with `--resume`.

    Everything `main()` does after parsing, so `hitl.py` can answer a gate and
    bring the run back in one step. Returns the exit code to hand on — the
    relaunched run's own, or 1 with a printed reason when nothing was launched.

    This is also where an issue-triggered run lands its label, because this is
    where such a run ENDS once it has stopped for a human at least once. See
    `_land_label`.
    """
    from adw_modules import agents, artifacts, git_helper, hitl
    from adw_modules.utils import anchor

    cfg = agents.load_config(config)
    main_root = git_helper.main_root()
    sessions = anchor(main_root, f"{cfg.defaults.data_dir}/sessions")
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
    code = subprocess.run(argv).returncode
    # RE-READ, because `state` above is a snapshot from before the run started
    # and the process that just ended is the authority on what this session
    # knows now. That is not a nicety: a session recorded before `run.json`
    # carried `issue_number` has it as 0 here, and the resumed chain BACKFILLS
    # it — the issue phase is `kind="code"`, so `replay.py` re-runs it for real
    # and `Run.record_issue` writes the number and project on the way past.
    # Landing the label off the stale snapshot would decline every session that
    # predates the field, which is precisely the set of runs already suspended
    # at a gate when this shipped.
    _land_label(cfg, main_root, artifacts.read_run(session_dir) or state, code)
    return code


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
