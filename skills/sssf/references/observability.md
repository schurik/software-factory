# Observability Reference

The event schema, the seven SQLite tables, and the polling contract — the one data path is **agents → sqlite → web ui**.

## Two stores, one truth

**Files are the raw record** (`events.jsonl`, `run.json`, `envelopes/<phase_id>.json`, `raw_output.jsonl` streams, `envelope.json`, `agent_map.json`); **SQLite (`sssf.db`) is the queryable mirror** the UI reads. `tracer.py` writes both. Losing the db loses nothing that can't be rebuilt from files.

**The factory only ever WRITES to the db — nothing in it reads back.** Not a run, not a watcher, not `just kill`, `just status`, `just worktrees` or the uninstaller. Every question about a session is answered from that session's own directory through `adw_modules/artifacts.py`:

| Question | File |
|---|---|
| what did this phase produce (a resume) | `sessions/<adw_id>/envelopes/<phase_id>.json` |
| which phases passed, how far the numbering got | `sessions/<adw_id>/events.jsonl` |
| which workflow ran, with what argv, how it ended, where it came from | `sessions/<adw_id>/run.json` |
| what has this run got alive, and how do I stop it | `sessions/<adw_id>/processes.jsonl` |
| is a watcher up, and what did it last see | `watchers/<kind>.json` |

That is portability, not tidiness: the db is a local mirror of the event stream, and the day those events go to a hosted API instead there is no file here to query — code that reads it would have to be written twice. Delete `sssf.db` and every command above still works; all you lose is the visualizer's history. The watcher heartbeat is written to BOTH (the file for `just status`, the row for the UI's badges), because a write is free and the badge is the visualizer's.

Location comes from `observability.db` in `sssf.config.yaml`, default `adws/adw_data/sssf.db` — inside the **target** repo, gitignored.

## Event schema

`tracer.py` emits these types, every one logged against its `adw_id` **and** `phase_id`:

| Type | Emitted when |
|---|---|
| `phase_start` | a `run.phase(...)` block is entered |
| `agent_start` | a coding agent is spawned or resumed for `ph.call(...)` |
| `tool_call` | a tool (`read`, `bash`, `edit`, `write`) returns — **one event per real call**, named `bash: ls -la src`, payload `{tool, tool_call_id, args, result_snippet, ok, duration_ms, agent}` |
| `handoff` | an envelope crosses from one agent to the next |
| `replay` | a resumed run answered an agent phase from this session's record instead of calling the agent — payload carries `source_seq`, `source_phase`, `output_type`, `agent`. No `agent_start`/`agent_end` accompanies it, and the phase adds nothing to the session's tokens or cost, because no agent ran |
| `gate_pass` | a gate found no failed checks — payload carries `attempt`, `checks` (the evidence), and an empty `violations` |
| `gate_fail` | a gate found at least one failed check — payload carries `attempt`, `checks`, and `violations` |
| `log` | an explicit `ph.log(...)` from the ADW script |
| `agent_end` | the agent's run completes; envelope parsed or not — payload carries `cost`, `usage` (the per-component breakdown), `context_tokens`, `context_window` |
| `phase_end` | the block exits; carries the resolved status |
| `error` | a raise inside a phase block |

`parent_id` nests spans, so an agent phase expands into its tool-call spans in the UI.

**Spend is itemised per phase.** `agent_end.usage` carries tokens *and* dollars for each component pi reports — `input`, `output`, `cache_read`, `cache_write` — summed across every send the phase made, so a phase that retried on a bad envelope or a failed gate shows what all its attempts cost, not just the last one. The four components sum to `total_tokens`, and their costs sum to `total_cost`; the visualizer's Cost panel renders them as a table you can add up by eye.

`reasoning_tokens` is the thinking share and is **inside** `output_tokens`, not a fifth component — measured across every session on disk, reasoning never exceeds output and the four components always reconcile to the total. It bills at the output rate, so the panel nests it under output rather than adding it. Runs predating the breakdown have no `usage` key at all; the lump `cost` and the event's own `tokens` still stand, and the UI says so rather than rendering zeroes.

**Context is occupancy, not spend.** `events.tokens` and `sessions.total_tokens` bill every turn, so they only grow — an agent that burned 100k tokens may be sitting in a 15k window. `context_tokens` is how full the window actually was when the agent stopped, which is what the visualizer's per-lane Context bar measures against `context_window`.

