#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""/worktrees — list, reclaim and remove the per-run worktrees. Run from a repo root.

Usage:
    uv run <skill>/scripts/worktrees.py list
    uv run <skill>/scripts/worktrees.py prune [--force]
    uv run <skill>/scripts/worktrees.py remove <adw_id> [--force]

One worktree per run, kept on failure, adds up — and a killed run leaves one
behind on purpose, so "left behind" and "orphaned" are not the same thing. Only
the trace db can tell them apart: git has no idea whether the process that made
a directory is still alive.

`prune` is conservative by design. It takes a worktree only when the run that
owns it has ENDED and the tree is CLEAN. A running session keeps its worktree
because it is using it; a failed one keeps it because that is where you go to
see what happened; anything holding uncommitted work keeps it because the work
exists nowhere else. `--force` widens it to every ended run's worktree,
uncommitted work included — say it out loud, or do not do it. Branches are
always retained: the branch is the record, the worktree is a copy of it.

Deliberately not an ADW. Cleaning up takes no prompt and is not agents-plus-code
work, and giving it a session would create the very thing it removes. It is
thin for the same reason ADWs are (SKILL.md rule 6): every decision below lives
in `adws/adw_modules/worktree.py`, which this imports from the repo it is run in.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "adws"))      # the stamped factory in this repo

CONFIG = "adws/adw_sssf_config/sssf.config.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["list", "prune", "remove"])
    parser.add_argument("adw_id", nargs="?", help="remove: which run's worktree")
    parser.add_argument("--force", action="store_true",
                        help="also take worktrees holding uncommitted work")
    parser.add_argument("--config", default=CONFIG)
    args = parser.parse_args()

    try:
        from adw_modules import agents, git_helper, worktree
    except ImportError as error:
        print(f"no factory here — run this from a repo with adws/ stamped in ({error})",
              file=sys.stderr)
        return 1

    root = git_helper.main_root()
    if not git_helper.is_repo(root):
        print("not a git repository", file=sys.stderr)
        return 1
    cfg = agents.load_config(args.config)
    git_helper.worktree_prune(root)               # forget records whose directory is gone
    found = worktree.inventory(root, cfg.worktree, str(cfg.observability.db))

    if args.action == "list":
        _show(found)
        return 0

    if args.action == "remove":
        if not args.adw_id:
            print("remove needs an adw_id", file=sys.stderr)
            return 2
        targets = [w for w in found if w.adw_id == args.adw_id]
        if not targets:
            print(f"no worktree for {args.adw_id}", file=sys.stderr)
            return 1
    else:
        targets = [w for w in found if worktree.reclaimable(w, args.force)]
        kept = [w for w in found if w not in targets]
        if kept:
            print(f"keeping {len(kept)}:")
            _show(kept)

    if not targets:
        print("nothing to remove")
        return 0

    failed = 0
    for info in targets:
        try:
            worktree.remove(root, info.path, force=args.force)
            print(f"removed {info.path}  (branch {info.branch} retained)")
        except RuntimeError as error:
            failed += 1
            print(f"kept {info.path} — {error}", file=sys.stderr)
    return 1 if failed else 0


def _show(rows) -> None:
    if not rows:
        print("no run worktrees")
        return
    width = max(len(r.adw_id) for r in rows)
    for row in rows:
        flags = ", ".join(f for f in ("dirty" if row.dirty else "",
                                      "gone" if row.prunable else "") if f)
        print(f"  {row.adw_id:<{width}}  {row.status:<8}  {row.branch:<24}  "
              f"{row.path}{'  [' + flags + ']' if flags else ''}")


if __name__ == "__main__":
    sys.exit(main())
