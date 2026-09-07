# Phase 7 — One command to start it, one place that says it is running

## Goal

The factory's long-running parts start with **one command**, and "is anything actually watching?" has an **answer** — in the terminal and in the trace UI — instead of being something you infer from an hour of silence.

## Why now

It depends on nothing unbuilt. Phases 5 and 6 delivered the watchers; this is the operating layer over them, and the cost of not having it is paid on every single session rather than on some future one.

## Current state

Three long-running processes, three terminals, three commands:

```bash
just obs            # trace UI  (two processes: the api on :4600, vite on :4601)
just issues-watch   # the issue watcher
just prs-watch      # the review watcher
```

Four things go wrong with that, and all four were observed in ordinary use.

1. **You forget one, and nothing tells you.** A labelled issue with no watcher polling produces exactly as much output as a labelled issue with a busy factory: none. The trace UI does not help either — it shows runs, and the whole problem is that there is no run. This is the single most expensive failure the system invites, because the feedback arrives an hour later in the form of a question to yourself.

2. **`just obs` orphaned its own API server.** The recipe was `(… bun run server/index.ts &) && bunx vite` — the api backgrounded in a subshell, so ctrl-c killed vite and left the api holding `:4600`. The next `just obs` died on `EADDRINUSE` naming no culprit.

3. **`SSSF_SKILL` was a copy-paste.** `install.py` printed the line; a human pasted it into `.env`. Every clone and every second machine re-ran that ritual, and the failure when it was skipped (`not set - put the path install.py printed into .env`) landed on a recipe, not on the install.

4. **`pr_watch`'s reap stopped more than it should.** On a merged pull request it SIGTERMed *any* live session attached to that PR. For the review run it started, that is correct cleanup — the threads are moot and `keep_published` would push onto a landed branch. But an `adw_simple_sdlc` that opened the pull request itself is typically still reviewing and documenting when someone merges it, and killing that is not cleanup, it is discarding a run mid-flight because a human was quick with the merge button.

## Design

### `scripts/up.py` — a supervisor, not a daemon

One foreground process that owns the others. It sits **outside** the factory alongside `issue_watch.py` and `worktrees.py`, for the reason they do: a process manager is above the control plane, not a phase inside it, and nothing in it knows what a phase is.

- Each child is spawned with `start_new_session=True`, so it gets its own process group. `bun run vite` and `uv run` both spawn children of their own; signalling only the pid we hold is precisely how `:4600` got orphaned. Shutdown is `SIGTERM` to the group, then `SIGKILL` to whatever is left after 8s.
- Output is piped and prefixed per service (`obs`, `ui`, `issues`, `prs`), one lock around the terminal.
- A child that dies is restarted with backoff; three deaths inside a minute and it says so and stays down, with the rest still running. A supervisor that hides a broken service behind an endless respawn is worse than one that admits it.
- **Preflight before anything spawns**: no `bun` → start without the UI; `:4600` already taken → start without the UI and name the likely squatter; no forge CLI → warn, because every poll will fail to list anything; each roster harness's `reachable()` → warn, because that failure otherwise surfaces mid-chain an hour later. A watcher whose `enabled` is `false` is not started, and the reason is printed.
- **Not a daemon.** No pidfile, no detach, no `stop`. The terminal is the handle — the same bargain `vite` makes. Cron keeps the single-poll recipes it already had.

### The heartbeat — `watchers` in the trace db

The supervisor answers "start it all"; it cannot answer "is it up" for a watcher started some other way, or five minutes after the terminal scrolled. One new table does:

```sql
watchers (kind PRIMARY KEY, status, pid, project, interval_s, note, started_at, last_poll_at)
```

- Written by both watchers on **every poll** (`tracer.watcher_beat`), as `working` while a launched run blocks them, `error` on a failed poll, `disabled` when the config is off, and `stopped` on the way out.
- **A row is a belief, not a fact** — the same rule `sessions.status` already lives under. Every reader probes the pid with signal 0, so a `SIGKILL`ed watcher reads as gone rather than as whatever it last wrote.
- **No row means never started here**, which is a different thing from `stopped`, and is rendered differently. A db written before the table existed reads as exactly that rather than as an error.

Getting the `stopped` beat to actually land needed one non-obvious fix. The watchers had no `SIGTERM` handler, so the supervisor's shutdown killed them where they stood; adding one exposed the real bug — `killpg` signals `uv` alongside the watcher, and `uv` forwards a second `SIGTERM` that re-raises `SystemExit` from inside the `finally` that was writing the goodbye beat. The handler ignores everything after the first signal.

### Three readers, one truth

