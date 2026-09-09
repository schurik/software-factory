# Phase 8 — Linked Branches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An issue-triggered run's branch is created through `gh issue develop` so it appears in the issue's Development panel while the run is still going, and every run's branch name carries a slug of its issue title or its prompt.

**Architecture:** A new pure module `adw_modules/branches.py` becomes the single owner of the branch-name format (build it, parse it back, decide it). `adw_modules/issues.py` grows two forge commands — `peek()` to read an issue title before any phase exists, `develop()` to have GitHub create the linked branch. `session.ensure()` composes them and hands the resulting name and pinned base commit to `worktree.ensure()`, which stays forge-free and reuses its existing branch-exists path. Every failure on the forge side degrades to today's local-only branch with a note.

**Tech Stack:** Python 3.11+ (`uv run` with inline script metadata), pydantic v2, `gh` CLI, git worktrees. Tests are pytest, run through `uv run --with`.

**Spec:** `docs/phase-8-linked-branches.md` — read it before Task 1. This plan implements it section by section.

## Global Constraints

- **Repository:** all work happens in `software-factory`, under `skills/sssf/`. The stamped copy in any consuming repo (e.g. `property-rent/adws/`) is updated only in Task 8.
- **`templates/adws/` is copied verbatim into consuming repos** by `install.py` (`stamp(TEMPLATES / "adws", root / "adws", …)`, `scripts/install.py:147`). **Never put tests, fixtures or scratch files under `templates/`** — they would be stamped into every user's repo. Tests live in `skills/sssf/tests/`, which is not stamped.
- **SKILL.md hard rule 4 (four-param rule):** any function taking more than four parameters takes one concrete pydantic type instead. `WorktreeRequest` is the pattern; this plan adds `BranchRequest` and `LinkedBranchRequest` for exactly this reason.
- **SKILL.md hard rule 6:** ADW scripts stay thin — all logic lands in `adw_modules/`. The 14 ADW edits in Task 6 are one keyword argument each, nothing more.
- **No new runtime dependencies.** The ADWs declare their deps inline (`pydantic`, `python-dotenv`, `pyyaml`, `rich`); this phase adds none. `pytest` is supplied by `uv run --with pytest` at test time only.
- **Nothing raises on the forge path.** `peek()` and `develop()` return their result type with `ok=False` and a note. A branch name is a convenience; a chain that dies because a sidebar entry could not be written is worse than a chain with no sidebar entry.
- **Documentation language is English**, matching the rest of the repository.
- **Test command, from the repository root, used unchanged in every task:**

  ```bash
  uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
  ```

---

## File Structure

| File | Responsibility |
|---|---|
| `skills/sssf/templates/adws/adw_modules/branches.py` | **New.** The branch-name format: build, parse, decide. The only module that knows a slug exists. |
| `skills/sssf/templates/adws/adw_modules/data_types.py` | New models `BranchRequest`, `BranchPlan`, `IssueBrief`, `LinkedBranchRequest`, `LinkedBranch`; new fields on `WorktreeRequest`; new config keys on `WorktreeConfig` and `IssuesConfig`. |
| `skills/sssf/templates/adws/adw_modules/git_helper.py` | One new plumbing call: `fetch_branch()`. |
| `skills/sssf/templates/adws/adw_modules/issues.py` | Two new forge commands: `peek()`, `develop()`. |
| `skills/sssf/templates/adws/adw_modules/worktree.py` | Honour a caller-supplied branch and base commit; read recorded metadata before recomputing a name; delegate parsing to `branches.session_of`. |
| `skills/sssf/templates/adws/adw_modules/session.py` | The composition point: `ensure(cfg, adw_id, *, prompt, issue)`. |
| `skills/sssf/templates/adws/adw_*.py` | 14 call sites, one keyword each; `adw_pr_review.py` loses its private `_session_of`. |
| `skills/sssf/templates/config/base.yaml` | Three new keys with their comments. |
| `skills/sssf/references/config.md`, `skills/sssf/cookbooks/create_config.md` | The same three keys, documented. |
| `skills/sssf/tests/` | **New, not stamped.** `conftest.py` plus four test modules. |

---

### Task 1: The format module and the test harness

**Files:**
- Create: `skills/sssf/tests/conftest.py`
- Create: `skills/sssf/tests/test_branches.py`
- Create: `skills/sssf/templates/adws/adw_modules/branches.py`

**Interfaces:**
- Consumes: `WorktreeConfig` from `adw_modules.data_types` (existing — has `branch_prefix: str = "sssf/"`; Task 2 adds `branch_slug: bool = True`, so this task adds it too as part of the same edit).
- Produces:
  - `branches.slugify(text: str, limit: int = 40) -> str`
  - `branches.issue_slug(number: int, title: str, limit: int = 40) -> str`
  - `branches.branch_for(config: WorktreeConfig, adw_id: str, slug: str = "") -> str`
  - `branches.session_of(branch: str, prefix: str) -> str`

- [ ] **Step 1: Create the test harness**

`skills/sssf/tests/conftest.py` — puts the stamped module tree on `sys.path` so tests import the real templates rather than a copy:

```python
"""Tests import the TEMPLATE modules directly — the same files install.py stamps.

`templates/adws` is not a package root anyone installs, so it goes on sys.path
here rather than being pip-installed. Nothing in this directory is stamped into
a consuming repo: install.py copies `templates/`, and this lives beside it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "templates" / "adws"))
```

- [ ] **Step 2: Write the failing test**

`skills/sssf/tests/test_branches.py`:

```python
import pytest

from adw_modules import branches
from adw_modules.data_types import WorktreeConfig


@pytest.fixture
def config():
    return WorktreeConfig()


@pytest.mark.parametrize("text,expected", [
    ("Fix the floorEuro rounding", "fix-the-flooreuro-rounding"),
    ("  Add tenant export to CSV  ", "add-tenant-export-to-csv"),
    ("# Fix the floorEuro rounding", "fix-the-flooreuro-rounding"),
    ("## Deep heading\n\nbody text", "deep-heading"),
    ("\n\n\nSecond line is the first", "second-line-is-the-first"),
    ("Mietvertrag: Kaution & Nebenkosten!", "mietvertrag-kaution-nebenkosten"),
    ("---", ""),
    ("", ""),
    ("@{weird}", "weird"),
    ("trailing dot.", "trailing-dot"),
    # A ref may not END in ".lock" — but every "." here has already become a
    # hyphen, so the illegal shape cannot survive to be stripped.
    ("release.lock", "release-lock"),
    ("a..b", "a-b"),
])
def test_slugify_shapes(text, expected):
    assert branches.slugify(text) == expected


def test_slugify_truncates_on_a_hyphen_boundary():
    # 40 chars would land mid-word; the last whole word wins instead.
    slug = branches.slugify("rounding behaviour of the euro formatter helper", limit=40)
    assert slug == "rounding-behaviour-of-the-euro-formatter"
    assert len(slug) <= 40
    assert not slug.endswith("-")


def test_issue_slug_keeps_the_number_when_the_title_slugs_to_nothing():
    assert branches.issue_slug(42, "Fix rounding") == "42-fix-rounding"
    assert branches.issue_slug(42, "!!!") == "42"


def test_branch_for_with_and_without_a_slug(config):
    assert branches.branch_for(config, "a1b2c3d4") == "sssf/a1b2c3d4"
    assert (branches.branch_for(config, "a1b2c3d4", "42-fix-rounding")
            == "sssf/a1b2c3d4-42-fix-rounding")


def test_branch_slug_false_restores_the_old_name(config):
    config.branch_slug = False
    assert branches.branch_for(config, "a1b2c3d4", "42-fix-rounding") == "sssf/a1b2c3d4"


@pytest.mark.parametrize("branch,expected", [
    ("sssf/a1b2c3d4-42-fix-rounding", "a1b2c3d4"),
    ("sssf/a1b2c3d4", "a1b2c3d4"),          # the pre-phase-8 format still resolves
    ("feature/something", ""),
    ("", ""),
])
def test_session_of(branch, expected):
    assert branches.session_of(branch, "sssf/") == expected


def test_session_of_round_trips_every_branch_for(config):
    for slug in ("", "42-fix-rounding", "add-tenant-export-to-csv"):
        branch = branches.branch_for(config, "a1b2c3d4", slug)
        assert branches.session_of(branch, config.branch_prefix) == "a1b2c3d4"
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'adw_modules.branches'`.

