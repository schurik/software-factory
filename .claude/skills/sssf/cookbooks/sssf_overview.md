# SSSF Overview

The system map the orchestrator reads on startup — what SSSF is, how a stamped repo is laid out, and which cookbook to load next.

## What SSSF is

Super Simple Software Factory builds repeatable **agents plus code** workflows. Deterministic Python (an ADW script) owns sequencing, retries, and acceptance; agents are bounded nodes inside that graph. Agent proposes, code disposes.

Your job as orchestrator: **run the system, observe the system, help the engineer interact with it.** You do not do the work an ADW exists to do.

## Layout of a stamped repo

```
adws/
├── adw_sssf_config/
│   └── sssf.config.yaml         the agent roster — one agent, one prompt, one purpose
├── adw_prompt.py                smallest ADW: one agent, one prompt, traced end-to-end
├── adw_plan.py, adw_scout.py, adw_build.py, adw_plan_build.py, adw_build_test.py, adw_plan_build_test.py
├── adw_build_review.py          build → review: is this what was asked for? (not testing)
├── adw_document.py              write up the work just done, from git diff vs main
├── adw_quality.py               lint/typecheck/build/test as CODE — no agent rediscovers a command
├── adw_plan_build_test_quality.py   the above, folded into one chain
├── adw_simple_sdlc.py           plan → build → test → review → document → integrate; commits each product
├── adw_integrate.py             land a finished run's branch, the way the config says to
├── adw_issue_sdlc.py            the same chain, but a labelled issue is the ask and hears the outcome
├── adw_issue_scout.py           read-only triage of an issue; comments its findings back
├── adw_pr_review.py             answer the review threads on a run's own PR, in that same session
├── adw_modules/                 ALL low-level logic — ADW scripts stay thin
│   ├── data_types.py            AgentCall, PhaseParams, Phase, Envelope + one output type per agent call
│   ├── agents.py                load_config, validate, resolve entry → interface + model + thinking
│   ├── runner.py                the Run object: run.phase(PhaseParams) → ph.call(AgentCall)
│   ├── agent_pi.py              Pi backend  ·  agent_cc.py  Claude Code backend
│   ├── tool_calls.py            the normalized tool-call record both backends emit
│   ├── gates.py                 gate(envelope, run) -> GateReport — one check per item verified
│   ├── permissions.py           the write boundary: unauthorized changes rolled back, phase dies
│   ├── worktree.py              a git worktree + branch per run  ·  integration.py  lands it again
│   ├── quality.py               lint/typecheck/build/test blocks → QualityResult → envelope
│   ├── changes.py               git diff vs a resolved base → ChangeSet → envelope for the documenter
│   ├── issues.py                fetch a work item, hand it on as an envelope, write the outcome back
│   ├── pull_requests.py         the same, one step later: read review threads, answer them, resolve them
│   ├── prompts.py, session.py, tracer.py, console.py, git_helper.py, utils.py
└── adw_data/
    ├── prompt_engineering/{agent}/{system.md,user.md}   tracked — edit prompts HERE, never in the skill
    │                                planner · builder · scout · reviewer · documenter
    ├── sessions/{adw_id}/                               gitignored runtime
    │   ├── agent_map.json       agent → coding-agent session_id + model
    │   ├── context_handoff/     the one place agents write files for the agents that follow
    │   └── {agent}/{prompts/, raw_output.jsonl, envelope.json}
    └── sssf.db                  gitignored SQLite trace db the visualizer polls
```

**Every run gets its own worktree and branch.** `.sssf-worktrees/<adw_id>` on `sssf/<adw_id>`, cut from whatever the main checkout had at run start. The engineer's tree is never touched, two runs can execute at once, and a failed run keeps its worktree because that is where you go to see what happened. A chain's commits are therefore **on its branch, not on the engineer's** until the integration phase lands them the way `worktree.integration` says to — a branch that has not landed is not a failed run. `just worktrees`, `just worktrees-prune`, `just integrate <adw_id>`.