| Where | Answers |
|---|---|
| `just status` | Both watchers, last poll, note; runs actually in flight; outstanding worktrees |
| Trace UI top bar | The same two as badges, polled every 10s — green polling, blue mid-run, red down-or-erroring, dashed grey never-started |
| `GET /api/watchers` | The rows, plus a pid probe per row |

The UI badge is the load-bearing one: the trace UI is the screen already open while you wait, which is exactly when the answer matters. `server/db.ts` grew a `hasTable()` probe alongside its existing `hasColumn()`, on the same latch-once rule, because the first watcher to start creates the table under a server that is already serving.

### The narrowed reap

`_reap` now stops **only a review run** — decided by `sessions.adw_name` via a new `tracer.session_adw_names()` reader, matched on the ADW's stem so a session that answered review feedback twice (`adw_pr_review + adw_pr_review`) still matches. An empty name is *not* a review run: the conservative reading, because the mistake being guarded against is stopping something that should have kept going.

Any other live run on a merged pull request is left alone and reported: one line saying its commits would now push onto a landed branch, and `just kill <adw_id>` if that is not wanted. Worktree release, label drop and lock removal are unchanged for every kind — those are cleanup, and cleanup was never the problem.

The watcher itself is **never** stopped by a merge. Only the pinned `pr_watch.py loop --pr <n>` exits, because a watcher asked to follow one pull request is finished when that pull request is.

### `SSSF_SKILL`, written rather than printed

`install.py` creates `.env` from the harness's `.env.sample` and fills the key in — it is the one moment anything knows the answer. It never overwrites a value that is already there; a path from another machine is reported and left for a human to decide about.

## Work items

| # | Item | Files |
|---|---|---|
| 1 | The supervisor | `skills/sssf/scripts/up.py` (new) |
| 2 | Heartbeat write + read | `templates/adws/adw_modules/tracer.py` (`watchers` table, `watcher_beat`, `watcher_states`, `session_adw_names`) |
| 3 | Beats on every poll, `SIGTERM` → clean exit | `scripts/issue_watch.py`, `scripts/pr_watch.py` |
| 4 | Narrowed reap | `scripts/pr_watch.py` |
| 5 | `/api/watchers` + the badges | `apps/visualizer/{shared/types.ts,server/db.ts,server/index.ts,src/lib/{api,types}.ts,src/components/WatcherChips.vue,src/App.vue}` |
| 6 | `just up`, `just status`, `obs` via the supervisor | `templates/justfile` |
| 7 | `.env` written, next-steps rewritten | `scripts/install.py` |
| 8 | Docs | `README.md`, `SKILL.md`, `cookbooks/{install,sssf_overview}.md`, `references/{config,observability}.md` |

## Risks and open questions

- **The pid probe is machine-local.** `alive` is only meaningful because the api, the watchers and the runs share a host — which is what this app already assumes, since it reads a local sqlite file. A remote deployment would need the watcher to write a lease with an expiry instead. Not built, because nothing deploys that way yet.
- **`watcher_beat` creates the trace db if it is missing**, with the full schema rather than its own table alone: a db holding only `watchers` would 500 every session query in the visualizer.
- **One row per kind, not per process.** Two issue watchers on one repo is a thing to notice, not to record twice; the pid says which one wrote last. The file lock is still what actually prevents the duplicate work.
- **`max_concurrent` is still not reachable from a watcher.** `_launch` is serial, so one watcher has at most one run in flight, and the bound only ever binds hand-launched runs. Unchanged here, and worth its own item.

## Verification

- `just up` in a stamped repo starts UI, api and both watchers; ctrl-c leaves **no** survivor and `:4600` free.
- Killing a watcher with `SIGKILL` shows `not running` in `just status` and a red badge, within one UI poll.
- Stopping it with `just up`'s ctrl-c shows `stopped`, not `not running`.
- A repo that has never run a watcher shows dashed grey badges and `never started here`, not an empty top bar.
- A db from before this phase serves `/api/watchers` as two `unknown` rows rather than a 500.
- `just up --only obs` is `just obs`, and both release the port.

## Done when

Starting the factory is one command, forgetting to start it is visible from the screen you are already looking at, and a merge stops only what that merge actually finished.

## As built

Built as designed. Two things came out differently:

- **The `stopped` beat needed the double-`SIGTERM` fix** described under *The heartbeat*. It was found by testing rather than by reading: the beat landed when the watcher was signalled directly and vanished when `up.py` signalled the group.
- **`just obs` was not kept as a second implementation.** It delegates to `up.py --only obs`, which is what makes it stop orphaning the api.