- [ ] **Step 4: Add the config flag the tests read**

In `skills/sssf/templates/adws/adw_modules/data_types.py`, class `WorktreeConfig` (line ~446), directly after `branch_prefix`:

```python
    branch_prefix: str = "sssf/"     # the run's branch is <prefix><adw_id>[-<slug>]
    # A branch called sssf/a1b2c3d4 says which run made it and nothing about
    # what it was for. With this on, the run's issue title or prompt is slugged
    # onto the end — the adw_id stays FIRST so `branches.session_of` can still
    # take it back off. false restores the bare <prefix><adw_id>.
    branch_slug: bool = True
```

- [ ] **Step 5: Write the module**

Create `skills/sssf/templates/adws/adw_modules/branches.py` with the four pure functions (`plan()` arrives in Task 5):

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add skills/sssf/tests skills/sssf/templates/adws/adw_modules/branches.py \
        skills/sssf/templates/adws/adw_modules/data_types.py
git commit -m "branches: one owner for the run's branch-name format"
```

---

### Task 2: `worktree.py` honours a given branch, and remembers one it was given

**Files:**
- Create: `skills/sssf/tests/test_worktree_branch.py`
- Modify: `skills/sssf/templates/adws/adw_modules/data_types.py` (class `WorktreeRequest`, line ~608)
- Modify: `skills/sssf/templates/adws/adw_modules/worktree.py` (`ensure`, lines 65–116; `inventory`, lines 200–217)

**Interfaces:**
- Consumes: `branches.session_of` (Task 1).
- Produces:
  - `WorktreeRequest.branch: str = ""` and `WorktreeRequest.base_commit: str = ""`
  - `worktree.recorded_branch(main_root, config: WorktreeConfig, adw_id: str) -> str`

- [ ] **Step 1: Write the failing test**

`skills/sssf/tests/test_worktree_branch.py`:

```python
import subprocess
from pathlib import Path

import pytest

from adw_modules import worktree
from adw_modules.data_types import WorktreeConfig, WorktreeRequest


def _git(cwd, *args):
    completed = subprocess.run(["git", *args], cwd=str(cwd),
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A one-commit git repository — the smallest thing worktree.ensure accepts."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "first")
    return root


def test_default_branch_is_unchanged(repo):
    workspace = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=WorktreeConfig()))
    assert workspace.branch == "sssf/a1b2c3d4"


def test_a_given_branch_and_base_commit_are_used(repo):
    head = _git(repo, "rev-parse", "HEAD")
    workspace = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=WorktreeConfig(),
        branch="sssf/a1b2c3d4-42-fix-rounding", base_commit=head))
    assert workspace.branch == "sssf/a1b2c3d4-42-fix-rounding"
    assert workspace.base_commit == head
    assert _git(workspace.repo_root, "rev-parse", "--abbrev-ref", "HEAD") == \
        "sssf/a1b2c3d4-42-fix-rounding"


def test_a_pruned_worktree_reattaches_to_the_recorded_branch(repo):
    """The bug this fixes: a second process must not cut a SECOND branch.

    A successful run's worktree is pruned by design, so `just integrate <id>`
    enters with the directory gone. Recomputing the name would produce a bare
    sssf/<adw_id>, which does not exist — and the run's commits would be
    orphaned on the branch nobody looked for.
    """
    config = WorktreeConfig()
    first = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=config,
        branch="sssf/a1b2c3d4-42-fix-rounding"))
    assert worktree.release(first).startswith("removed")
    assert not Path(first.repo_root).exists()

    # Re-entry knows only the adw_id — exactly what adw_integrate.py has.
    second = worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=config))
    assert second.branch == "sssf/a1b2c3d4-42-fix-rounding"


def test_recorded_branch_is_empty_when_nothing_was_recorded(repo):
    assert worktree.recorded_branch(repo, WorktreeConfig(), "deadbeef") == ""


def test_inventory_reports_the_adw_id_of_a_slugged_branch(repo):
    worktree.ensure(WorktreeRequest(
        main_root=repo, adw_id="a1b2c3d4", config=WorktreeConfig(),
        branch="sssf/a1b2c3d4-42-fix-rounding"))
    found = worktree.inventory(repo, WorktreeConfig())
    assert [(info.adw_id, info.branch) for info in found] == [
        ("a1b2c3d4", "sssf/a1b2c3d4-42-fix-rounding")]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests/test_worktree_branch.py -q
```

Expected: FAIL — `WorktreeRequest` rejects the `branch` keyword (pydantic: *unexpected keyword argument*), and `worktree.recorded_branch` does not exist.

- [ ] **Step 3: Add the request fields**

In `data_types.py`, class `WorktreeRequest`:

```python
class WorktreeRequest(BaseModel):
    """Everything worktree.ensure() needs. One object, never loose params."""

    main_root: Path
    adw_id: str
    config: WorktreeConfig = Field(default_factory=WorktreeConfig)
    # Both empty on the ordinary path, and then this module decides everything.
    # `branches.plan()` fills them when the branch was named — or created at the
    # forge — before the worktree existed. Passing base_commit is not an
    # optimisation: a branch the forge cut from ORIGIN's base tip has a
    # merge-base with the LOCAL base that is older than its real branch point.
    # NOT every diff in the run measures from base_commit — most chains pin
    # their own baseline instead (`git_helper.rev(..., "HEAD")`, read right
    # after `session.ensure()` returns), and `changes.resolve_base` takes a REF
    # and answers `merge_base(ref, HEAD)`, so a caller that hands it base_commit
    # directly gets it back unchanged (merge_base of an ancestor sha is itself)
    # while one that hands it base_ref does not. What base_commit actually is:
    # the honest branch point for whichever chain measures from it, and the
    # value `integration.py` compares HEAD against to decide a branch has
    # nothing to land.
    branch: str = ""
    base_commit: str = ""
