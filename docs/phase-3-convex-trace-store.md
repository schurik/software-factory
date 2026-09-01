# Phase 3 — Convex as the central trace store

## Goal

Every run, from every repository, lands in one queryable place — without a local run ever depending on the network to succeed.

## Why now

Observability is the point of this system. Today the trace is complete but **local, anonymous and per-repo**: a gitignored SQLite file with no column recording which project, repository, branch or commit a run belonged to. You cannot ask "which agent fails its gates most often across all our repos", because there is no *across*.

This phase is also the hard dependency for Phase 4. There is nothing to learn from until the runs are in one place.

## Current state

### The tracer

`adw_modules/tracer.py`, one instance per ADW process, constructed once at `session.py:40`.

- `sqlite3.connect(db, isolation_level=None)` — autocommit, one write per call, no batching.
- `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000` (`tracer.py:109-111`), so the visualizer reads while runs write.
- **No `check_same_thread=False` and no lock.** The connection is single-threaded by construction.
- **No `close()` anywhere** in the codebase.
- Additive column migrations via the `MIGRATIONS` list (`tracer.py:94-99`), applied by `_migrate()` against `PRAGMA table_info`. This mechanism is built exactly for what this phase needs.

Seven tables: `sessions`, `phases`, `events`, `envelopes`, `gate_results`, `processes`, `agent_sessions`.

### The thirteen write paths

| Method | Line | Writes |
|---|---|---|
| `event` | 129 | INSERT `events` **and** the JSONL line |
| `session_start` | 140 | INSERT…ON CONFLICT `sessions` |
| `session_start` (adw_name) | 153 | UPDATE `sessions` — read-modify-write |
| `session_request` | 157 | UPDATE `sessions.request` (clipped to 500) |
| `session_finish` | 161 | UPDATE `sessions.status`, `ended_at`; also calls `processes_end_all` |
| `session_add_usage` | 168 | UPDATE `sessions` with `+=` |
| `process_start` | 183 | INSERT `processes` |
| `process_end` | 191 | UPDATE newest open row for a pid |
| `processes_end_all` | 200 | UPDATE all open rows |
| `phase_upsert` | 220 | INSERT…ON CONFLICT `phases` |
| `envelope_row` | 234 | INSERT `envelopes` |
| `gate_row` | 243 | INSERT `gate_results` |
| `agent_session_row` | 260 | INSERT…ON CONFLICT `agent_sessions` |

## Correct this before designing anything

**The JSONL file is not a complete record.** Only `event()` (`tracer.py:123-136`) writes it. `session_*`, `phase_upsert`, `envelope_row`, `gate_row`, `process_*` and `agent_session_row` write **SQLite only**.

A sink that tails `events.jsonl` — the obvious first design — loses sessions, phases, envelopes, gates, processes and agent sessions. Roughly 40% of the semantics, including everything you would want to query across repos.

`references/observability.md:7` invites the mistake: *"Losing the db loses nothing that can't be rebuilt from files."* That is roughly true for a human reconstructing a run by hand — phase transitions survive as `phase_start`/`phase_end` events, gate outcomes as `gate_pass`/`gate_fail` events, valid envelopes as `envelope.json` — but it is not true row-for-row. `processes` has no file mirror at all, invalid envelopes exist only in the database, and session status only survives as console log text.

**Backfill reads SQLite.** It is the complete record. The JSONL stays what it already is: the crash-safe raw event stream, flushed per line.

## Design

### 1. Local first, always

Local SQLite remains the system of record for a run. Convex is a **best-effort mirror**. A run must never fail, block or slow down because Convex is unreachable — the factory works offline, and syncs when it can.

### 2. Sink mechanics

**Fan-out, not subclassing.** Add a `sinks: list[Sink]` parameter to `Tracer.__init__` and one `self._fanout(kind, payload)` call inside each of the thirteen methods. Subclassing and overriding `event()` alone covers only ~60% of the semantics, and overriding all thirteen duplicates the schema knowledge in two places.

