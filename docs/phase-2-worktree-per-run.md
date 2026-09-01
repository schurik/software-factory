# Phase 2 — Worktree and branch per run

## Goal

Every run executes in its own git worktree on its own branch. The engineer's working tree is never touched, two runs can execute concurrently without colliding, and a failed run leaves its state somewhere inspectable instead of somewhere in the way.

## Why now

This is the smallest change with the largest safety payoff, it blocks nothing else, and it is the prerequisite for container sandboxing and remote runners later. Upstream is explicit that it does not do this: *"this runs on your current branch. For real work you want a branch per run, a sandbox around the agent, and a merge step at the end."*

Until it lands, an agent chain that goes wrong does so in the tree you are working in, and `commit_all()` (`git_helper.py:43-53`) commits whatever else happened to be staged.

## Current state

### One source of truth, and it is already right

`runner.py:53` sets `self.repo_root = git_helper.repo_root()` once at `Run` construction. Everything that spawns work is supposed to derive from it.

`git_helper.repo_root()` (`git_helper.py:31-40`) returns `git rev-parse --show-toplevel`, resolved — and **inside a worktree that returns the worktree directory**, which is exactly what we want. The building block works.

### The single central blocker

```python
# git_helper.py:9-13
def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    ...
```

**No `cwd=`.** Every one of the module's fourteen functions therefore runs in the ADW process's working directory, not in `run.repo_root`. A run executing in a worktree would still `commit_all()` into the main checkout. This one function is the change that makes the rest possible.

Affected callers: `commit_all`, `rev`, `short_sha`, `merge_base`, `diff_files`, `diff_stat`, `diff_counts`, `diff_text`, `untracked_files`, `is_dirty`, `ref_exists`, `current_branch`, `create_branch`, `is_repo`, `repo_root` — used from `adw_simple_sdlc.py:65,70,80`, `adw_plan_build.py:44`, `adw_plan_build_test.py:74`, `adw_plan_build_test_quality.py:86`, and throughout `changes.py`.

### Already correct — these follow for free

Once `run.repo_root` points at the worktree, these need no change at all:

| Location | What |
|---|---|
| `agents.py:125` → `agent_pi.py:245` | `PiRequest.cwd=str(run.repo_root)` → `Popen(cwd=…)`; the agent already runs where we tell it |
| `quality.py:77` | `subprocess.run(spec.argv, cwd=run.repo_root, env=operator_env())` |
| `permissions.py:59,65` | `snapshot()` runs its git commands in `run.repo_root` |
| `permissions.py:154,159` | Rollback (`unlink`, `git checkout --`) in `run.repo_root` |

The write-boundary enforcement is already worktree-correct. That is a meaningful amount of the risk already handled.

### Also process-cwd-bound, and in scope

| Location | Problem |
|---|---|
| `gates.py:30,38,51,65` | `Path(a)` resolves artifact paths against the **process cwd**. An agent writing `specs/plan.md` inside the worktree would not be found by `artifacts_exist`. |
| `gates.py:101` | `tests_pass` runs `subprocess.run(command, shell=True)` with neither `cwd=` nor `env=operator_env()` |
| `runner.py:54` | `session_dir = Path(cfg.defaults.data_dir) / "sessions" / adw_id` — process-cwd-relative |
| `session.py:40-41` | `Tracer(cfg.observability.db, …)` — process-cwd-relative |
| `agents.py:87` | `context_handoff_dir` is injected into the agent's prompt as a **relative** path |
| `agents.py:121-122` | `.resolve()` absolutises against the process cwd |
| `agents.py:33, 66` | Config path and prompt-file existence checks, process-cwd-relative |

### What does not exist yet

- **No `git worktree` call anywhere** in the skill.
- **No merge or PR phase** in any of the twelve ADWs.
- `git_helper.current_branch()` (`:16-17`) and `create_branch()` (`:20-22`) exist and are **called by nothing**. The building blocks are there, unused.
- `permissions.py:8-11` already warns that a builder with `bash` can run `git checkout` — a worktree does not change that, and the snapshot/rollback enforcement must keep measuring in `run.repo_root`.

## Design

### 1. Create the worktree in `session.ensure()`

```
git worktree add <worktrees_dir>/<adw_id> -b sssf/<adw_id> <base_ref>
```

