# Phase 1 — Dual coding-agent backend

## Goal

`coding_agent: pi | claude_code` becomes real and selectable **per agent**, so one roster can run a Claude Code planner alongside a Pi builder in the same run, and the trace, the gates and the visualizer cannot tell the difference.

## Why now

The config schema already accepts `claude_code` (`data_types.py:306` on `AgentConfig`, `:326` on `ConfigDefaults`), and the tracer already carries a `coding_agent` column (`tracer.py:83`) that `agent_map` already persists (`agents.py:199`). The seam was designed in; only the dispatch and the adapter were left out. This is the cheapest of the four phases and it removes the hard dependency on per-provider API keys — a Claude subscription becomes a viable way to run the factory.

## Current state

### The seam is exactly one function

`execute.send()` at `agents.py:112-137` is the only place the factory touches a coding agent. Everything it consumes from a result is six attributes:

| Consumed | Where |
|---|---|
| `.text` | `agents.py:272` (parsed into the envelope) |
| `.tokens`, `.cost` | `agents.py:134` → `run.add_usage()` |
| `.usage: UsageBreakdown` | `agents.py:135` → phase totals |
| `.context_tokens`, `.context_window` | `agents.py:195-197`, `:211` |

Plus two callbacks the caller supplies: `on_spawn`/`on_exit` bracket the child pid so a hung agent stays killable (`agents.py:130-133` → the `processes` table).

### Pi-specific call sites

| File | Lines | What |
|---|---|---|
| `agents.py` | 18 | `from . import agent_pi` — module-level import, no dispatch |
| `agents.py` | 61-63 | `validate()` appends a problem for any `coding_agent != "pi"` |
| `agents.py` | 69 | `agent_pi.resolve_model(agent.model)` — model must exist in Pi's catalog |
| `agents.py` | 109, 112 | Type annotations `agent_pi.PiResult` |
| `agents.py` | 114-126 | `PiRequest(...)` built directly, including `session_dir` (Pi's `--session-dir`), `extensions` (Pi's `-e`) and Pi's `thinking` scale |
| `agents.py` | 127-133 | `agent_pi.run(request, on_event, on_spawn, on_exit)` |
| `agents.py` | 237 | `agent_pi.ToolCallTracker()` — parses Pi's `message_end` / `tool_execution_*` shapes |
| `agent_cc.py` | 11-15 | The stub: `run(*args, **kwargs)` raises `NotImplementedError` |
| `data_types.py` | 373-385 | `PiRequest` |
| `data_types.py` | 411-428 | `UsageBreakdown.add_turn` reads Pi's key names |
| `data_types.py` | 436-447 | `PiResult` |
| `agent_pi.py` | whole file | `PI_PATH`, the `pi --list-models` catalog, `~/.pi/agent/models.json`, argv construction, JSONL tailing |

### Already backend-agnostic — do not touch

`gates.py`, `permissions.py`, `runner.py`, `console.py`, `changes.py`, `quality.py`, `prompts.py`, `session.py`.

### Two traps

1. **`PiResult.session_id` exists but `execute` never reads it** (`data_types.py:439`). Pi's `--session-id` is create-or-continue, so the factory invents the id up front (`agents.py:228-232`) and never needs it back. Claude Code does not work that way.
2. **A session is reused only if the model is unchanged** — `agents.py:229-231` compares `entry["model"] == agent.model` and mints a fresh id otherwise. Any new state stored per agent has to survive that rule.

## Claude Code CLI semantics — probed, not assumed

Verified against `claude` v2.1.257:

- `claude -p --output-format stream-json --verbose` streams NDJSON on stdout, one JSON object per line. Structurally this is what `agent_pi.run()` already tails, so the read loop, the raw-output file and the flush-per-line behaviour carry over unchanged.
- **`--session-id <uuid>` is create-only.** A second invocation with the same id fails hard:
  ```
  Error: Session ID 3f2b9c10-1a2b-4c3d-8e4f-5a6b7c8d9e01 is already in use.
  ```
  Continuing requires `--resume <uuid>`, which preserves context and reports the same `session_id` in its `system/init` event. **This is the central divergence from Pi.**