**Never block the agent.** `_event_forwarder` (`agents.py:235-250`) runs **synchronously inside the coding agent's stdout tail**. An HTTP call there stalls every tool call the agent makes. The sink must be a `queue.Queue` plus a worker thread that batches and posts.

**The worker thread must never touch `self.conn`.** The connection has no `check_same_thread=False`; using it from the worker raises `ProgrammingError`. The sink receives plain dicts, not database handles.

**Drain points.** There is no `tracer.close()` to hook, so add explicit drains at three places, all of which already exist:

- `session_finish` (`tracer.py:160`) — the normal path
- `atexit` — the crash path
- the signal handler at `session.py:30-32` — the `kill` path, which already finalises the trace

**Ordering.** SQLite guarantees insertion order via `rowid`; HTTP does not. `now_iso()` (`utils.py:45-46`) has millisecond resolution, so it is not unique under fast event streams. Assign a **per-`adw_id` monotonic sequence** in the tracer and carry it on every record. This also replaces the `rowid` cursor, which is local-only and useless to Convex.

**Payload shape.** Hook the sink at the `EventRecord` level, before `json.dumps` — the JSONL already writes `payload` as a real object while SQLite stores a string. Convex wants the object.

### 3. Idempotency

Ingest must be safe to replay, because that is what makes offline runs recoverable.

| Record | Key | Notes |
|---|---|---|
| `phases` | `phase_id` | Deterministic: `f"{adw_id}_{seq:02d}_{name}"` (`runner.py:75`). Ideal. |
| `agent_sessions` | `(adw_id, agent)` | Already the SQLite PK. Upsert. |
| `events` | `event_id` | Random (`evt_` + 12 hex) but a stable PK once minted. Fine as an idempotency key. |
| `envelopes` | `envelope_id` | Same. |
| `gate_results` | — | Local `AUTOINCREMENT`, no global key. **Mirror the `gate_pass`/`gate_fail` event instead**, which carries the same `{item, ok, note}` data and `attempt`, and already has an `event_id`. |
| `processes` | `(adw_id, pid, started_at)` | Local `AUTOINCREMENT`; synthesise. |
| `sessions` | `(project, repo, adw_id)` | See below. |

**Do not ingest `session_add_usage` deltas.** `tracer.py:167-172` is a `+=` UPDATE — replaying it double-counts tokens and cost. Convex should **re-aggregate totals from the `agent_end` event payloads**, which carry absolute per-phase usage. Same reasoning applies to `session_start`'s `adw_name` concatenation (`tracer.py:148-154`), which is read-modify-write and races between joined processes.

### 4. Project identity — the missing columns

Nothing in the schema records which project or repository a run belonged to. Worse, `adw_id` is `secrets.token_hex(4)` (`utils.py:41-42`) — **32 bits**. Across many repositories that collides at roughly 10⁴ runs by the birthday bound. `adw_id` alone cannot be a global key.

Add to `sessions` via the existing additive `MIGRATIONS` list (`tracer.py:94-99`):

| Column | Source |
|---|---|
| `project` | New config key — the logical grouping, since one project may span repos |
| `repo` | `git remote get-url origin`, normalised, via `git_helper` |
| `branch` | `git_helper.current_branch()` — currently unused, finally has a caller |
| `commit` | `git_helper.rev("HEAD")` at run start |

Populate them in `session.ensure()`, where `git_helper` is already imported and `repo_root` is already resolved. Under Phase 2 these describe the run's base ref, which is more useful than describing the worktree.

`(project, repo)` becomes the Convex partition key; `(project, repo, adw_id)` the session key.

The visualizer tolerates new columns without changes — `server/db.ts:111-124` probes with `hasColumn`/`optionalColumn` and substitutes `NULL AS col` for anything missing.

### 5. The Convex project

New top-level `convex/`:

