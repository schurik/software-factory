# Config Reference

The full `sssf.config.yaml` spec: every field, how defaults merge, and how model / thinking / tools / extensions map onto the coding agent.

It lives at **`adws/adw_sssf_config/sssf.config.yaml`** — the default path every `adw_*.py` and the justfile resolve, and where `install.py` / `make_config.py` stamp it. Pass `--config <path>` to any ADW (or set `SSSF_CONFIG` for the justfile) to run against a different roster.

## Shape

```yaml
defaults:
  coding_agent: pi
  model: google/gemini-3.6-flash        # ALWAYS provider/model-id
  thinking: medium
  harness_engineering: []
  tools: [read, bash, edit, write, grep, find, ls]
  data_dir: adws/adw_data

observability:
  db: adws/adw_data/sssf.db
  poll_ms: 500

worktree:
  enabled: true
  dir: .sssf-worktrees
  branch_prefix: "sssf/"
  base_ref: ""
  keep_on_success: false
  integration:
    mode: merge                           # none | merge | pr
    merge_flags: ["--no-ff"]
    remote: origin
    open_pr: false
    pr_command: ["gh", "pr", "create", "--fill"]

agents:
  - name: planner
    coding_agent: pi
    model: google/gemini-3.6-flash        # ALWAYS provider/model-id
    thinking: high
    color: "#a78bfa"
    purpose: Turn a request into a plan the builder can implement without asking questions.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/planner/system.md
      user: adws/adw_data/prompt_engineering/planner/user.md
    harness_engineering:
      - json-enforcer
    tools:
      - read
      - bash
```

## Fields

### `defaults`

| Field | Type | Meaning |
|---|---|---|
| `coding_agent` | `pi` \| `claude_code` | Which interface runs the agent. **v1 implements `pi` only**; `claude_code` is specced and stubbed in `agent_cc.py`, landing in v2. |
| `model` | string | Model id. For Pi, any id registered in `~/.pi/agent/models.json`. Default `gemini-3.6-flash`. |
| `thinking` | enum | Reasoning effort — see below. Default `medium`. |
| `color` | hex string | Lane color for every agent that does not set its own. Default empty — the visualizer falls back to its own palette. |
| `harness_engineering` | list[string] | Coding-agent extensions. Pi: extension names. Claude Code: reserved (MCP, hooks). |
| `tools` | list[string] | Roster-wide tool allowlist. Every agent that omits its own `tools` inherits this. Unset = all tools usable. |
| `protected_files` | list[string] | Paths **no** agent may modify unless it names them in its own `writes`. Default: `adws/adw_modules/`, `adws/adw_sssf_config/`, `adws/adw_*.py` — an agent must not be able to edit the machinery that decides whether its work passed. |
| `data_dir` | path | Runtime home. Sessions land at `{data_dir}/sessions/{adw_id}/{agent_name}/`. Default `adws/adw_data`. **Resolved against the MAIN checkout, never against a run's worktree** — see [Worktree per run](#worktree-per-run). |

### `observability`

| Field | Type | Meaning |
|---|---|---|
| `db` | path | SQLite trace db. `tracer.py` writes it directly; the visualizer polls it. Default `adws/adw_data/sssf.db`. |
| `poll_ms` | int | Visualizer live-poll cadence in ms. History uses the same queries, lazy-paged. Default `500`. |

### `worktree`

