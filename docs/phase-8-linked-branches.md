# Phase 8 — The branch names its issue, and the issue knows its branch

## Goal

Two halves of one change:

- An issue-triggered run's branch shows up in the issue's **Development** panel the moment the run starts — before any commit, any agent, any pull request.
- Every run's branch says **what it is about**, not only which run it was: `sssf/a1b2c3d4-42-floor-euro-rounding` instead of `sssf/a1b2c3d4`.

## Why now

It depends on **[Phase 2](phase-2-worktree-per-run.md)** (the branch per run) and **[Phase 5](phase-5-issue-tracking.md)** (the run that comes from an issue), both built. It is small, and the cost of not having it is paid every time you read `git branch`, open `just worktrees`, or look at an issue and cannot tell whether anything picked it up.

There is also a latent bug in the current branch handling that a slug would turn from harmless into destructive. See *A name that is recomputed rather than remembered* below.

## Current state

### One of the two links exists

| Link | How it is made | Today |
|---|---|---|
| **Pull request → issue** | `Closes #<n>` in the PR body | Built. `integration._pr_body()` appends it for every issue-triggered run, ahead of `pr_body_template`, so the forge closes the issue on merge. |
| **Branch → issue** — the Development panel, which is what tells you an issue is being worked *before* there is anything to review | GraphQL `createLinkedBranch`, i.e. `gh issue develop` | Nothing. |

The missing half is the one that matters while a run is in flight. Between the watcher claiming an issue and the pull request appearing, the issue carries a `sssf:running` label and nothing else — no branch, no way to get from the issue to the work.

### The forge will not link a branch after the fact

Introspecting the mutation against a live account:

```
createLinkedBranch(issueId!, oid!, name, repositoryId)
  oid  — "The commit SHA to base the NEW branch on"
  name — "The name of the NEW branch. Defaults to issue number and title."
```

