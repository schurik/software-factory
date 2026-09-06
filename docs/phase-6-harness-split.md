# Phase 6 — Harness split

## Goal

Make **a harness a first-class unit**: one coding-agent CLI plus everything specific to
it — its options, its roster shape, its extensions, and **the prompts its agents are
given**. Adding Codex (or anything after it) becomes one module plus one template
directory, and installing the factory starts by asking which harness this repository
runs on.

## Why now

Phase 1 made a second backend real, but only *below* the seam. `agents.py` dispatched to
`agent_pi.py` or `agent_cc.py`, and everything above that dispatch stayed pi-shaped:

- **One prompt set for both.** `templates/prompt_engineering/planner/system.md` and
  `scout/system.md` ended in a `## Subagents` section describing `subagent_create` /
  `_continue` / `_list` / `_remove` — tools that exist only because a pi extension
  registers them. On Claude Code those names map to nothing, so the same prompt is a
  paragraph of fiction, and copying its `tools:` list into a claude_code agent is a
  validation error.
- **One roster, one env sample.** `templates/sssf.config.yaml` was a pi roster with
  claude_code described in comments; `subagents.ts` was stamped even into a repository
  that would never load it.
- **`install.py` took no harness at all.** A Claude Code install was a pi install the
  operator then edited by hand.
- **Harness settings were a hard-coded field.** `AgentConfig.claude_code:
  ClaudeCodeConfig` meant a third harness would have to edit `data_types.py` — shared
  code — to carry its own options.

## What changed

### The seam is a package

`adw_modules/harnesses/` replaces the two flat driver modules: `pi.py`,
`claude_code.py`, and an `__init__.py` holding the registry and documenting the contract
(`NAME`, `Options`, `resolve_model`, `reachable`, `validate_agent`, `new_session_id`,
`ToolCallTracker`, `run`). `agents.py` dispatches on that and nothing else.

Registration is an explicit import, not a directory scan: a half-written module should
fail at import, not during a run's config validation.

### A harness owns its options

`ClaudeCodeConfig` moved out of `data_types.py` and became `claude_code.Options`, with
`extra="forbid"`. `AgentConfig` carries an untyped `harness_options: dict`, parsed by
the harness that reads it. So:

```yaml
defaults:
  harness: claude_code
  harness_options:                 # keyed BY HARNESS — a mixed roster needs a block each
    claude_code: { safe_mode: true, permission_mode: bypassPermissions }
agents:
  - name: builder
    harness_options: { permission_mode: acceptEdits }   # flat; merged KEY BY KEY
```

The merge is per key, not per block — overriding `permission_mode` no longer silently
drops `safe_mode`. An options block written for the wrong harness now fails validation
instead of being ignored.

### `coding_agent` → `harness`, everywhere

Config key, `agent_map.json`, the `agent_start` payload, the `agent_sessions` column,
and the visualizer. The column was renamed in place rather than added: nothing had run
this factory yet, so there was no trace db to carry forward.

`AgentConfig.harness` is a plain `str`, not a `Literal`. A closed list would have made
every new harness a third edit in shared code; `agents.validate()` checks the name
against the registry and lists what exists, which is the error the `Literal` gave anyway.

### One template directory per harness

```
templates/
  config/base.yaml                 # observability, worktree, issues, pull_requests
  harnesses/<name>/
    about.md                       # line 1: the install question's one-liner
                                   # the rest: this harness's post-install steps
    defaults.yaml                  # the generated config's `defaults:` block
    agents.yaml                    # its `agents:` roster, in this harness's vocabulary
    env.sample                     # only the keys this harness actually needs
    prompt_engineering/<agent>/    # a FULL prompt set — not a shared base plus a patch
    harness_engineering/           # pi: subagents.ts · claude_code: a README
```

The stamped `sssf.config.yaml` is `defaults.yaml` + `base.yaml` + `agents.yaml`,
concatenated — plain text, comments intact, no templating engine.

Prompts are duplicated per harness deliberately. The alternative (a shared body plus an
appended fragment) keeps the skill DRY but makes the stamped file no longer the whole
truth about what an agent is sent, and the divergence is not cosmetic: it is one harness
having a fan-out mechanism the other does not.

### The installer asks

`install.py --harness <name>` answers ahead of time for a scripted or agent-driven
install. Without the flag it asks, listing what the skill ships with each harness's
one-liner from its `about.md`, re-asking on a bad answer. With no terminal to ask at, a
missing flag is an error — never a silent `pi`. Nothing is written until the question is
answered, so an abort leaves the repository untouched. After stamping, the harness's own
`about.md` body is printed as its next steps, so a new harness brings its instructions
with it instead of editing the installer.

Harnesses are discovered by listing `templates/harnesses/*/`, so the install question
cannot drift from what exists.

## As built

| Area | Files |
|---|---|
| Seam | `adw_modules/harnesses/{__init__,pi,claude_code}.py` (from `agent_pi.py`, `agent_cc.py`) |
| Config models | `adw_modules/data_types.py` — `AgentConfig.harness`, `harness_options`, `AgentRequest.options`; `ClaudeCodeConfig` and `PiRequest` removed |
| Dispatch + merge | `adw_modules/agents.py` — `harness_for()`, per-harness `harness_options` merge in `load_config()` |
| Trace | `adw_modules/tracer.py` — `agent_sessions.harness`; visualizer `shared/types.ts`, `server/db.ts`, `PhaseDetail.vue` |
| Templates | `templates/config/base.yaml`, `templates/harnesses/{pi,claude_code}/` |
| Installer | `scripts/_harness.py` (discovery, config assembly, the question), `scripts/install.py`, `scripts/make_config.py` |
| Docs | `references/harnesses.md` (new), `references/config.md`, `SKILL.md`, the install/config cookbooks |

**Verified:** both harnesses stamp into a fresh repo and their configs parse; the
claude_code roster loads through `agents.load_config()` and passes `agents.validate()`
end to end (models resolved, options merged, CLI reachable); a per-agent option override
keeps its siblings; a claude_code options block on a pi agent fails validation; the
install question re-asks on a bad answer and errors without a TTY.

## Not done here, on purpose

- **No Codex module.** The seam and the docs (`references/harnesses.md`) say exactly
  what one costs — a module and a template directory — but an untested driver against a
  CLI nobody has run here is worth less than the checklist.
- **No prompt-sharing mechanism.** If a third and fourth harness make the duplication
  hurt, the answer is a shared body plus per-harness fragments composed at stamp time —
  and it can be added later without changing anything a stamped repository sees.