```

- [ ] **Step 4: Make `ensure()` use them, and read the record first**

In `worktree.py`, replace the branch computation and the two base-commit assignments inside `ensure()` (currently lines 82–115):

```python
    root = anchor(main, config.dir)
    path = root / request.adw_id
    meta = _meta_path(root, request.adw_id)
    # WHAT THIS RUN'S BRANCH IS CALLED, most specific answer first: what the
    # caller named, else what an earlier process in this session recorded, else
    # the default. The middle one is the load-bearing case — a successful run's
    # worktree is pruned, so a later process enters with the directory gone and
    # only an adw_id in hand. Recomputing the name there was harmless only while
    # the name was a pure function of the adw_id; with a slug in it, the recompute
    # would miss and `worktree_add` would cut a SECOND branch from the base,
    # orphaning the run's commits on the first one.
    branch = (request.branch or _read_meta(meta).get("branch")
              or f"{config.branch_prefix}{request.adw_id}")

    if path.is_dir():
        return _reattach(path, main, branch, meta)

    # A worktree whose directory was deleted by hand still holds its
    # registration, and `worktree add` refuses the same path twice. Pruning is
    # administrative — it forgets records for directories that are already gone
    # and touches nothing that exists.
    git_helper.worktree_prune(main)
    ensure_dir(root)

    if git_helper.branch_exists(main, branch):
        # The run's branch outlived its worktree — a pruned success, or a rerun.
        # Check it out again rather than branching a second time from the base:
        # the branch is the record, and re-creating it would discard the record.
        git_helper.worktree_add(main, path, branch)
        recorded = _read_meta(meta)
        base_ref = recorded.get("base_ref") or _base_ref_of(main, config)
        base_commit = (request.base_commit or recorded.get("base_commit")
                       or git_helper.merge_base(path, base_ref, "HEAD"))
    else:
        base_ref = _base_ref_of(main, config)
        base_commit = request.base_commit or git_helper.rev(main, base_ref)
        git_helper.worktree_add(main, path, branch, base_ref)
```

- [ ] **Step 5: Add `recorded_branch()` and point `inventory()` at `branches`**

In `worktree.py`, add after `_write_meta()`:

```python
def recorded_branch(main_root, config: WorktreeConfig, adw_id: str) -> str:
    """The branch an earlier process in this session recorded, or "".

    The metadata file lives BESIDE the worktree, so it is still there after a
    successful run's tree was pruned. `branches.plan()` reads it before it names
    anything, which is what stops a second process from renaming the session.
    """
    return _read_meta(_meta_path(anchor(main_root, config.dir), adw_id)).get("branch", "")
```

Change the import line at the top of the file:

```python
from . import branches, git_helper, tracer
```

and inside `inventory()` replace the `adw_id = (...)` expression:

```python
        adw_id = branches.session_of(branch, config.branch_prefix) or path.name
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
```

Expected: all tests PASS, including Task 1's.

- [ ] **Step 7: Commit**

```bash
git add skills/sssf/tests/test_worktree_branch.py \
        skills/sssf/templates/adws/adw_modules/worktree.py \
        skills/sssf/templates/adws/adw_modules/data_types.py
git commit -m "worktree: accept a given branch and base commit, remember the branch"
```

---

### Task 3: `issues.peek()` — the title, before a run exists

**Files:**
- Create: `skills/sssf/tests/test_issues_link.py`
- Modify: `skills/sssf/templates/adws/adw_modules/data_types.py` (after class `IssueRef`, line ~675)
- Modify: `skills/sssf/templates/adws/adw_modules/issues.py`

**Interfaces:**
- Consumes: `IssuesConfig`, `IssueRef`, `issues._run`, `issues._aim`, `issues.resolve_project` (all existing).
- Produces:
  - `IssueBrief(ok: bool, number: int, title: str, url: str, author: str)`
  - `issues.peek(tree, config: IssuesConfig, ref: IssueRef) -> IssueBrief`

- [ ] **Step 1: Write the failing test**

`skills/sssf/tests/test_issues_link.py`:

```python
import json
import subprocess

import pytest

from adw_modules import issues
from adw_modules.data_types import IssueRef, IssuesConfig


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


@pytest.fixture
def calls(monkeypatch):
    """Record every forge invocation instead of running one."""
    recorded = []
    replies = {}

    def fake_run(argv, cwd):
        recorded.append(argv)
        return replies.get("next", _completed())

    monkeypatch.setattr(issues, "_run", fake_run)
    return recorded, replies


def test_peek_reads_number_title_and_url(monkeypatch, tmp_path, calls):
    recorded, replies = calls
    replies["next"] = _completed(stdout=json.dumps({
        "number": 42, "title": "floorEuro rounds down on negative amounts",
        "url": "https://github.com/acme/widgets/issues/42",
        "author": {"login": "alex"}}))

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is True
    assert brief.number == 42
    assert brief.title == "floorEuro rounds down on negative amounts"
    assert brief.author == "alex"
    assert recorded[0] == ["gh", "issue", "view", "42", "--repo", "acme/widgets",
                           "--json", "number,title,url,author"]


def test_peek_never_raises_when_the_forge_refuses(tmp_path, calls):
    recorded, replies = calls
    replies["next"] = _completed(stderr="gh: not authenticated", returncode=1)

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is False
    assert brief.title == ""
    assert brief.number == 42          # what we asked for is still worth knowing


def test_peek_never_raises_on_junk_output(tmp_path, calls):
    recorded, replies = calls
    replies["next"] = _completed(stdout="not json at all")

    brief = issues.peek(tmp_path, IssuesConfig(project="acme/widgets"),
                        IssueRef(number=42))

    assert brief.ok is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests/test_issues_link.py -q
```

Expected: FAIL — `AttributeError: module 'adw_modules.issues' has no attribute 'peek'`.

- [ ] **Step 3: Add the type**

In `data_types.py`, immediately after class `IssueRef`:

```python
class IssueBrief(BaseModel):
    """The two fields a branch name needs, read before the run exists.

    Not an `IssueContext`: that one carries a `body_path`, and there is no run
    to write a body into yet. `ok=False` is an ordinary answer here — the branch
    is named without a title, and the run proceeds.
    """

    ok: bool = False
    number: int = 0
    title: str = ""
    url: str = ""
    author: str = ""
```

- [ ] **Step 4: Add `peek()`**

In `issues.py`, immediately before `fetch()`:

```python
def peek(tree, config: IssuesConfig, ref: IssueRef) -> IssueBrief:
    """Read an issue's title WITHOUT a run — the branch is named before phases.

    `fetch()` cannot serve this: it writes the body into `run.context_handoff_dir`,
    and at naming time there is no Run, no session directory and no trace row.
    The overlap is one `gh issue view`, which is worth paying rather than
    threading a pre-fetched payload through `session.ensure()` into a phase that
    has not opened yet.

    NEVER RAISES, unlike `fetch()`. A chain that cannot read its issue has
    nothing to plan against and should die; a chain that cannot read its issue's
    TITLE has only a duller branch name.
    """
    brief = IssueBrief(number=ref.number)
    project = ref.project or resolve_project(config, tree)
    argv = _aim([*config.fetch_command], project, ref.number)
    argv += ["--json", "number,title,url,author"]
    completed = _run(argv, tree)
    if completed.returncode != 0:
        return brief
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return brief
    author = payload.get("author") or {}
    return IssueBrief(
        ok=True,
        number=int(payload.get("number", ref.number)),
        title=payload.get("title") or "",
        url=payload.get("url") or "",
        author=(author.get("login", "") if isinstance(author, dict) else str(author)),
    )