- **`schema.ts`** — the seven tables, mirroring the SQLite shapes plus `project`, `repo`, `branch`, `commit` and the monotonic sequence. Indexes for the queries the UI actually runs: by project, by `(project, repo)`, by status, by started-at descending, and by `adw_id` for a session detail view.
- **`http.ts`** — routes a batch ingest HTTP action.
- **`ingest`** — an HTTP action taking a batch of records and applying them idempotently.
- **Query functions** for the cross-repo UI.

**Auth**: `CONVEX_URL` and `CONVEX_INGEST_TOKEN` in `.env`, added to `templates/env.sample` (which today carries only `OPENROUTER_API_KEY`, `FIREWORKS_API_KEY`, `OPENAI_API_KEY` and the optional `PI_PATH`/`PI_MODELS_PATH`/`ENGINEER_NAME`). Never committed; `.env` is already in `GITIGNORE_ENTRIES`.

### 6. Size limits

Convex enforces a **1 MiB maximum document size**. Current clipping:

| Clipped today | Limit |
|---|---|
| `sessions.request` | 500 (`tracer.py:158`) |
| `processes.command` | 500 (`tracer.py:186`) |
| `phases.error` | 1000 (`runner.py:90`) |
| tool-call string args, `result_snippet` | 20 000 each (`agent_pi.py:26-27`) |
| event `name` | 80 (`agent_pi.py:28`) |
| invalid-envelope raw | last 2000 (`agents.py:293`) |
| console lines | 160 (`console.py:19`) |

**Unbounded, and therefore the risk:**

- `envelopes.payload_json` — `envelope.model_dump_json(indent=2)` (`agents.py:293`), no ceiling at all. A plan envelope with a long artifact list and prose reaches 100 KB easily.
- `gate_results.checks_json` and the `gate_pass`/`gate_fail` event payloads — unbounded number of checks.
- `ph.log(**payload)` dicts (`runner.py:27-31`) — the console mirror is clipped, the event is not.
- Non-string tool-call args: `_clip` only applies `if isinstance(value, str)` (`agent_pi.py:180`), so a large array or object argument passes through whole.

**Clip at the sink, not in the tracer.** Clipping in the tracer would make the local record lossy too, and the whole point of local-first is that SQLite has everything. Clip in the sink, with an explicit `truncated: true` flag on the record so a reader knows the Convex copy is abridged and where to find the full one. Bound batch payloads well under 1 MiB.

A typical `tool_call` at ~20–60 KB is safe. Envelopes and large gate payloads are not guaranteed.

### 7. Backfill and re-sync

A CLI entry point that reads a local `sssf.db` and replays it into Convex. Because every record has a stable key, running it twice is a no-op. This is what makes an offline run recoverable, and it is also the migration path for existing local traces.

### 8. `archived` moves

`sessions.archived` is reader state — set only by the visualizer (`server/db.ts:147`), never by any tracer. In a cross-repo view it must live in Convex, or triage done in one place will not be visible in the other.

### 9. UI

The existing Bun + Vue visualizer (`Bun.serve` + `bun:sqlite` on 4600, Vite 7 + Vue 3.5 on 4601) keeps working against local SQLite, unchanged.

For the cross-repo view, decide and record: extend the existing app with a Convex-backed data source, or ship a second app. Either way, **Convex reactivity replaces polling** — the three poll loops currently hardcode 500 ms (`SessionsList.vue:33`, `SessionTrace.vue:84`, `SessionCard.vue:65`).

Note in passing: **`observability.poll_ms` is dead config.** It exists in `sssf.config.yaml:37`, in `data_types.py:343`, and `references/observability.md:150` states the UI "polls on a rowid cursor every `observability.poll_ms` (default 500)" — but no visualizer file reads it. Either wire it or remove it; a documented knob that does nothing is worse than either.

## Work items

