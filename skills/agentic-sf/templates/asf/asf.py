#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""asf — the one entry point. A workflow is a directory; this runs it.

Usage:
    uv run asf/asf.py list                       every workflow, one line each
    uv run asf/asf.py check [<workflow>]         load and validate, spawn nothing
    uv run asf/asf.py run <workflow> "<prompt or path/to/prompt.md>"
                        [--config asf/factory.yaml] [--adw-id a1b2c3d4] [--resume]
                        [--hitl all|none|every|plan]

Run from the repository root — every path in factory.yaml is relative to it.
`check` is what `run` does before it opens a session, so a workflow that
`check` accepts is one `run` will start; a repository can put it in CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import factory, utils, workflow  # noqa: E402  (path set above)

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


def cmd_run(args) -> int:
    loaded = workflow.load(args.workflow, args.config)
    return workflow.run(loaded, utils.resolve_prompt(args.prompt), args.adw_id,
                        args.resume, args.hitl)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asf", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="every workflow, one line each").set_defaults(func=cmd_list)

    check = sub.add_parser("check", help="load and validate, spawn nothing")
    check.add_argument("workflow", nargs="?", help="one workflow; default: all")
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="run one workflow against a prompt")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
