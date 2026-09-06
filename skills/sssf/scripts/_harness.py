"""Which harnesses this skill can stamp, and how their config is assembled.

Shared by `install.py` and `make_config.py`, stdlib only — the skill's scripts
run under `uv run` with no dependencies, so nothing here may import yaml or the
factory's own modules.

A harness is discovered by the presence of `templates/harnesses/<name>/`, not by
a list in code. Adding Codex means adding that directory (and the matching
`adw_modules/harnesses/<name>.py` the stamped code dispatches on) — no edit
here, and no way for the installer's list to drift from what exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_ROOT / "templates"
HARNESS_TEMPLATES = TEMPLATES / "harnesses"

# The three files a generated sssf.config.yaml is concatenated from, in order:
# the harness's defaults, the harness-agnostic middle, the harness's roster.
CONFIG_PARTS = ("defaults.yaml", None, "agents.yaml")


def names() -> list[str]:
    """Every harness this skill ships templates for."""
    return sorted(d.name for d in HARNESS_TEMPLATES.iterdir()
                  if d.is_dir() and (d / "defaults.yaml").is_file())


def about(harness: str) -> tuple[str, str]:
    """`about.md` split into (one-line summary, the post-install steps).

    The harness ships its own description and its own next steps, so neither
    the installer nor the question it asks has a per-harness branch in it.
    """
    path = HARNESS_TEMPLATES / harness / "about.md"
    if not path.is_file():
        return harness, ""
    head, _, body = path.read_text().partition("\n")
    return head.strip(), body.strip()


def render_config(harness: str) -> str:
    """The full sssf.config.yaml text for this harness."""
    parts = []
    for name in CONFIG_PARTS:
        source = (TEMPLATES / "config" / "base.yaml") if name is None \
            else (HARNESS_TEMPLATES / harness / name)
        parts.append(source.read_text().rstrip() + "\n")
    return "\n".join(parts)


def choose(requested: str | None) -> str:
    """The harness to stamp: the flag, or the engineer's answer to the question.

    Which harness a repository runs on is a decision the repository owns — like
    its test commands and whether a machine may move its base branch — so it is
    asked, never defaulted. `--harness` answers it ahead of time for a scripted
    or agent-driven install; without a terminal to ask at, a missing flag is an
    error rather than a silent `pi`.
    """
    available = names()
    if requested:
        if requested not in available:
            sys.exit(f"unknown harness {requested!r} — this skill ships: "
                     f"{' | '.join(available)}")
        return requested
    if not sys.stdin.isatty():
        sys.exit("which harness? pass --harness <name> — this skill ships: "
                 f"{' | '.join(available)}\n"
                 "(nothing to ask on: stdin is not a terminal)")

    print("Which coding-agent harness should this repository's roster run on?\n")
    for index, name in enumerate(available, start=1):
        summary, _ = about(name)
        print(f"  {index}. {summary}")
    print()
    while True:
        try:
            answer = input(f"harness [{'/'.join(available)}]: ").strip()
        except EOFError:
            sys.exit("\nno answer — nothing was stamped")
        if answer in available:
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(available):
            return available[int(answer) - 1]
        print(f"  not one of {' | '.join(available)} — try again, or Ctrl-C to abort")
