# Update Config

Add or retune agents in `sssf.config.yaml`.

## Retune model or thinking

Edit the agent's entry in place:

```yaml
  - name: builder
    model: google/gemini-3.6-flash   # ALWAYS provider/model-id
    thinking: high                   # was medium
```

**The shape of `model` belongs to the agent's harness.** On `pi` write it as `provider/model-id`, never a bare id: the same model is usually carried by several providers, and an ambiguous pattern raises in `agents.validate()` — grounding every agent that inherits it. On `claude_code` write an alias (`opus`, `sonnet`, `haiku`) or a full id, and a `provider/id` pattern is rejected outright. See [references/harnesses.md](../references/harnesses.md).

Thinking is one ladder — `off | minimal | low | medium | high | xhigh | max` — read differently per harness: pi's reasoning effort, honored when the model is registered with `reasoning: true` in `~/.pi/agent/models.json`; Claude Code's `--effort`, where `off` and `minimal` both collapse to `low`.

**A model change means a fresh session.** `agent_map.json` records the model and the harness each coding-agent session was created with. When a joined run (`--adw-id`) finds the config's model no longer matches the recorded one, that agent starts a **new** session rather than resuming — the map is updated, never a bad resume. Thinking changes do not invalidate a session; model and harness changes do. Expect the agent to lose its accumulated context window on the first run after the change.

## Recolor an agent's lane

```yaml
  - name: builder
    color: "#22d3ee"      # hex; the starter roster ships violet/cyan/amber/green
```

Purely cosmetic and safe to change mid-project: the color rides the `agent_start` event and the `agent_sessions` row, so the visualizer picks it up on the next run without touching past sessions. Omit the key to let the UI's fallback palette choose.

## Retune tools

Pi's seven builtins: `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`. The last three are **off in bare Pi**, so an agent that doesn't name them will shell out through `bash` to search and list.

Set the roster-wide floor in `defaults`, then narrow per agent:

```yaml
defaults:
  tools: [read, bash, edit, write, grep, find, ls]

agents:
  - name: reviewer
    tools:                # explicit list wins over defaults
      - read
      - grep
      - find
      - ls
      - bash
      - write
```

**Resolution:** the agent's own list wins → else it inherits `defaults.tools` → else `None`, meaning all tools. An empty list is not "all tools"; it is a tool-less agent, and it will stall.

Narrow by role, not by reflex:

- Any agent that must produce a `context_handoff/` artifact needs **`write`** — without it, it falls back to a `bash` heredoc to create the file the gate checks for.
- Withhold `edit`/`write` only where the restriction *is* the guarantee. The reviewer's contract is "change nothing", so withholding `edit` makes that structural instead of merely prompted.
- Recon agents should get the full read surface (`read`, `grep`, `find`, `ls`) — cheaper and more legible in the trace than the equivalent `bash` calls.

**Extension tools count against the allowlist.** `--tools` filters built-in, extension, and custom tools alike. Once an agent has a `tools` list — its own, or inherited from `defaults` — a tool registered by one of its `harness_engineering` extensions is dropped unless it is named there. Nothing errors: the extension loads, the run passes, the tool is just never offered. Any agent with a tool-registering extension must list that tool by name.

## Add harness extensions

```yaml
    harness_engineering:
      - .pi/extensions/json_guard.ts    # a pi extension FILE PATH
```

Entries are harness-specific. On `pi` they are extension **file paths**, passed through as `pi -e <path>`; on `claude_code` they are `mcp:<file.json>` / `agents:<json-or-file>` / `plugin:<dir-or-zip>` entries, and `safe_mode: true` suppresses all three (validation refuses that combination rather than loading nothing). Applied to that agent only. Reach for an output-tightening extension when an agent keeps wrapping its envelope in prose and burning correction retries. The pi roster ships one — `subagents.ts` on the planner and the scout, with its four tools named in their `tools:` lists; the claude_code roster ships none.

**Adding a tool-registering extension is a two-part edit.** The extension path goes in `harness_engineering`, *and* the tool name it registers goes in that agent's `tools` list:

```yaml
  - name: reviewer
    harness_engineering:
      - .pi/extensions/ast_query.ts     # registers tool: ast_query
    tools:
      - read
      - grep
      - find
      - ls
      - bash
      - ast_query                       # REQUIRED — or the extension loads and its tool is filtered out
```