```

Add `IssueBrief` to the `from .data_types import (...)` list at the top of `issues.py`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/sssf/tests/test_issues_link.py \
        skills/sssf/templates/adws/adw_modules/issues.py \
        skills/sssf/templates/adws/adw_modules/data_types.py
git commit -m "issues: peek() reads an issue title before the run exists"
```

---

### Task 4: `issues.develop()` — the forge creates the linked branch

**Files:**
- Modify: `skills/sssf/tests/test_issues_link.py` (append)
- Modify: `skills/sssf/templates/adws/adw_modules/git_helper.py` (append to the integration section)
- Modify: `skills/sssf/templates/adws/adw_modules/data_types.py`
- Modify: `skills/sssf/templates/adws/adw_modules/issues.py`

**Interfaces:**
- Consumes: `IssueBrief`-era helpers `_aim`, `_run`, `resolve_project`; `git_helper.branch_exists` (existing).
- Produces:
  - `git_helper.fetch_branch(cwd, remote: str, branch: str) -> subprocess.CompletedProcess`
  - `LinkedBranchRequest(ref: IssueRef, branch: str, base_ref: str, remote: str)`
  - `LinkedBranch(ok: bool, created: bool, branch: str, head: str, notes: list[str])`
  - `issues.develop(tree, config: IssuesConfig, request: LinkedBranchRequest) -> LinkedBranch`
  - `IssuesConfig.link_branch: bool`, `IssuesConfig.develop_command: list[str]`

**Background the implementer needs:** GitHub's `createLinkedBranch` mutation **creates** a ref (`oid` is documented as "the commit SHA to base the NEW branch on"). There is no API that links a branch which already exists, so the forge has to make it first and the worktree is cut from what it made. `gh issue develop` prints the new branch's URL on stdout (`https://github.com/<owner>/<repo>/tree/<branch>`), and reuses a branch of the same name only when that branch is **already linked** to the issue — which makes a rerun idempotent.

- [ ] **Step 1: Write the failing test**

Append to `skills/sssf/tests/test_issues_link.py`:

```python
from adw_modules import git_helper
from adw_modules.data_types import LinkedBranchRequest


@pytest.fixture
def forge(monkeypatch):
    """Record `gh` calls and `git fetch`es without running either."""
    # `local_branches` is what `branch_exists` answers from — it has to
    # distinguish the BASE branch (which exists, so `--base` is passed) from the
    # RUN's branch (which usually does not, so the fetch happens).
    state = {"gh": _completed(), "fetch": _completed(),
             "local_branches": {"main"},
             "head": "0" * 40, "calls": [], "fetched": []}

    def fake_run(argv, cwd):
        state["calls"].append(argv)
        return state["gh"]

    def fake_fetch(cwd, remote, branch):
        state["fetched"].append((remote, branch))
        return state["fetch"]

    monkeypatch.setattr(issues, "_run", fake_run)
    monkeypatch.setattr(issues.git_helper, "fetch_branch", fake_fetch)
    monkeypatch.setattr(issues.git_helper, "branch_exists",
                        lambda cwd, name: name in state["local_branches"])
    monkeypatch.setattr(issues.git_helper, "rev", lambda cwd, ref="HEAD": state["head"])
    return state


def _request(branch="sssf/a1b2c3d4-42-fix-rounding", base_ref="main"):
    return LinkedBranchRequest(ref=IssueRef(number=42, project="acme/widgets"),
                               branch=branch, base_ref=base_ref, remote="origin")


def test_develop_creates_fetches_and_reports_the_head(tmp_path, forge):
    forge["gh"] = _completed(
        stdout="https://github.com/acme/widgets/tree/sssf/a1b2c3d4-42-fix-rounding\n")
    forge["head"] = "abc123" + "0" * 34

    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())

    assert result.ok is True
    assert result.created is True
    assert result.branch == "sssf/a1b2c3d4-42-fix-rounding"
    assert result.head == "abc123" + "0" * 34
    assert forge["calls"][0] == [
        "gh", "issue", "develop", "42", "--repo", "acme/widgets",
        "--name", "sssf/a1b2c3d4-42-fix-rounding", "--base", "main"]
    assert forge["fetched"] == [("origin", "sssf/a1b2c3d4-42-fix-rounding")]


def test_develop_omits_base_when_it_is_not_a_branch_name(tmp_path, forge):
    issues.develop(tmp_path, IssuesConfig(project="acme/widgets"),
                   _request(base_ref="3f9a1c2d"))
    assert "--base" not in forge["calls"][0]


def test_develop_skips_the_fetch_when_the_branch_is_already_local(tmp_path, forge):
    """A rerun: gh reuses the linked branch, and a fetch would be rejected as a
    non-fast-forward the moment the earlier run committed anything."""
    forge["local_branches"].add("sssf/a1b2c3d4-42-fix-rounding")
    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())
    assert result.ok is True
    assert forge["fetched"] == []


def test_develop_failing_reports_nothing_created(tmp_path, forge):
    forge["gh"] = _completed(stderr="could not create linked branch", returncode=1)
    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())
    assert result.ok is False
    assert result.created is False
    assert "could not create linked branch" in " ".join(result.notes)


def test_develop_reports_a_created_branch_it_could_not_fetch(tmp_path, forge):
    """The one state a caller must treat differently: the remote branch EXISTS.
    Reusing its name locally would diverge from it and be rejected at push."""
    forge["gh"] = _completed(
        stdout="https://github.com/acme/widgets/tree/sssf/a1b2c3d4-42-fix-rounding\n")
    forge["fetch"] = _completed(stderr="could not read from remote", returncode=1)

    result = issues.develop(tmp_path, IssuesConfig(project="acme/widgets"), _request())

    assert result.ok is False
    assert result.created is True
    assert result.branch == "sssf/a1b2c3d4-42-fix-rounding"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests/test_issues_link.py -q
```

Expected: FAIL — `ImportError: cannot import name 'LinkedBranchRequest'`.

- [ ] **Step 3: Add the git plumbing**

In `git_helper.py`, at the end of the integration section (after `remote_tip`):

```python
def fetch_branch(cwd: Pathish, remote: str, branch: str) -> subprocess.CompletedProcess:
    """Bring a branch the REMOTE owns into this repository under the same name.

    `git fetch <remote> <branch>:<branch>` creates the local ref without checking
    it out, which is what a worktree needs — it is handed a branch, not a tree.
    Returns the completed process: a rejected fetch is data, exactly as a
    rejected push is.
    """
    return _ask_git(cwd, "fetch", remote, f"{branch}:{branch}")
```

- [ ] **Step 4: Add the types**

In `data_types.py`, after `IssueBrief`:

```python
class LinkedBranchRequest(BaseModel):
    """Everything `issues.develop()` needs. One object, never loose params."""

    ref: IssueRef
    branch: str                     # the name to ask the forge for
    base_ref: str = ""              # "" or a non-branch ref = let the forge choose
    remote: str = "origin"          # where the created branch is fetched from


class LinkedBranch(BaseModel):
    """What the forge did about a branch for an issue. Evidence, not a claim.

    `created` is separate from `ok` and it matters: a run that created the remote
    branch but could not fetch it must NOT fall back to a local branch of the
    same name — the two would diverge, and the divergence would surface as a
    rejected push at integrate time, hours later.
    """

    ok: bool = False
    created: bool = False
    branch: str = ""
    head: str = ""                  # sha of the fetched tip, "" when not fetched
    notes: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Add the config keys**

In `data_types.py`, class `IssuesConfig`, after `state_command`:

```python
    # The Development panel on an issue, which is the only thing that says "a
    # run has this" BEFORE a pull request exists. `gh issue develop` is the only
    # way to write it: GitHub's createLinkedBranch mutation CREATES a ref
    # ("the commit SHA to base the NEW branch on"), and no public API links one
    # that already exists — so the forge makes the branch and the worktree is cut
    # from what it made. Off, or unreachable, and the run keeps a local branch.
    link_branch: bool = True
    develop_command: list[str] = Field(default_factory=lambda: ["gh", "issue", "develop"])
