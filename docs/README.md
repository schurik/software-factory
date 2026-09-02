# Software Factory — Roadmap

This directory holds the design documents for turning this fork of the **Super Simple Software Factory** (SSSF) into an autonomous software factory that covers the full SDLC.

Each phase is a self-contained work item with its own document. Read this page first for the shape of the whole thing, then the phase you are about to build.

---

## Where we start from

Upstream SSSF is a Claude Code skill at `.claude/skills/sssf/` that stamps a deterministic Python control plane into any repository. Its core decision — **code owns sequencing, retries and acceptance; the agent owns only the work inside one bounded phase** — is the right one, and it is the expensive part to get right. It is already built:

| Capability | Where it lives today |
|---|---|
| One coding-agent session per agent, with its own system prompt | `adw_modules/agents.py`, `adw_modules/agent_pi.py` |
| Per-agent model, effort, tools, prompts, write permissions | `adws/adw_sssf_config/sssf.config.yaml` |
| Typed artifact handoff between agents | `EnvelopeBase` subclasses in `adw_modules/data_types.py`, plus `context_handoff/` |
| Claims verified after the fact, not trusted | `adw_modules/gates.py` |
| Write boundary enforced with rollback | `adw_modules/permissions.py` |
| Streaming trace written while the run is in flight | `adw_modules/tracer.py` → SQLite (WAL) + JSONL |
| Live trace UI | `.claude/skills/sssf/apps/visualizer/` (Bun + Vue) |
| Per-repo extensibility | `scripts/install.py` stamps templates into any repo |

The decision taken was therefore **extend, not rewrite**, and to treat this as a **hard fork**: upstream is a starting point, not a supplier. There is no obligation to keep merges from it clean.

## What is missing

Upstream's README says it plainly: *"no sandbox, no branch per run, no merge step, no cloud, and no human-in-the-loop approval phase."* Concretely:

1. ~~**`coding_agent: claude_code` is a stub.** The config schema accepts it, but `adw_modules/agent_cc.py` raises `NotImplementedError` and `agents.py:61` rejects any backend but Pi at validation time.~~ **Fixed in Phase 1:** both backends are real and selectable per agent, behind one protocol and one normalised tool-call record.
2. **The trace is local and anonymous.** It lives in a gitignored SQLite file inside each repo. There is no cross-repo view, and no column anywhere records which project, repository, branch or commit a run belonged to.
3. ~~**Runs execute in the dirty working tree, on the current branch.** Two concurrent runs collide. A failed run leaves its mess where you were working.~~ **Fixed in Phase 2:** a git worktree and branch per run, plus a configurable integration phase that lands it.
4. **Past runs are never read back.** The trace is a write-only archive.

## Decisions already fixed

These are settled; the phase documents assume them rather than re-argue them.

| Decision | Choice |
|---|---|
| Relationship to upstream | Hard fork. Core modules may be restructured. |
| Coding-agent backend | Both Pi and Claude Code, first-class, selectable per agent |
| Central store | Convex DB with Convex HTTP actions, as a new standalone Convex project in `convex/` |
| Execution environment | Local, with a git worktree and branch per run as the first step |
| Document language | English, matching the rest of the repository |

## The four phases

| Phase | Document | Unlocks | Status |
|---|---|---|---|
| 1 | [Dual backend](phase-1-dual-backend.md) | Run agents on Claude Code or Pi, chosen per agent, in the same run | **built** — see its *As built* section |
| 2 | [Worktree per run](phase-2-worktree-per-run.md) | Isolated, concurrent, non-destructive runs; the prerequisite for sandboxing | **built** — see its *As built* section |
| 3 | [Convex trace store](phase-3-convex-trace-store.md) | One queryable place for every run from every repo | not started |
| 4 | [Learning loop](phase-4-learning-loop.md) | Past runs improve future runs | not started |

### Dependency order

```
Phase 1 (dual backend) ─┐
                        ├─→ Phase 3 (Convex) ─→ Phase 4 (learning)
Phase 2 (worktree) ─────┘
```

- **Phase 1 and Phase 2 are independent of each other.** They touch disjoint modules: Phase 1 changes `agents.py`, `agent_cc.py`, `agent_pi.py` and `data_types.py`; Phase 2 changes `git_helper.py`, `gates.py`, `runner.py` and `session.py`. Either can go first, and both are roughly a day of work.
- **Phase 3 does not strictly require Phase 1**, but is meaningfully easier after it: once tool-call events are normalised across backends, the Convex schema does not need a per-backend shape.
- **Phase 4 hard-depends on Phase 3.** There is nothing to learn from until the central store exists.

Recommended order: **2 → 1 → 3 → 4**. Phase 2 first, because running in a dirty working tree is the thing most likely to cost you real work while you build everything else. Phases 2 and 1 are done; Phase 3 is next, and it inherits a tool-call event shape that is already normalised across backends — so its schema needs no per-backend variant.

## Deliberate non-goals for now

Named here so nobody plans around their absence by accident:

- **Container sandboxing.** Phase 2 is the prerequisite; a `docker`/devcontainer executor slots in behind the same seam afterwards.
- **Remote cloud runners and a job queue.** A run is one Python process, which maps cleanly onto one container — but the queue and worker layer sits *above* the factory, not inside it.
- **Human-in-the-loop approval phases.** Worth having; not on this roadmap yet.
- **Automatic prompt rewriting.** Explicitly out of scope even in Phase 4; surfacing evidence to the engineer comes first.

## Constraints every phase must respect

The ten hard rules in `.claude/skills/sssf/SKILL.md` apply to everything generated here. The ones that bite most often:

- **Rule 2 — the synced triad.** An output type in `data_types.py`, its JSON example in the agent's `user.md` `## Report` section, and `output_type=` at every call site are *one* contract. Change one, change all three in the same edit.
- **Rule 4 — the four-param rule.** More than four parameters means introduce a concrete data type. `AgentCall` and `PhaseParams` are the pattern.
- **Rule 6 — ADW scripts stay thin.** Low-level logic lives in `adw_modules/`.
- **Rule 7 — every phase earns a description.** Validated at construction in `data_types.py:31-53`; blank and name-echo are both rejected.
- **Rule 9 — `tools:` is a capability list, `writes:` is the boundary.** Enforcement lives in `permissions.py`, after every agent call.
- **Rule 10 — every ADW ends in `run.finish(accepted=…)`.** Phases passing is not the same as the run being accepted.
