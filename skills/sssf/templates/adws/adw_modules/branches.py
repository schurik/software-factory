"""The run's branch NAME — built here, taken apart here, and nowhere else.

The name used to be written in one place (`worktree.ensure`) and parsed in two
that did not know about each other (`worktree.inventory`, `adw_pr_review`), which
was survivable only while the name was exactly `<prefix><adw_id>`. The moment it
carries a slug, a second parser is a second bug: `adw_pr_review` REFUSES to run
when it cannot decode a pull request's head branch, so the failure would land on
a human who had already asked for a review.

So the format has one owner. `branch_for` writes it, `session_of` reads it, and
the adw_id stays FIRST — `new_id()` is eight hex characters with no hyphen, so
splitting on the first hyphen is unambiguous, and a pre-slug branch (no hyphen at
all) returns the same answer it always did. Nothing needs migrating.

A slug is a NAME, not an identifier. Nothing ever reads it back.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import git_helper, issues, worktree
from .data_types import (BranchPlan, BranchRequest, LinkedBranchRequest, SSSFConfig,
                         WorktreeConfig)

# What a git ref may not contain, plus everything that is merely ugly in a
# branch name. Collapsed to single hyphens rather than escaped: this is a label
# for humans, and an unreadable escape sequence would defeat the point.
_NOT_ALLOWED = re.compile(r"[^a-z0-9]+")
# Markdown a prompt FILE opens with. `utils.resolve_prompt` runs before main(),
# so `just plan /tmp/feature.md` hands the ADW the file's contents — a first
# line of "# Fix the rounding" must slug to the sentence, not to its marker.
_MARKER = re.compile(r"^[#>*\-\s]+")


def slugify(text: str, limit: int = 40) -> str:
    """A branch-safe label from arbitrary text. Empty is a valid answer.

    Reads the FIRST NON-EMPTY LINE, because the text may be a whole prompt file
    and a branch name is one line long by construction.
    """
    line = next((raw for raw in (text or "").splitlines() if raw.strip()), "")
    line = _MARKER.sub("", line).strip()
    slug = _NOT_ALLOWED.sub("-", line.lower()).strip("-")
    if len(slug) <= limit:
        return slug
    # Truncate on a word boundary so the last word is whole or absent — never a
    # half word, which reads like a typo rather than an abbreviation. A cut that
    # lands exactly ON a boundary is already whole and keeps its last word.
    cut = slug[:limit]
    if slug[limit] != "-" and "-" in cut:
        cut = cut.rsplit("-", 1)[0]
    return cut.strip("-")


def issue_slug(number: int, title: str, limit: int = 40) -> str:
    """`<number>-<title>` — and just the number when the title slugs to nothing.

    The number goes first because it is the part that is always there and always
    meaningful; a trailing bare hyphen from an emoji-only title is not.
    """
    slug = slugify(title, limit)
    return f"{number}-{slug}" if slug else str(number)


def branch_for(config: WorktreeConfig, adw_id: str, slug: str = "") -> str:
    """The run's branch name. The ONLY place this string is assembled."""
    branch = f"{config.branch_prefix}{adw_id}"
    if slug and config.branch_slug:
        return f"{branch}-{slug}"
    return branch


def session_of(branch: str, prefix: str) -> str:
    """The adw_id a branch names, or "" when the branch is not this factory's.

    The inverse of `branch_for`, and the reason the adw_id is the first segment.
    """
    if not branch or not branch.startswith(prefix):
        return ""
    return branch.removeprefix(prefix).split("-", 1)[0]


def plan(cfg: SSSFConfig, request: BranchRequest) -> BranchPlan:
    """Decide the run's branch: reuse a recorded one, name a new one, link it.

    Order is most-specific-first, and the FIRST case is the one that keeps a
    session whole. A worktree is pruned on success, so a later process
    (`just integrate <id>`, a review run, a rerun) arrives holding only an
    adw_id — and it must land on the branch the session already has rather than
    invent a name from whatever prompt it happens to carry.

    Everything after that degrades: no worktrees, no issue, no remote, no auth,
    a tracker that is not GitHub — each ends with a perfectly usable local
    branch and a note saying why the Development panel is empty.
    """
    result = BranchPlan()
    config = cfg.worktree
    if not config.enabled:
        return result                 # no worktree, no branch, nothing to name

    recorded = worktree.recorded_branch(request.main_root, config, request.adw_id)
    if recorded:
        result.branch = recorded
        return result

    slug = ""
    if request.issue is not None:
        brief = issues.peek(request.main_root, cfg.issues, request.issue)
        slug = issue_slug(brief.number or request.issue.number, brief.title)
    elif request.prompt:
        slug = slugify(request.prompt)
    result.branch = branch_for(config, request.adw_id, slug)

    if request.issue is None or not cfg.issues.link_branch:
        return result
    remote = config.integration.remote
    if not git_helper.has_remote(request.main_root, remote):
        result.notes.append(f"no remote named {remote!r} — the branch is local, "
                            f"so #{request.issue.number} has nothing to link to")
        return result

    linked = issues.develop(request.main_root, cfg.issues, LinkedBranchRequest(
        ref=request.issue, branch=result.branch,
        base_ref=_base_ref_of(request.main_root, config), remote=remote))
    result.notes.extend(linked.notes)
    if linked.ok:
        result.branch = linked.branch
        result.base_commit = linked.head
        result.linked = True
    elif linked.created:
        # The remote branch EXISTS and we cannot use it. Taking its name for a
        # local branch cut from a different base would diverge from it, and the
        # divergence would surface as a rejected push at integrate time. Fall all
        # the way back to the name nothing else can be holding.
        result.branch = branch_for(config, request.adw_id)
        result.notes.append(f"falling back to {result.branch} so the local branch "
                            f"cannot diverge from the one at the forge")
    return result


def _base_ref_of(main_root: Path, config: WorktreeConfig) -> str:
    """What the linked branch should be cut from, as a NAME the forge knows.

    `worktree._base_ref_of` answers the same question for the local checkout and
    may answer with a sha; `issues.develop` drops `--base` when it gets one.
    """
    if config.base_ref:
        return config.base_ref
    branch = git_helper.current_branch(main_root)
    return "" if branch == "HEAD" else branch
