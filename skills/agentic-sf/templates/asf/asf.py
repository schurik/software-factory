#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""asf — the one entry point. A workflow is a directory; this runs it.

Usage:
    uv run asf/asf.py list                       every workflow, one line each
    uv run asf/asf.py check [<workflow>]         load and validate, spawn nothing
    uv run asf/asf.py doctor                     is this repo ready to run? checks + fixes
    uv run asf/asf.py run <workflow> "<prompt or path/to/prompt.md>"
                        [--adw-id a1b2c3d4] [--resume] [--hitl all|none|every|plan]

    uv run asf/asf.py pending                    runs stopped at a gate, waiting for you
    uv run asf/asf.py show <adw_id>              what a waiting run wants you to read
    uv run asf/asf.py approve <adw_id> [-m "remarks"]
    uv run asf/asf.py reject  <adw_id>  -m "what to change"
    uv run asf/asf.py abort   <adw_id> [-m "why"]
    uv run asf/asf.py resume  <adw_id> [--dry-run]   pick a failed run back up

`--config asf/factory.yaml` is accepted before or after the subcommand. Run
from the repository root — every path in factory.yaml is relative to it.
`check` is what `run` does before it opens a session, so a workflow that
`check` accepts is one `run` will start; a repository can put it in CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import factory, operate, utils, workflow  # noqa: E402  (path set above)

DEFAULT_CONFIG = factory.DEFAULT_CONFIG


def cmd_list(args) -> int:
    rows = workflow.available(args.config)
    if not rows:
        print(f"no workflows under {workflow.workflows_dir(args.config)}")
        return 1
    width = max(len(name) for name, _ in rows)
    for name, description in rows:
        print(f"  {name.ljust(width)}   {description}")
    return 0


def cmd_check(args) -> int:
    names = [args.workflow] if args.workflow else [n for n, _ in workflow.available(args.config)]
    if not names:
        print(f"no workflows under {workflow.workflows_dir(args.config)}")
        return 1
    failed = False
    for name in names:
        try:
            loaded = workflow.load(name, args.config)
        except SystemExit as error:
            print(f"✗ {name}\n  {error}")
            failed = True
            continue
        chain = " -> ".join(step.stage.name for step in loaded.steps)
        print(f"✓ {name}: {chain}   agents: {', '.join(loaded.required_agents)}")
    return 1 if failed else 0


def cmd_doctor(args) -> int:
    code = operate.doctor(factory.load(args.config))
    print()
    args.workflow = None
    return max(code, cmd_check(args))


def cmd_run(args) -> int:
    loaded = workflow.load(args.workflow, args.config)
    return workflow.run(loaded, utils.resolve_prompt(args.prompt), args.adw_id,
                        args.resume, args.hitl)


def cmd_pending(args) -> int:
    return operate.pending(factory.load(args.config))


def cmd_show(args) -> int:
    return operate.show(factory.load(args.config), args.adw_id)


def cmd_decide(args) -> int:
    return operate.decide(factory.load(args.config), args.config, args.command, args.adw_id,
                          args.notes, args.no_resume)


def cmd_resume(args) -> int:
    return operate.relaunch(factory.load(args.config), args.config, args.adw_id, args.dry_run)


def _config_on(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """`--config` after the subcommand too. SUPPRESS, never a default, on the
    subcommand's copy: argparse copies a subcommand's namespace over the outer
    one, and a copy carrying a default would silently overwrite a `--config`
    typed before the subcommand."""
    parser.add_argument("--config", default=argparse.SUPPRESS)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asf", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    _config_on(sub.add_parser("list", help="every workflow, one line each")
               ).set_defaults(func=cmd_list)
    check = _config_on(sub.add_parser("check", help="load and validate, spawn nothing"))
    check.add_argument("workflow", nargs="?", help="one workflow; default: all")
    check.set_defaults(func=cmd_check)
    _config_on(sub.add_parser("doctor", help="is this repo ready to run? checks + fixes")
               ).set_defaults(func=cmd_doctor)

    run = _config_on(sub.add_parser("run", help="run one workflow against a prompt"))
    run.add_argument("workflow")
    run.add_argument("prompt", help="inline text or a path to a prompt file")
    run.add_argument("--adw-id", default=None, help="join or pin an existing session")
    run.add_argument("--resume", action="store_true",
                     help="replay this session's recorded agent phases instead of "
                          "paying for them again; needs --adw-id")
    run.add_argument("--hitl", default="",
                     help="which gates stop for you: all | none | every | gate,names "
                          "— over the workflow's and factory.yaml's say")
    run.set_defaults(func=cmd_run)

    _config_on(sub.add_parser("pending", help="runs stopped at a gate")
               ).set_defaults(func=cmd_pending)
    show = _config_on(sub.add_parser("show", help="what a waiting run wants you to read"))
    show.add_argument("adw_id")
    show.set_defaults(func=cmd_show)
    for verdict in ("approve", "reject", "abort"):
        one = _config_on(sub.add_parser(verdict, help=f"{verdict} the gate a run waits at"))
        one.add_argument("adw_id")
        one.add_argument("-m", "--notes", default="",
                         help="your words for the agent (required on reject)")
        one.add_argument("--no-resume", action="store_true",
                         help="record the decision and stop; do not relaunch the run")
        one.set_defaults(func=cmd_decide)
    resume = _config_on(sub.add_parser("resume", help="pick a failed run back up"))
    resume.add_argument("adw_id")
    resume.add_argument("--dry-run", action="store_true", help="print the command only")
    resume.set_defaults(func=cmd_resume)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