- `--session-id` requires a **valid UUID**. `_agent_session_id` currently mints `f"sssf-{adw_id}-{agent}-{new_id(4)}"`, which is not one.
- Every invocation emits a `{"type":"system","subtype":"commands_changed",…}` event of **~20 KB** (measured: 20 494 bytes) listing every skill and slash command available to the operator. It is pure noise.
- Event types observed: `active_goal`, `autocompact_state`, `system/commands_changed`, `system/init`, `assistant`, `user`, `rate_limit_event`, `system/post_turn_summary`, `result`.
- The terminal `result` event carries everything the tracer needs:
  ```json
  {"type":"result","subtype":"success","is_error":false,"num_turns":1,
   "result":"…final assistant text…","total_cost_usd":0.001036,
   "usage":{"input_tokens":498,"output_tokens":4,
            "cache_read_input_tokens":0,"cache_creation_input_tokens":0},
   "modelUsage":{"claude-sonnet-5":{"contextWindow":1000000,…}}}
  ```
  `total_cost_usd` and `num_turns` are **per invocation**, not cumulative — they sum across calls exactly the way `PiResult.tokens`/`.cost` already accumulate.

## Design

### 1. A backend protocol

Introduce a `CodingAgent` protocol whose single method mirrors what `agent_pi.run()` already is:

```python
def run(request: AgentRequest,
        on_event: Callable[[dict], None] | None = None,
        on_spawn: Callable[[int], None] | None = None,
        on_exit: Callable[[int], None] | None = None) -> AgentResult
```

Generalise `PiRequest`/`PiResult` into `AgentRequest`/`AgentResult` in `data_types.py`, keeping `PiRequest = AgentRequest` aliases while the transition lands so nothing breaks mid-refactor. The four-param rule (SKILL.md rule 4) already forced a request object, so this is a rename plus a couple of fields, not a redesign.

Backends live in `adw_modules/agent_pi.py` and `adw_modules/agent_cc.py`; `agents.py` selects with a small registry keyed on `agent.coding_agent` and holds no `import agent_pi` at module level for anything but the registry.

Each backend also exposes:

- `resolve_model(pattern) -> tuple[str, str] | str` — Pi resolves against `pi --list-models`; Claude Code passes an alias or full model id straight through.
- `reachable() -> None` — raises with a useful message if the CLI is missing. Pi already effectively has this via the catalog call; Claude Code gets a `claude --version` probe.
- A tool-call tracker class producing the normalised record shape (below).

### 2. The create-vs-resume state machine

The backend needs to know whether this agent's session already exists. That state has a home already: `run.agent_map`, persisted to `session_dir/agent_map.json` (`runner.py:59-63`) and reloaded when a later ADW joins the run with a pinned `--adw-id`.

Extend the per-agent entry from `{session_id, model, coding_agent}` to also carry `native_session_id` and `started: bool`. Then:

- **Pi**: `--session-id <id>` every time. `started` is informational.
- **Claude Code**: first call uses `--session-id <uuid>`; every later call uses `--resume <uuid>`. `execute` must read `result.session_id` back from the `system/init` or `result` event and write it into the map — the attribute exists and is currently ignored.

Derive the UUID deterministically rather than storing a random one: `uuid5(NAMESPACE, f"{adw_id}:{agent_name}:{model}")`. Including the model keeps the existing "model changed, new session" rule at `agents.py:229-231` working without a special case, and makes a re-run with a pinned `adw_id` land on the same session.

### 3. Flag mapping

| Config key | Pi | Claude Code | Note |
|---|---|---|---|
| `model` | `--provider <p> --model <id>`, resolved via `pi --list-models` | `--model <alias or full id>` | Pi requires `provider/model-id`; Claude Code has no provider half. `validate()` must not apply Pi's pattern rule to a Claude Code agent. |
| `thinking` | `--thinking off\|minimal\|low\|medium\|high\|xhigh\|max` | `--effort low\|medium\|high\|xhigh\|max` | `off` and `minimal` have no Claude Code equivalent. Decide and document the collapse (recommendation: both → `low`, and warn once). |
| `prompt_engineering.system` | `--system-prompt <text>` | `--system-prompt <text>` | On Claude Code this also disables system-prompt snapshotting, so the prompt applies fresh on every launch — which is what the factory wants. |
| `tools` | `--tools read,bash,edit,write,grep,find,ls` | `--tools Read,Bash,Edit,Write,Grep,Glob` | **Names differ in case and spelling.** Needs an explicit map; `find` → `Glob`, `ls` → `Bash`-or-`Glob` depending on how literally you take it. An unmapped name must fail loudly, not silently drop a capability. |
| `harness_engineering` | `-e <file.ts>`, repeatable | `--mcp-config`, `--agents`, `--plugin-dir` | Not a one-to-one mapping. Pi extensions are TypeScript files; Claude Code equivalents are MCP servers, subagent definitions and plugins. Document that the key is backend-specific and validate its contents per backend. |
| session | `--session-id` (create-or-continue) | `--session-id` then `--resume` | See above. |
| working directory | `cwd=` on `Popen` | `cwd=` on `Popen`, plus `--add-dir` | `--add-dir` matters once Phase 2 puts the run in a worktree outside the main checkout. |
| budget | — | `--max-budget-usd` | Optional per-agent cost ceiling; worth exposing in config later. |

