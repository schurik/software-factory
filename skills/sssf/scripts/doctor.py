#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/doctor — everything that would fail later, in one screen, before it costs anything.

Usage:
    uv run <skill>/scripts/doctor.py [--config adws/adw_sssf_config/sssf.config.yaml]

Run from the root of a repo the factory is installed into. Exits 0 when nothing
is fatal, 1 when something is — so it is usable as a first CI step, not only as
something to read.

WHY THIS EXISTS. Every run is cheap to start and expensive to lose. The failures
that hurt are not the interesting ones: a provider key that was never set, a
`base_ref` that does not resolve, a `data_dir` nothing can write, a quality block
still shipping the placeholder it was stamped with. Each of them is knowable in
milliseconds, and each of them used to surface partway into a chain, after an
agent had already been paid for.

The checks themselves are NOT here. They live in `adws/adw_modules/preflight.py`,
stamped into the repo and yours to edit, because what counts as "ready to run"
belongs to the repository. This file is the screen: it loads the config, asks
preflight everything, and prints the answers with their fixes.

`session.ensure()` asks the same module for the cheap subset before every single
run — see `preflight.before_run`. Doctor is the full sweep, including the checks
too slow or too situational to put in front of a scout run.

Like everything else in the factory, it never reads the trace db: the answers
come from the config, the filesystem, the environment and the harnesses, so
`just doctor` works on a repo that has never run anything — and on one whose db
was deleted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "adws"))      # the stamped factory in this repo

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
MARKS = {"ok": (GREEN, "✓"), "warn": (YELLOW, "~"), "fatal": (RED, "✗")}


def paint(color: str, text: str) -> str:
    return f"{color}{text}{RESET}" if sys.stdout.isatty() else text


def _load(config_path: str):
    """The config and the main checkout — or a usable refusal."""
    if not Path(config_path).is_file():
        sys.exit(f"no {config_path} here — run this from a repo the factory is "
                 f"installed into, or install it first (`/sssf install`)")
    from adw_modules import agents, git_helper
    return agents.load_config(config_path), git_helper.main_root()


def report(config_path: str) -> int:
    from adw_modules import preflight

    cfg, main_root = _load(config_path)
    print(f"sssf doctor — {main_root}\n")

    findings = preflight.everything(cfg, main_root)
    width = max((len(finding.check) for finding in findings), default=0)
    for finding in findings:
        color, mark = MARKS[finding.level]
        print(f"  {paint(color, mark)} {finding.check.ljust(width)}  {finding.detail}")
        # Only a problem earns a second line. A green check with advice attached
        # is how a report becomes something people stop reading.
        if finding.level != "ok" and finding.fix:
            print(f"    {paint(DIM, '→ ' + finding.fix)}")

    fatal = [finding for finding in findings if finding.level == "fatal"]
    warn = [finding for finding in findings if finding.level == "warn"]
    print()
    if fatal:
        print(paint(RED, f"  {len(fatal)} fatal, {len(warn)} warning(s) — "
                         f"a run would not get past this"))
        return 1
    if warn:
        print(paint(YELLOW, f"  {len(warn)} warning(s) — runs work, with the "
                            f"caveats above"))
        return 0
    print(paint(GREEN, "  all clear"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=CONFIG)
    return report(parser.parse_args().config)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
