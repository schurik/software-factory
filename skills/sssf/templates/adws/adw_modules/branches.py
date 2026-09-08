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

from .data_types import WorktreeConfig

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