```

- [ ] **Step 6: Add `develop()`**

In `issues.py`, after `peek()`:

```python
def develop(tree, config: IssuesConfig, request: LinkedBranchRequest) -> LinkedBranch:
    """Have the FORGE create the run's branch, linked to the issue, then fetch it.

    Order is forced by the API, not chosen: `createLinkedBranch` creates a ref
    and nothing links an existing one, so a branch cut locally first can never be
    linked afterwards.

    Idempotent for a rerun: `gh issue develop --name X` reuses X when X is
    already linked to this issue, and the fetch is skipped when the branch is
    already here — a local branch that has moved ahead would reject the fetch as
    a non-fast-forward, and losing that branch is the one outcome worth avoiding.

    NEVER RAISES. Every failure is a note plus `ok=False`.
    """
    result = LinkedBranch(branch=request.branch)
    project = request.ref.project or resolve_project(config, tree)
    argv = _aim([*config.develop_command], project, request.ref.number)
    argv += ["--name", request.branch]
    # `--base` names a branch AT THE FORGE. A pinned sha or a detached base has
    # no name there, so the flag is dropped and the repository default is used —
    # noted rather than failed, because a linked branch off the default base is
    # still a linked branch.
    if request.base_ref and git_helper.branch_exists(tree, request.base_ref):
        argv += ["--base", request.base_ref]
    else:
        result.notes.append(f"base {request.base_ref or '(none)'} is not a branch "
                            f"name — the forge's default base was used")

    completed = _run(argv, tree)
    if completed.returncode != 0:
        result.notes.append(f"`{' '.join(config.develop_command)}` failed: "
                            f"{(completed.stderr or completed.stdout).strip()[-300:]}")
        return result
    result.created = True
    result.branch = _branch_of(completed.stdout) or request.branch

    if git_helper.branch_exists(tree, result.branch):
        result.ok = True
        result.head = git_helper.rev(tree, result.branch)
        result.notes.append(f"{result.branch} is linked to #{request.ref.number}")
        return result

    fetched = git_helper.fetch_branch(tree, request.remote, result.branch)
    if fetched.returncode != 0:
        result.notes.append(
            f"{result.branch} was created at the forge but could not be fetched: "
            f"{fetched.stderr.strip()[-300:]}")
        return result
    result.ok = True
    result.head = git_helper.rev(tree, result.branch)
    result.notes.append(f"{result.branch} is linked to #{request.ref.number}")
    return result


def _branch_of(output: str) -> str:
    """The branch name out of the tree url `gh issue develop` prints.

    Taken from the forge's answer rather than from what was asked for, because
    the forge sanitises names and reuses an existing linked branch under whatever
    IT calls that branch. "" when there is no url to read, and the caller keeps
    the name it asked for.
    """
    for word in output.split():
        marker = "/tree/"
        if word.startswith("http") and marker in word:
            return word.split(marker, 1)[1].strip()
    return ""
```

Add `LinkedBranch` and `LinkedBranchRequest` to the `from .data_types import (...)` list.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add skills/sssf/tests/test_issues_link.py \
        skills/sssf/templates/adws/adw_modules/{issues,git_helper,data_types}.py
git commit -m "issues: develop() has the forge create the run's linked branch"
```

---

### Task 5: `branches.plan()` — the decision

**Files:**
- Create: `skills/sssf/tests/test_branch_plan.py`
- Modify: `skills/sssf/templates/adws/adw_modules/data_types.py`
- Modify: `skills/sssf/templates/adws/adw_modules/branches.py`

**Interfaces:**
- Consumes: `branches.branch_for`/`issue_slug`/`slugify` (Task 1), `worktree.recorded_branch` (Task 2), `issues.peek` (Task 3), `issues.develop` (Task 4), `git_helper.has_remote` (existing).
- Produces:
  - `BranchRequest(main_root: Path, adw_id: str, prompt: str, issue: Optional[IssueRef])`
  - `BranchPlan(branch: str, base_commit: str, linked: bool, notes: list[str])`
  - `branches.plan(cfg: SSSFConfig, request: BranchRequest) -> BranchPlan`

- [ ] **Step 1: Write the failing test**

`skills/sssf/tests/test_branch_plan.py`:

```python
import pytest

from adw_modules import branches
from adw_modules.data_types import (BranchRequest, IssueBrief, IssueRef, LinkedBranch,
                                    SSSFConfig)


@pytest.fixture
def cfg():
    config = SSSFConfig()
    config.issues.project = "acme/widgets"
    return config


@pytest.fixture
def forge(monkeypatch):
    """No forge and no git: every outward call is stubbed and recorded."""
    # The title is deliberately short enough that `issue_slug` does not truncate
    # it: the locally computed name and the forge's answer are then the SAME
    # string, so a test can tell "the forge's name won" from "nothing happened"
    # only by what it asserts, not by accident of length.
    state = {"brief": IssueBrief(ok=True, number=42, title="floorEuro rounds down"),
             "linked": LinkedBranch(ok=True, created=True, head="abc1234",
                                    branch="sssf/a1b2c3d4-42-flooreuro-rounds-down"),
             "recorded": "", "remote": True, "develop_calls": []}

    monkeypatch.setattr(branches.issues, "peek",
                        lambda tree, config, ref: state["brief"])
    # `_base_ref_of` asks the checkout what it has checked out, and tmp_path is
    # not a git repository — without this every linking test dies in git_helper.
    monkeypatch.setattr(branches.git_helper, "current_branch",
                        lambda main_root: "main")

    def fake_develop(tree, config, request):
        state["develop_calls"].append(request)
        return state["linked"]

    monkeypatch.setattr(branches.issues, "develop", fake_develop)
    monkeypatch.setattr(branches.worktree, "recorded_branch",
                        lambda main_root, config, adw_id: state["recorded"])
    monkeypatch.setattr(branches.git_helper, "has_remote",
                        lambda cwd, remote: state["remote"])
    return state


def test_a_prompt_run_is_named_after_its_prompt(cfg, forge, tmp_path):
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="7b3c9a01",
        prompt="Add tenant export to CSV\n\nlonger body here"))

    assert plan.branch == "sssf/7b3c9a01-add-tenant-export-to-csv"
    assert plan.linked is False
    assert forge["develop_calls"] == []


def test_an_issue_run_is_named_and_linked(cfg, forge, tmp_path):
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4-42-flooreuro-rounds-down"
    assert plan.base_commit == "abc1234"
    assert plan.linked is True
    assert len(forge["develop_calls"]) == 1
    assert forge["develop_calls"][0].branch.startswith("sssf/a1b2c3d4-42-")


def test_link_branch_false_names_but_does_not_link(cfg, forge, tmp_path):
    cfg.issues.link_branch = False
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch.startswith("sssf/a1b2c3d4-42-")
    assert plan.linked is False
    assert plan.base_commit == ""
    assert forge["develop_calls"] == []


def test_a_failed_link_keeps_the_slugged_local_name(cfg, forge, tmp_path):
    forge["linked"] = LinkedBranch(ok=False, created=False,
                                   notes=["gh: not authenticated"])
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4-42-flooreuro-rounds-down"
    assert plan.linked is False
    assert "not authenticated" in " ".join(plan.notes)


def test_a_created_but_unfetchable_branch_falls_back_to_the_bare_name(cfg, forge,
                                                                     tmp_path):
    """The remote branch exists; reusing its name locally would diverge from it."""
    forge["linked"] = LinkedBranch(ok=False, created=True,
                                   branch="sssf/a1b2c3d4-42-flooreuro-rounds-down",
                                   notes=["could not be fetched"])
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == "sssf/a1b2c3d4"
    assert plan.linked is False


def test_a_recorded_branch_wins_over_everything(cfg, forge, tmp_path):
    forge["recorded"] = "sssf/a1b2c3d4-42-the-original-name"
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", prompt="something else entirely"))

    assert plan.branch == "sssf/a1b2c3d4-42-the-original-name"
    assert forge["develop_calls"] == []


def test_no_remote_means_no_forge_call(cfg, forge, tmp_path):
    forge["remote"] = False
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.linked is False
    assert forge["develop_calls"] == []


def test_worktrees_disabled_skips_the_whole_thing(cfg, forge, tmp_path):
    cfg.worktree.enabled = False
    plan = branches.plan(cfg, BranchRequest(
        main_root=tmp_path, adw_id="a1b2c3d4", issue=IssueRef(number=42)))

    assert plan.branch == ""
    assert forge["develop_calls"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests/test_branch_plan.py -q
```