### 4. Determinism

A default `claude -p` run auto-discovers the operator's `CLAUDE.md`, skills, plugins, hooks and MCP servers. The probe run in this repository loaded **30+ skills** that had nothing to do with the task. That makes a factory run depend on whose machine it executed on, which is the exact failure the factory exists to eliminate.

Pin it off. Recommended baseline for every Claude Code agent invocation:

- `--strict-mcp-config` — only MCP servers the factory passes explicitly.
- `--setting-sources ""` — ignore user, project and local settings files.
- Consider `--bare` as the default (skips hooks, LSP, plugin sync, auto-memory and `CLAUDE.md` auto-discovery), with an opt-out config key for agents that genuinely want the repository's own `CLAUDE.md`.

Make this a documented, overridable default rather than a hardcoded one — some repositories will want their `CLAUDE.md` loaded, and that should be a config decision, not a code decision.

**Permissions.** Non-interactive runs need `--permission-mode` set (or `--dangerously-skip-permissions`). This is only acceptable because two other things are true: `permissions.py` snapshots the tree before the call and rolls back any write outside the agent's `writes:` allowlist afterwards, and Phase 2 puts the run in an isolated worktree. Say so explicitly in the config comments — the next person to read it will otherwise assume the factory is careless.

### 5. Event normalisation

Add a `ClaudeToolCallTracker` mirroring `agent_pi.ToolCallTracker` (`agent_pi.py:141-207`). Claude Code announces a call as a `tool_use` block inside an `assistant` message and returns it as a `tool_result` block inside a `user` message; fold the pair into **one record per completed call**, emitted the moment it returns, with the identical dict shape:

```
tool, tool_call_id, args, ok, label, result_snippet, started_at, ended_at, duration_ms
```

Reuse the same clipping constants (`RESULT_SNIPPET_CHARS = 20_000`, `ARG_VALUE_CHARS = 20_000`, `LABEL_CHARS = 80`) and the same `PRIMARY_ARGS` label heuristic. Getting this right is what lets `_event_forwarder` (`agents.py:235-250`) and the whole visualizer stay untouched.

**Noise filter.** Drop `system/commands_changed` before it reaches the tracer, the JSONL or the raw-output file — 20 KB per invocation of skill listings would dwarf the actual trace. Make an explicit decision on the rest:

| Event | Recommendation |
|---|---|
| `system/commands_changed` | Drop entirely |
| `system/init` | Keep — carries `session_id`, `model`, `cwd`, `tools` |
| `assistant` / `user` | Keep — the tool-call stream and the final text |
| `result` | Keep — usage, cost, final text, error state |
| `autocompact_state` | Drop, or keep once at start; it is static per run |
| `rate_limit_event` | Keep as a `log` event — genuinely useful when a run stalls |
| `system/post_turn_summary` | Drop |
| `active_goal` | Drop |

### 6. Usage accounting

`UsageBreakdown.add_turn` (`data_types.py:411-428`) hardcodes Pi's key names (`input`, `output`, `cacheRead`, `cacheWrite`, `reasoning`, and `usage["cost"][…]`). Give each backend its own adapter that produces a `UsageBreakdown`, rather than teaching one function two vocabularies.

Claude Code mapping, from the `result` event:

| `UsageBreakdown` field | Source |
|---|---|
| `input_tokens` | `usage.input_tokens` |
| `output_tokens` | `usage.output_tokens` |
| `cache_read_tokens` | `usage.cache_read_input_tokens` |
| `cache_write_tokens` | `usage.cache_creation_input_tokens` |
| `reasoning_tokens` | `usage.output_tokens_details.thinking_tokens` |
| `total_cost` | `total_cost_usd` |
| `AgentResult.context_window` | `modelUsage.<model>.contextWindow` |
| `AgentResult.context_tokens` | sum of input + cache_read + cache_creation on the last assistant turn |

Per-token cost breakdowns (`input_cost`, `output_cost`, …) are not exposed by Claude Code — only the total. Leave the components at zero and document it, rather than inventing a split.

### 7. Validation

`validate()` (`agents.py:52-73`) currently does two Pi-specific things: it rejects non-Pi backends (`:61-63`) and it resolves every model against Pi's catalog (`:69`). Branch both per backend. Keep the fail-fast, collect-all-problems, one-`SystemExit` shape — it is good and every ADW depends on it.