**A run can start from a tracked issue.** Off by default. When `issues.enabled` is on, `scripts/issue_watch.py` polls for a routing label a human applied, claims the issue, and launches the ADW that label maps to. Fetching the issue is a `kind="code"` phase, and its result reaches the next agent as an ordinary envelope — the body arrives as an ARTIFACT framed as a user's description of a problem, never as instructions, because on this one path the prompt is written by whoever can file an issue. `issues.force_pr` keeps such a run off the base branch whatever the integration mode says.

**Two backends, chosen per agent.** `coding_agent: pi` runs `pi -p --mode json` (model is `provider/model-id`, needs that provider's key); `coding_agent: claude_code` runs `claude -p` (model is an alias or a full id, and the CLI brings its own auth, so a subscription needs no key). One chain may mix them, and everything downstream — gates, permissions, the trace, the visualizer — cannot tell which ran a phase. Starter default: `pi`, `gemini-3.6-flash`, thinking `medium`.

## The phase model

Every ADW run is a sequence of **phases**, each one `with run.phase(PhaseParams(...))`. Three kinds, three swim lanes:

- **engineer** — the human lane; today the system-input phase (who asked, and for what).
- **agent** — `ph.call(AgentCall(...))`: prompt in → typed envelope out → gates verified.
- **code** — deterministic steps that stand alone (git commit, run the suite, fetch an issue, land a branch). Never buried inside an agent phase. **A known command is code, not an agent**: if you can write the invocation down, it belongs here — an agent rediscovering `bun test` every run costs a million tokens to learn what a subprocess already knows. Failures still reach the next agent as an envelope, through `quality.as_envelope` / `changes.as_envelope` / `issues.as_envelope`.

**Success must be earned — every phase defaults to `fail`.** A clean exit flips it to success; agent phases additionally require the envelope to parse and all gates to come back green. A raise keeps it failed, records an error event, and aborts the run. `retries=N` on an agent phase buys extra gate-correction rounds through the same session before that raise happens.

## Envelopes

Agents have exactly two output channels: reference files written into `context_handoff/`, and a **final valid-JSON response** parsed against the output type the call declared. Code persists it as `envelope.json` and injects it into the next agent's `user.md` via `{{previous_envelope}}`. Bad JSON is never a restart — the harness re-prompts the *same session, context intact*, until it parses (bounded). See `references/handoff.md`.

**The output contract is a synced triad**: the type in `data_types.py` ↔ the `## Report` JSON example in the agent's `user.md` ↔ `output_type=` at the call site. Editing any one of the three means editing all three in the same change — drift between them taxes every call with correction retries.

## Running an ADW

```bash
uv run adws/adw_plan.py "add a /health endpoint"
uv run adws/adw_plan_build.py requests/health.md --adw-id a1b2c3d4
```

The prompt is inline text or a file path. `--adw-id` is optional on every ADW: given one, the run joins that session (same dirs, same `context_handoff/`, agents resume their existing context windows); omitted, a fresh id is minted and printed.

## When you have finished reading this

You are done with startup. List the ADWs (`ls adws/adw_*.py`, plus each `Phases:` docstring line) as a table, and **wait for the engineer's request.**

Do not survey anything else — not the trace db, not the config, not past runs, not the repo tree. You do not yet know what the request is, so anything you gather now is a guess about what will matter, spent from the context the real work needs. Every cookbook and reference below is lazy-loaded, one per request, and that is the whole design.

## Where to go next

Load one cookbook per request — this overview is the only one you read up front.

| Request | Cookbook |
|---|---|
| Turn a request into the prompt an ADW gets | `how_to_prompt_for_the_eng.md` — **read before every launch** |
| Set the system up in a repo | `install.md` |
| Write a new ADW script | `create_adw.md` |
| Change an existing ADW chain | `update_adw.md` |
| Generate `sssf.config.yaml` | `create_config.md` |
| Add or retune an agent | `update_config.md` |
| Add low-level logic or a gate | `update_modules.md` |
| Run and monitor a workflow | `how_to_prompt_for_the_eng.md`, then `run_adw.md` |
| Land a run's branch, clean up worktrees | `references/config.md#worktreeintegration` |
| Start runs from issues, run the watcher | `references/config.md#issues` |

References, loaded when you need the spec: `references/config.md` (full config schema), `references/handoff.md` (envelope + session layout), `references/observability.md` (events, db tables, polling).