Expected: FAIL — `ImportError: cannot import name 'BranchRequest'`.

- [ ] **Step 3: Add the types**

In `data_types.py`, immediately after `class LinkedBranch` (Task 4) — **not** beside `WorktreeRequest`. `BranchRequest` annotates a field as `Optional[IssueRef]`, and pydantic resolves annotations when the class is created, so it must come *after* `IssueRef` (line ~670) even though `from __future__ import annotations` is in force. Placing it at line ~608 raises `NameError: name 'IssueRef' is not defined` at import.

```python
class BranchRequest(BaseModel):
    """What `branches.plan()` has to work with. One object, never loose params."""

    main_root: Path
    adw_id: str
    prompt: str = ""                # already resolved: a path became its contents
    issue: Optional[IssueRef] = None


class BranchPlan(BaseModel):
    """The name the run's branch will have, and how it came to have it.

    `base_commit` is set only when the FORGE created the branch — that is the one
    case where the branch point is not derivable from the local checkout. It is
    the honest branch point for a forge-created branch: not something every diff
    in the run measures from, but the value `integration.py` compares HEAD
    against to decide a branch has nothing to land.
    """

    branch: str = ""
    base_commit: str = ""
    linked: bool = False
    notes: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add `plan()`**

Append to `branches.py`, and extend its imports:

```python
from pathlib import Path

from . import git_helper, issues, worktree
from .data_types import (BranchPlan, BranchRequest, LinkedBranchRequest, SSSFConfig,
                         WorktreeConfig)
```

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
```

Expected: all PASS.

- [ ] **Step 6: Check for an import cycle**

`branches` imports `worktree`, and `worktree` imports `branches` (Task 2, Step 5). Python tolerates this only in one direction of module-level use, and `worktree` uses `branches.session_of` inside a function body, so it resolves. Verify rather than assume:

```bash
uv run --with pydantic --with python-dotenv python -c \
  "import sys; sys.path.insert(0, 'skills/sssf/templates/adws'); \
   import adw_modules.worktree, adw_modules.branches, adw_modules.session; print('ok')"
```

Expected: `ok`. If it reports a circular import, move `worktree`'s import inside `inventory()` — a local import in the one function that needs it — and re-run.

- [ ] **Step 7: Commit**

```bash
git add skills/sssf/tests/test_branch_plan.py \
        skills/sssf/templates/adws/adw_modules/{branches,data_types}.py
git commit -m "branches: plan() decides the run's branch and links it to its issue"
```

---

### Task 6: Wire it into every ADW

**Files:**
- Modify: `skills/sssf/templates/adws/adw_modules/session.py` (`ensure`, lines 55–89)
- Modify: 12 prompt ADWs — `adw_build.py`, `adw_build_review.py`, `adw_build_test.py`, `adw_document.py`, `adw_plan.py`, `adw_plan_build.py`, `adw_plan_build_test.py`, `adw_plan_build_test_quality.py`, `adw_prompt.py`, `adw_quality.py`, `adw_scout.py`, `adw_simple_sdlc.py`
- Modify: 2 issue ADWs — `adw_issue_sdlc.py`, `adw_issue_scout.py`
- Modify: `adw_pr_review.py` (delete `_session_of`, lines 57–59)

**Interfaces:**
- Consumes: `branches.plan`, `BranchRequest`, `BranchPlan` (Task 5); `WorktreeRequest.branch`/`.base_commit` (Task 2); `branches.session_of` (Task 1).
- Produces: `session.ensure(cfg, adw_id=None, *, prompt: str = "", issue: IssueRef | None = None) -> Run`

- [ ] **Step 1: Change `session.ensure()`**

In `session.py`, replace the head of `ensure()` (lines 55–63) with:

```python
def ensure(cfg: SSSFConfig, adw_id: str | None = None, *, prompt: str = "",
           issue: IssueRef | None = None) -> Run:
    adw_id = adw_id or new_id(8)
    main_root = git_helper.main_root()          # the engineer's checkout, always
    # WHAT THE BRANCH IS CALLED is decided before the worktree exists, because
    # the worktree is cut from it. An issue run's branch is created AT THE FORGE
    # here — the only order in which it can be linked to the issue at all — and
    # everything that can fail about that degrades to a local branch and a note.
    plan = branches.plan(cfg, BranchRequest(main_root=main_root, adw_id=adw_id,
                                            prompt=prompt, issue=issue))
    workspace = worktree.ensure(WorktreeRequest(main_root=main_root, adw_id=adw_id,
                                                config=cfg.worktree,
                                                branch=plan.branch,
                                                base_commit=plan.base_commit))
```

Change the imports at the top of `session.py`:

```python
from . import branches, git_helper, worktree
from .data_types import BranchRequest, IssueRef, RunSpec, SSSFConfig, WorktreeRequest
```

and the console line at the end of `ensure()` (line 79):

```python
    run.console.note(_workspace_line(workspace, plan))
```

with `_workspace_line` taking the plan:

```python
def _workspace_line(workspace, plan) -> str:
    """The one line that says where this run's work will actually land.

    The plan's notes ride along on it rather than getting a line of their own:
    "linked to issue #42" and "not linked, because…" are both facts about this
    branch, and the branch is what this line is for.
    """
    if not workspace.enabled:
        return f"workspace: {workspace.repo_root} (no worktree — running in place)"
    verb = "joined" if workspace.joined else "created"
    line = (f"workspace: {verb} {workspace.repo_root} on {workspace.branch} "
            f"from {workspace.base_ref} @ {workspace.base_commit[:7]}")
    return f"{line} · {' · '.join(plan.notes)}" if plan.notes else line
```