- `base_ref` is pinned once, at run start, and recorded on the session.
- `Run.repo_root` becomes the worktree path.
- A **joined run** — a second ADW invoked with the same pinned `--adw-id`, which `session.ensure()` already supports and `max_phase_seq()` (`tracer.py:206`) already accounts for — must **re-attach** to the existing worktree, never create a second one. Check for the path first.
- Config gets a `worktrees_dir` key (default something like `.sssf-worktrees/`, outside the repo tree or gitignored — see stamping below).

### 2. The two mandatory code fixes

**(a) `git_helper._git()` takes a `cwd`.** Every function in the module grows an explicit working directory, fed from `run.repo_root`. This is mechanical but touches every caller, so it is worth doing as its own commit with no behaviour change beyond the parameter.

**(b) Gates resolve against `run.repo_root`.** `gates.py` already receives `run` in every gate signature (`gate(envelope, run) -> GateReport`) and currently ignores it. Use it: resolve artifact paths against `run.repo_root`, and run `tests_pass` with `cwd=run.repo_root, env=operator_env()`. The missing `operator_env()` is a pre-existing bug — without it the gate's subprocess inherits the ephemeral `uv run` virtualenv, which is exactly what `utils.operator_env()` (`utils.py:16-38`) exists to prevent.

### 3. The crux decision: where does `data_dir` live?

`data_dir` (default `adws/adw_data`) holds the trace database, the session directories, `context_handoff/`, prompt audit copies and raw agent output. It is currently process-cwd-relative (`runner.py:54`, `session.py:40`). Under worktrees there are two options and they are not equivalent.

**Option A — `data_dir` stays in the main checkout (recommended).**

- One trace database across all concurrent runs. The visualizer keeps working. Cross-run queries stay possible.
- The trace survives worktree removal, which matters because a successful run's worktree gets pruned.
- **Cost:** `context_handoff_dir` now lives outside the agent's working directory, so the relative path injected into the prompt at `agents.py:87` becomes wrong. It must be made **absolute**. Same for anything else the agent is told about by path.
- **Cost:** `permissions.always_writable()` (`permissions.py:110-124`), which exempts `data_dir` from the write boundary, becomes a no-op — `data_dir` is no longer inside the tree being snapshotted. That is harmless but should be documented rather than left as a confusing dead branch.
- With Claude Code as a backend (Phase 1), the agent needs `--add-dir <data_dir>` to be allowed to write its handoff artifacts at all.

**Option B — `data_dir` inside the worktree.**

- Everything stays relative and the prompt path needs no change.
- **But** each worktree gets its own `sssf.db`, which defeats the purpose: no cross-run view, and the trace vanishes when the worktree is pruned.

**Recommendation: Option A.** Enumerate and change every path that depends on it: `runner.py:54,56-58`, `session.py:40-41`, `agents.py:87,121-122`, `quality.py:55-59` (`_check_dir` under `context_handoff_dir`), `changes.py` (the `changes.diff` destination).

### 4. Integration phase

A new phase, `kind="code"`, `owner="git"` — following SKILL.md rule 8, since merging is a known command, not a judgement call. It runs at the end of a chain and does one of:

- merge or rebase `sssf/<adw_id>` back onto the base branch, per repository convention; or
- push the branch and open a PR, leaving the merge to a human.

Which one should be config, not code. Note SKILL.md rule 7: the phase needs a real description, and rule 10 still applies — the ADW ends in `run.finish(accepted=…)`, and integration succeeding is not the same as the run being accepted.

### 5. Lifecycle and cleanup

| Outcome | Worktree | Branch |
|---|---|---|
| Run succeeded and integrated | Pruned | Retained (cheap, and it is the record) |
| Run failed | **Kept** — this is where you go to see what happened | Retained |
| Run killed (SIGTERM/SIGINT) | **Kept** deliberately | Retained |

`session.py:30-32` already turns SIGTERM/SIGINT into `SystemExit` so the trace finalises; the worktree must survive that path untouched. Add a `git worktree prune` step and a way to list and clean orphans, since a killed run intentionally leaves one behind.

### 6. Things that assume HEAD and the diff target share a checkout

Re-examine both under worktrees:

