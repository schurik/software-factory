---
name: sssf
description: Super Simple Software Factory — deploy and operate repeatable agents+code workflows (ADWs) in any codebase. Use when the user says /sssf install, wants to create/run/update an ADW, manage the agent roster in sssf.config.yaml, or observe running agent workflows. Keywords - sssf, software factory, ADW, AI developer workflow, agent pipeline, install factory.
argument-hint: "[install | create adw | run adw | update config | ...]"
---

# Super Simple Software Factory (SSSF)

Reusable combination of **agents plus code**: deterministic Python ADW scripts own sequencing, retries, and acceptance; coding agents (Pi or Claude Code, chosen per agent) work inside bounded phases; typed JSON envelopes carry context between them; everything streams into SQLite for the polled visualizer. Agent proposes, code disposes.

## Startup

Three steps. Then stop.

1. Read [cookbooks/sssf_overview.md](cookbooks/sssf_overview.md) — the system map.
2. `ls adws/adw_*.py` and read each file's `Phases:` docstring line.
3. Print the ADWs as a table — name, the chain, one line on when to reach for it — and **wait for the engineer's request.**

```
| ADW | Chain | Use when |
|---|---|---|
| adw_scout | engineer → scout | read-only recon; nothing changes |
| adw_simple_sdlc | plan → build → test → review → document, 3 commits | the work is real and its shape is not obvious |
```

**Nothing else.** No trace-db queries, no reading the config or the ADW scripts' bodies, no repo inventory, no last-runs summary, no diagnosing an old failure, no "current state" dashboard. None of it was asked for, and it is not free:

- **Volunteered state is guessed state.** An orchestrator that improvised a status board queried a `runs` table and a `payload` column — neither exists (`sessions`, `payload_json`). The spec that would have said so is `references/observability.md`, one lazy read away. Probing to look prepared is how you end up confidently wrong in your first message.
- **It spends the context the real task needs**, before you know what the task is.
- **It is stale on arrival.** State printed before the request describes a system that the very next run changes.

Everything else — the db schema, the roster, the handoff contract — is lazy-loaded through the routing table below, when a request actually calls for it. Reading it early defeats the mechanism.

Two exceptions, both narrow: if the engineer's first message already contains a request, skip the waiting and route it; and if the factory is plainly not installed (no `adws/`, no config), say that in one line instead of the table.

## Orchestrator rules

You run the system, observe the system, and help the user interact with it. **You do no ADW work yourself:**

- Never implement, plan, or test in an agent's place — launch the ADW and watch it.
- Never edit files inside `adws/adw_data/sessions/` — that is the run record.
- Observe by querying `adws/adw_data/sssf.db` (WAL — reads never block writers) **when observing is the task**. This is a capability, not a startup step: query it to follow a run you launched or one the engineer asked about, never to volunteer a status report nobody requested.
- Report phase status plainly: name, owner, status, error if any.

## Where a run's work lands

Every run executes in its own git worktree, `<worktrees_dir>/<adw_id>`, on its own branch `sssf/<adw_id>`, cut from a base ref pinned at run start. **The engineer's working tree is never touched**, two runs can execute at once, and a failed run leaves its state somewhere you can open instead of somewhere in the way. Three things follow, and they surprise people who expect v1's behaviour:

- **A chain's commits are on its branch, not on yours.** `just integrate <adw_id>` (or the `integrate` phase at the end of `adw_simple_sdlc`) is what lands them, the way `worktree.integration` in the config says to. A branch that has not landed is not a failed run.
- **A branch that has been pushed stays pushed.** Once a session's branch is on the remote (`mode: pr`), every later commit phase in that session pushes to it, so an open pull request shows what the session actually contains — and integrating again updates that PR instead of trying to open a second one. Nothing publishes a branch on its own; the first push is still `just integrate <adw_id>`.
- **A failed or killed run keeps its worktree.** So does any worktree with uncommitted work in it. `just worktrees` lists them with the state of the run that owns each; `just worktrees-prune` reclaims the ones nothing needs.
- **The trace does not move.** `data_dir` and the db are anchored to the main checkout, so one db holds every concurrent run and survives a pruned worktree.

