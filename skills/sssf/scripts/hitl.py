#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/pending /show /approve /reject /abort — answer a run that stopped for you.

Usage:
    uv run <skill>/scripts/hitl.py pending [--config ...]
    uv run <skill>/scripts/hitl.py show <adw_id>
    uv run <skill>/scripts/hitl.py approve <adw_id> [-m "remarks"] [--no-resume]
    uv run <skill>/scripts/hitl.py reject  <adw_id>  -m "what to change" [--no-resume]
    uv run <skill>/scripts/hitl.py abort   <adw_id> [-m "why"] [--no-resume]

A gate that fires (`hitl:` in the config, or `--hitl` on the chain) ends the
run's process with exit 75 and leaves the session reading `waiting`, with
`run.json` saying which gate, which round, and a digest of exactly what it
showed. `pending` lists those. `show` prints the artifact. The three verdicts
write ONE file — `sessions/<adw_id>/decisions/<gate>_<round>.json`, carrying
that digest — and then re-launch the same workflow with `--resume`, which
replays every recorded agent phase for free and reaches the gate with its
answer in hand. A reject goes back to the agent that produced the artifact,
in the same coding-agent session, and the run stops at the gate again.

A run that is still ATTENDED — asking at its own terminal — records the same
`waiting_for` while it polls, and a verdict written here reaches it without
any relaunch; `--no-resume` is implied when the run is alive.

Deliberately not an ADW, like `resume.py`: choosing a verdict takes no agent,
and giving it a session would create the very thing it answers. Thin for the
reason ADWs are (SKILL.md rule 6): the decision record and its rules live in
`adws/adw_modules/hitl.py`, imported from the repo this is run in.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # resume.py, beside this file
sys.path.insert(0, str(Path.cwd() / "adws"))               # the stamped factory in this repo

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
SHOW_LINES = 400        # a plan is a page or two; a diff can be anything


def _sessions(config: str):
    from adw_modules import agents, artifacts, git_helper
    cfg = agents.load_config(config)
    return artifacts.sessions_root(git_helper.main_root(), cfg.defaults.data_dir)


def pending(config: str) -> int:
    from adw_modules import artifacts
    waiting = artifacts.waiting_sessions(_sessions(config))
    if not waiting:
        print("no run is waiting at a gate")
        return 0
    width = max(len(adw_id) for adw_id in waiting)
    for adw_id, what in sorted(waiting.items(), key=lambda item: item[1].since):
        print(f"{adw_id:<{width}}  {what.gate} · round {what.round}  since {what.since}")
        if what.summary:
            print(f"{'':<{width}}  {what.summary}")
        print(f"{'':<{width}}  just show {adw_id} · just approve {adw_id} · "
              f"just reject {adw_id} -m \"...\" · just abort {adw_id}")
    return 0


def show(adw_id: str, config: str) -> int:
    from adw_modules import artifacts
    state = artifacts.read_run(_sessions(config) / adw_id)
    if state is None:
        print(f"{adw_id}: no such session — `just sessions`")
        return 1
    if state.waiting_for is None:
        print(f"{adw_id}: not waiting at a gate (status {state.status})")
        return 1
    what = state.waiting_for
    print(f"{adw_id} · gate {what.gate} · round {what.round} · since {what.since}")
    print(f"  subject: {what.summary}")
    if what.notes:
        print(f"  the agent's notes: {what.notes}")
    print(f"  tree:    {state.repo_root} ({state.branch})")
    for path in what.paths:
        print(f"\n─── {path} ───")
        try:
            lines = Path(path).read_text().splitlines()
        except OSError as error:
            print(f"  (unreadable: {error})")
            continue
        print("\n".join(lines[:SHOW_LINES]))
        if len(lines) > SHOW_LINES:
            print(f"… {len(lines) - SHOW_LINES} more line(s) — open the file for the rest")
    print(f"\nanswer: just approve {adw_id} [-m remarks] · just reject {adw_id} -m \"...\" · "
          f"just abort {adw_id}")
    return 0


def decide(verdict: str, adw_id: str, notes: str, config: str, no_resume: bool) -> int:
    from adw_modules import artifacts, hitl
    from adw_modules.utils import engineer_name
    session_dir = _sessions(config) / adw_id
    try:
        decision = hitl.answer(session_dir, verdict, notes, by=engineer_name())
    except RuntimeError as error:
        print(f"{adw_id}: {error}")
        return 1
    print(f"{adw_id}: {decision.verdict} recorded at "
          f"{hitl.decision_path(session_dir, decision.gate, decision.round)}")

    state = artifacts.read_run(session_dir)
    if state is not None and state.status == "running":
        print("  the run is attended and polling — it picks this up on its own")
        return 0
    if no_resume:
        print(f"  not relaunched (--no-resume): `just resume {adw_id}` when ready")
        return 0
    from resume import relaunch
    code = relaunch(adw_id, config)
    after = artifacts.read_run(session_dir)
    if code == hitl.EXIT_WAITING and after is not None and after.waiting_for is not None:
        what = after.waiting_for
        print(f"{adw_id}: waiting again — gate {what.gate}, round {what.round}. "
              f"`just show {adw_id}`")
    return code


def _config_on(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Let `--config` be written AFTER the subcommand as well as before it.

    This is the only script here with subcommands, and argparse binds a
    parent-level option on the parent side of one only: `hitl.py --config x
    approve <id>` parses, and `hitl.py approve <id> --config x` exits 2 with
    `unrecognized arguments`. The justfile writes the second order on all five
    recipes — which is also the order a person types — so every one of them
    failed, and failed QUIETLY: argparse writes to stderr, `just approve`
    printed nothing an operator would notice, and the run stayed waiting. A
    gate that cannot be answered through the documented interface is a gate
    that cannot be answered.

    `SUPPRESS`, never a default, on the subcommand's copy. argparse parses a
    subcommand into a fresh namespace and copies every name in it back over the
    outer one, so a copy carrying a default would overwrite the `--config` an
    operator typed BEFORE the subcommand with that default — silently, which is
    worse than the exit 2 it replaces. Suppressed, the name is absent unless it
    was actually typed, and the outer value survives. `parents=[...]` cannot do
    this: the shared Action is one object, and both orders lose the outer value.
    """
    parser.add_argument("--config", default=argparse.SUPPRESS,
                        help="the config this repo runs on (also accepted before "
                             "the subcommand)")
    return parser


def _parser() -> argparse.ArgumentParser:
    """The CLI, built where a test can reach it — `templates/justfile` and this
    are a contract, and nothing else checks that the lines it writes parse."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    _config_on(sub.add_parser("pending", help="runs stopped at a gate"))
    _config_on(sub.add_parser("show", help="what a waiting run wants you to read")
               ).add_argument("adw_id")
    for verdict in ("approve", "reject", "abort"):
        one = _config_on(sub.add_parser(verdict))
        one.add_argument("adw_id")
        one.add_argument("-m", "--notes", default="",
                         help="your words for the agent (required on reject)")
        one.add_argument("--no-resume", action="store_true",
                         help="record the decision and stop; do not relaunch the run")
    return parser


def main() -> int:
    args = _parser().parse_args()

    if args.command == "pending":
        return pending(args.config)
    if args.command == "show":
        return show(args.adw_id, args.config)
    return decide(args.command, args.adw_id, args.notes, args.config, args.no_resume)


if __name__ == "__main__":
    sys.exit(main())