- `adw_simple_sdlc.py:65` pins `baseline = git_helper.rev("HEAD")` before the first commit and diffs against it at `:152`. The pin is a SHA, so it survives — but only if `_git` runs in the right tree.
- `changes.resolve_base()` (`changes.py:24-49`) is a four-step cascade over `merge_base(ref, "HEAD")` and `is_dirty()`. It is already branch-aware, but its whole premise is that HEAD and the diff target are in the same checkout. Under a worktree branched from `base_ref`, `merge_base("main", "HEAD")` still does the right thing — confirm it, do not assume it.
- `adw_document.py:36` defaults `base` to `"main"`. With a per-run branch the more correct default is the run's pinned base ref.

### 7. Stamping

- A new module under `templates/adws/adw_modules/` is stamped automatically by the recursive call at `scripts/install.py:69` and is already covered by `protected_files: adws/adw_modules/`.
- The worktrees directory needs an entry in `GITIGNORE_ENTRIES` (`scripts/install.py:23-33`) if it lives inside the repo.
- A new ADW under `templates/adws/adw_*.py` is also stamped automatically, is covered by `protected_files: adws/adw_*.py`, needs a `Phases:` docstring line (SKILL.md's startup step reads it) and ideally a `justfile` recipe — note that six of the twelve existing ADWs have no recipe, so this is a convention, not a requirement.

### 8. Not used, deliberately: `claude --worktree`

The Claude Code CLI has a `--worktree` flag. Do not use it. The factory must own the worktree lifecycle identically for both backends, and the worktree has to exist before any agent is spawned — `permissions.snapshot()` runs against it before the first call.

## Work items

1. Give `git_helper._git()` a `cwd` parameter; thread `run.repo_root` through every caller. No behaviour change.
2. Fix `gates.py` path resolution and `tests_pass` (`cwd=`, `env=operator_env()`).
3. Add `worktrees_dir` to the config schema and `references/config.md`; add the gitignore entry to `install.py`.
4. Create and re-attach the worktree in `session.ensure()`; record the pinned base ref on the session.
5. Move `data_dir` anchoring to the main checkout; make `context_handoff_dir` absolute in the prompt; document `always_writable`'s new status.
6. Add the integration phase and its config switch (merge vs. PR).
7. Add prune/cleanup and orphan listing.
8. Re-check `changes.resolve_base()`, `adw_simple_sdlc.py`'s baseline and `adw_document.py`'s default base under worktrees.

## Risks and open questions

- **Threading `cwd` through `git_helper` touches every caller.** It is mechanical, but it is the kind of change where one missed call site silently writes to the wrong tree. Do it as an isolated commit and grep for every `git_helper.` reference.
- **A builder with `bash` can still `cd` out of the worktree.** A worktree is isolation, not a sandbox. `permissions.py` remains the actual boundary, and it only sees the tree it snapshots. Real containment is the container-sandbox phase, not this one.
- **Disk.** One worktree per run, kept on failure, adds up. Cleanup policy needs to be real, not aspirational.
- **Repository conventions vary** on merge vs. rebase, and on how generated files and lockfiles are regenerated. The integration phase must be configurable rather than opinionated.
- **Untracked and gitignored files do not come along.** A worktree starts clean; anything the repository needs that is not committed (`.env`, local caches, `node_modules`) has to be provisioned, or every run pays a cold-start cost. Decide the policy and document it.

## Verification

1. Start two runs concurrently against the same repository. Each lands on its own `sssf/<adw_id>` branch, in its own worktree, and the main working tree is unchanged (`git status` clean throughout).
2. Both runs' traces land in **one** database, and the visualizer shows both.
3. Dirty the main working tree, then run. The run must ignore it entirely, and `commit_all()` must not pick it up.
4. Kill a run mid-phase. The trace finalises as `fail`, the worktree survives, and its branch is inspectable.
5. Run a chain with a permission breach (an agent writing outside its `writes:`). The rollback must still work, measured inside the worktree.
6. Run the same `--adw-id` through two chained ADWs. The second must re-attach to the existing worktree, and `max_phase_seq()` must continue the sequence.

## Done when

- [ ] No `git_helper` function runs in the process cwd by accident; all take an explicit `cwd`.
- [ ] Every run gets its own worktree and branch; joined runs re-attach.
- [ ] The main working tree is never modified by a run, dirty or clean.
- [ ] Gates resolve artifacts inside the worktree and `tests_pass` runs there with `operator_env()`.
- [ ] The trace lives in the main checkout and survives worktree pruning.
- [ ] An integration phase merges or opens a PR, configurably.
- [ ] Failed and killed runs keep their worktree; successful ones are pruned; orphans are listable.
- [ ] Two concurrent runs complete without interfering.
