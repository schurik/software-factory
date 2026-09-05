# Create Config

Generate `sssf.config.yaml` — the agent roster for a target repo.

## Generate it

```bash
uv run <skill>/scripts/make_config.py
```

Writes `adws/adw_sssf_config/sssf.config.yaml` — creating the directory if needed — with the starter agents (planner, builder, scout, reviewer, documenter) wired to the prompt files the install cookbook stamped into `adws/adw_data/prompt_engineering/`. That path is the default every ADW and the justfile look for; `--config` overrides it. `make_config.py` refuses to overwrite an existing config unless you pass `--force`, so retuning an existing roster is a hand edit — see `update_config.md`.

## The rule

**One agent, one prompt, one purpose.** An entry defines who an agent *is*: its coding agent, model, thinking level, and exactly one system prompt plus one user prompt. How it gets *used* — the output type, a per-call user prompt override — lives at the ADW call site, never here.

## Schema

```yaml
defaults:
  coding_agent: pi                 # pi | claude_code — per agent, and one chain may mix them
  model: google/gemini-3.6-flash   # ALWAYS provider/model-id — a bare id is ambiguous
  thinking: medium                 # off | minimal | low | medium | high | xhigh | max
  harness_engineering: []          # pi extension names
  data_dir: adws/adw_data          # runtime home: {data_dir}/sessions/{adw_id}/{agent_name}/

  # Off-limits to every agent that does not name them in its own `writes`.
  # `tools` alone cannot protect these — bash runs `git checkout`, write reaches
  # any path — so this is enforced after every call, in permissions.py.
  protected_files: [adws/adw_modules/, adws/adw_sssf_config/, "adws/adw_*.py"]

observability:
  db: adws/adw_data/sssf.db        # tracer writes here; the UI polls it
  poll_ms: 500                     # visualizer live-poll cadence

worktree:                          # a git worktree + branch per run
  enabled: true                    # false = run in the main checkout, v1 behaviour
  dir: .sssf-worktrees             # relative to the main checkout; gitignored
  branch_prefix: "sssf/"           # the run's branch is <prefix><adw_id>
  base_ref: ""                     # "" = whatever the main checkout has checked out
  keep_on_success: false           # a clean accepted run's tree is a redundant copy
  integration:                     # how that branch gets back — THIS IS A REPO DECISION
    mode: merge                    # none | merge | pr
    merge_flags: ["--no-ff"]
    remote: origin                 # mode: pr
    open_pr: false                 # mode: pr — also run pr_command after pushing
    pr_command: ["gh", "pr", "create", "--fill"]
    pr_body_template: ""           # --body; {adw_id} {branch} {base_ref} {issue_number} {issue_url}

issues:                            # a labelled work item can start a run. OFF by default
  enabled: false
  project: ""                      # "" infers from the origin remote; set it for cron
  route: {}                        # label -> ADW script; empty means nothing ever launches
  states: {queued: "sssf:queued", running: "sssf:running",
           done: "sssf:done", failed: "sssf:failed"}
  trusted_authors: []              # [] = the human who applied the label is the authorization
  max_concurrent: 2
  force_pr: true                   # an issue-triggered run may not move the base branch

agents:
  - name: planner                  # ADW scripts name agents, never models
    coding_agent: pi
    model: google/gemini-3.6-flash
    thinking: high
    color: "#a78bfa"               # optional hex — this agent's lane color in the visualizer
    purpose: Turn a request into a plan the builder can implement without asking questions.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/planner/system.md
      user: adws/adw_data/prompt_engineering/planner/user.md

  - name: scout
    thinking: high                 # unset keys fall through to defaults
    purpose: Find and report where things live; change nothing.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/scout/system.md
      user: adws/adw_data/prompt_engineering/scout/user.md
    tools:                         # optional allowlist — omit the key entirely for all tools
      - read
      - grep
      - find
    writes: []                     # what it may change IN THE REPO. [] = nothing
```

Every agent entry merges over `defaults`, so an entry only states what differs. The tool vocabulary is `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`; a `claude_code` agent may name Claude Code's own tools instead, and a name that maps to neither is a validation error rather than a silent drop.

**`tools` is a capability list; `writes` is the boundary.** They are not the same question and only one of them is enforceable. `bash` runs anything (`git checkout` included) and `write` reaches any path, so no tool list can make "this agent changes nothing" true. `writes` per agent, plus `defaults.protected_files`, is checked against the tree after every call in `permissions.py`: an unauthorized change is rolled back and the phase dies. `writes: []` means read-only **with respect to the repo** — every agent can always write its own report under `data_dir`.

## After generating

1. Each agent needs its prompt pair to exist on disk: `adws/adw_data/prompt_engineering/{name}/system.md` and `user.md`. `agents.validate()` fails the run at startup if either is missing.
2. Write `purpose` as one sentence and make the system prompt say the same thing — the two should not drift.
3. Validate by running the smallest ADW that names your agents; a bad entry fails fast, before anything spawns.

## The three blocks that are repo decisions, not roster decisions

`defaults` and `agents` describe how the factory thinks. These describe what it
is allowed to do in *this* repository, and they are the ones to put to the
engineer rather than to pick for them:

- **`worktree.integration.mode`** — may a machine move the base branch? `merge` for a solo repo, `pr` where a human reviews first, `none` to leave every branch for a person.
- **`issues`** — may an issue start a run at all? Off until the repo opts in; this is the one path where the prompt is written by whoever can file an issue.
- **`defaults.protected_files`** — what an agent may never edit. The factory's own code by default, so nothing can edit the machinery that judges its work.

Full field-by-field spec, thinking-level mapping, and model resolution: `references/config.md` — including [worktree per run](../references/config.md#worktree-per-run), [integration](../references/config.md#worktreeintegration), [issues](../references/config.md#issues) and [write permissions](../references/config.md#write-permissions--writes-and-protected_files). Retuning an existing roster: `update_config.md`.