On **pi** it is computed the way pi computes it for its own footer and its auto-compaction trigger (`calculateContextTokens` in the coding agent's `core/compaction/compaction.ts`): take the last *valid* assistant turn — skipping `aborted` and `error` turns — and read `usage.totalTokens`, falling back to `input + output + cacheRead + cacheWrite`. Cache reads count; cached prompt is still prompt. `context_window` is the same `contextWindow` pi reads from `~/.pi/agent/models.json`, so `context_tokens / context_window` is the number pi would show.

On **Claude Code** it is the same part-sum in that CLI's vocabulary — `input_tokens + output_tokens + cache_read_input_tokens + cache_creation_input_tokens` on the last assistant turn — and `context_window` comes from the terminal `result` event's `modelUsage`, looked up by the model the session actually ran on. That lookup matters: `modelUsage` also lists models Claude Code used for its own side work, and taking the first entry would report a summariser's 200k window for a run on a 1M one.

Both are NULL on rows written before the columns existed, and the lane draws no bar rather than a misleading empty one.

**Cost is not symmetric between harnesses.** Pi reports a per-component breakdown; Claude Code reports `total_cost_usd` and nothing else. `usage.total_cost` reconciles on both, while `input_cost`, `output_cost`, `cache_read_cost` and `cache_write_cost` stay at zero for a Claude Code agent — left empty deliberately, rather than invented from a split the CLI never published.

Two caveats worth knowing. Pi adds an *estimate* for any messages trailing the last assistant usage; in a batch (`-p`) run the session ends on that message, so the two agree. And if auto-compaction fires as the very last act of a run, the recorded number is the pre-compaction size — pi itself reports `null` in that window rather than guessing.

**Gates record evidence, not just a verdict.** A gate returns one `{item, ok, note}` check per thing it looked at, and `violations` are derived from the failed ones. Both land in `gate_results` (`checks_json` + `violations_json`) and in the `gate_pass`/`gate_fail` payload, so a green gate can answer *what did you verify* — `{"item": "…/plan.md", "ok": true, "note": "exists, 454B"}` — rather than only *did it pass*. Rows written before this existed have `checks_json` NULL; treat that as "no evidence recorded", not "nothing checked".

The gate event payload carries `attempt` too, so the `gate_results` table and the event stream are equivalent sources — a live consumer can group gate results per correction round from events alone, without a second query.

**A `tool_call` is the one event that spans time**, so it fills both `started_at` and `ended_at` on the row — the tool's real start and return. Every other type is a point in time: `started_at` is when it was recorded and `ended_at` stays NULL. Lay tool calls out on a time axis from those columns, never by parsing `payload_json` (`duration_ms` is in the payload too, as pi's own number, but it is a convenience, not the source for layout).

**Where a run ran is written at the START.** The four workspace columns are set the moment the session opens, not when it ends — a killed run is exactly the one whose worktree you need to find, and it never reaches an end. They also outlive what they describe: an accepted run's worktree is removed while its branch is kept, so `branch` stays the answer to "where did this run's work go" long after `repo_root` is gone.

**Where the ask came from and where the work went** are `trigger`, `issue_url` and `pr_url`, and they are written by three different things at three different times: `trigger` at session start (`engineer`, overwritten by an issue phase), `issue_url` when the issue is fetched, `pr_url` by `integration.integrate()` when a pull request is actually opened. `trigger` is NULL only on rows older than the column — a run nobody can classify and a run somebody typed are different answers, and stamping `engineer` at start is what keeps them apart. A NULL `pr_url` covers three different endings — the run merged instead, integration refused, or the chain never got that far — so the *reason* lives in the integrate phase's notes rather than in a column.

**Provenance survives a session, not just a process.** `just integrate <adw_id>` re-enters an ADW hours later, and `session.ensure()` reads `trigger`/`issue_url` back into the Run at start. Without that, a second process would default to `engineer` and `issues.force_pr` would let an issue-triggered run merge into the base branch — the one control that phase put in code rather than in config, defeated by the documented follow-up path.

**Streaming is solved by construction.** Every harness tails its CLI's JSONL stdout line by line and the tracer inserts each event into `sssf.db` **while the agent is still working** — never batched at phase end (verified in the first smoke run of each: tool calls visible mid-run). Everything downstream is a poll → render.