It is isolation, not a sandbox — an agent with `bash` can leave the worktree, and `permissions.py` is still the boundary. Details in [references/config.md](references/config.md#worktree-per-run).

## Request routing (lazy-load the cookbook, then follow it)

| Request | Cookbook |
|---|---|
| `/sssf install`, set up the factory in this repo | [cookbooks/install.md](cookbooks/install.md) |
| create a new ADW / workflow | [cookbooks/create_adw.md](cookbooks/create_adw.md) |
| land a run's branch, clean up worktrees | [references/config.md](references/config.md#worktreeintegration) |
| start runs from tracked issues, run the watcher | [references/config.md](references/config.md#issues) |
| modify an existing ADW chain | [cookbooks/update_adw.md](cookbooks/update_adw.md) |
| create the config / agent roster | [cookbooks/create_config.md](cookbooks/create_config.md) |
| add or retune an agent (model, thinking, tools, prompts) | [cookbooks/update_config.md](cookbooks/update_config.md) |
| extend adw_modules with new low-level logic | [cookbooks/update_modules.md](cookbooks/update_modules.md) |
| run / monitor an ADW | [cookbooks/how_to_prompt_for_the_eng.md](cookbooks/how_to_prompt_for_the_eng.md) **first**, then [cookbooks/run_adw.md](cookbooks/run_adw.md) |
| turn a request into an ADW prompt | [cookbooks/how_to_prompt_for_the_eng.md](cookbooks/how_to_prompt_for_the_eng.md) |

Deep specs, when needed: [references/config.md](references/config.md) · [references/handoff.md](references/handoff.md) · [references/observability.md](references/observability.md)

## Hard rules (enforced across everything the factory generates)

1. **Validate before running** — every ADW declares `REQUIRED_AGENTS` and calls `agents.validate()` first; a missing/misnamed agent fails before anything spawns.
2. **Typed outputs only** — every agent call pairs with a concrete `EnvelopeBase` subclass in `adw_modules/data_types.py`; parse failures re-prompt the same session (context intact), never restart.
   **The output contract is a synced triad**: (a) the type in `data_types.py`, (b) the JSON example in the agent's `user.md` `## Report` section, (c) `output_type=` at every call site. These are ONE contract — change any one, update all three in the same edit (grep the type name to find every call site).
3. **Gates validate claims, not guesses** — `gate(envelope, run) -> list[str]` violations; failures return to the same session as corrections.
4. **Four-param rule** — any function with more than 4 parameters takes one concrete data type instead (`AgentCall`, `PhaseParams` are the pattern).
5. **One agent, one prompt, one purpose** — identity lives in `system.md`; task shape (user prompt + output type) lives at the call site.
6. **ADW scripts stay thin** — all low-level logic lives in `adw_modules/`.
7. **Every phase earns a description** — one sentence on what it does and why, never a restatement of its name. It is the only intent the trace, the console, and the UI ever show; `commit_plan: "Commit the plan"` is rejected at construction, blank is too.
8. **A known command is code, not an agent** — if you can write the invocation down (`bun test`, `ruff check`), it belongs in a `kind="code"` phase via `adw_modules/quality.py`. Agents are for the parts that need reading and deciding; failures come back to the builder as an envelope either way.
9. **`tools:` is a capability list, `writes:` is the boundary** — `bash` runs anything (including `git checkout`) and `write` reaches any path, so a tool list can never make "this agent changes nothing" true. `writes:` per agent and `protected_files` in defaults are enforced in `adw_modules/permissions.py` after every agent call: unauthorized changes are rolled back and the phase dies. The session runtime under `data_dir` is always writable — a read-only agent is read-only with respect to the REPO, never mute.
10. **Every ADW ends in `run.finish()`** — phases passing is not the same as the run being accepted. A test phase that ran a red suite succeeded at its job. Pass `accepted=` so the exit code, the session status, and the banner are decided together and cannot disagree.

## Scope

Two coding-agent backends, selectable **per agent**, and one chain may mix them:

- `coding_agent: pi` — `pi -p --mode json`. `model` is `provider/model-id`, resolved against `pi --list-models`, and needs that provider's key in `.env`. Starter default: `gemini-3.6-flash` via openrouter, thinking `medium`.
- `coding_agent: claude_code` — `claude -p`. `model` is an alias (`opus`, `sonnet`, `haiku`) or a full id; the CLI brings its own auth, so a Claude subscription runs the factory with no API key at all.

`model`, `thinking`, `tools` and `harness_engineering` each mean something backend-specific, and validation applies the right rule per agent — see [references/config.md](references/config.md#backends). Everything downstream is backend-agnostic: gates, `permissions.py`, the trace schema and the visualizer cannot tell which one produced a phase.

`claude_code` agents run with `safe_mode: true` by default — no `CLAUDE.md`, skills, plugins, hooks or MCP servers — because a run that depends on whose machine it executed on is the failure this factory exists to remove. That is config, not code; a repository that wants its own `CLAUDE.md` sets it false.

The visualizer app ships in a later pass — observe via sqlite queries until then.
