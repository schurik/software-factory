# Harnesses

A **harness** is one coding-agent CLI plus everything specific to it: how a model
pattern is written, which tool names exist, what `thinking` maps to, how a session is
created versus continued, which options block it reads — and, because the answers differ,
what its agents are *told* in their prompts.

The factory ships two a repository can pick at install time, plus one that answers
from a script and never calls a model — see [The `fake` harness](#the-fake-harness-a-scripted-stand-in)
at the bottom.

| | `harness: pi` | `harness: claude_code` |
|---|---|---|
| Command | `pi -p --mode json` | `claude -p --output-format stream-json --verbose` |
| Binary | `PI_PATH`, default `pi` | `CLAUDE_PATH`, default `claude` |
| Auth | the provider key named by `model`'s provider half, from `.env` | the CLI's own — `claude auth`, so a **subscription needs no key** |
| `model` | `provider/model-id`, resolved against `pi --list-models` | an alias (`opus`, `sonnet`, `haiku`) or a full id (`claude-sonnet-5`) |
| `thinking` | `--thinking <level>` | `--effort <level>`; `off` and `minimal` collapse to `low` |
| `tools` | lowercase pi names | Claude Code names, or pi's names mapped onto them |
| `harness_engineering` | `-e <file.ts>`, repeatable | `mcp:` / `agents:` / `plugin:` entries |
| `harness_options` | none yet (argv + env) | `safe_mode`, `bare`, `setting_sources`, `strict_mcp_config`, `permission_mode`, `add_dirs`, `max_budget_usd` |
| Sessions | `--session-id`, create-or-continue | `--session-id` to create, `--resume` after that |
| Cost detail | per component **and** total | total only |
| Fan-out in the starter prompts | `subagent_*`, from `harness_engineering/subagents.ts` | none — the planner and scout do their own recon |

Everything downstream of the agent call — gates, `permissions.py`, the trace schema, the
visualizer — is harness-agnostic and cannot tell which one produced a phase. A chain may
mix harnesses; `agents.validate()` applies each agent's own harness's rules.

## Two halves, and both are per-harness

**The code half** is `adws/adw_modules/harnesses/<name>.py`. `agents.py` dispatches on it
and knows nothing else about any harness:

| Name | What it is |
|---|---|
| `NAME` | the config value (`harness: pi`) |
| `Options` | pydantic model for `harness_options`, `extra="forbid"` |
| `resolve_model(pattern)` | pattern → whatever the CLI needs; `ValueError` if unwritable |
| `reachable()` | raise unless the CLI can be executed (cache it — one probe per process) |
| `credentials(agent)` | *optional* — `Finding`s on whether this agent can authenticate at all. Read by `preflight.py` and `agents.validate()`, never by `agents.execute`. A CLI that brings its own auth may omit it (or say so, which is what `claude_code` does). **Never read a key's value** — whether the variable naming it is set is the entire answer, and `fatal` is only for a name the harness actually knows |
| `validate_agent(agent)` | harness-specific config problems, as a list of strings |
| `new_session_id(adw_id, agent)` | a fresh id for this agent's context window |
| `ToolCallTracker` | folds the CLI's event stream into `tool_calls.py` records |
| `run(request, on_event, on_spawn, on_exit)` | one turn: `AgentRequest` → `AgentResult` |

**The template half** is `<skill>/templates/harnesses/<name>/`, which is what the
installer stamps:

| File | Becomes |
|---|---|
| `about.md` | line 1 is the one-liner the install question shows; the rest is printed as that harness's post-install steps |
| `defaults.yaml` | the `defaults:` block of the generated `sssf.config.yaml` |
| `agents.yaml` | its `agents:` roster — models, tool names and extensions in this harness's vocabulary |
| `env.sample` | `.env.sample` — the keys this harness actually needs |
| `prompt_engineering/<agent>/{system,user}.md` | `adws/adw_data/prompt_engineering/` — **a full prompt set per harness** |
| `harness_engineering/` | `adws/adw_data/harness_engineering/` — extensions, MCP configs, or a README explaining what belongs there |

The generated config is those two YAML files with the harness-agnostic middle
(`templates/config/base.yaml`: `observability`, `worktree`, `issues`, `pull_requests`)
between them — plain concatenation, comments intact.

### Why the prompts are per-harness, not shared

The starter pi planner and scout are told to fan out with `subagent_create` /
`_continue` / `_list` / `_remove`, tools that exist only because
`harness_engineering/subagents.ts` registers them. On Claude Code those names map to
nothing, and an unmapped tool name is a validation error — so the same prompt on the
other harness is at best a paragraph of fiction and at worst a roster that will not
start. Prompts are a full set per harness for that reason, and because it keeps the
stamped file the whole truth: what you read in
`adws/adw_data/prompt_engineering/<agent>/system.md` is exactly what the agent is sent.

The cost is duplication in the skill. When you edit a shared instruction, edit it in
every harness's set — `grep -rl "<the sentence>" templates/harnesses/*/prompt_engineering`.

## The `fake` harness: a scripted stand-in

`adw_modules/harnesses/fake.py` is registered like any other harness and is **not a
production harness**: it answers from a script written into its `harness_options` and
never calls a model. It exists because the expensive half of a run is the agent, and
everything around the agent is deterministic machinery that used to be testable only by
paying for a real chain.

Two uses, both real:

- **The factory's own test suite** (`skills/sssf/tests/`) walks whole chains with it —
  phases, gates, gate corrections, JSON re-prompts, envelopes, permissions, budgets,
  the trace — in seconds, with no network and no token.
- **Developing a new ADW.** The SHAPE of a chain (which phases, in which order, with
  which gates and output types) is settled long before the prompts are any good. Put the
  roster on `harness: fake` until the shape holds, then move it back. The iteration is
  free.

```yaml
  - name: planner
    harness: fake
    model: fake
    harness_options:
      replies:                       # one reply per SEND, not per phase
        - writes: {"specs/plan.md": "# Plan\n"}
          envelope: {status: success, summary: planned, artifacts: [specs/plan.md]}
```

`writes`/`deletes` change the run's worktree before the reply is reported, which is what
lets a scripted turn drive gates that measure the TREE. A phase sends more than once when
a gate fails or the JSON does not parse, so a first reply that claims an artifact it never
wrote followed by one that writes it is a real exercise of the correction loop.
`strict: true` makes running out of script a loud failure instead of repeating the last
reply; `script: <file.json>` keeps a long one out of the roster. `sleep:` past the agent's
`timeout_seconds` raises `AgentTimeout`, the same exception a real harness raises.

**It is deliberately absent from `templates/harnesses/`**, so the installer never offers
it as the harness a repository runs on — a factory whose whole roster is fake produces
nothing. Its `credentials()` says what it is in every `just doctor` report, and the trace
records `harness: fake` on every phase it touched.

## Adding a harness (Codex, or whatever is next)

1. **`templates/adws/adw_modules/harnesses/<name>.py`** — the eight names above.
   `pi.py` is the shorter model to copy; `claude_code.py` shows what a harness with real
   `Options` and a create-vs-resume session model looks like.
2. **Register it**: one import and one entry in `harnesses/__init__.py`. Registration is
   explicit on purpose — a half-written module should fail at import, not during a run's
   config validation.
3. **`templates/harnesses/<name>/`** — the six template files above. The installer
   discovers harnesses by listing that directory, so nothing in `scripts/` needs editing:
   the new harness simply appears in the install question.
4. **Nothing else.** The installer, `agents.py`, the tracer, gates, `permissions.py` and
   the visualizer are already generic. If you find yourself editing one of them to make a
   harness work, the seam is in the wrong place — say so rather than widening it.

Two things worth getting right the first time:

- **`reachable()` is a probe, and it runs at validation.** Cache it (`lru_cache`), keep
  it under a timeout, and make the error say how to install the CLI. A harness whose
  probe hangs makes every ADW hang before its first phase.
- **`run()` must stream.** Read the child's stdout line by line and call `on_event` as
  events arrive — the trace is written while the run is in flight, and a harness that
  buffers to completion turns the live UI into a progress bar with one step. Use
  `stdin=DEVNULL`; a non-interactive CLI that inherits a terminal can block forever
  waiting for input that never comes.
- **...and it must arm `request.timeout_seconds`.** That streaming read is a block on a
  child that may never write again, and it is the one failure the factory cannot see:
  no events, no tokens, no output, a phase that started and never ended. Wrap the read
  AND the `wait()` in `limits.Deadline(process, request.timeout_seconds)` and raise
  `limits.AgentTimeout(deadline.reason(NAME, request.raw_output_path), result)` when
  `deadline.fired` — ahead of the returncode check, or a terminated child reads as an
  ordinary crash. Pass the partial `AgentResult`: a hung turn was still paid for, and
  `agents.execute` banks its usage against the session's budget. Both harnesses do this
  identically; copy either one.