**One tool-call shape, whichever harness ran it.** `adw_modules/tool_calls.py` owns the record — `tool`, `tool_call_id`, `args`, `ok`, `label`, `result_snippet`, and the call's real span — and each harness has a tracker that folds its own event vocabulary into it. Pi announces a `toolCall` block and closes with `tool_execution_end`; Claude Code announces a `tool_use` block in an `assistant` message and closes with `tool_result` blocks in the following `user` message, several at once when calls ran in parallel. Neither the trace schema nor the visualizer knows the difference.

**Claude Code noise is dropped before it is written anywhere** — not just before the tracer, but before the raw-output file too. `system/commands_changed` is the reason: ~20 KB of skill and slash-command listings on every invocation, which would dwarf the actual tool calls in `events.payload_json`. `active_goal`, `autocompact_state`, `post_turn_summary`, `task_summary` and `thinking_tokens` go with it. `system/init` (session id, model, cwd, tools), `assistant`, `user`, `result` and `rate_limit_event` are all kept — the last one because a stalled run is exactly when you want to know a rate limit was hit.

## Tables

```sql
sessions (
  adw_id        TEXT PRIMARY KEY,
  request       TEXT,              -- the ask: what the engineer typed, or "#<n> <issue title>"
  status        TEXT,              -- running | success | fail
  engineer      TEXT,
  started_at    TEXT, ended_at TEXT,
  total_tokens  INTEGER, total_cost REAL,
  repo_root     TEXT,              -- the worktree the run executed in
  branch        TEXT,              -- sssf/<adw_id>
  base_ref      TEXT,              -- what that branch was cut from...
  base_commit   TEXT,              -- ...pinned to a sha at run start
  trigger       TEXT,              -- engineer | issue. NULL only on rows older
                                   -- than the column: a run nobody can classify
                                   -- and a run somebody typed are different answers
  issue_url     TEXT,              -- the work item that caused it, when there was one
  pr_url        TEXT               -- the pull request its branch became. NULL covers
                                   -- three endings — merged instead, integration
                                   -- refused, or the chain never got that far — and
                                   -- the integrate phase's notes say which
);

phases (
  phase_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  seq           INTEGER,
  name TEXT, kind TEXT, owner TEXT, description TEXT,
  status        TEXT DEFAULT 'fail',   -- success must be earned
  attempt       INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
  error         TEXT,
  started_at    TEXT, ended_at TEXT
);

events (
  event_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,   -- every event logs against adw + phase
  parent_id     TEXT,                     -- span nesting
  type          TEXT,   -- phase_start | phase_end | agent_start | agent_end | tool_call
                        -- | handoff | gate_pass | gate_fail | replay | log | error
  name          TEXT,
  payload_json  TEXT,
  tokens        INTEGER,
  started_at    TEXT, ended_at TEXT   -- ended_at set only on events that span time
);

envelopes (
  envelope_id   TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  agent         TEXT,
  output_type   TEXT,              -- name of the data_types model it parsed against
  payload_json  TEXT,
  valid         INTEGER,
  attempt       INTEGER,
  created_at    TEXT
);

gate_results (
  id            INTEGER PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  attempt       INTEGER,
  gate          TEXT,
  passed        INTEGER,
  violations_json TEXT,             -- derived: the failed checks, as "item: note"
  checks_json   TEXT,               -- [{item, ok, note}] — everything the gate looked at
  created_at    TEXT
);

processes (                        -- adw_id → pid, so a stuck run can be stopped
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  adw_id        TEXT REFERENCES sessions,
  kind          TEXT,               -- 'adw' (the workflow process) | 'agent' (a coding-agent child)
  name          TEXT,               -- '' for the adw, the agent name for a child
  pid           INTEGER,
  command       TEXT,               -- what the pid WAS; pids get recycled, so verify before killing
  started_at    TEXT, ended_at TEXT -- ended_at NULL = believed alive
);

watchers (                         -- is anything going to START a run right now
  kind          TEXT PRIMARY KEY,   -- 'issues' | 'prs' — one row per KIND, not per process
  status        TEXT,               -- 'polling' | 'working' | 'stopped' | 'disabled' | 'error'
  pid           INTEGER,            -- probe this; the row is a belief, like a running session
  project       TEXT,
  interval_s    INTEGER,
  note          TEXT,               -- one line on what the last poll saw
  started_at    TEXT, last_poll_at TEXT
);

agent_sessions (                   -- the queryable mirror of agent_map.json
  adw_id        TEXT REFERENCES sessions,
  agent         TEXT,
  harness       TEXT, model TEXT, color TEXT,   -- color: the config's lane swatch
  session_id    TEXT,
  context_tokens INTEGER,           -- window occupancy after the agent's last turn
  context_window INTEGER,           -- the model's ceiling, per harness
  created_at    TEXT, last_used_at TEXT,
  PRIMARY KEY (adw_id, agent)
);
```

