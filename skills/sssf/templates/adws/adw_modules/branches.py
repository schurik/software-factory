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
    branch, silently where nothing went wrong (worktrees off, no issue, link
    off) and with a note saying why the Development panel is empty where
    something was tried and did not land.
    """
    result = BranchPlan()
    config = cfg.worktree
    if not config.enabled:
        return result                 # no worktree, no branch, nothing to name

    recorded = worktree.recorded_branch(request.main_root, config, request.adw_id)
    if recorded:
        result.branch = recorded
        return result

    # No record, and no way to re-derive a slug we did not choose. The metadata
    # file above is gitignored and local-only, so a fresh clone, a
    # `git clean -fdx`, or a second checkout of the same repository loses it
    # while the branch — a real git ref — survives. Ask git what this session
    # already has before inventing a bare name it will not match: that mismatch
    # is exactly the bug this phase exists to fix, re-entered through a
    # different door, and it bites hardest in `adw_pr_review`, which has no
    # integration phase to notice its commits landed somewhere nobody is
    # looking. Restricted to a genuinely JOINING run — no issue, no prompt —
    # because a fresh run always has a name to build and must never adopt a
    # stray branch instead. Exactly one match is required: two is ambiguous,
    # and falling through to the invented name below is the conservative
    # answer to "which one?".
    if request.issue is None and not request.prompt:
        existing = git_helper.branches_matching(
            request.main_root, f"{config.branch_prefix}{request.adw_id}*")
        if len(existing) == 1:
            result.branch = existing[0]
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
    # `_branch_of` deliberately trusts whatever the forge printed — it sanitises
    # names, so the string it hands back is not always the one asked for. But
    # trusting it BLINDLY means a name that does not decode to this adw_id would
    # still be adopted, and `session_of` would then answer "" for it forever:
    # `adw_pr_review` refuses to run when it cannot decode a pull request's head
    # branch, on the one path where a human has already asked for a review. So
    # the forge's name wins only when it still says who made it.
    if linked.ok and session_of(linked.branch, config.branch_prefix) == request.adw_id:
        result.branch = linked.branch
        result.base_commit = linked.head
        result.linked = True
    elif linked.created:
        # The remote branch EXISTS and we cannot use it — either because the
        # fetch failed, or because the forge handed back a name we cannot
        # decode. Taking its name for a local branch cut from a different base
        # would diverge from it, and the divergence would surface as a rejected
        # push at integrate time. Fall all the way back to the name nothing
        # else can be holding.
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
    if not git_helper.ref_exists(main_root, "HEAD"):
        # A fresh `git init` has nothing to branch from yet (worktree.py:79-80
        # guards the same fact before its own _base_ref_of). Asking
        # current_branch would run `rev-parse --abbrev-ref HEAD`, which RAISES
        # on an unborn HEAD — and this runs before the worktree exists, before
        # a single phase opens, so that raise would kill the whole chain over a
        # branch name. "" tells issues.develop to let the forge pick its own
        # default base, which is exactly right for a repo with nothing local
        # to cut from.
        return ""
    branch = git_helper.current_branch(main_root)
    return "" if branch == "HEAD" else branch
