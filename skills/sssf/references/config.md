# Config Reference

The full `sssf.config.yaml` spec: every field, how defaults merge, and how model / thinking / tools / extensions map onto **each** coding agent — `pi` and `claude_code` are both real, and selectable per agent.

It lives at **`adws/adw_sssf_config/sssf.config.yaml`** — the default path every `adw_*.py` and the justfile resolve, and where `install.py` / `make_config.py` stamp it. Pass `--config <path>` to any ADW (or set `SSSF_CONFIG` for the justfile) to run against a different roster.

## Shape

```yaml
defaults:
  coding_agent: pi
  model: google/gemini-3.6-flash        # pi: ALWAYS provider/model-id
  thinking: medium
  harness_engineering: []
  tools: [read, bash, edit, write, grep, find, ls]
  data_dir: adws/adw_data
  claude_code:                          # claude_code agents only; pi ignores it
    safe_mode: true
    bare: false
    setting_sources: []
    strict_mcp_config: true
    permission_mode: bypassPermissions
    add_dirs: []
    max_budget_usd: 0

observability:
  db: adws/adw_data/sssf.db
  poll_ms: 500

issues:                                 # off by default; a run from a work item
  enabled: false
  project: ""                           # "" = the origin remote of this checkout
  fetch_command:   ["gh", "issue", "view"]
  list_command:    ["gh", "issue", "list"]
  comment_command: ["gh", "issue", "comment"]
  state_command:   ["gh", "issue", "edit"]
  route: {}                             # label -> ADW script
  states: {queued: "sssf:queued", running: "sssf:running",
           done: "sssf:done", failed: "sssf:failed"}
  trusted_authors: []
  max_concurrent: 2
  force_pr: true

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
      - adws/adw_data/harness_engineering/subagents.ts
    tools:
      - read
      - bash

  - name: reviewer
    coding_agent: claude_code
    model: sonnet                         # claude_code: an alias or a full model id
    thinking: high                        # -> --effort high
    claude_code:
      permission_mode: bypassPermissions
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/reviewer/system.md
      user: adws/adw_data/prompt_engineering/reviewer/user.md
    tools:
      - read
      - grep
```

Both agents above run in the same chain and hand each other the same typed envelopes. Nothing downstream — gates, permissions, the trace schema, the visualizer — knows which backend produced a phase.

## Fields

### `defaults`

