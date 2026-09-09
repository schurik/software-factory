#!/usr/bin/env -S uv run
# /// script
# dependencies = []
# ///
"""/uninstall — delete the stamped factory out of a repo. The skill is untouched.

Usage:
    uv run <skill>/scripts/uninstall.py [--dry-run] [--yes]
                                        [--branches] [--force] [--config ...]

The inverse of `install.py`, and the only thing here that is not reversible.
Run it from the **target repo root** — the cwd is what gets emptied, never the
skill, which keeps every generator and can stamp the factory back tomorrow.

What goes: `adws/` entire (the ADWs, `adw_modules/`, the config, the prompt and
harness engineering you own, and the whole run record under `adw_data/`), the
per-run worktrees and their git metadata, `.env.sample`, the stamped `justfile`,
and the `# sssf runtime` block from `.gitignore`.

Three files live in a namespace the repository had before the factory arrived —
`justfile`, `.env`, `.gitignore` — so each is compared against what was stamped
and **kept the moment it differs**. A `.env` holding your API keys and a
justfile holding your own recipes are not the factory's to delete; they are
reported instead, with the one line to remove by hand.

Refuses while anything is still running. A half-deleted factory under a live
run is the one state worse than either end of this, and a watcher that survives
its config launches runs into a repo that no longer has ADWs to run. `just kill
<adw_id>` and ctrl-c on `just up` first, or `--force` to say you have.

Deliberately not an ADW: removing the factory takes no prompt, and it cannot be
agents-plus-code work when the code it would ride on is what is being deleted.
Stdlib only, and it imports nothing from `adws/` for the same reason — this has
to work on a factory that is already broken, which is when it is most wanted.
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness                                    # noqa: E402  (path set above)
import install                                     # noqa: E402  (the stamp list)

SKILL_ROOT = _harness.SKILL_ROOT
TEMPLATES = _harness.TEMPLATES

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
DEFAULT_DB = "adws/adw_data/sssf.db"
DEFAULT_WORKTREE_DIR = ".sssf-worktrees"
DEFAULT_BRANCH_PREFIX = "sssf/"

# Runtime, never stamped: excluded when counting "files the skill never wrote",
# so a hundred session directories do not read as a hundred of your own edits.
RUNTIME_NAMES = {"sessions", "sssf.db", "issue-locks", "pr-locks", "__pycache__"}


# ── reading the repo, without the factory's own code ─────────────────────────

def scalar(text: str, section: str, key: str) -> str | None:
    """One `key:` from one top-level `section:` of the config. No yaml here.

    Two values decide what gets deleted outside `adws/` — where the worktrees
    are and what their branches are called — and both are config, not code. A
    dependency on pyyaml to read two strings would cost this script the one
    property that matters: running on a repo whose factory no longer works.
    Anything unparseable falls back to the stamped default and says so.
    """
    body = re.search(rf"^{section}:\s*$\n((?:^[ \t].*$\n?|^\s*$\n?)*)",
                     text, re.MULTILINE)
    if not body:
        return None
    found = re.search(rf"^\s+{key}:\s*(.+?)\s*(?:#.*)?$", body.group(1), re.MULTILINE)
    return found.group(1).strip().strip("\"'") if found else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError, ValueError):
        return False
    except PermissionError:
        return True                                # someone else's, but it exists
    return True


def live_processes(db: Path) -> list[str]:
    """Runs and watchers the trace believes are alive AND whose pid still is.

    Both halves are needed. A SIGKILL or a reboot leaves a session row saying
    `running` forever, so the row alone would block an uninstall on a machine
    where nothing has run for a week; the pid alone would miss a run whose
    process is healthy. A pid that has been recycled onto some stranger's
    process is the acceptable error here — it costs a `--force`, not a kill.
    """
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    alive = []
    try:
        rows = conn.execute(
            "SELECT s.adw_id, p.pid FROM sessions s"
            "  JOIN processes p ON p.adw_id = s.adw_id"
            " WHERE s.status = 'running' AND p.kind = 'adw' AND p.ended_at IS NULL"
        ).fetchall()
        alive += [f"run {adw_id} (pid {pid}) — stop it with: just kill {adw_id}"
                  for adw_id, pid in rows if pid and _pid_alive(pid)]
        rows = conn.execute(
            "SELECT kind, pid FROM watchers WHERE status IN ('polling', 'working')"
        ).fetchall()
        alive += [f"{kind} watcher (pid {pid}) — ctrl-c the `just up` that owns it"
                  for kind, pid in rows if pid and _pid_alive(pid)]
    except sqlite3.Error:
        pass                                       # no schema yet, or mid-write
    finally:
        conn.close()
    return alive


def git(root: Path, *argv: str) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(root), *argv],
                          capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def run_worktrees(root: Path, wt_dir: str, prefix: str) -> list[Path]:
    """The per-run worktrees, from git rather than from the directory listing.

    `rm -rf .sssf-worktrees` is the obvious move and the wrong one: it leaves
    the administrative files under `.git/worktrees` behind, so the repo keeps
    claiming a dozen worktrees that are not there and the branches they held
    stay checked out — every later `git worktree add` for a reused id then
    fails. Ask git what exists, remove them the way git removes them.
    """
    code, out = git(root, "worktree", "list", "--porcelain")
    if code != 0:
        return []
    container = (root / wt_dir).resolve()
    found, path = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = Path(line.split(" ", 1)[1]).resolve()
        elif line.startswith("branch ") and path is not None:
            branch = line.split(" ", 1)[1].removeprefix("refs/heads/")
            if branch.startswith(prefix) or container in path.parents:
                found.append(path)
            path = None
    return found


def run_branches(root: Path, prefix: str) -> list[str]:
    code, out = git(root, "for-each-ref", "--format=%(refname:short)",
                    f"refs/heads/{prefix}*")
    return out.splitlines() if code == 0 and out else []


# ── what the skill stamped, and what it did not ──────────────────────────────

def stamped_targets(root: Path) -> set[Path]:
    """Every path `install.py` could have written, unioned over all harnesses.

    Unioned because a stamped repo does not record which harness it chose in a
    form this script could trust, and it does not need to: the union is only
    ever used to answer "is this file one the skill wrote", and no harness's
    prompt set collides with another's.
    """
    targets: set[Path] = set()

    def walk(src: Path, dest: Path) -> None:
        if not src.exists():
            return
        if src.is_dir():
            for child in src.iterdir():
                if child.name != "__pycache__":
                    walk(child, dest / child.name)
            return
        targets.add(dest)

    walk(TEMPLATES / "adws", root / "adws")
    for harness in _harness.names():
        home = TEMPLATES / "harnesses" / harness
        walk(home / "prompt_engineering", root / "adws/adw_data/prompt_engineering")
        walk(home / "harness_engineering", root / "adws/adw_data/harness_engineering")
    targets.add(root / CONFIG)
    return targets


def unstamped_files(root: Path) -> list[Path]:
    """Files under `adws/` the skill never wrote: your ADWs, your modules.

    They are deleted too — `adws/` is the factory and the factory is what goes —
    but they are the part of this that is genuinely yours, so they are counted
    and named before the question is asked rather than after.
    """
    stamped = stamped_targets(root)
    extra = []
    for path in sorted((root / "adws").rglob("*")):
        if path.is_dir() or path in stamped:
            continue
        if RUNTIME_NAMES & set(path.relative_to(root).parts):
            continue
        if path.suffix == ".pyc":
            continue
        extra.append(path)
    return extra


def shipped_env_samples() -> list[str]:
    return [(TEMPLATES / "harnesses" / h / "env.sample").read_text()
            for h in _harness.names()
            if (TEMPLATES / "harnesses" / h / "env.sample").is_file()]


def is_as_stamped(path: Path, candidates: list[str]) -> bool:
    """Does this file still hold exactly what the skill put in it?

    The test for the files that share a namespace with the repository.
    Identical to something shipped means nobody has written anything of their
    own into it, and only then may it be deleted.
    """
    if not path.is_file():
        return False
    return any(path.read_text() == text for text in candidates)


def env_is_only_stamped(env: Path) -> bool:
    """A `.env` that is the sample plus the `SSSF_SKILL=` the installer wrote.

    `SSSF_SKILL` is compared away rather than compared, because the installer
    filled it in itself — it is the factory's line, not yours. Every other
    difference from the sample is yours, and one filled-in API key is enough
    to keep the whole file.
    """
    if not env.is_file():
        return False

    def strip(text: str) -> list[str]:
        return [line for line in text.splitlines()
                if not line.startswith("SSSF_SKILL=")]

    return any(strip(env.read_text()) == strip(sample)
               for sample in shipped_env_samples())


# ── the plan ─────────────────────────────────────────────────────────────────

def build_plan(root: Path, db: Path, wt_dir: str, prefix: str) -> dict:
    justfile = root / "justfile"
    env_sample = root / ".env.sample"
    env = root / ".env"
    gitignore = root / ".gitignore"

    delete: list[Path] = []
    keep: list[str] = []

    if (root / "adws").is_dir():
        delete.append(root / "adws")
    if env_sample.is_file():
        if is_as_stamped(env_sample, shipped_env_samples()):
            delete.append(env_sample)
        else:
            keep.append(".env.sample — not one this skill ships today; either it is "
                        "yours or it came from an older version. Read it, then "
                        "delete it yourself")
    if justfile.is_file():
        stamped_justfile = (TEMPLATES / "justfile").read_text()
        if is_as_stamped(justfile, [stamped_justfile]):
            delete.append(justfile)
        elif justfile.read_text().startswith(stamped_justfile.splitlines()[0]):
            keep.append("justfile — a stamped one that has since diverged (your "
                        "recipes, or an older version of this skill). The sssf "
                        "recipes are still in it; delete or strip it yourself")
        else:
            keep.append("justfile — this repo's own; install.py never wrote to it")
    if env.is_file():
        if env_is_only_stamped(env):
            delete.append(env)
        else:
            keep.append(".env — it holds values the sample does not; only the "
                        "SSSF_SKILL line is removed")

    _, dropped, precise = gitignore_without_sssf(gitignore)
    return {
        "delete": delete,
        "keep": keep,
        "worktrees": run_worktrees(root, wt_dir, prefix),
        "worktree_dir": root / wt_dir,
        "branches": run_branches(root, prefix),
        "unstamped": unstamped_files(root) if (root / "adws").is_dir() else [],
        "gitignore": dropped,
        "gitignore_precise": precise,
        "env": env,
        "db": db,
    }


def describe(plan: dict, root: Path, branches: bool) -> None:
    print(f"uninstall sssf from {root}\n")
    if plan["delete"]:
        print("  delete:")
        for path in plan["delete"]:
            count = f"  ({sum(1 for p in path.rglob('*') if p.is_file())} files)" \
                if path.is_dir() else ""
            print(f"    - {path.relative_to(root)}{count}")
    if plan["worktrees"]:
        print(f"  remove {len(plan['worktrees'])} run worktree(s), git metadata "
              f"included:")
        for path in plan["worktrees"]:
            print(f"    - {path}")
    if plan["gitignore"]:
        print(f"  .gitignore: drop the sssf runtime block "
              f"({len(plan['gitignore'])} entries)")
    elif not plan["gitignore_precise"]:
        print("  .gitignore: left alone — it has sssf's entries but not the "
              "`# sssf runtime` header, so which of them are yours is a guess. "
              "Remove by hand.")
    if plan["branches"]:
        verb = "delete" if branches else "KEEP (pass --branches to delete)"
        print(f"  {len(plan['branches'])} run branch(es): {verb}")
        for name in plan["branches"][:10]:
            print(f"    - {name}")
        if len(plan["branches"]) > 10:
            print(f"    … and {len(plan['branches']) - 10} more")
    if plan["keep"]:
        print("  kept, and why:")
        for note in plan["keep"]:
            print(f"    · {note}")
    if plan["unstamped"]:
        print(f"\n  {len(plan['unstamped'])} file(s) under adws/ this skill never "
              f"stamped — your own work, deleted with the rest:")
        for path in plan["unstamped"][:10]:
            print(f"    ! {path.relative_to(root)}")
        if len(plan["unstamped"]) > 10:
            print(f"    ! … and {len(plan['unstamped']) - 10} more")
    if plan["db"].exists():
        print(f"\n  the run record goes with it: {plan['db'].relative_to(root)} and "
              f"every session under adws/adw_data/sessions/")


# ── doing it ─────────────────────────────────────────────────────────────────

def gitignore_without_sssf(gitignore: Path) -> tuple[list[str], list[str], bool]:
    """(the lines that stay, the lines that go, was the block found).

    **The `# sssf runtime` header is what makes this safe.** `.gitignore` is the
    repository's file, and half the entries the installer appends are ones a
    repo plausibly had already — `__pycache__/`, `*.pyc`, `.env`. Matching on
    the entries alone would silently un-ignore a repository's own `.env` on the
    way out, which is a leak, not a tidy-up. So only the contiguous run of
    known entries that FOLLOWS the header the installer wrote is taken, every
    header (a second install appends a second block), and a line that is not
    one of the installer's ends the run.

    Without the header — a hand-tidied `.gitignore` — nothing is touched and
    the caller says so, because guessing is exactly what the header exists to
    avoid.
    """
    if not gitignore.is_file():
        return [], [], True
    lines = gitignore.read_text().splitlines()
    kept, dropped, index = [], [], 0
    found = False
    while index < len(lines):
        if lines[index].strip() != "# sssf runtime":
            kept.append(lines[index])
            index += 1
            continue
        found = True
        index += 1
        while index < len(lines) and lines[index] in install.GITIGNORE_ENTRIES:
            dropped.append(lines[index])
            index += 1
    return kept, dropped, found or not any(
        line in install.GITIGNORE_ENTRIES for line in lines)


def strip_gitignore(root: Path, plan: dict, removed: list[str]) -> None:
    """Take the sssf block back out, and the file with it if nothing else is left."""
    gitignore = root / ".gitignore"
    if not plan["gitignore"]:
        return
    kept, dropped, _ = gitignore_without_sssf(gitignore)
    if not any(line.strip() for line in kept):
        gitignore.unlink()
        removed.append(".gitignore (nothing left in it)")
        return
    gitignore.write_text("\n".join(kept).rstrip("\n") + "\n")
    removed.append(f".gitignore (-{len(dropped)} entries)")


def strip_env(env: Path, removed: list[str]) -> None:
    """The one line the installer wrote into a `.env` that is otherwise yours."""
    if not env.is_file():
        return
    lines = env.read_text().splitlines()
    kept = [line for line in lines if not line.startswith("SSSF_SKILL=")]
    if len(kept) != len(lines):
        env.write_text("\n".join(kept).rstrip("\n") + "\n")
        removed.append(".env (-1 SSSF_SKILL line)")


def execute(plan: dict, root: Path, branches: bool) -> list[str]:
    removed: list[str] = []

    for path in plan["worktrees"]:
        code, out = git(root, "worktree", "remove", "--force", str(path))
        if code != 0:
            shutil.rmtree(path, ignore_errors=True)
            print(f"  git would not remove {path} ({out}) — the directory is gone, "
                  f"pruning the metadata")
        removed.append(f"worktree {path}")
    if plan["worktrees"]:
        git(root, "worktree", "prune")
    container = plan["worktree_dir"]
    if container.is_dir():
        shutil.rmtree(container, ignore_errors=True)
        removed.append(str(container.relative_to(root)))

    if branches:
        for name in plan["branches"]:
            code, out = git(root, "branch", "-D", name)
            removed.append(f"branch {name}" if code == 0
                           else f"branch {name} NOT deleted ({out})")

    for path in plan["delete"]:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path.relative_to(root)))

    strip_gitignore(root, plan, removed)
    if plan["env"] not in plan["delete"]:
        strip_env(plan["env"], removed)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and change nothing")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation; required with no terminal")
    parser.add_argument("--branches", action="store_true",
                        help="also delete the sssf/<adw_id> branches — they hold "
                             "the only copy of any run that never landed")
    parser.add_argument("--force", action="store_true",
                        help="uninstall even while runs or watchers are alive")
    parser.add_argument("--config", default=CONFIG)
    args = parser.parse_args()

    root = Path.cwd().resolve()

    # The skill keeps every generator and is what makes a re-install possible,
    # so the one directory this must never be pointed at is its own.
    if root == SKILL_ROOT or SKILL_ROOT in root.parents or root in SKILL_ROOT.parents:
        print(f"refusing: {root} is the skill, or holds it ({SKILL_ROOT}).\n"
              f"run this from the repo the factory was stamped into.", file=sys.stderr)
        return 1

    text = (root / args.config).read_text() if (root / args.config).is_file() else ""
    wt_dir = scalar(text, "worktree", "dir") or DEFAULT_WORKTREE_DIR
    prefix = scalar(text, "worktree", "branch_prefix") or DEFAULT_BRANCH_PREFIX
    db = root / (scalar(text, "observability", "db") or DEFAULT_DB)

    # Deleting the tree you are standing in half-works and is confusing about
    # which half. A run's worktree is also the one place your own uncommitted
    # work can be, which is the other reason to stop here.
    for path in run_worktrees(root, wt_dir, prefix):
        if path == root or path in root.parents:
            print(f"refusing: this is a run's worktree ({path}).\n"
                  f"cd to the main checkout and uninstall from there.", file=sys.stderr)
            return 1

    plan = build_plan(root, db, wt_dir, prefix)
    if not plan["delete"] and not plan["worktrees"] and not plan["gitignore"]:
        print(f"no factory here — nothing of sssf's is in {root}")
        return 0

    describe(plan, root, args.branches)

    alive = live_processes(db)
    if alive and not args.force:
        print("\nstill running — stop these first, or pass --force:", file=sys.stderr)
        for line in alive:
            print(f"  ! {line}", file=sys.stderr)
        return 1
    if alive:
        print("\n  --force: uninstalling anyway, with these alive:")
        for line in alive:
            print(f"    ! {line}")

    if args.dry_run:
        print("\n--dry-run: nothing was deleted")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("\nnothing deleted. this cannot be undone, so it asks — pass --yes "
                  "to answer ahead of time.\n(nothing to ask on: stdin is not a "
                  "terminal)", file=sys.stderr)
            return 1
        try:
            answer = input("\ndelete all of this? there is no undo [type 'yes']: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "yes":
            print("nothing deleted")
            return 0

    removed = execute(plan, root, args.branches)
    print(f"\nsssf uninstalled from {root}")
    for item in removed:
        print(f"  - {item}")
    if plan["keep"]:
        print("\n  left for you:")
        for note in plan["keep"]:
            print(f"    · {note}")
    left = run_branches(root, prefix)
    if left:
        print(f"\n  {len(left)} sssf branch(es) still here — the record of runs that "
              f"never landed. `git branch -D` them, or re-run with --branches.")
    print(f"\nthe skill is untouched: {SKILL_ROOT}")
    print("re-stamp any time with:  uv run <skill>/scripts/install.py --harness ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
