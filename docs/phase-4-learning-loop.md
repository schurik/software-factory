# Phase 4 — Learning from past runs

## Goal

Past runs stop being a write-only archive. Two things become possible: seeing **which parts of the factory actually cost you** across every repository, and **feeding relevant prior work into a live run**.

## Why now

Not now — this is last, and deliberately so.

**Hard prerequisite: [Phase 3](phase-3-convex-trace-store.md).** Until every run from every repository is in one queryable store, there is no corpus to learn from. Building retrieval against per-repo SQLite files would produce something that works on one machine and nowhere else.

## Current state

The trace already contains far more than anything reads back. Per `references/observability.md` and the code:

- **Per-phase spend is already itemised.** The `agent_end` event carries `usage` broken into `input`, `output`, `cache_read`, `cache_write`, summed across every send in that phase (`agents.py:204-212`). Nobody queries it.
- **Gate outcomes carry evidence, not just verdicts.** `GateReport` records `{item, ok, note}` per check (`data_types.py:252-283`), persisted both as `gate_results.checks_json` and as `gate_pass`/`gate_fail` events with an `attempt` number. That is a per-agent, per-gate failure history nobody reads.
- **Retry counts are recorded.** `phases.attempt` and `retries`, plus every invalid envelope persisted as its own row (`agents.py:275`) with the raw text. Which agent produces malformed JSON, and how often, is already in the data.
- **Context occupancy is tracked.** `agent_sessions.context_tokens` against `context_window` — how close each agent runs to its ceiling.
- **Envelopes are the work products.** Every plan, review, scout finding and build report is stored as structured JSON keyed to its phase.

What is missing is not instrumentation. It is that nothing ever reads it back.

## Design

Keep two capabilities separate. They have different consumers, different risk profiles, and one is much easier than the other.

### A. Analytics — queries over Convex

Read-only, no effect on any running chain, and buildable the day Phase 3 lands. Questions worth answering first:

| Question | From |
|---|---|
| Which agent burns the most tokens per accepted run? | `agent_end.usage`, grouped by `phases.owner` |
| Which gate fails most, and on which agent? | `gate_fail` events by `gate` and `phases.owner` |
| Which agents produce malformed envelopes? | `envelopes` where `valid = 0`, by agent |
| Which phases retry most? | `phases.attempt` vs. `retries` |
| Is a model change actually paying for itself? | Cost and acceptance rate per `agent_sessions.model` over time |
| Which agents run near their context ceiling? | `context_tokens / context_window` |
| Where does wall-clock time actually go? | Phase durations, derived from `started_at`/`ended_at` |

This is the part that changes decisions — which model to put on which agent, which prompt to rewrite, which gate is too strict. Build it first.

### B. Retrieval — past work into a live run

This fits the existing handoff model exactly, with no new mechanism.

A retrieval step is a phase whose output is an ordinary `EnvelopeBase` subclass, passed to the next agent as `AgentCall(previous=…)`. The consuming agent **cannot tell it from any other upstream envelope** — which is precisely the trick `quality.as_envelope()` (`quality.py:193-211`) and `changes.as_envelope()` (`changes.py:87-103`) already use to hand deterministic results to an agent through the same door an agent report would have used.

So: a `kind="code"` phase, per SKILL.md rule 8 — querying a store is a known operation, not a judgement call — producing something like a `RecallOutput` envelope with the prior runs it found and why.

**Obligations that come with a new output type** (SKILL.md rule 2, the synced triad):

1. The type in `adw_modules/data_types.py`
2. Its JSON example in the consuming agent's `user.md` `## Report` section
3. `output_type=` at every call site

All three change in one edit. And per rule 7, the new `PhaseParams` needs a real description — validated at construction in `data_types.py:31-53`, where both blank and name-echo are rejected.

## Open questions

These are genuinely open. Stating them is more useful than guessing.

- **What does "relevant" mean?** Candidates, roughly in order of how cheap they are: same repository; touches overlapping files; failed the same gate; same agent and same model; semantically similar request. The first four are metadata filters over Convex. Only the last needs embeddings.
- **Do we need embeddings at all?** Probably not at first. Metadata filters over `(project, repo, changed_files, gate, agent)` will answer most of it, and they are debuggable in a way vector similarity is not. Revisit once the metadata approach demonstrably runs out.
- **How do we avoid feeding failures forward?** The archive contains bad plans, rejected reviews and rolled-back builds. Retrieval that surfaces a failed run's plan as precedent makes the factory worse. At minimum, filter on run acceptance — and note that `run.finish(accepted=…)` (SKILL.md rule 10) already separates "every phase passed" from "the run was accepted", which is exactly the distinction needed.
- **Context budget.** Injected history competes with the actual task for the window. `agent_sessions.context_tokens` vs. `context_window` already tells you how much room each agent has; use it as a real constraint, not an afterthought.
- **Staleness.** A plan from six months ago describes a codebase that no longer exists. Recency weighting, or a hard cutoff, or filtering by whether the referenced files still exist.
- **Cross-repo leakage.** Retrieval across projects is the interesting case and the risky one. A run in repo A should not surface repo B's code in its prompt unless that is explicitly wanted. Default to same-project; make cross-project opt-in.

## Explicit non-goal

**Automatic prompt rewriting.** Not in this phase, even though the data supports it.

Surfacing evidence to the engineer comes first: "the reviewer has rejected `diff_matches_claims` on eleven of the last twenty builder runs" is actionable, auditable, and leaves the human in the loop. A system that silently edits its own prompts based on its own metrics is much harder to debug, and much easier to get subtly wrong — you lose the ability to say which prompt produced which result.

Revisit only once the analytics have been in use long enough that the patterns are obvious to a human reading them.

## Work items

Ordered; the first three are independently useful.

1. **Analytics queries** over Convex for the questions above.
2. **A dashboard view** — per agent, per model, per gate: cost, retries, failure rate, acceptance rate.
3. **Decide the relevance model** and write it down before implementing anything.
4. **`RecallOutput` envelope type**, with its synced triad.
5. **A `kind="code"` recall phase** producing that envelope from a Convex query.
6. **Wire it into one ADW**, gated behind config so it can be turned off.
7. **Measure**: does a chain with recall actually produce better outcomes than the same chain without? If it cannot be shown to, it is not worth the context it costs.

## Verification

1. Analytics queries return results consistent with the local SQLite for the same runs — cross-check a handful by hand.
2. The recall phase produces a valid envelope that the next agent consumes without any prompt change beyond its `## Report` section.
3. Recall respects the acceptance filter: a deliberately failed run does not surface as precedent.
4. Recall respects the context budget: injected content does not push the consuming agent near its ceiling, verified against `agent_sessions.context_tokens`.
5. A/B the same request through the same chain with recall on and off, and compare acceptance rate, retry count and cost. Report the result honestly, including if it is worse.

## Done when

- [ ] Cross-repo analytics answer the questions listed above.
- [ ] A dashboard shows cost, retries and failure rate per agent, model and gate.
- [ ] The relevance model is written down and justified.
- [ ] A recall phase feeds prior work into a live run through an ordinary envelope.
- [ ] Failed runs are excluded from retrieval.
- [ ] Retrieval is measured against a no-retrieval baseline, and the measurement is published.
