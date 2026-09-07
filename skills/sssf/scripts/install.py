#!/usr/bin/env -S uv run
# /// script
# dependencies = []
# ///
"""/install — stamp the SSSF factory from the skill into the cwd. Idempotent.

Usage:
    uv run <skill>/scripts/install.py [--harness pi|claude_code] [--force]

Asks which harness if the flag is not given (see `_harness.choose`), then
stamps THAT harness's world: adws/ (modules + starter ADWs), its prompt set
under adws/adw_data/prompt_engineering/, its harness_engineering/ assets, a
sssf.config.yaml assembled for it, its .env.sample, the justfile, and the
.gitignore entries (including the per-run worktree directory).
Existing files are skipped unless --force.
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness                                    # noqa: E402  (path set above)

SKILL_ROOT = _harness.SKILL_ROOT
TEMPLATES = _harness.TEMPLATES

GITIGNORE_ENTRIES = [
    "adws/adw_data/sessions/",
    "adws/adw_data/sssf.db*",
    # One file per issue the watcher has ever claimed. Runtime, and the flock
    # is what matters — the file itself is just something to hold it on.
    "adws/adw_data/issue-locks/",
    # Same story one step later, for pull requests the review watcher has
    # claimed. Removed when a pull request merges; the flock is the point.
    "adws/adw_data/pr-locks/",
    ".env",
    # One worktree per run, checked out inside the repo. Ignored rather than
    # committed for the obvious reason, and because chains that end in a commit
    # phase call `git add -A` — without this, a run's first commit would try to
    # add the tree it is running in.
    ".sssf-worktrees/",
    # The ADWs are Python, so importing adw_modules writes bytecode next to it.
    # Chains that end in a commit phase call `git add -A`, so without this a
    # stamped repo commits its own .pyc files — 15 of them showed up in the
    # first repo that was ever installed into from scratch.
    "__pycache__/",
    "*.pyc",
]


def stamp(src: Path, dest: Path, force: bool, stamped: list, skipped: list) -> None:
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.name == "__pycache__":
                continue
            stamp(child, dest / child.name, force, stamped, skipped)
        return
    if dest.exists() and not force:
        skipped.append(str(dest))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    stamped.append(str(dest))


def ensure_gitignore(root: Path, stamped: list) -> None:
    gitignore = root / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.exists() else []
    missing = [e for e in GITIGNORE_ENTRIES if e not in existing]
    if missing:
        with gitignore.open("a") as f:
            f.write("\n# sssf runtime\n" + "\n".join(missing) + "\n")
        stamped.append(f"{gitignore} (+{len(missing)} entries)")


def ensure_env(root: Path, sample: Path, stamped: list, notes: list) -> None:
    """Create `.env` from the sample and fill SSSF_SKILL in, in the one place
    that knows the answer.

    `SSSF_SKILL` is where the justfile finds the operational scripts and the
    trace UI, and every agent keeps its skills somewhere different, so nothing
    downstream can guess it. Printing it and trusting a copy-paste was the old
    design, and it produced the same failure every time a repo was cloned or a
    second machine picked the factory up: `just issues` exits with "not set",
    or worse, `.env` carries a path from another machine.

    Three cases, in this order, and none of them overwrite an answer:
      * no .env      — copy the sample, then fill the key in
      * key empty    — fill it in
      * key set      — left alone, and reported when it points somewhere else
    """
    env = root / ".env"
    if not env.exists() and sample.exists():
        env.write_text(sample.read_text())
        stamped.append(str(env))
    if not env.exists():
        return

    lines = env.read_text().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("SSSF_SKILL="):
            continue
        current = line.split("=", 1)[1].strip().strip("\"'")
        if not current:
            lines[index] = f"SSSF_SKILL={SKILL_ROOT}"
            env.write_text("\n".join(lines) + "\n")
            notes.append(f"SSSF_SKILL={SKILL_ROOT}  (written into .env)")
        elif Path(current).resolve() != SKILL_ROOT:
            notes.append(f"SSSF_SKILL in .env is {current}, but this install ran "
                         f"from {SKILL_ROOT} — left as it is, change it if that is "
                         f"the stale one")
        return
    # A .env the engineer wrote themselves, with no key to fill.
    with env.open("a") as f:
        f.write(f"\nSSSF_SKILL={SKILL_ROOT}\n")
    notes.append(f"SSSF_SKILL={SKILL_ROOT}  (appended to .env)")


def write_config(harness: str, dest: Path, force: bool,
                 stamped: list, skipped: list) -> None:
    """The one stamped file that is assembled rather than copied."""
    if dest.exists() and not force:
        skipped.append(str(dest))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_harness.render_config(harness))
    stamped.append(str(dest))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", help="which coding-agent harness this roster runs "
                                          "on; asked interactively if omitted")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args()

    # Asked BEFORE anything is written: the harness decides which prompts, which
    # roster and which env sample get stamped, so an abort here leaves the repo
    # untouched rather than half-installed.
    harness = _harness.choose(args.harness)
    harness_templates = TEMPLATES / "harnesses" / harness

    root = Path.cwd()
    stamped, skipped, notes = [], [], []

    stamp(TEMPLATES / "adws", root / "adws", args.force, stamped, skipped)
    stamp(harness_templates / "prompt_engineering",
          root / "adws" / "adw_data" / "prompt_engineering", args.force, stamped, skipped)
    stamp(harness_templates / "harness_engineering",
          root / "adws" / "adw_data" / "harness_engineering", args.force, stamped, skipped)
    write_config(harness, root / "adws" / "adw_sssf_config" / "sssf.config.yaml",
                 args.force, stamped, skipped)
    stamp(harness_templates / "env.sample", root / ".env.sample",
          args.force, stamped, skipped)
    # The recipes are part of the operating experience, and several cookbooks
    # plus the run banner tell you to use them, so a stamped repo has to have
    # them. Skipped like any other file if the repo already has a justfile.
    stamp(TEMPLATES / "justfile", root / "justfile", args.force, stamped, skipped)
    ensure_gitignore(root, stamped)
    # Last, because it reads the .env.sample this run just stamped.
    ensure_env(root, root / ".env.sample", stamped, notes)

    print(f"sssf installed into {root} on the {harness} harness")
    print(f"  stamped: {len(stamped)} file(s)")
    for s in stamped:
        print(f"    + {s}")
    if skipped:
        print(f"  skipped (already exist, use --force to overwrite): {len(skipped)}")
    # The justfile's operational recipes (up, issues, prs, kill, worktrees) run
    # scripts out of the skill, not out of this repo, and every agent keeps its
    # skills in a different place — so the justfile guesses nothing and this is
    # the only moment anyone knows the answer. `ensure_env` has already written
    # it; this says so, every time, because a recipe that silently resolves to a
    # path that does not exist is the worst version of this.
    print(f"\nthe skill is here: {SKILL_ROOT}")
    for note in notes:
        print(f"  {note}")

    # The harness ships its own post-install steps in about.md, so a new
    # harness brings its instructions with it instead of editing this script.
    _, steps = _harness.about(harness)
    if steps:
        print(f"\nbefore the first run ({harness}):\n")
        print(steps)

    print("\nnext steps:")
    print("  1. open .env             # SSSF_SKILL is filled in; add whatever else it asks for")
    print("  2. just demo             # two cheap read-only runs, end to end")
    print("  3. just sessions         # what just happened")
    print("  4. just up               # trace UI + both watchers, all at once")
    print("\n  no just? the raw form of step 2 is:")
    print("     uv run adws/adw_prompt.py \"say hello\" --agent scout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