It **creates**. There is no public mutation that links a ref which already exists — the web UI has been able to since [September 2022](https://github.blog/changelog/2022-09-07-link-existing-branches-to-an-issue/), the API has not. `gh issue develop --name X` reuses a branch only when that branch is *already linked*; otherwise it calls the mutation, which fails on a name that already has a ref ([cli/cli#7854](https://github.com/cli/cli/issues/7854)).

That single fact decides the shape of this phase. The factory's present order — cut `sssf/<adw_id>` locally, push it at integrate time — can never be linked retroactively. **For an issue run, the forge has to create the branch first**, and the worktree is cut from what it made.

Worth noting for the second half of the goal: that `name` default is `<number>-<title-slug>`, which is the naming convention this phase adopts anyway.

### The name is written in one place and parsed in two

```
worktree.py:84            branch = f"{config.branch_prefix}{request.adw_id}"     ← written
worktree.py:209           branch.removeprefix(config.branch_prefix)              ← parsed
adw_pr_review.py:57-59    _session_of(branch, prefix)                            ← parsed
```

The two parsers do not know about each other, and `adw_pr_review` **refuses to run** when its answer comes back empty — a pull request whose head branch it cannot decode has no session to join. Appending anything to the name without first giving the format one owner would break the review path silently, in the one place that only fails when a human has already asked for a review.

`pr_watch.py:91` also filters on the prefix, but only on the prefix, so a suffix does not reach it.

### The name is decided before the issue is read

`session.ensure()` builds the worktree and its branch (`session.py:58`) before an ADW has run a single phase. In `adw_issue_sdlc.py` that is line 66; the issue phase that fetches the title is line 92. So at naming time the run knows its `adw_id` and nothing else.

(The same file's docstring claims the issue phase runs "before the worktree has been touched". That has not been true since the worktree moved into `session.ensure()`, and it is corrected here.)

### A name that is recomputed rather than remembered

`worktree.ensure()` re-derives `<prefix><adw_id>` on every entry, and only consults the recorded metadata when the worktree **directory** still exists. A successful run's worktree is pruned by design, so a later process in that session — `just integrate <adw_id>`, `adw_pr_review` joining, a rerun — lands on the branch-exists path and gets the right branch **because the recomputed name happens to match**.

With a slug in the name, it stops matching: the second process would compute a bare `sssf/<adw_id>`, find no such branch, and cut a **new one from the base**, orphaning the run's commits. The fix — read the recorded name first — is worth having on its own, and is a prerequisite here rather than a side quest.

## Design

### 1. `adw_modules/branches.py` — one owner for the format

A small module that knows how a branch name is built and taken apart, and nothing else:

```python
slugify(text, limit=40) -> str            # lowercase, [a-z0-9-], collapsed, cut on a hyphen
branch_for(config, adw_id, slug) -> str   # "sssf/a1b2c3d4-42-floor-euro-rounding"
session_of(branch, prefix) -> str         # "a1b2c3d4"   ← the inverse
plan(cfg, main_root, adw_id, *, prompt, issue) -> BranchPlan
```

`session_of` is `removeprefix(prefix).split("-", 1)[0]`, which is why the **adw_id stays first**: `new_id()` is eight hex characters with no hyphen, so the split is unambiguous, and an old-format branch (`sssf/a1b2c3d4`, no hyphen at all) returns the same answer it always did. `worktree.inventory()` and `adw_pr_review` both delete their local copy and call this one.

`slugify` strips what git refuses in a ref — a leading `-`, a trailing `.`, `..`, `@{`, a `.lock` ending — rather than escaping it, and truncates on a hyphen boundary so the last word is whole or absent.

The slug source is the only thing that differs by trigger:

| Trigger | Slug | Example |
|---|---|---|
| Issue | `<number>-<title>` | `sssf/a1b2c3d4-42-floor-euro-rounds-down` |
| Prompt | the prompt's first non-empty line | `sssf/7b3c9a01-add-tenant-export-to-csv` |
| Neither (`just integrate <id>`, a joined run) | — the recorded name is reused | `sssf/a1b2c3d4-42-floor-euro-rounds-down` |

**First line, not the whole prompt**, because `utils.resolve_prompt()` runs in `__main__` before `main()` does: an ADW invoked as `just plan /tmp/feature.md` is handed the file's *contents*, not its path. Leading markdown heading markers are stripped, so a prompt file that opens with `# Fix the floorEuro rounding` slugs to what it says rather than to `h-fix-the-flooreuro-rounding`.

`plan()` is the only function in the module that talks to anything outside it, and it is the only one that can fail.

### 2. Two more commands on `issues.py`

Both follow the rule that file already states for `fetch_command` and `comment_command`: the forge CLI is installed and authenticated in the engineer's shell, so this is a **command, not an API call**, and it runs under `operator_env()`.

- **`peek(tree, config, ref)`** — `gh issue view <n> --json number,title,url`, aimed with `--repo` like everything else there. It exists because the branch is named before any phase runs, and `fetch()` cannot be reused for it: `fetch()` needs a live `Run` to write the body into `context_handoff/`, and there is no run yet. It **never raises** — a naming nicety must not kill a chain before its first phase.
- **`develop(tree, config, ref, branch, base_ref, remote)`** — `gh issue develop <n> --name <branch> [--base <base_ref>]`, followed by an explicit `git fetch <remote> <branch>:<branch>`. The explicit fetch is deliberate: without `-c`, gh's own fetching is not a contract worth depending on, and the local ref has to exist before `worktree add` can use it. Returns `ok`, the branch name the forge actually created, and notes. It **never raises** either.

`--base` is passed only when the pinned base ref is a plain branch name. A sha or a detached base is not something the forge can branch from by name, so the flag is dropped and GitHub uses the repository default — noted, not failed.

Three new configuration keys, all defaulting to today's behaviour plus the feature on:

```yaml
worktree:
  branch_slug: true            # false restores sssf/<adw_id> exactly
issues:
  link_branch: true            # false skips the forge call entirely
  develop_command: ["gh", "issue", "develop"]
```

### 3. `session.ensure()` is the composition point

```python
def ensure(cfg, adw_id=None, *, prompt="", issue: IssueRef | None = None) -> Run:
```

Every ADW's change is one keyword argument on a line it already has: the twelve prompt chains pass `prompt=prompt`, `adw_issue_sdlc` and `adw_issue_scout` pass `issue=IssueRef(number=number)`, and `adw_integrate` / `adw_pr_review` pass neither — they join a session that already has a name, and passing one would let a second process rename the first's branch.

`branches.plan()` then decides, in this order:

1. **A recorded branch for this adw_id wins.** The worktree metadata file lives *beside* the worktree, not inside it, so it survives pruning — reading it first is what fixes the recompute bug above, and it means a joined run can never be renamed by a different prompt.
2. Otherwise build the slug and the name.
3. If this is an issue run, `issues.link_branch` is on, the worktree is enabled and the remote exists — `issues.develop()`. On success, take the name the forge returned (it may sanitise) and pin `base_commit` to the tip of the fetched branch.
4. On **any** failure — no remote, no auth, a fine-grained token without the `createLinkedBranch` permission ([cli/cli#6562](https://github.com/cli/cli/issues/6562)), a tracker that is not GitHub — keep the local-only branch of the same name and carry a note. The run proceeds. Linking is a convenience; a factory that refuses to work because a sidebar entry could not be written is worse than one without the sidebar entry.

### 4. `worktree.py` stays forge-free

`WorktreeRequest` gains `branch` and `base_commit`, both defaulting to empty, and `ensure()` falls back to `f"{prefix}{adw_id}"` when they are. Nothing else moves: after `develop()` fetched it, the linked branch **already exists locally**, so `ensure()` takes the branch-exists path that has been there since Phase 2 and checks it out into the worktree. No second creation path.

`base_commit` is passed explicitly for one specific reason. On the branch-exists path it is otherwise `merge_base(path, base_ref, "HEAD")`, which for a linked branch answers with the *local* base tip while the branch was actually cut from **origin's**. Every diff in the run — `changes.py`, the reviewer's input, the documenter's input — measures from `base_commit`, so a stale answer there quietly widens all of them by whatever origin was ahead by.

### 5. What the operator sees

The existing workspace line carries the outcome, because it is already the one line that says where a run's work will land:

```
workspace: created .sssf-worktrees/a1b2c3d4 on sssf/a1b2c3d4-42-floor-euro-rounds-down
           from main @ 3f9a1c2 · linked to issue #42
```

and on the fallback path, the reason instead:

```
           from main @ 3f9a1c2 · not linked: gh issue develop failed (…)
```

## Work items

| # | Item | Files |
|---|---|---|
| 1 | The format module | `templates/adws/adw_modules/branches.py` (new) |
| 2 | `peek` + `develop`, and the config keys they read | `templates/adws/adw_modules/issues.py`, `data_types.py` |
| 3 | `WorktreeRequest.branch` / `.base_commit`, metadata read before recompute, `session_of` via `branches` | `templates/adws/adw_modules/worktree.py` |
| 4 | `ensure()` signature and composition | `templates/adws/adw_modules/session.py` |
| 5 | One keyword at fourteen call sites (twelve prompt chains, two issue chains), `_session_of` deleted in favour of `branches.session_of`, `adw_issue_sdlc` docstring corrected | `templates/adws/adw_*.py` |
| 6 | Config defaults and documentation | `templates/config/base.yaml`, `references/config.md`, `cookbooks/create_config.md` |

## Risks and open questions

- **A linked branch is cut from origin's base tip, not the local one.** If the engineer's `main` holds unpushed commits, an issue run will not have them. For issue runs this is arguably the correct base — `issues.force_pr` means the work targets origin regardless — but it is a real behaviour change and the reason `base_commit` is pinned explicitly rather than inferred.
- **One extra `gh` call per issue run.** `peek()` reads the title before the session exists; `fetch()` reads the whole issue again inside the issue phase. Deduplicating them would mean handing the peeked payload through `session.ensure()` into a phase that has not opened yet, which is more coupling than one ~300ms call is worth.
- **Only GitHub gets the link.** `develop_command` is configurable in the same way every other tracker command is, but nothing else in this design pretends to know what a linked branch means on GitLab or Jira. Elsewhere the flag is set to `false` and the slug half still works.
- **`peek()` could move the trust check earlier, and does not.** Now that the author is readable before the worktree exists, `issues.trusted()` could refuse an untrusted author at zero cost instead of after a tree has been created. That is the docstring's original claim and worth doing — as its own item, not smuggled into this one.
- **A slug is a name, not an identifier.** Nothing reads it back. Two runs against the same issue produce two branches whose adw_ids differ and whose slugs match, and that is fine; the adw_id remains the only thing anything parses.

## Verification

- **Round trip.** `session_of(branch_for(cfg, id, slug)) == id`, for a slug, an empty slug, a slug that is entirely punctuation, and an old-format `sssf/<adw_id>` branch.
- **End to end, in a stamped repo.** File a test issue, label it, let the watcher route it to `adw_issue_scout` — the cheapest chain that produces a branch. Then confirm: the issue's Development panel names the branch; the worktree is on `sssf/<id>-<n>-<slug>`; `just worktrees` reports the right adw_id for it; the pull request still carries `Closes #<n>`; and `adw_pr_review` against that pull request resolves the session rather than refusing it.
- **The fallback is not a failure.** The same run with `issues.link_branch: false`, and again with a `develop_command` pointed at something that does not exist: both finish accepted, both say why the branch is not linked.
- **The recompute bug is fixed.** Run a chain to a pruned worktree, then `just integrate <adw_id>` — it must land on the recorded branch, not cut a new one.
- **Nothing needs migrating.** A session started before this phase, on a bare `sssf/<adw_id>`, is still joinable and its pull request still reviewable.

## Done when

An issue you labelled shows the branch working on it while the run is still going, and `git branch` reads like a list of tasks instead of a list of run ids — with every path that cannot reach the forge behaving exactly as it does today.
