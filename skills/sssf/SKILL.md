---
name: sssf
description: Super Simple Software Factory — deploy and operate repeatable agents+code workflows (ADWs) in any codebase. Use when the user asks to install the factory (/sssf:sssf install in Claude Code, /skill:sssf install in pi, or plain words in any other agent), wants to create/run/update an ADW, manage the agent roster in sssf.config.yaml, observe running agent workflows, or uninstall the factory and clean the repo back up. Keywords - sssf, software factory, ADW, AI developer workflow, agent pipeline, install factory, uninstall factory, remove adws.
argument-hint: "[install | create adw | run adw | update config | uninstall | ...]"
---

# Super Simple Software Factory (SSSF)

Reusable combination of **agents plus code**: deterministic Python ADW scripts own sequencing, retries, and acceptance; coding agents (on the pi or Claude Code harness, chosen at install and overridable per agent) work inside bounded phases; typed JSON envelopes carry context between them; everything streams into SQLite for the polled visualizer. Agent proposes, code disposes.

## `<skill>` in every command below

The directory this `SKILL.md` lives in. You read this file, so you already know
that path — substitute it, never the literal `<skill>`. It is not the repository
you are installing into: those commands run from the **target repo root**, and
`<skill>` only ever addresses the generators and the trace UI that ship here.

Where it usually is, by harness: `${CLAUDE_PLUGIN_ROOT}/skills/sssf` when this
came from the Claude Code plugin (Claude Code expands that variable; nothing
else does), otherwise the skills directory that harness scans —
`~/.claude/skills/sssf`, `~/.pi/agent/skills/sssf`, `~/.codex/skills/sssf`,
`~/.config/opencode/skills/sssf`, `~/.agents/skills/sssf`, or the project-scoped
twin of any of those.

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
- Observe by querying `adws/adw_data/sssf.db` (WAL — reads never block writers) **when observing is the task**. This is a capability, not a startup step: query it to follow a run you launched or one the engineer asked about, never to volunteer a status report nobody requested. It is the visualizer's mirror, and yours to read — but never the FACTORY's: the code answers every question about a session from that session's own directory, so a repo whose db was deleted still runs, resumes, kills and uninstalls. See [references/observability.md](references/observability.md).
- Report phase status plainly: name, owner, status, error if any.

## Where a run's work lands

Every run executes in its own git worktree, `<worktrees_dir>/<adw_id>`, on its own branch `sssf/<adw_id>`, cut from a base ref pinned at run start. **The engineer's working tree is never touched**, two runs can execute at once, and a failed run leaves its state somewhere you can open instead of somewhere in the way. Three things follow, and they surprise people who expect v1's behaviour:

- **A chain's commits are on its branch, not on yours.** `just integrate <adw_id>` (or the `integrate` phase at the end of `adw_simple_sdlc`) is what lands them, the way `worktree.integration` in the config says to. A branch that has not landed is not a failed run.
- **A branch that has been pushed stays pushed.** Once a session's branch is on the remote (`mode: pr`), every later commit phase in that session pushes to it, so an open pull request shows what the session actually contains — and integrating again updates that PR instead of trying to open a second one. Nothing publishes a branch on its own; the first push is still `just integrate <adw_id>`.
- **A failed or killed run keeps its worktree.** So does any worktree with uncommitted work in it. `just worktrees` lists them with the state of the run that owns each; `just worktrees-prune` reclaims the ones nothing needs.
- **A run that died part-way is picked up, not restarted.** `just resume <adw_id>` re-launches the same workflow against the same session with `--resume`: the agent phases this session already recorded are answered from its own directory under `data_dir` — never the trace db, which a run only ever writes (`↺ replayed`, no tokens, gates still checked against the tree as it is now), everything code owns runs again for real, and the chain reaches the phase that failed. Re-running a chain from the top pays for the whole thing twice.
- **A run can stop and wait for you.** A gate the config's `hitl:` block or `--hitl` turns on ends the process with exit 75 and the session reading `waiting`: `just pending` lists them, `just show <adw_id>` prints the artifact, and `just approve` / `just reject -m "…"` / `just abort` answer it — a reject goes back to the agent that made the artifact, in the same session, and the run asks again. Nothing spends while it waits, and a stale answer (the plan changed since it was shown) is refused. Never approve on the engineer's behalf. [references/config.md](references/config.md#human-in-the-loop-gates).
- **An open pull request has a way back in.** Review comments are not the end of the line: `just pr-review <n>` reads `sssf/<adw_id>` off the pull request's head ref, joins THAT session, and pushes its answer onto the branch already under review — the pull request is updated, never replaced. Unresolved threads are the queue, so nothing is worked twice. `just prs` polls for it.
- **The merge is what ends the session, not the first run.** `just prs` also reaps: a review run still working a branch that has already landed is stopped, and its worktree released. The branch is never deleted — that belongs to whoever merged.
- **The trace does not move.** `data_dir` and the db are anchored to the main checkout, so one db holds every concurrent run and survives a pruned worktree.
- **A run is refused before it costs anything.** `session.ensure()` asks `adw_modules/preflight.py` first, so an unresolvable `base_ref` or an unwritable `data_dir` aborts before a session, a branch or a process record exists — and `agents.validate()` now checks that the key behind each required agent's model is actually set, which used to surface mid-chain. `just doctor` is the same module asked for everything, including the checks too slow to put in front of every run.
- **Nothing polls unless something is polling.** The trace UI and the two watchers are `just up` — one foreground process that owns all three and stops them together. `just status` says whether they are actually up and when each last polled, and the UI carries the same two badges; a watcher nobody started is the one failure this system produces silently, so never answer "it should pick that up" without one of those two. [references/config.md](references/config.md#running-the-watchers).

It is isolation, not a sandbox — an agent with `bash` can leave the worktree, and `permissions.py` is still the boundary. Details in [references/config.md](references/config.md#worktree-per-run).

## What a run may spend, and how long a turn may take

Two bounds, and both failures were silent before they existed.

- **`defaults.timeout_seconds`** — 1800s of wall clock per agent turn, `0` disables it, any agent may raise its own. A harness call is a subprocess plus a blocking read of its output, so an agent that stops emitting blocks the run forever and puts nothing in the trace to notice. The child is killed and the phase fails as `agent_timeout`.
- **`budget.max_cost_usd` / `budget.max_tokens`** — off by default. What one SESSION may spend across every process that joins it, counted from what the trace already recorded, so `--adw-id` re-entry cannot reset it. **A ceiling stops the next turn, never the one in flight**: the turn being paid for finishes and keeps its envelope, and the phase after it fails as `budget_exceeded`. The branch survives the abort like any other failed run, so `just integrate <adw_id>` still lands what was built.

Neither is a sandbox — a run that means to spend $40 says so in the config. [references/config.md](references/config.md#limits--what-a-run-may-spend-and-how-long-a-turn-may-take).

## Request routing (lazy-load the cookbook, then follow it)

| Request | Cookbook |
|---|---|
| install / set up the factory in this repo | [cookbooks/install.md](cookbooks/install.md) |
| "is this repo ready to run?" / something failed before the first phase | `just doctor` — [cookbooks/install.md](cookbooks/install.md#post-install-checklist) |
| remove the factory / clean the repo back up | [cookbooks/uninstall.md](cookbooks/uninstall.md) |
| create a new ADW / workflow | [cookbooks/create_adw.md](cookbooks/create_adw.md) |
| land a run's branch, clean up worktrees | [references/config.md](references/config.md#worktreeintegration) |
| pick a failed run back up where it stopped | [cookbooks/run_adw.md](cookbooks/run_adw.md) |
| stop a run for a human, approve or reject a plan, "why is this run waiting?" | [references/config.md](references/config.md#human-in-the-loop-gates), then [cookbooks/run_adw.md](cookbooks/run_adw.md#when-a-run-is-waiting-for-you) |
| cap what a run may spend, stop a hung agent | [references/config.md](references/config.md#limits--what-a-run-may-spend-and-how-long-a-turn-may-take) |
| start runs from tracked issues, run the watcher | [references/config.md](references/config.md#issues) |
| start everything / "is the watcher even running?" | [references/config.md](references/config.md#running-the-watchers) |
| answer review comments on a run's pull request | [references/config.md](references/config.md#pull_requests) |
| modify an existing ADW chain | [cookbooks/update_adw.md](cookbooks/update_adw.md) |
| create the config / agent roster | [cookbooks/create_config.md](cookbooks/create_config.md) |
| add or retune an agent (model, thinking, tools, prompts) | [cookbooks/update_config.md](cookbooks/update_config.md) |
| move an agent to another harness, or add a harness (Codex, …) | [references/harnesses.md](references/harnesses.md) |
| extend adw_modules with new low-level logic | [cookbooks/update_modules.md](cookbooks/update_modules.md) |
| run / monitor an ADW | [cookbooks/how_to_prompt_for_the_eng.md](cookbooks/how_to_prompt_for_the_eng.md) **first**, then [cookbooks/run_adw.md](cookbooks/run_adw.md) |
| turn a request into an ADW prompt | [cookbooks/how_to_prompt_for_the_eng.md](cookbooks/how_to_prompt_for_the_eng.md) |

Deep specs, when needed: [references/config.md](references/config.md) · [references/harnesses.md](references/harnesses.md) · [references/handoff.md](references/handoff.md) · [references/observability.md](references/observability.md)

## Hard rules (enforced across everything the factory generates)

1. **Validate before running** — every ADW declares `REQUIRED_AGENTS` and calls `agents.validate()` first; a missing/misnamed agent fails before anything spawns.
2. **Typed outputs only** — every agent call pairs with a concrete `EnvelopeBase` subclass in `adw_modules/data_types.py`; parse failures re-prompt the same session (context intact), never restart.
   **The output contract is a synced triad**: (a) the type in `data_types.py`, (b) the JSON example in the agent's `user.md` `## Report` section, (c) `output_type=` at every call site. These are ONE contract — change any one, update all three in the same edit (grep the type name to find every call site).
3. **Gates validate claims, not guesses** — `gate(envelope, run) -> list[str]` violations; failures return to the same session as corrections.
4. **Four-param rule** — any function with more than 4 parameters takes one concrete data type instead (`AgentCall`, `PhaseParams` are the pattern).
5. **One agent, one prompt, one purpose** — identity lives in `system.md`; task shape (user prompt + output type) lives at the call site.
6. **ADW scripts stay thin** — all low-level logic lives in `adw_modules/`.
7. **Every phase earns a description** — one sentence on what it does and why, never a restatement of its name. It is the only intent the trace, the console, and the UI ever show; `commit_plan: "Commit the plan"` is rejected at construction, blank is too.
8. **A known command is code, not an agent** — if you can write the invocation down (`bun test`, `ruff check`), it belongs in a `kind="code"` phase via `adw_modules/quality.py`. Agents are for the parts that need reading and deciding; failures come back to the builder as an envelope either way. A block nobody wired up is not a passing check: it fails with exit 78 and the fix in its message, and `just doctor` names every one of them before a run starts.
9. **`tools:` is a capability list, `writes:` is the boundary** — `bash` runs anything (including `git checkout`) and `write` reaches any path, so a tool list can never make "this agent changes nothing" true. `writes:` per agent and `protected_files` in defaults are enforced in `adw_modules/permissions.py` after every agent call: unauthorized changes are rolled back and the phase dies. The session runtime under `data_dir` is always writable — a read-only agent is read-only with respect to the REPO, never mute.
10. **Every ADW ends in `run.finish()`** — phases passing is not the same as the run being accepted. A test phase that ran a red suite succeeded at its job. Pass `accepted=` so the exit code, the session status, and the banner are decided together and cannot disagree.

## Scope

Two **harnesses**, and the install asks which one this repository runs on:

- `harness: pi` — `pi -p --mode json`. `model` is `provider/model-id`, resolved against `pi --list-models`, and needs that provider's key in `.env`. Its starter prompts tell the planner and scout to fan out with the `subagent_*` tools its `subagents.ts` extension registers.
- `harness: claude_code` — `claude -p`. `model` is an alias (`opus`, `sonnet`, `haiku`) or a full id; the CLI brings its own auth, so a Claude subscription runs the factory with no API key at all. No subagent tools, so its prompts do not mention any.

A third, `harness: fake`, ships alongside them and is **not a production harness**: it answers from a script in its `harness_options` and never calls a model. The install never offers it — a roster that is all fake produces nothing — but an agent may name it, which is what makes a new ADW's first ten iterations free while its chain's SHAPE is still moving. `just doctor` says so on every agent that uses it. [references/harnesses.md](references/harnesses.md#the-fake-harness-a-scripted-stand-in).

The choice decides **what gets stamped**: that harness's roster, prompt set, `harness_engineering/` assets and `.env.sample`. A roster may still name a different harness per agent, and one chain may mix them — but an agent moved between harnesses needs its prompt moved too.

`model`, `thinking`, `tools`, `harness_engineering` and `harness_options` each mean something harness-specific, and validation applies the right rule per agent — see [references/harnesses.md](references/harnesses.md), which also covers adding a third (Codex, …): one module in `adw_modules/harnesses/` plus one directory in `templates/harnesses/`, and nothing else. Everything downstream is harness-agnostic: gates, `permissions.py`, the trace schema and the visualizer cannot tell which one produced a phase.

`claude_code` agents run with `safe_mode: true` by default — no `CLAUDE.md`, skills, plugins, hooks or MCP servers — because a run that depends on whose machine it executed on is the failure this factory exists to remove. That is config, not code; a repository that wants its own `CLAUDE.md` sets it false.

The visualizer app ships in a later pass — observe via sqlite queries until then.