**A hung agent emits nothing**, which is exactly when you need its pid: no events, no tokens, no output to read. `processes` is the only table that can answer "what is this run running, and how do I stop it" — `just procs <adw_id>` lists what is live, `just kill <adw_id>` stops children before the parent, and both verify the recorded `command` still matches the pid before signalling it. A killed run finalizes its own trace: SIGTERM and SIGINT are turned into `SystemExit` in `session.ensure`, so the session lands on `fail` with its process rows closed instead of reading `running` forever.

**The one table that is not about a run.** Every other row here answers "what happened"; `watchers` answers "is anything going to make something happen". Both watchers upsert their row on every poll (`tracer.watcher_beat`), mark it `working` while a run they launched is blocking them, and `stopped` on the way out — so a watcher stopped on purpose is distinguishable from one that died. It exists because a watcher that is not running and a watcher with nothing to do produce exactly the same output: nothing. `just status` and the trace UI's top-bar badges (`GET /api/watchers`) read it, and both probe the pid, because a `SIGKILL`ed watcher leaves the row saying `polling` forever — the same "belief, not fact" rule as `sessions.status`. A kind with **no row** has never been started in this repo, which is not the same as `stopped`, and is shown differently.

**Derived, never stored:** phase durations (`ended_at − started_at`), session phase-progress (query `phases` by `adw_id`), lane layout (`kind` + `owner`).

Phase status invariants: `queued` only for manifest-declared phases not yet entered (dashed in the UI); `running` on enter; only a clean exit writes `success` — agent phases additionally need the envelope parsed and gates green; everything else resolves to `fail`.

## WAL pragmas

Open **every** connection — writer and reader — with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
```

WAL allows readers during writes. Writers are the tracers of running ADW processes; concurrent writers are fine given one small transaction per event plus `busy_timeout`. The visualizer reads on a readonly connection with exactly one exception: archiving a session (`POST /api/sessions/:adw_id/archive`) opens a second connection to set `sessions.archived`. That flag is review triage — it says a human has looked at the run — so it is the reader's state living on the row, and no tracer ever writes or reads it.

## Reading the trace for provenance

```sql
-- where the ask came from and where the work went
SELECT adw_id, trigger, status, substr(request,1,40), issue_url, pr_url
  FROM sessions ORDER BY started_at DESC LIMIT 10;

-- runs a stranger asked for, and whether their PR exists
SELECT adw_id, status, issue_url, pr_url FROM sessions WHERE trigger = 'issue';

-- why a branch did not land: the integrate phase's own notes
SELECT e.payload_json FROM events e JOIN phases p ON p.phase_id = e.phase_id
 WHERE p.name = 'integrate' AND e.type = 'log' AND e.adw_id = ?;
```

**A `running` session is a belief, not a fact.** Nothing reaps a row left behind by a SIGKILL, an OOM or a reboot, so anything that budgets on that count must check the pid: `processes` holds one `kind='adw'` row per run, written before the first phase opens precisely so a hung or dead run stays identifiable. `tracer.running_adw_pids()` is the read; signal 0 is the liveness probe. The issue watcher does exactly this — counting believed-running sessions instead would wedge it at `max_concurrent` permanently, looking identical to a busy factory.

## Polling contract

**The UI never receives pushes.** No ingest endpoint, no WebSocket, no backfill or dedup logic.

Live view polls on a rowid cursor every `observability.poll_ms` (default 500):

```sql
SELECT ... FROM events WHERE adw_id = ? AND rowid > ? ORDER BY rowid LIMIT 500;
```

Keep the highest `rowid` returned as the next cursor. History is **the same queries** with filters, lazy-paged as the engineer scrolls or drills in — one mechanism serves both live and past runs, which is why there is no separate replay path.