- [ ] **Step 2: Wire the 12 prompt ADWs**

In each of the twelve files listed above, the call is on the line after `agents.validate(...)`. Change:

```python
    run = session.ensure(cfg, adw_id)
```

to:

```python
    run = session.ensure(cfg, adw_id, prompt=prompt)
```

`adw_document.py` and `adw_prompt.py` have extra parameters in `main()` but the same `prompt` variable and the same call line — no other difference.

- [ ] **Step 3: Wire the 2 issue ADWs**

In `adw_issue_sdlc.py` (line 66) and `adw_issue_scout.py` (line 35):

```python
    run = session.ensure(cfg, adw_id, issue=IssueRef(number=number))
```

`IssueRef` is already imported in both files (they build one for `issues.fetch`). Leave `adw_integrate.py` and `adw_pr_review.py` calling `session.ensure(cfg, adw_id)` unchanged: they join a session that already has a name, and passing a prompt would let a second process rename the first process's branch.

- [ ] **Step 4: Correct the `adw_issue_sdlc.py` docstring**

Its module docstring currently ends with a claim that stopped being true when the worktree moved into `session.ensure()`. Replace that paragraph:

```
The issue phase runs FIRST, before the worktree has been touched and before any
agent is spawned, so an untrusted author or an unreadable issue costs nothing.
```

with:

```
The issue phase runs before any agent is spawned, so an untrusted author or an
unreadable issue costs no model calls. It does not run before the WORKTREE: the
branch is cut in `session.ensure()`, which now also reads the issue's title to
name it and asks the forge to link it. An untrusted author therefore costs one
`gh issue view`, one branch and one empty worktree — and nothing else.
```

- [ ] **Step 5: Delete the duplicate parser in `adw_pr_review.py`**

Remove `_session_of` (lines 57–59) entirely, add `branches` to the `from adw_modules import (...)` list, and change its one call site (line 79):

```python
    resolved = branches.session_of(context.branch, cfg.worktree.branch_prefix)
```

- [ ] **Step 6: Verify every call site was updated**

```bash
cd skills/sssf/templates/adws
grep -rn "session.ensure(" adw_*.py
```

Expected exactly 16 lines: 12 with `prompt=prompt`, 2 with `issue=IssueRef(number=number)`, and `adw_integrate.py` / `adw_pr_review.py` with `session.ensure(cfg, adw_id)`.

```bash
grep -rn "_session_of" adw_*.py
```

Expected: no output.

- [ ] **Step 7: Verify everything still imports and compiles**

```bash
cd "$(git rev-parse --show-toplevel)"
uv run --with pydantic --with python-dotenv --with pyyaml --with rich python -c \
  "import sys; sys.path.insert(0, 'skills/sssf/templates/adws'); \
   import adw_modules.session, adw_modules.branches, adw_modules.issues, \
          adw_modules.worktree; print('ok')"
python3 -m compileall -q skills/sssf/templates/adws && echo "compiled"
uv run --with pytest --with pydantic --with python-dotenv pytest skills/sssf/tests -q
```

Expected: `ok`, `compiled`, and all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add skills/sssf/templates/adws
git commit -m "session: name the run's branch from its issue or prompt, and link it"
```

---

### Task 7: Configuration template and documentation

**Files:**
- Modify: `skills/sssf/templates/config/base.yaml` (`worktree:` block line ~30, `issues:` block line ~70)
- Modify: `skills/sssf/references/config.md` (`### worktree` table line ~120, `### issues` table line ~161)
- Modify: `skills/sssf/cookbooks/create_config.md` (the duplicated `worktree:`/`issues:` blocks, line ~45 onward)

**Interfaces:**
- Consumes: `WorktreeConfig.branch_slug` (Task 1), `IssuesConfig.link_branch` / `.develop_command` (Task 4). No code changes here — defaults already carry the behaviour, so a repository that never edits its config gets the feature.

- [ ] **Step 1: Add the keys to `templates/config/base.yaml`**

In the `worktree:` block, after `branch_prefix`:

```yaml
  branch_prefix: "sssf/"           # the run's branch is <prefix><adw_id>[-<slug>]
  # A branch named only for its run says nothing about what it was for. On, the
  # run's issue title (or its prompt's first line) is slugged onto the end:
  #   sssf/a1b2c3d4-42-floor-euro-rounds-down
  # The adw_id stays FIRST so everything that reads a branch back — the review
  # chain, `just worktrees` — still finds it. false restores <prefix><adw_id>.
  branch_slug: true
```

In the `issues:` block, after `state_command`:

```yaml
  # The Development panel on an issue — the only thing that says "a run has
  # this" BEFORE a pull request exists. GitHub creates linked branches and never
  # links existing ones, so an issue-triggered run's branch is created HERE, by
  # the forge, and the worktree is cut from it. Anything that stops that (no
  # remote, no auth, a tracker that is not GitHub) leaves the run on a local
  # branch of the same name and says so in the run's first line.
  link_branch: true
  develop_command: ["gh", "issue", "develop"]
```

- [ ] **Step 2: Mirror them in `cookbooks/create_config.md`**

That cookbook carries its own copy of the same two blocks. Apply the identical additions there, comments included — a reader following the cookbook must end up with the file `base.yaml` would have produced.

- [ ] **Step 3: Document them in `references/config.md`**

In the `### worktree` table, after the `branch_prefix` row:

```markdown
| `branch_slug` | bool | Default `true`. Appends a slug of the run's issue title (`sssf/a1b2c3d4-42-floor-euro-rounds-down`) or of its prompt's first line (`sssf/7b3c9a01-add-tenant-export-to-csv`). The adw_id stays the first segment, so `branches.session_of` still recovers it and pre-existing branches still resolve. `false` restores `<prefix><adw_id>`. |
```

In the `### issues` table, after the `fetch_command, …` row:

```markdown
| `link_branch` | bool | Default `true`. An issue-triggered run's branch is created by `gh issue develop`, so it appears in the issue's **Development** panel while the run is still going — not only once a pull request exists. GitHub's API creates linked branches and cannot link an existing one, which is why the forge makes the branch and the worktree is cut from it. Any failure (no remote, no auth, a token without the `createLinkedBranch` permission, a tracker that is not GitHub) leaves the run on an ordinary local branch and notes why. |
| `develop_command` | list[string] | The forge CLI for the above, aimed with `--repo <project>` like every other command here. Default `["gh", "issue", "develop"]`. |
```

Then add this paragraph directly under the `issues` table's existing trust-boundary list:

```markdown
**The two links are not the same link.** `Closes #<n>` in a pull request body (see `pr_body_template`) ties the *pull request* to the issue and closes it on merge; it says nothing until the pull request exists. `link_branch` ties the *branch* to the issue at the moment the run starts. A repository wants both, and gets both by default.
```

- [ ] **Step 4: Verify the documented defaults match the code**

```bash
cd "$(git rev-parse --show-toplevel)"
uv run --with pydantic --with python-dotenv python -c \
  "import sys; sys.path.insert(0, 'skills/sssf/templates/adws'); \
   from adw_modules.data_types import WorktreeConfig, IssuesConfig; \
   print(WorktreeConfig().branch_slug, IssuesConfig().link_branch, \
         IssuesConfig().develop_command)"