Note the pre-existing sharp edge, unchanged by this phase: validation checks that a model is *written* correctly, not that its provider is reachable or its key is set. A missing key still fails partway into a chain.

## Work items

Ordered so each is independently testable.

1. **Generalise the types.** `AgentRequest`/`AgentResult` in `data_types.py`, with `PiRequest`/`PiResult` aliases retained. No behaviour change; existing runs must still pass.
2. **Extract the protocol and registry.** Move Pi behind it; `agents.py` dispatches on `agent.coding_agent` but only `pi` is registered. Still no behaviour change.
3. **Per-backend usage adapters.** Split `UsageBreakdown.add_turn` into a Pi adapter and leave a seam for the second.
4. **Implement `agent_cc.py`**: argv construction, the create-vs-resume state machine, the NDJSON read loop, the noise filter, `resolve_model`, `reachable`.
5. **Implement `ClaudeToolCallTracker`** and wire `_event_forwarder` to pick the tracker per backend.
6. **Extend `agent_map`** with `native_session_id` and `started`; make `execute` read `result.session_id` back.
7. **Branch `validate()`** per backend; add the `claude --version` probe.
8. **Config surface**: `CLAUDE_PATH` next to `PI_PATH` in `templates/env.sample`; the tool-name map and effort mapping documented in `references/config.md`; the determinism defaults documented in `templates/sssf.config.yaml` comments; update the "v1 scope" paragraph in `SKILL.md`.
9. **Smoke test both backends** with `adw_prompt.py`.

New files under `templates/adws/adw_modules/` are stamped automatically by the recursive `stamp()` call at `scripts/install.py:69` — no installer change is needed — and are already covered by `protected_files: adws/adw_modules/`.

## Risks and open questions

- **Tool-name mapping is lossy.** Pi's `find` and `ls` do not map cleanly onto Claude Code's tool set. An unmapped name must be a validation error, not a silent drop — a "read-only" agent that silently lost a tool is a correctness bug, and `--tools` filtering is already documented upstream as a place where capabilities disappear quietly.
- **`harness_engineering` is genuinely backend-specific.** The existing `subagents.ts` extension has no Claude Code equivalent; a Claude Code agent wanting subagents uses `--agents` with a different schema. Do not try to unify these — validate per backend and document the split.
- **`--json-schema` exists on Claude Code** and could enforce the envelope shape at the CLI level, removing some of the `JSON_FIX_ATTEMPTS` correction loop. Tempting, but it would make the two backends behave differently on malformed output. Recommendation: do not use it in this phase; revisit once both backends are stable.
- **Cost comparability.** Pi reports a per-component cost breakdown; Claude Code reports only a total. Any cross-backend cost dashboard has to tolerate that asymmetry.
- **Subscription vs. API-key auth.** The probe ran with `apiKeySource: "none"` (subscription auth). Rate limits then apply per account rather than per key, and a `rate_limit_event` mid-run is a real operational mode, not an edge case.

## Verification

`pi` is **not installed** in the environment where this was researched, so the two backends cannot be verified in the same place. State that honestly in any status report rather than implying both were exercised.

**Claude Code, end to end:**

1. `uv run adws/adw_prompt.py "reply with a one-line summary of this repo" --agent scout` with `scout.coding_agent: claude_code` — expect a green run, a parsed envelope, and events in `adws/adw_data/sssf.db`.
2. Confirm no `commands_changed` payload landed anywhere: the largest `events.payload_json` for the run should be a real tool call, not a skill listing.
3. Run a two-agent chain (`adw_plan_build.py`) and confirm from `agent_map.json` that the second call resumed rather than created — the same `native_session_id`, and no `already in use` error.
4. Run a chain with a Claude Code planner and a Pi builder and confirm the visualizer renders both lanes identically.
5. Confirm `agent_sessions.context_tokens` / `context_window` are populated for a Claude Code agent.

**Pi regression**, wherever Pi is installed: `just demo` must behave exactly as before the refactor.

## Done when

- [ ] `coding_agent: claude_code` runs a real agent; the stub and the `validate()` rejection are gone.
- [ ] Backend is selectable per agent and two backends run in one chain.
- [ ] A multi-call agent phase resumes its Claude Code session instead of creating a second one.
- [ ] `commands_changed` never reaches the trace.
- [ ] Tokens, cost, `context_tokens` and `context_window` are populated for both backends.
- [ ] The visualizer renders both backends with no visualizer changes.
- [ ] Pi behaviour is unchanged, verified where Pi is installed.
- [ ] `references/config.md`, `templates/env.sample` and `SKILL.md`'s scope paragraph reflect reality.
