---
name: agentic-sf
description: Agentic Software Factory — workflows as directories (workflow.yaml + tasks + agent bindings) over a closed stage vocabulary, with one entry point. The successor design to sssf, built beside it on the same engine and the same trace db. Use when the user asks to install agentic-sf (/sssf:agentic-sf install, or plain words), run a workflow with asf, list or check workflows, create or edit a workflow directory, tune an agent's identity or a stage's task, or compare a run with sssf. Keywords - agentic-sf, asf, software factory, workflow.yaml, stage, task file, agent directory, factory.yaml.
argument-hint: "[install | run <workflow> \"<prompt>\" | list | check | create workflow | edit agent | ...]"
---

# Agentic Software Factory (asf)

The same idea as sssf — deterministic code owns sequencing, retries and
acceptance; coding agents work inside bounded phases; typed envelopes cross
the seams; everything streams into SQLite — with a different surface:

- **One entry point.** `just do "<prompt>"`, or `uv run asf/asf.py run <workflow>
  "<prompt>"`. `list`, `check`, `doctor` and the gate verbs beside it.
- **A workflow is a directory**, not a script you copy. `workflow.yaml` names
  stages from a closed vocabulary and gives each its options. Loops and
  conditions live in the stages; the YAML gets numbers.
- **An agent is a directory** (`agent.yaml` + `system.md`). Its task is not
  there: a task belongs to the stage that calls the agent, and a workflow may
  override it with its own file.

## `<skill>` in every command below

The directory this `SKILL.md` lives in. Substitute it, never the literal
`<skill>`. Commands run from the **target repo root**.

## Startup

Two steps. Then stop.

1. If `asf/factory.yaml` does not exist, say in one line that the factory is
   not installed here and offer the install cookbook. Otherwise:
2. Run `just list` (or `uv run asf/asf.py list`) and print it — one line per
   workflow, name and description — and **wait for the engineer's request.**

Nothing else. No trace-db queries, no reading the config, no reading stages
or engine code, no "current state" summary. Everything below is lazy-loaded
when a request calls for it.

Exception: if the engineer's first message already contains a request, skip
the waiting and route it.

## Orchestrator rules

You run the system and help the engineer interact with it. **You do no
workflow work yourself**: never plan, implement or test in an agent's place —
launch the workflow and watch it. Never edit files under `asf/data/`; that is
the run record. The trace db (`adws/adw_data/sssf.db`, shared with sssf) is
yours to query when observing is the task, never to volunteer a status board.

## Where things live in a stamped repo

```
asf/
  factory.yaml            the manifest: defaults, budget, gates, trace, worktree. No agents in it.
  asf.py                  the runner: list | check | run
  agents/<name>/          agent.yaml (model, thinking, tools, writes, purpose) + system.md (identity)
  workflows/<name>/       workflow.yaml, optional tasks/<key>.md, optional agents/<x>.md
  stages/<name>/          stage.py (the contract) + its default task files
  engine/                 the machinery: session, worktree, gates, permissions, hitl, tracer, …
  data/                   runtime: sessions/<adw_id>/ — never edit
```

Every run works in its own worktree on branch `asf/<adw_id>`; the checkout is
never touched. A run that is not accepted keeps its worktree. A gate a stage
turns on with `hitl: true` stops the run with exit 75 and the session reading
`waiting`.

## Request routing

| Request | Do |
|---|---|
| install / set up the factory here | `uv run <skill>/scripts/install.py --harness claude_code\|pi`, then `just doctor` |
| "is this repo ready to run?" / something failed before the first phase | `just doctor` — every check with its fix, then every workflow checked; spawns nothing |
| run a workflow | `just do "<prompt>"` (sdlc), `just quick`, `just ship`, or `just run <name> "<prompt>" [--hitl all\|none\|plan]` |
| which workflows exist / what does X do | `uv run asf/asf.py list`; read `asf/workflows/<name>/workflow.yaml` |
| is this workflow runnable | `uv run asf/asf.py check <name>` — spawns nothing, names every problem |
| create a workflow | copy the closest directory under `asf/workflows/`, edit `workflow.yaml`, run `check`. Read [references/design.md](references/design.md#workflows) first |
| tailor an agent's TASK for one workflow | add `asf/workflows/<name>/tasks/<key>.md` — keys are the stage's TASKS (plan, implement, fix). Keep the `## Report` block matching the type; `check` verifies it |
| tailor an agent's IDENTITY for one workflow | bind it in `workflow.yaml` under `agents:` with `system_append: [agents/<x>.md]` — append, never replace |
| change an agent for every workflow | edit `asf/agents/<name>/agent.yaml` or `system.md` |
| add a stage to the vocabulary | a directory under `asf/stages/` meeting the contract in `asf/engine/stage.py`; [references/design.md](references/design.md#stages) |
| pick a failed run back up | `just resume <id>` — replays recorded agent phases, re-runs what code owns |
| a run is waiting at a gate / "why is this run waiting?" | `just pending`, `just show <id>`, then `just approve <id> [-m]`, `just reject <id> -m "..."` or `just abort <id>`. Never approve on the engineer's behalf |
| watch a run | `just sessions`, `just phases <id>`, `just tail <id>`; `just obs` boots sssf's visualizer over the shared db |

## Hard rules

1. **A workflow is refused before it costs anything.** `check` and `run` load
   the whole directory first: unknown stage, unknown option, a `verify` with no
   build before it, an agent the roster lacks, a task whose report block
   drifted from the envelope type, a binding that widens `writes` or `tools`.
2. **The vocabulary is closed.** No `loop:` or `if:` in workflow.yaml, ever. A
   shape the vocabulary cannot express is a new stage in Python, or a
   `workflow.py` escape hatch (not yet stamped).
3. **Bindings narrow, never widen.** The roster is the security boundary; a
   workflow is edited often.
4. **Identity is appended, never replaced.** Five workflows must not become
   five builders.
5. **Tasks carry the words, stages carry the facts.** A stage passes
   `{{variables}}`; no prose lives in Python.
6. Everything sssf's hard rules say about envelopes, gates, `writes:`,
   `protected_files`, phase descriptions and `run.finish(accepted=)` holds
   unchanged — it is the same engine.

## What is not here yet

Slices one and two: plan, implement, verify, review, document, commit,
integrate; `sdlc`, `quick` and `ship`; the loader and runner; the gate CLI;
doctor; the justfile. Not yet ported from sssf: issue and pull-request inputs
and their watchers, `up`/`status`, kill, worktree pruning, uninstall. The
visualizer needs no port — same db, same schema; `just obs` needs `SSSF_SKILL`
in `.env` pointing at the sssf skill.
