"""Session lifecycle: pin-or-create an adw_id, build the run's workspace and Run.

`ensure(cfg, adw_id)` joins the session if it exists or creates it under
exactly that id (pinned ids for repeatable runs); omitted, a fresh id is
minted and printed so the next ADW can pick it up.

This is also where a run stops being able to hurt the engineer's checkout. The
worktree is created here, before anything else exists, because everything
downstream derives its tree from `run.repo_root`: agents are spawned in it, the
permission snapshot fingerprints it before the first call, gates measure it, and
commit phases commit it. Create it later and each of those would need its own
opt-in.

The order below matters. `main_root` is resolved first — from the git common
dir, so it is the engineer's checkout even when an ADW is launched from inside
a worktree — and the trace db and session dir are anchored to it. One db for
every concurrent run, and a record that outlives the worktree it describes.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from . import branches, git_helper, worktree
from .data_types import BranchRequest, IssueRef, RunSpec, SSSFConfig, WorktreeRequest
from .runner import Run
from .tracer import Tracer
from .utils import anchor, engineer_name, new_id


def _finalize_when_killed(run: Run) -> None:
    """A killed run still closes its own trace, and still keeps its worktree.

    Python's default SIGTERM handling exits without unwinding, so `just kill`
    (or any `kill <pid>`) would leave the session reading `running` forever and
    its process rows open — the trace would claim work is in flight that is
    already dead. Turning the signal into SystemExit both finalizes here and
    lets the phase context manager record the phase as failed on the way out.

    Nothing here touches the worktree. A killed run is exactly the one whose
    tree you want to open afterwards, and `run.finish()` — the only place that
    releases one — is never reached on this path.
    """
    def handler(signum, _frame):
        run.tracer.session_finish(run.adw_id, ok=False)   # also closes process rows
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


def ensure(cfg: SSSFConfig, adw_id: str | None = None, *, prompt: str = "",
           issue: IssueRef | None = None) -> Run:
    adw_id = adw_id or new_id(8)
    main_root = git_helper.main_root()          # the engineer's checkout, always
    # WHAT THE BRANCH IS CALLED is decided before the worktree exists, because
    # the worktree is cut from it. An issue run's branch is created AT THE FORGE
    # here — the only order in which it can be linked to the issue at all — and
    # everything that can fail about that degrades to a local branch and a note.
    plan = branches.plan(cfg, BranchRequest(main_root=main_root, adw_id=adw_id,
                                            prompt=prompt, issue=issue))
    workspace = worktree.ensure(WorktreeRequest(main_root=main_root, adw_id=adw_id,
                                                config=cfg.worktree,
                                                branch=plan.branch,
                                                base_commit=plan.base_commit))
    tracer = Tracer(anchor(main_root, cfg.observability.db),
                    anchor(main_root, f"{cfg.defaults.data_dir}/sessions/{adw_id}/events.jsonl"))
    run = Run(RunSpec(cfg=cfg, adw_id=adw_id, engineer=engineer_name(),
                      workspace=workspace), tracer)
    tracer.session_start(adw_id, run.engineer, adw_name=Path(sys.argv[0]).stem)
    # Read AFTER session_start, so a brand-new session has its row to read from
    # and a joined one has the first process's answer rather than this one's
    # default. An issue-triggered session that a later ADW re-enters must still
    # know it was issue-triggered — integration.py refuses to merge on that —
    # and a session whose branch is already a pull request must know THAT, or it
    # proposes the branch a second time instead of pushing to the PR it has.
    run.adopt_provenance(*tracer.session_provenance(adw_id))
    tracer.session_workspace(adw_id, workspace)
    # This process is the run. Record it before any phase opens, so a run that
    # hangs in its first agent call is still killable by adw_id.
    tracer.process_start(adw_id, "adw", "", os.getpid(),
                         " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]))
    _finalize_when_killed(run)
    run.console.session_started(adw_id, run.engineer)
    run.console.note(_workspace_line(workspace, plan))
    return run


def _workspace_line(workspace, plan) -> str:
    """The one line that says where this run's work will actually land.

    The plan's notes ride along on it rather than getting a line of their own:
    "linked to issue #42" and "not linked, because…" are both facts about this
    branch, and the branch is what this line is for.
    """
    if not workspace.enabled:
        return f"workspace: {workspace.repo_root} (no worktree — running in place)"
    verb = "joined" if workspace.joined else "created"
    line = (f"workspace: {verb} {workspace.repo_root} on {workspace.branch} "
            f"from {workspace.base_ref} @ {workspace.base_commit[:7]}")
    return f"{line} · {' · '.join(plan.notes)}" if plan.notes else line