| Field | Type | Meaning |
|---|---|---|
| `coding_agent` | `pi` \| `claude_code` | Which backend runs the agent. Both are implemented (`agent_pi.py`, `agent_cc.py`) and selectable per agent; one chain may mix them. |
| `model` | string | Backend-specific. Pi: `provider/model-id`, resolved against `pi --list-models`. Claude Code: an alias (`opus`, `sonnet`, `haiku`) or a full id (`claude-sonnet-5`) — a `provider/id` pattern is **rejected at validation**. |
| `thinking` | enum | Reasoning effort — see below. Default `medium`. |
| `color` | hex string | Lane color for every agent that does not set its own. Default empty — the visualizer falls back to its own palette. |
| `harness_engineering` | list[string] | Coding-agent extensions, and **not interchangeable between backends**. Pi: TypeScript extension paths (`-e`). Claude Code: `mcp:<file.json>`, `agents:<json-or-file>`, `plugin:<dir-or-zip>`. |
| `claude_code` | block | Determinism and permission settings for `claude_code` agents — see [Claude Code determinism](#claude-code-determinism). Inert for pi. |
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
| `pr_body_template` | string | Passed as `--body`, rendered with `{adw_id}`, `{branch}`, `{base_ref}`, `{issue_number}`, `{issue_url}`. Empty (default) passes nothing. `Closes #{issue_number}` here is what makes an issue-triggered run close its own issue on merge. **`gh` rejects `--fill` together with an explicit body** — drop `--fill` from `pr_command` if you set this. |

`merge` picks its own path and refuses rather than guessing:

- **Nobody has the base branch checked out** → it is fast-forwarded as a ref update (`git fetch . <branch>:<base>`), touching no working tree.
- **The main checkout is on the base branch and clean** → a real merge runs there, and aborts cleanly on conflict.
- **The main checkout is on the base branch and dirty** → refused. The engineer has work in progress; the run's work is safe on its branch, so waiting costs nothing.
- **Uncommitted work in the run's own worktree** → refused, in every mode. Merging would ship less than the run produced.

A refusal fails the *integration*, not the *run*: the phase ran and reported, the commits exist, the branch is kept, and `just integrate <adw_id>` finishes the job later.

**`pr` mode is repeatable, and a published branch stays published.** A session does not end when its pull request is opened — `just plan-build --adw-id <id>` commits onto the same branch hours later — so two things hold:

- **Every commit phase pushes, if the branch is already on the remote.** `integration.keep_published(run)` runs right after `commit_all` and pushes only when `refs/remotes/<remote>/<branch>` already exists, i.e. when an earlier integration published this branch. On a branch nobody pushed it does nothing: putting a branch on the remote for the first time stays the engineer's call. A rejected push (someone else moved the branch) is a note in the commit phase's log, not a failed phase — the commits are on the branch either way.
- **Integrating a second time updates the pull request; it never opens another.** The push is what carries the new commits, and `pr_command` is skipped once the session has a `pr_url`. If the trace has none but the forge refuses because a PR already exists, the url in that refusal is taken and recorded — a push that updated a pull request is reported as the success it is, not as a failed run.

### `issues`

Where work items come from, and which chain each label asks for. **Off by default**, because this is the one path where the prompt is written by whoever can file an issue rather than by the engineer at the keyboard.

| Field | Type | Meaning |
|---|---|---|
| `enabled` | bool | Default `false`. The watcher refuses to poll until this is on. |
| `project` | string | **Which repo is watched.** `""` resolves once at startup from the `origin` remote of the main checkout. Set it explicitly for cron: a scheduler has an arbitrary working directory, and a watcher that silently polls the wrong project looks exactly like one with nothing to do. A tracker that is not the forge has no remote to infer from and must set it. |
| `fetch_command`, `list_command`, `comment_command`, `state_command` | list[string] | The forge CLI, always aimed with `--repo <project>` appended — never left to the process cwd. Commands rather than API calls for the reason `pr_command` gives: the CLI is installed and authenticated in your shell already. |
| `route` | map[label → script] | Which ADW a label asks for. **Empty means nothing ever launches**, which is the safe default. |
| `states` | object | The label state machine: `queued`, `running`, `done`, `failed`. |
| `trusted_authors` | list[string] | `[]` accepts every issue author, because the human who applied the routing label is then the only authorization. Narrow it where anyone can label. |
| `max_concurrent` | int | Runs in flight, counted from `sessions.status='running'`. Bounds parallelism, **not spend**. |
| `force_pr` | bool | Default `true`. An issue-triggered run's `merge` is downgraded to `pr` in `integration.integrate()`. `mode: none` still wins. |

**The flip is the claim; a file lock is the exclusion.** Moving an issue off `queued` records the claim where humans already look, but it does not *win* anything: the forge has no conditional label change, and `gh issue edit --remove-label queued` succeeds whether or not the issue still carries it. So the watcher takes a `flock` on `<data_dir>/issue-locks/<project>-<n>.lock` before claiming and holds it for the whole run.

That excludes a second watcher **on the same machine**, which is the deployment this is built for — one watcher per repository, from cron. Two watchers on two machines would both claim, and nothing available at the forge would prevent it; run one. A crashed watcher leaves a `running` label, and moving it back to `queued` by hand is the whole recovery.

**The trust boundary**, in the order it is enforced:

1. **The routing label is the authorization** — a human applied it, and the watcher never looks at an issue without one.
2. **`trusted_authors`**, checked in the issue phase, before any agent is spawned.
3. **The body is an artifact, not a field** — it reaches the consuming agent as `artifacts[0]` framed as a user's description of a problem, never as instructions. It is also absent from the persisted envelope. Which agent that is, is the ADW's choice: a scout, a planner, or something that enriches the issue first.
4. **`writes:` and `protected_files`** are unchanged but now load-bearing: an agent editing the machinery that judges it is rolled back by `permissions.py`.
5. **`force_pr`** — the base branch is not reachable from this path.

The trigger itself sits *above* the factory: `<skill>/scripts/issue_watch.py` (`just issues`, `issues-status`, `issues-watch`), run by cron or by hand.

### `pull_requests`

Review feedback on the factory's own pull requests, answered inside the session that opened them. **Off by default**, for the reason `issues` is: the text driving the agents is written by whoever can review, not by the engineer at the keyboard.

This is the other end of a branch's life. `adw_pr_review.py` reads `sssf/<adw_id>` off the pull request's head ref, joins **that** session, works the worktree it already has (re-created from the branch if an accepted run pruned it), and `integration.keep_published()` pushes onto the branch that is already on the remote — so the pull request is **updated, never replaced**, and no phase in the chain can reach the base branch.

| Field | Type | Meaning |
|---|---|---|
| `enabled` | bool | Default `false`. The watcher refuses to poll until this is on; `just pr-review <n>` by hand does not consult it, exactly as `just issue 42` does not consult `issues.enabled`. |
| `project` | string | Which repo. `""` resolves once from the `origin` remote — same rule and same cron failure mode as `issues.project`. A graphql query must name its repository, so an unresolved project is refused rather than guessed. |
| `list_command` | list[string] | How open pull requests are listed for the watcher, filtered to `worktree.branch_prefix`. |
| `comment_command`, `state_command` | list[string] | The pull-request-level comment, and the one label move. Aimed with `--repo <project>`, like every other forge call here. |
| `graphql_command` | list[string] | **Why this exists.** Review *threads* — their ids, and `isResolved` — are unreachable from `gh pr view --json`, which returns issue-level comments and review bodies but never the inline threads where the asks are. One query returns the state *and* the threads in one snapshot; two commands would disagree about a pull request merged between them. Replies and resolves go back the same way. |
| `trusted_reviewers` | list[string] | `[]` accepts everyone — being able to review this repository's pull requests *is* the authorization. Narrow it where that is not true, such as a public repository where anyone may comment. |
| `ignore_authors` | list[string] | Bots whose comments are never work an agent can do (coverage reporters, changelog nags). The factory's own comments are skipped regardless. |
| `reply_to_threads`, `resolve_threads` | bool | The two halves of the write-back. The reply goes **first**, and a failed reply **skips** the resolve: a resolved thread with no answer in it reads as feedback that was dismissed. |
| `max_threads` | int | Bounds the prompt, not the pull request. A review with eighty threads is a conversation to have, not a batch to hand an agent. |
| `max_concurrent` | int | Runs in flight, counted from the same session table `issues` uses. |
| `reap_merged` | bool | Default `true`. See **Merged is the end of the session** below. |
| `states.failed` | string | The only label this path uses. |

**The unresolved thread is the queue.** No label to claim, no state file, no watermark column. A thread a reviewer opened is outstanding until something resolves it, and the run that answers it resolves it — the same trick `issue_watch` plays with a label, except the forge maintains this state for us and shows it to the reviewer who wrote it. A second run therefore finds only what is genuinely left. Four things are excluded from the queue: resolved threads, outdated ones (the diff moved out from under them), `ignore_authors`, and any thread whose **last word is the factory's own** — without that test a run answers its own answer, forever.

`states.failed` exists for the one case the forge's state cannot express: a run that ends red leaves its threads open, which is exactly the condition that launched it. Without a mark the next poll relaunches the same failing run forever. A human removing the label is the restart.

**Merged is the end of the session.** A merged pull request vanishes from the watcher's queue on its own, but three things would otherwise hang. `pr_watch`'s reap pass — over the worktrees on disk plus the sessions that believe they are running, never over the repository's history, so the work shrinks as the factory tidies up — handles each:

1. **A run still working that branch is stopped**, with `SIGTERM`, which `session.py` turns into a clean finish. Letting it continue is not merely pointless: `keep_published` would push onto a branch that has already landed and may already be deleted.
2. **Its worktree is released**, by the same conservative rule `just worktrees-prune` uses — ended session, clean tree. Uncommitted work stays put even here. **The branch is never deleted**, locally or on the remote; that belongs to whoever merged.
3. **The `states.failed` label and the lock file are dropped.**

Idempotent throughout: a second pass finds no process, no worktree and no label.

**The trust boundary**, in the order it is enforced:

1. **The branch prefix is the authorization** — only `sssf/<adw_id>` is worked. A branch no run of this factory produced has no pinned base, no trace and no worktree, and is refused before a session exists.
2. **`trusted_reviewers`**, checked in the `pr` phase, before any agent is spawned.
3. **The threads are an artifact, not a field** — they reach the builder as `artifacts[0]`, framed as reviewers' requests to weigh rather than instructions to follow, and the prompt slot carries only the operator's own sentence.
4. **`writes:` and `protected_files`** are unchanged but load-bearing, as on the issue path.
5. **The base branch is unreachable** — this chain has no integration phase at all, and `integration.integrate()` additionally downgrades a `merge` on a `pr_review`-triggered session, so a later `just integrate` cannot land it either.

The trigger sits *above* the factory: `<skill>/scripts/pr_watch.py` (`just prs`, `prs-status`, `prs-watch`). `prs-watch --pr <n>` watches a single pull request and exits when it merges or closes.

### `agents[]`

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The identifier ADW scripts use. **ADWs name agents, never models.** |
| `purpose` | yes | One sentence: what this agent is for. Should match its `system.md` Purpose. |
| `prompt_engineering.system` | yes | Path to the system prompt — who the agent is, its single purpose, its output contract. |
| `prompt_engineering.user` | yes | Path to the default user prompt — the task template with `{{prompt}}`, `{{previous_envelope}}`, `{{context_handoff_dir}}`. |
| `color` | no | Hex swatch (`"#a78bfa"`) for this agent's lane in the visualizer. Travels config → `agent_sessions.color` → `/api/sessions/:adw_id`, and rides the `agent_start` event so a lane is colored while the agent is still running. Unset = the UI's fallback palette. |
| `coding_agent`, `model`, `thinking`, `color`, `harness_engineering`, `claude_code` | no | Override the corresponding `defaults` key. `claude_code` is replaced as a whole block, not merged key by key. |
| `tools` | no | Allowlist. **Omitting the key means all tools usable.** A capability list, not a boundary — see `writes`. |
| `writes` | no | What this agent may modify **in the repo**, enforced after every call. Omitted = unrestricted (still barred from `protected_files`). `[]` = no repo writes at all. A list = only those paths: a trailing `/` is a directory prefix, `*` matches within one path segment, `**` crosses segments, anything else is an exact path. Naming a `protected_files` path here is what unlocks it. **The session runtime under `data_dir` is always writable** — `writes: []` means read-only with respect to the repo, not unable to write its own report. |

Output types are deliberately absent: config defines who an agent *is*; the ADW call site defines how it's *used*. One agent serves many calls — same system prompt, different user prompt + output type per call.

## Worktree per run

`session.ensure()` creates `<dir>/<adw_id>` on branch `<branch_prefix><adw_id>` before anything else exists, and `run.repo_root` points at it. Agents are spawned there, gates measure there, quality blocks run there, the permission snapshot fingerprints it, and commit phases commit it — the isolation follows from that one assignment rather than from every call site opting in.

**Two roots, and the difference matters.** `run.repo_root` is the run's worktree. `run.main_root` is the engineer's checkout, and it owns `data_dir`: the trace db, session dirs, `context_handoff/`, prompt audit copies and raw agent output all resolve against it. That is deliberate — one db across concurrent runs, one place the visualizer reads, and a record that outlives a pruned worktree. Two consequences to know:

- **`context_handoff_dir` is injected into prompts as an absolute path.** There is one worktree per RUN, not per agent — every agent in a session shares `run.repo_root` — so a relative path would not lose the agents each other. It would lose them the code: `changes.capture` and the quality blocks write to `run.context_handoff_dir` in the main checkout, so the two halves of one handoff would land in different trees, and the agents' half would sit inside the tree `commit_all` stages and `permissions.py` fingerprints.
- **`always_writable()` is inert under worktrees.** `data_dir` can no longer appear in the change-set `permissions.py` compares, so there is nothing left for the exemption to exempt. It stays because it is still load-bearing when the two roots are the same directory.

**Lifecycle.** A joined run (a second ADW pinned to the same `--adw-id`) re-attaches to the existing worktree, and re-creates it from its branch if an earlier accepted run already pruned it — the branch is the record, the worktree is a copy of it. A failed or killed run keeps its worktree; that is where you go to see what happened. Anything holding uncommitted work is kept whatever the outcome, because a plan-only chain never commits and its plan lives nowhere else.

**Reclaiming disk** is `just worktrees` / `just worktrees-prune` (`<skill>/scripts/worktrees.py`). Prune takes a worktree only when the run that owns it has ended and its tree is clean; `--force` widens that to uncommitted work, and nothing reclaims a worktree out from under a live run. Branches are always retained.

**It is isolation, not a sandbox.** An agent with `bash` can `cd` anywhere. `permissions.py` remains the boundary, and it measures the tree this creates. Container sandboxing is the next phase, and it slots in behind the same seam.

Worktrees are skipped — both roots become the same directory, and everything behaves as it did in v1 — when `enabled: false`, when the repository is not a git repo, or when it has no commit to branch from.

## Defaults merging

`agents.py` merges each entry **over** `defaults`, key by key. An entry states only what differs; anything unset inherits. `agents.validate(cfg, REQUIRED_AGENTS)` then confirms every name an ADW declares exists, has both prompt files present on disk, and — **asking the agent's own backend, not one hard-coded rule** — that its model, tools, thinking level and `harness_engineering` entries are ones that backend can actually honor, and that the backend's CLI is on PATH. Any miss fails the run immediately, with every problem listed at once: **no agent is ever spawned against a half-valid config.**

Validation still checks that a model is *written* correctly, not that its provider answers or that its key is set. A missing credential surfaces when that agent runs, not at startup.

## Backends

| | `coding_agent: pi` | `coding_agent: claude_code` |
|---|---|---|
| Command | `pi -p --mode json` | `claude -p --output-format stream-json --verbose` |
| Binary | `PI_PATH`, default `pi` | `CLAUDE_PATH`, default `claude` |
| Auth | the provider key named by `model`'s provider half, from `.env` | the CLI's own — `claude auth`, so a **subscription needs no key** |
| `model` | `provider/model-id`, resolved against `pi --list-models` | an alias (`opus`, `sonnet`, `haiku`) or a full id (`claude-sonnet-5`) |
| `thinking` | `--thinking <level>` | `--effort <level>`; `off` and `minimal` collapse to `low` |
| `tools` | lowercase pi names | mapped from pi's names, or Claude Code names written directly |
| `harness_engineering` | `-e <file.ts>`, repeatable | `mcp:` / `agents:` / `plugin:` entries |
| Sessions | `--session-id`, create-or-continue | `--session-id` to create, `--resume` after that |
| Cost detail | per component **and** total | total only |

The seam is one function (`agents.execute` → `driver.run`) and one record shape (`adw_modules/tool_calls.py`). Everything downstream — gates, `permissions.py`, the trace schema, the visualizer — is backend-agnostic and stayed untouched when the second backend landed.

## Thinking levels

One ladder, lowest to highest:

```
off | minimal | low | medium | high | xhigh | max
```

**Pi** maps it to its reasoning-effort control, honored when the model is registered with `reasoning: true` in `~/.pi/agent/models.json`. On a non-reasoning model the setting is inert — no error, no effect.

**Claude Code** maps it to `--effort`, whose ladder starts two rungs higher: `off` and `minimal` have no equivalent and both collapse to `low`. The collapse is warned once per run and rides the event stream, so it shows up in the trace rather than being something you infer from a bill.

Rough guidance either way: `high`/`xhigh` for planners and reviewers, `medium` for builders, `low` for mechanical read-and-report agents.

## Model resolution

### Pi — `provider/model-id`

**For a pi agent, always write `model` as `provider/model-id`.** `agents.py` hands the string to the Pi interface, which resolves it against pi's merged catalog — `~/.pi/agent/models.json` plus pi's built-in providers. The same model is usually carried by more than one provider (`gemini-3.6-flash` lives under `google` *and* under `openrouter` as `google/gemini-3.6-flash`), and a bare id that matches several **raises at resolution**:

```
agent 'scout': model pattern 'gemini-3.6-flash' is ambiguous:
  [('google', 'gemini-3.6-flash'), ('openrouter', 'google/gemini-3.6-flash'), ...]
```

That is `agents.validate()` doing its job — it fails before anything spawns rather than silently billing the wrong provider — but it means every agent in the roster inheriting that default is grounded until the pattern is qualified. Qualifying is the whole fix: `google/gemini-3.6-flash`, `openai/gpt-5.6-terra`, `fireworks/accounts/fireworks/models/kimi-k3`. The leading segment is matched against the provider list first, so the rest of the string can contain slashes.

Other consequences worth knowing:

- A model must be in the catalog before any agent can name it. An unknown id fails at resolution, before spawn. `pi --list-models` is the catalog the resolver actually reads.
- **Ambiguity can appear without you touching the config.** Registering a new provider that carries a model you already use turns a formerly-fine bare pattern ambiguous. If a roster stops validating and nobody edited it, that is why.
- Provider credentials come from the environment, not the config — the key that matches the provider you named (`GEMINI_API_KEY` for `google/...`, `OPENROUTER_API_KEY` for `openrouter/...`).
- The resolved model is recorded per session in `agent_map.json` and mirrored into the `agent_sessions` table. **Changing an agent's model invalidates its session**: a joined run starts that agent fresh instead of resuming a context window built by a different model. Changing its `coding_agent` does the same, and more bluntly — a pi session id is not a UUID, and Claude Code would refuse it outright.

### Claude Code — an alias or a full id

There is no provider half and nothing to resolve against: `sonnet`, `opus`, `haiku`, or a full id like `claude-sonnet-5`. The CLI owns that list.

The mistake worth naming is inheritance. A `claude_code` agent that does not override `model` inherits `defaults.model`, which in the starter roster is a pi pattern — so validation **rejects a `/` outright** with the reason spelled out, rather than letting it fail three phases into a chain:

```
agent 'reviewer': model 'google/gemini-3.6-flash' is a pi provider/model-id pattern;
Claude Code takes an alias (opus, sonnet, haiku) or a full model id (claude-sonnet-5).
```

## Sessions, and why Claude Code needs two ids

Pi's `--session-id` creates *or* continues, so running an agent and continuing it are the same call. Claude Code's is **create-only** — a second use fails with `Session ID <uuid> is already in use` — and continuing takes `--resume <uuid>` instead. Every agent phase does more than one send in practice (a JSON retry, a gate correction), so this is not an edge case.

`agent_map.json` carries the state that settles it: `session_id`, `model`, `coding_agent`, `native_session_id`, and `started`. The first send creates and flips `started`; every send after that resumes. The map is written **the moment a session exists**, not at the end of the phase, so a run that dies mid-phase does not leave behind a session it can neither create nor resume.

Claude Code ids are derived, not random — `uuid5(namespace, "<adw_id>:<agent>:<model>")` — so a re-run pinned to the same `--adw-id` lands on the session it left. If the map is lost but the session still exists, the create fails and the backend resumes it instead of dying.

## Claude Code determinism

A default `claude -p` auto-discovers the operator's `CLAUDE.md`, skills, plugins, hooks and MCP servers. A probe run in the SSSF repository loaded 30+ skills that had nothing to do with the task — which makes a factory run depend on whose machine it executed on, the exact failure the factory exists to eliminate. The `claude_code:` block pins it off, and is **configuration rather than code** because some repositories genuinely do want their own `CLAUDE.md` loaded.

| Field | Default | Flag | Meaning |
|---|---|---|---|
| `safe_mode` | `true` | `--safe-mode` | No `CLAUDE.md`, skills, plugins, hooks, MCP servers, custom agents or commands. Auth, model selection, built-in tools and permissions still work, so a **subscription still authenticates**. |
| `bare` | `false` | `--bare` | Stricter still, and takes precedence over `safe_mode`. It forces `ANTHROPIC_API_KEY`/`apiKeyHelper` auth and never reads OAuth or the keychain, so turning it on takes a subscription-only roster offline. |
| `setting_sources` | `[]` | `--setting-sources` | Which settings files to load: `user`, `project`, `local`. Empty loads none. |
| `strict_mcp_config` | `true` | `--strict-mcp-config` | Only MCP servers this config passes explicitly. |
| `permission_mode` | `bypassPermissions` | `--permission-mode` | See below. |
| `add_dirs` | `[]` | `--add-dir` | Extra roots the file tools may reach. The worktree (cwd) and the session runtime are added automatically. |
| `max_budget_usd` | `0` | `--max-budget-usd` | Per-call ceiling. `0` = none. |

**`safe_mode` and `harness_engineering` are mutually exclusive**, and validation says so rather than letting it fail quietly: safe mode suppresses exactly the MCP servers, plugins and custom agents that key exists to load. Verified — an `--agents` definition passed under `--safe-mode` does not reach the session.

**Permissions.** A non-interactive run has to answer its own permission prompts. `bypassPermissions` is only acceptable because two other things are true, and both are load-bearing: `permissions.py` fingerprints the tree before the call and rolls back every write outside the agent's `writes:` allowlist afterwards, and the run happens in its own worktree. The factory verifies after the fact here exactly as it does everywhere else — it is not taking the agent's word for anything.

Two sharp edges:

- **The CLI refuses `bypassPermissions` when running as root.** Use `acceptEdits` there.
- **`dontAsk` is not a quieter `bypassPermissions`** — it *denies* whatever it would have prompted for, and hands the model a paragraph explaining the denial. An agent under it reads as one that mysteriously stopped using `bash`.

## Tools

`tools` is written in **pi's vocabulary** and translated per backend. Pi's seven builtin tool names:

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

### Tool names on Claude Code

Claude Code's tool names differ in case and in spelling, so `agent_cc.py` maps them:

| pi | Claude Code | Note |
|---|---|---|
| `read` | `Read` | |
| `bash` | `Bash` | |
| `edit` | `Edit` | |
| `write` | `Write` | |
| `grep` | `Grep` | |
| `find` | `Glob` | |
| `ls` | `Glob` | Claude Code has no directory-listing tool. It maps to `Glob`, the read-only equivalent, and deliberately **not** to `Bash` — handing a read-only agent a shell to make one tool name resolve would turn a mapping table into a privilege escalation. `find` and `ls` therefore collapse onto one entry. |

Claude Code's own names (`Read`, `Bash`, `WebFetch`, `Task`, `TodoWrite`, …) pass through untouched, so a roster written for this backend can name them directly instead of going through pi's words.

**A name that maps to neither is a validation error, not a silent drop.** `--tools` filtering is exactly where capabilities disappear quietly, and an agent that lost one looks like a model that stopped trying. Pi's `subagent_*` tools are the case you will hit first: they come from a pi extension and have no Claude Code equivalent, so an agent moving backends has to drop them and its `harness_engineering` entry together.

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

**This key is backend-specific and the two halves do not translate.** Nothing tries to unify them; each backend validates its own entries and rejects the other's.

**Pi:** entries are extension **file paths**, passed through as `pi -e <path>`, one flag per entry, scoped to that agent only. This is where per-agent harness changes live — e.g. an output-tightening extension for an agent that keeps wrapping its envelope in prose. The starter roster ships with none.

**Claude Code:** entries are `<kind>:<value>`, because the nearest equivalents are three unrelated things rather than one:

| Entry | Flag |
|---|---|
| `mcp:<file-or-json>` | `--mcp-config` |
| `agents:<json-or-file>` | `--agents` |
| `plugin:<dir-or-zip>` | `--plugin-dir` |

A bare path with no prefix is a validation error — the code does not guess from a file suffix. And remember that `claude_code.safe_mode` (on by default) suppresses all three: an agent that uses this key must turn it off.

**If the extension registers a tool, name that tool in the agent's `tools` list too** — `--tools` filters extension tools exactly like builtins, so an unnamed extension tool is silently unavailable no matter that the extension loaded fine. See [Extension tools must be named explicitly](#extension-tools-must-be-named-explicitly) above. Extensions that only shape output or add flags (no tool registration) need no `tools` change.