1. Add `project`, `repo`, `branch`, `commit` to `sessions` via `MIGRATIONS`; populate in `session.ensure()`; add the `project` config key.
2. Add the per-`adw_id` monotonic sequence to the tracer.
3. Introduce the `Sink` protocol and the `sinks` list; add `_fanout` to all thirteen write methods. No network yet — verify with a logging sink.
4. Implement the queued, threaded HTTP sink with batching, retry, size guard and the three drain points.
5. Scaffold the Convex project: `schema.ts`, `http.ts`, the ingest action, the query functions.
6. Implement idempotent ingest, including the gate-via-event decision and usage re-aggregation from `agent_end`.
7. Implement backfill/re-sync from a local `sssf.db`.
8. Move `archived` to Convex.
9. Build the cross-repo view; resolve `poll_ms`.
10. Update the two places that document the absence of a push path, both of which this phase makes untrue: `references/observability.md:3` (*"the one data path is agents → sqlite → web ui"*) and `:148` (*"The UI never receives pushes. No ingest endpoint, no WebSocket, no backfill or dedup logic."*), plus the module docstring at `tracer.py:4` (*"No push transport"*).

## Risks and open questions

- **Latency in the hot path.** The single most likely way to get this wrong is a synchronous HTTP call inside `_event_forwarder`. Every tool call the agent makes would wait on it. Queue and thread are not optional.
- **Thread safety.** The worker must not touch the SQLite connection. Enforce it by shape — the sink takes dicts and has no database handle.
- **A run that produces more events than the sink can drain** at `session_finish` will either block the drain or drop the tail. Pick one, document it, and make the choice visible (a `sink_lag` metric, or a bounded queue that logs when it drops).
- **Two sources of truth drifting.** `archived` in Convex and the local db's copy is one instance; the size-guard truncation is another. The `truncated` flag and "SQLite is complete" have to be documented invariants, not implicit ones.
- **Cost.** Every tool call, every console line (`console.py:43` traces *every* line, and `gate_result` emits one event per check — a 50-check gate produces 51 events) becomes a Convex write. Estimate the write volume of a real run before committing to a plan tier, and consider sampling `log` events.
- **Secrets in the trace.** Tool-call args and result snippets are stored verbatim, up to 20 000 characters. Locally that is your own disk; centrally it is a shared store. Decide on redaction before the first real ingest, not after.

## Verification

1. **Offline run**: with `CONVEX_URL` unset or unreachable, a full `adw_simple_sdlc` run completes green and its local SQLite trace is complete. No stall, no error, no missing phases.
2. **Online run**: the same chain with Convex reachable produces matching row counts across all seven record types.
3. **Backfill**: replay the offline run's `sssf.db`. Convex now matches the local db.
4. **Idempotency**: run the backfill a second time. Row counts do not change, and no record is duplicated.
5. **Usage correctness**: session totals in Convex match the local `sessions.total_tokens` / `total_cost`, having been re-aggregated from `agent_end` rather than summed from deltas.
6. **Size guard**: force an oversized envelope. The Convex copy is truncated and flagged; the local copy is whole.
7. **Latency**: compare wall-clock phase durations with the sink enabled and disabled. The difference must be negligible.
8. **Cross-repo**: ingest runs from two different repositories and query them together by `project`.

## Done when

- [ ] `sessions` carries `project`, `repo`, `branch`, `commit`, populated on every run.
- [ ] All thirteen tracer write paths fan out to sinks.
- [ ] The sink is queued, threaded, batched and drains on normal exit, crash and kill.
- [ ] A run with Convex unreachable is unaffected.
- [ ] Backfill from SQLite reproduces a run exactly, and is idempotent.
- [ ] Oversized payloads are truncated at the sink and flagged, never in the local record.
- [ ] Session usage totals in Convex are re-aggregated, not delta-summed.
- [ ] A cross-repo view shows runs from more than one repository.
- [ ] `poll_ms` is either wired or removed.
- [ ] `references/observability.md` and `tracer.py`'s docstring no longer claim there is no push transport.