Skip the second half and it fails silently: extension loaded, run green, tool never available to the model. Extensions that only shape output or register flags — no new tool — need no `tools` change.

## Move an agent to another harness

Four edits, and the roster will not start if you skip one:

```yaml
  - name: reviewer
    harness: claude_code             # was pi
    model: sonnet                    # alias or full id — a provider/id pattern is rejected
    tools: [Read, Grep, Glob, Bash, Write]   # Claude Code's names (pi's are mapped too)
    harness_options:                 # merged key by key over defaults.harness_options.claude_code
      permission_mode: bypassPermissions
```

1. `harness:` and a `model:` of that harness's shape.
2. `tools:` in a vocabulary that harness has. `subagent_*` exists only on pi — an unmapped name is a validation error, never a silent drop.
3. `harness_engineering:` entries of that harness's kind, or none. Pi's `.ts` extensions have no Claude Code equivalent.
4. **The agent's prompt.** `system.md` was stamped for the old harness. The pi planner and scout carry a `## Subagents` section describing tools the new harness does not have — delete it, or the agent spends its turn trying to call them.

The agent starts a fresh session after the move: a pi session id is not a UUID, and Claude Code would refuse it outright.

## Add a new agent

Three steps, all required — skipping any one fails `agents.validate()` at ADW startup, before anything spawns:

1. **Prompts.** Create `adws/adw_data/prompt_engineering/{name}/system.md` (Purpose + Instructions — the agent's static identity, nothing else) and `user.md` (an h3 per incoming datum: `{{prompt}}`, `{{previous_envelope}}`, `{{context_handoff_dir}}`, then the task, then a `## Report` section showing the exact output JSON). Copy an existing pair as the shape.
2. **Config entry.** Name, purpose, prompt refs, plus anything that differs from `defaults` — including `harness:` if it does not run on the roster's default one.
3. **An output type.** Every agent call parses against a concrete Pydantic model in `adw_modules/data_types.py`. If none of `PlanOutput`, `BuildOutput`, `ScoutOutput`, `ReviewOutput`, `DocumentOutput` fits the new agent's report, add one — see `update_modules.md`. The user prompt's `Report` section must show exactly that JSON shape.

Then name the agent in an ADW's `REQUIRED_AGENTS` and call it.

## Retune what the factory may DO, not just how it thinks

Three blocks are about this repository's policy rather than any agent's
behaviour, and they are edited by hand like everything else here:

| Change | Key | Watch out |
|---|---|---|
| Stop a machine moving the base branch | `worktree.integration.mode: pr` | Also set `open_pr` and, if the PR should close its issue, `pr_body_template` — and drop `--fill` from `pr_command`, which is mutually exclusive with an explicit body |
| Let issues start runs | `issues.enabled: true` + a `route` | Nothing launches without a routing label a human applied. `project` empty infers from the origin remote, which is fine from a terminal and wrong under cron |
| Widen or narrow what an agent may touch | `writes:` per agent, `defaults.protected_files` | `tools` cannot do this job: `bash` runs `git checkout` and `write` reaches any path. Only `permissions.py` enforces, after every call |
| Keep a successful run's worktree | `worktree.keep_on_success: true` | Useful while debugging a chain; costs disk per run otherwise |
| Cap what a session may spend | `budget.max_cost_usd`, `budget.max_tokens` | Per **session**, not per process — a joined run counts from what the trace says was already spent. The ceiling stops the NEXT agent turn, never the one in flight, so nothing already paid for is thrown away. Ships off (`0`) |
| Stop an agent hanging forever | `defaults.timeout_seconds`, or per agent | Wall clock for ONE turn, and each JSON re-prompt and gate correction gets its own. Raise it for an agent whose honest work is long before you blame the limit; `0` disables it |

Full specs: [worktree](../references/config.md#worktree-per-run) · [integration](../references/config.md#worktreeintegration) · [issues](../references/config.md#issues) · [write permissions](../references/config.md#write-permissions--writes-and-protected_files) · [limits](../references/config.md#limits--what-a-run-may-spend-and-how-long-a-turn-may-take).

## Rules that do not bend

- ADW scripts name **agents**, never models. Swapping a model is a config edit and touches no Python.
- One agent, one prompt, one purpose. If an entry needs two purposes, it is two agents.
- Output types never appear in config — they live at the call site, paired with the user prompt.

Full spec: `references/config.md`.