Every run executes in its own git worktree, on its own branch, cut from a base ref pinned once at run start. See [Worktree per run](#worktree-per-run) below for what that changes.

| Field | Type | Meaning |
|---|---|---|
| `enabled` | bool | Default `true`. `false` runs in the main checkout exactly as v1 did. |
| `dir` | path | Where worktrees live, relative to the main checkout. Default `.sssf-worktrees` — gitignored by `install.py`. |
| `branch_prefix` | string | The run's branch is `<prefix><adw_id>`. Default `sssf/`. |
| `base_ref` | ref | What to cut from. Default `""` = whatever the main checkout has checked out when the run starts. Set it to pin every run to one branch. |
| `keep_on_success` | bool | Default `false`: an accepted run's worktree is removed **if it is clean**, and its branch is always retained. `true` keeps every worktree. |

### `worktree.integration`

How a run's branch gets back to its base. Configuration rather than code, because repositories disagree.

| Field | Type | Meaning |
|---|---|---|
| `mode` | `none` \| `merge` \| `pr` | Default `merge`. `none` leaves the branch alone. `pr` pushes it and leaves the merge to a human. |
| `merge_flags` | list[string] | Flags for the merge. Default `["--no-ff"]`, so a run stays legible as one merge. |
| `remote` | string | `pr` only: where the branch is pushed. Default `origin`. |
| `open_pr` | bool | `pr` only: also run `pr_command` after pushing. Default `false` — pushing is safe everywhere, opening a PR needs an authenticated CLI. |
| `pr_command` | list[string] | The forge CLI, run with `--base <base_ref> --head <branch>` appended. Default `["gh", "pr", "create", "--fill"]`. |

`merge` picks its own path and refuses rather than guessing:

- **Nobody has the base branch checked out** → it is fast-forwarded as a ref update (`git fetch . <branch>:<base>`), touching no working tree.
- **The main checkout is on the base branch and clean** → a real merge runs there, and aborts cleanly on conflict.
- **The main checkout is on the base branch and dirty** → refused. The engineer has work in progress; the run's work is safe on its branch, so waiting costs nothing.
- **Uncommitted work in the run's own worktree** → refused, in every mode. Merging would ship less than the run produced.

A refusal fails the *integration*, not the *run*: the phase ran and reported, the commits exist, the branch is kept, and `just integrate <adw_id>` finishes the job later.

### `agents[]`

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The identifier ADW scripts use. **ADWs name agents, never models.** |
| `purpose` | yes | One sentence: what this agent is for. Should match its `system.md` Purpose. |
| `prompt_engineering.system` | yes | Path to the system prompt — who the agent is, its single purpose, its output contract. |
| `prompt_engineering.user` | yes | Path to the default user prompt — the task template with `{{prompt}}`, `{{previous_envelope}}`, `{{context_handoff_dir}}`. |
| `color` | no | Hex swatch (`"#a78bfa"`) for this agent's lane in the visualizer. Travels config → `agent_sessions.color` → `/api/sessions/:adw_id`, and rides the `agent_start` event so a lane is colored while the agent is still running. Unset = the UI's fallback palette. |
| `coding_agent`, `model`, `thinking`, `color`, `harness_engineering` | no | Override the corresponding `defaults` key. |
| `tools` | no | Allowlist. **Omitting the key means all tools usable.** A capability list, not a boundary — see `writes`. |
| `writes` | no | What this agent may modify **in the repo**, enforced after every call. Omitted = unrestricted (still barred from `protected_files`). `[]` = no repo writes at all. A list = only those paths: a trailing `/` is a directory prefix, `*` matches within one path segment, `**` crosses segments, anything else is an exact path. Naming a `protected_files` path here is what unlocks it. **The session runtime under `data_dir` is always writable** — `writes: []` means read-only with respect to the repo, not unable to write its own report. |

Output types are deliberately absent: config defines who an agent *is*; the ADW call site defines how it's *used*. One agent serves many calls — same system prompt, different user prompt + output type per call.

## Worktree per run

`session.ensure()` creates `<dir>/<adw_id>` on branch `<branch_prefix><adw_id>` before anything else exists, and `run.repo_root` points at it. Agents are spawned there, gates measure there, quality blocks run there, the permission snapshot fingerprints it, and commit phases commit it — the isolation follows from that one assignment rather than from every call site opting in.

**Two roots, and the difference matters.** `run.repo_root` is the run's worktree. `run.main_root` is the engineer's checkout, and it owns `data_dir`: the trace db, session dirs, `context_handoff/`, prompt audit copies and raw agent output all resolve against it. That is deliberate — one db across concurrent runs, one place the visualizer reads, and a record that outlives a pruned worktree. Two consequences to know:

- **`context_handoff_dir` is injected into prompts as an absolute path.** It sits outside the agent's working directory now, and a relative path would resolve inside the worktree where no other agent would look.
- **`always_writable()` is inert under worktrees.** `data_dir` can no longer appear in the change-set `permissions.py` compares, so there is nothing left for the exemption to exempt. It stays because it is still load-bearing when the two roots are the same directory.

**Lifecycle.** A joined run (a second ADW pinned to the same `--adw-id`) re-attaches to the existing worktree, and re-creates it from its branch if an earlier accepted run already pruned it — the branch is the record, the worktree is a copy of it. A failed or killed run keeps its worktree; that is where you go to see what happened. Anything holding uncommitted work is kept whatever the outcome, because a plan-only chain never commits and its plan lives nowhere else.

**Reclaiming disk** is `just worktrees` / `just worktrees-prune` (`<skill>/scripts/worktrees.py`). Prune takes a worktree only when the run that owns it has ended and its tree is clean; `--force` widens that to uncommitted work, and nothing reclaims a worktree out from under a live run. Branches are always retained.

**It is isolation, not a sandbox.** An agent with `bash` can `cd` anywhere. `permissions.py` remains the boundary, and it measures the tree this creates. Container sandboxing is the next phase, and it slots in behind the same seam.

Worktrees are skipped — both roots become the same directory, and everything behaves as it did in v1 — when `enabled: false`, when the repository is not a git repo, or when it has no commit to branch from.

## Defaults merging

`agents.py` merges each entry **over** `defaults`, key by key. An entry states only what differs; anything unset inherits. `agents.validate(cfg, REQUIRED_AGENTS)` then confirms every name an ADW declares exists, resolves to a usable coding agent + model, and has both prompt files present on disk. Any miss fails the run immediately — **no agent is ever spawned against a half-valid config.**

## Thinking levels

Pi's reasoning-effort ladder, lowest to highest:

```
off | minimal | low | medium | high | xhigh | max
```

Mapped to Pi's reasoning effort control and honored when the model is registered with `reasoning: true` in `~/.pi/agent/models.json`. On a non-reasoning model the setting is inert — no error, no effect. Rough guidance: `high`/`xhigh` for planners and reviewers, `medium` for builders, `low` for mechanical read-and-report agents. (For Claude Code in v2, the same field maps to the thinking budget.)

## Model resolution

**Always write `model` as `provider/model-id`.** `agents.py` hands the string to the Pi interface, which resolves it against pi's merged catalog — `~/.pi/agent/models.json` plus pi's built-in providers. The same model is usually carried by more than one provider (`gemini-3.6-flash` lives under `google` *and* under `openrouter` as `google/gemini-3.6-flash`), and a bare id that matches several **raises at resolution**:

```
agent 'scout': model pattern 'gemini-3.6-flash' is ambiguous:
  [('google', 'gemini-3.6-flash'), ('openrouter', 'google/gemini-3.6-flash'), ...]
```

That is `agents.validate()` doing its job — it fails before anything spawns rather than silently billing the wrong provider — but it means every agent in the roster inheriting that default is grounded until the pattern is qualified. Qualifying is the whole fix: `google/gemini-3.6-flash`, `openai/gpt-5.6-terra`, `fireworks/accounts/fireworks/models/kimi-k3`. The leading segment is matched against the provider list first, so the rest of the string can contain slashes.

Other consequences worth knowing:

- A model must be in the catalog before any agent can name it. An unknown id fails at resolution, before spawn. `pi --list-models` is the catalog the resolver actually reads.
- **Ambiguity can appear without you touching the config.** Registering a new provider that carries a model you already use turns a formerly-fine bare pattern ambiguous. If a roster stops validating and nobody edited it, that is why.
- Provider credentials come from the environment, not the config — the key that matches the provider you named (`GEMINI_API_KEY` for `google/...`, `OPENROUTER_API_KEY` for `openrouter/...`).
- The resolved model is recorded per session in `agent_map.json` and mirrored into the `agent_sessions` table. **Changing an agent's model invalidates its session**: a joined run starts that agent fresh instead of resuming a context window built by a different model.

## Tools

`tools` maps to `pi --tools`. Pi's seven builtin tool names:

| Tool | Purpose | Pi's own default |
|---|---|---|
| `read` | read file contents | on |
| `bash` | execute bash commands | on |
| `edit` | find/replace edits | on |
| `write` | create/overwrite files | on |
| `grep` | search file contents | **off** |
| `find` | find files by glob | **off** |
| `ls` | list directory contents | **off** |

`grep`, `find`, and `ls` are off in bare Pi, so an agent that does not name them will shell out through `bash` to do the same work. The starter roster therefore sets `defaults.tools` to all seven and lets each agent narrow from there.

**Resolution order:** an agent's own `tools` list wins; an agent that omits the key inherits `defaults.tools`; if neither is set, `tools` stays `None` and all tools are usable. An empty list is not "all tools" — it is a tool-less agent, and it will stall.

## Write permissions — `writes` and `protected_files`

`tools` cannot express a safety boundary, because two of the tools are general
purpose. `bash` runs anything, including `git checkout`, which discards an
engineer's uncommitted work; `write` reaches any path, not only the one report
file an agent was granted it for. So "this agent changes nothing" is a claim a
tool list can state but never keep.

`adw_modules/permissions.py` keeps it, the same way every other claim in this
system is kept — after the fact, against the repo. Before an agent's first
prompt the working tree's change-set is fingerprinted; after its last send
(including JSON retries and gate corrections) it is fingerprinted again. Any
path that appeared, vanished, or changed is attributed to that agent.

Comparing change-sets rather than watching writes is deliberate: a path that was
modified before the agent ran and is clean afterwards has been **reverted**, and
a reversion is a modification. That is what catches `git checkout`.

A breach is not a gate violation. Gates are for work an agent can be asked to
redo; a write has already happened, so re-prompting fixes nothing. Instead:

1. every unauthorized change the agent **introduced** is rolled back — tracked
   files with `git checkout --`, untracked files by deletion;
2. a path that was **already dirty** before the agent ran is left untouched. The
   operator had uncommitted work there, and discarding it to tidy up would be
   the same harm this module exists to prevent;
3. the phase fails and names every path with what happened to it.

```yaml
defaults:
  protected_files: [adws/adw_modules/, adws/adw_sssf_config/, "adws/adw_*.py"]

agents:
  - name: builder      # no `writes` key -> unrestricted, minus protected_files
  - name: scout
    writes: []         # no repo writes; its findings still land in context_handoff/
  - name: planner
    writes: [specs/]
  - name: documenter
    writes: [app_docs/, docs/, "**/*.md", "*.md"]
```

**The session runtime under `data_dir` is always writable, for every agent.**
`context_handoff/` is how agents hand work to each other, and each agent's
prompts, `raw_output.jsonl`, and `envelope.json` sit beside it. That grant comes
from `data_dir` rather than from `.gitignore`: the runtime is normally ignored,
so it never even appears in a snapshot, but an agent's ability to record its own
work must not depend on a gitignore line someone can delete.

Narrow by role, not by reflex. Anything that must produce a `context_handoff/` artifact needs `write`, or it will resort to a `bash` heredoc. Withhold `edit`/`write` only where the restriction *is* the guarantee — a reviewer that cannot edit cannot quietly fix what it was asked to report.

### Extension tools must be named explicitly

`pi --tools` is an allowlist over **built-in, extension, and custom tools alike** — not just builtins. So the moment an agent has a `tools` list at all (its own, or one inherited from `defaults`), any tool registered by its `harness_engineering` extensions is **excluded unless it appears in that list by name**.

This fails quietly. The extension still loads, the run still succeeds, and the tool the extension exists to provide is simply never offered to the model — you find out by noticing the agent never called it.

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
      - ast_query                       # REQUIRED — the extension's tool, named or lost
```

Rule: **every entry in `harness_engineering` that registers a tool must have that tool name added to the agent's `tools` list.** Adding an extension is therefore a two-line change, never one. The alternative is dropping the `tools` key *and* leaving `defaults.tools` unset so the agent resolves to `None` (all tools) — but with a roster-wide `defaults.tools` in place, that escape hatch is closed; naming the tool is the only path.

## Harness engineering

`harness_engineering` entries are pi extension **file paths**, passed through as `pi -e <path>`, one flag per entry, scoped to that agent only. This is where per-agent harness changes live — e.g. an output-tightening extension for an agent that keeps wrapping its envelope in prose. The starter roster ships with none. On Claude Code the field is reserved for MCP config and hooks in v2.

**If the extension registers a tool, name that tool in the agent's `tools` list too** — `--tools` filters extension tools exactly like builtins, so an unnamed extension tool is silently unavailable no matter that the extension loaded fine. See [Extension tools must be named explicitly](#extension-tools-must-be-named-explicitly) above. Extensions that only shape output or add flags (no tool registration) need no `tools` change.