```

Expected: `True True ['gh', 'issue', 'develop']` — matching every default written in Steps 1–3.

- [ ] **Step 5: Commit**

```bash
git add skills/sssf/templates/config/base.yaml skills/sssf/references/config.md \
        skills/sssf/cookbooks/create_config.md
git commit -m "config: document branch_slug, link_branch and develop_command"
```

---

### Task 8: End-to-end in a stamped repository

**Files:**
- Modify: `/Users/alexander.buss/Developer/sidehustle/projects/vermietung/property-rent/adws/adw_modules/*.py` and `adws/adw_*.py` (the stamped copies)
- Modify: `docs/phase-8-linked-branches.md` (add the *As built* section)

**Interfaces:**
- Consumes: everything above. Produces: nothing new — this task is verification against a real forge, which no unit test can supply.

**Why by hand rather than `install.py --force`:** `stamp()` skips files that already exist unless `--force`, and `--force` would also overwrite `property-rent`'s customised `sssf.config.yaml` and `justfile`. The changed files are copied deliberately instead. No config edit is needed there: the new keys default to on.

- [ ] **Step 1: Copy the changed modules into the stamped repo**

```bash
SRC=/Users/alexander.buss/Developer/sidehustle/projects/software-factory/skills/sssf/templates/adws
DST=/Users/alexander.buss/Developer/sidehustle/projects/vermietung/property-rent/adws
cp "$SRC"/adw_modules/{branches,data_types,git_helper,issues,session,worktree}.py "$DST"/adw_modules/
cp "$SRC"/adw_*.py "$DST"/
cd "$(dirname "$DST")" && git status --short adws/
```

Expected: the six modules plus the ADW scripts show as modified; nothing else.

- [ ] **Step 2: Confirm the stamped copy imports**

```bash
cd /Users/alexander.buss/Developer/sidehustle/projects/vermietung/property-rent
uv run --with pydantic --with python-dotenv --with pyyaml --with rich python -c \
  "import sys; sys.path.insert(0, 'adws'); import adw_modules.session; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Prove a prompt run gets a slugged branch, without touching the forge**

```bash
cd /Users/alexander.buss/Developer/sidehustle/projects/vermietung/property-rent
uv run adws/adw_scout.py "Check how the euro formatter rounds negative amounts"
```

Expected: the run's first line reads
`workspace: created .sssf-worktrees/<id> on sssf/<id>-check-how-the-euro-formatter-rounds …`

Then confirm the inverse still works:

```bash
just worktrees
```

Expected: the worktree is listed with the **adw_id**, not with the slug.

- [ ] **Step 4: Prove an issue run gets a linked branch**

Open a throwaway issue and route it through the cheapest chain:

```bash
cd /Users/alexander.buss/Developer/sidehustle/projects/vermietung/property-rent
gh issue create --title "Phase 8 smoke test — ignore" \
  --body "Test issue for verifying linked branches. No change is expected."
# note the number it prints, then:
uv run adws/adw_issue_scout.py <number>
```

Expected, in order:

1. The run's first line names a branch shaped `sssf/<id>-<number>-phase-8-smoke-test-ignore` and ends with `· sssf/… is linked to #<number>`.
2. `gh issue view <number> --web` shows the branch under **Development**.
3. `git -C . branch --list 'sssf/*'` shows the branch locally, and `git rev-parse sssf/<…>` matches `git rev-parse origin/main`.

- [ ] **Step 5: Prove the fallback is not a failure**

```bash
uv run adws/adw_issue_scout.py <number> --adw-id fallback1 2>&1 | head -5
```

…after temporarily setting `link_branch: false` under `issues:` in `adws/adw_sssf_config/sssf.config.yaml`.

Expected: the run finishes accepted, the branch is still slugged, and no Development entry appears for that second branch. Restore the config afterwards.

- [ ] **Step 6: Prove the pruned-worktree path**

```bash
uv run adws/adw_integrate.py <the adw_id from Step 4>
```

Expected: it integrates the **slugged** branch — not a freshly cut `sssf/<id>`. Confirm with `git log --oneline -1 sssf/<slugged-name>`.

- [ ] **Step 7: Close the smoke-test issue and delete its branches**

```bash
gh issue close <number> --comment "Phase 8 verification complete."
git push origin --delete sssf/<slugged-name>
git branch -D sssf/<slugged-name>
```

- [ ] **Step 8: Record what actually happened**

Add an `## As built` section to `docs/phase-8-linked-branches.md`, in the style Phases 1, 2, 5, 6 and 7 use: what was built as designed, and every place reality differed from the design. If nothing differed, say so in one sentence — but check the open questions in the spec first (the base-commit behaviour and the extra `gh` call are the two most likely to have surprised).

Then flip the roadmap row:

```bash
cd /Users/alexander.buss/Developer/sidehustle/projects/software-factory
# docs/README.md, phases table, row 8: "not started" → "**built** — see its *As built* section"
```

- [ ] **Step 9: Commit both repositories**

```bash
cd /Users/alexander.buss/Developer/sidehustle/projects/software-factory
git add docs/phase-8-linked-branches.md docs/README.md
git commit -m "docs: phase 8 as built"

cd /Users/alexander.buss/Developer/sidehustle/projects/vermietung/property-rent
git add adws/
git commit -m "sssf: linked branches and issue-titled branch names"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 `branches.py` — one owner for the format | 1 (pure functions), 5 (`plan`), 2 + 6 (the two parsers deleted) |
| §1 slug sources, first-line rule | 1 (`slugify`, `issue_slug`), 5 (which source per trigger) |
| §2 `issues.peek()` | 3 |
| §2 `issues.develop()`, `--base` caveat | 4 |
| §2 three config keys | 1 (`branch_slug`), 4 (`link_branch`, `develop_command`), 7 (template + docs) |
| §3 `session.ensure()` signature, 14 call sites | 6 |
| §3 recorded-branch-wins, fallback matrix | 2 (the record), 5 (the fallbacks) |
| §4 `worktree.py` stays forge-free, `base_commit` pinned | 2 |
| §5 what the operator sees | 6 (Step 1, `_workspace_line`) |
| Verification: round trip | 1 (`test_session_of_round_trips_every_branch_for`) |
| Verification: end to end, fallback, recompute bug, no migration | 8; plus 2 (`test_a_pruned_worktree_reattaches…`) and 1 (old-format branch) as unit tests |
| Risk: created-but-unfetchable divergence | 4 (`created` flag), 5 (bare-name fallback) |

**Type consistency:** `BranchRequest`/`BranchPlan` (Task 5) are used with those exact field names in Task 6. `LinkedBranchRequest.ref/branch/base_ref/remote` and `LinkedBranch.ok/created/branch/head/notes` (Task 4) are read with those names in Task 5's `plan()`. `WorktreeRequest.branch/base_commit` (Task 2) are passed with those names in Task 6. `worktree.recorded_branch(main_root, config, adw_id)` (Task 2) is called with that arity in Task 5.

**Not covered, deliberately:** the spec's open question about moving `issues.trusted()` earlier now that `peek()` returns the author. It is a separate change to the trust boundary and gets its own item; `IssueBrief.author` exists so that item is a small one.
