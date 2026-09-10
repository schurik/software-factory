# Phase 9 — Human-in-the-loop gates

> **Status: planned.** The brainstorm below stands as written; the seven open
> questions at its end are answered in *Decisions*, and the implementation plan
> is [`superpowers/plans/2026-09-10-hitl-gates.md`](superpowers/plans/2026-09-10-hitl-gates.md).
> Nothing is built yet.

## Goal

A run can stop after any phase and hand its work product to a human. The
human reads the artifact — a plan, a diff, a review report — and either
**approves** (the chain continues), **rejects with notes** (the agent that
produced the artifact revises it, in the same session, and the human is asked
again), or **aborts** (the run ends, not accepted). The loop runs until the
human approves or aborts.

Whether a gate fires is policy, not code: a run can be told to trust every
gate and pass through, or to stop at every one, per gate and per run.

## Why now

The roadmap has listed this as a non-goal since the fork: *"Human-in-the-loop
approval phases. Worth having; not on this roadmap yet."* Three things built
since make it small:

- **The revise loop already exists.** `adw_build_review.py:49-66` is
  reviewer → `ReviewOutput(approved, findings, blocking)` → builder revises
  with `previous=review` → reviewer again, bounded. A human gate is that loop
  with a person where the reviewer is.
- **Resume already exists.** `--resume` answers recorded agent phases from
  the session directory and re-runs what code owns (`adw_modules/replay.py`).
  A run that stops to wait for a human is a run that can be picked up where
  it stopped — the machinery is the same.
- **The engineer lane already exists** (`PhaseKind = "engineer"`,
  `data_types.py:18`) and today does one thing: log the request. A gate is
  the first phase that gives that lane something to *do* mid-run.

## What is there today

| Need | Today |
|---|---|
| A place in the trace for a human decision | `kind="engineer"` phases render in the engineer lane; only `request` uses it (`runner.py:33-40`) |
| An agent revising its own work from feedback, context intact | `agents.execute` re-prompts the same session on gate violations (`agents.py:268-282`); the reviewer/revise loop does it at chain level |
| A run stopping and being picked up later | `--resume` + `replay.envelope_for()` matches recorded phases by name, owner, output type (`replay.py:69`) |
| A record that survives the process | `sessions/<adw_id>/run.json`, `envelopes/<phase_id>.json` — never the db (`artifacts.py`, `test_no_db_reads.py`) |
| A human channel into a running session | Issue labels (`issue_watch.py`), unresolved PR threads (`pull_requests.py`), and the terminal prompt |
| Session status vocabulary | `running \| success \| fail` — no "waiting" (`tracer.py:335`, `shared/types.ts:10`) |
| Deciding "still running?" | `resume.py:110` checks `status == "running"` and a live pid; `issue_watch._running_count` counts live pids against `max_concurrent` |

Nothing waits for a human today. The two forge paths are the closest thing:
they turn a human's words into a run's input, but always by *starting* a
process, never by continuing one that is standing still.

---

## The shape: five questions

### 1. What is a gate in the chain?

Three candidate shapes.

**A. A gate is its own engineer phase, plus a chain-level revise loop.**

```python
plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt, gates=[...]))

for round in hitl.rounds(run, "plan"):                     # 0 iterations when the gate is off
    with run.phase(PhaseParams(name=round.gate_phase, kind="engineer", owner=run.engineer,
                               description="Hand the plan to the engineer and wait for a verdict")) as ph:
        decision = ph.decide(hitl.Subject(envelope=plan, artifacts=plan.artifacts))
    if decision.approved:
        break
    with run.phase(PhaseParams(name=round.revise_phase, kind="agent", owner="planner",
                               description="Rework the plan along the engineer's notes")) as ph:
        plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt, previous=decision,
                                 gates=[gates.artifacts_exist, gates.files_non_empty]))
```

Trace: `plan → approve_plan → plan_revise_1 → approve_plan_2 → build`. Every
wait is a visible block in the engineer lane with a duration; every revision
is an agent phase with its own envelope, tokens, and gate results. On
`--resume`, `plan` and `plan_revise_1` replay from their records, and
`approve_plan_2` reads its decision from the record or waits.

**B. A gate is a gate callable on the agent call.**

```python
plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt,
                         gates=[gates.artifacts_exist, hitl.approval("plan")]))
```

A rejection is a gate violation whose text is the human's notes, and
`agents.execute` already loops those back into the same session as a
correction. Fully generic — one list entry after any agent, no per-agent
revise prompt. But the wait happens *inside* the agent phase: the phase reads
as one long agent block, the agent's wall clock and spend accounting wrap a
human's lunch break, `retries` doubles as the round limit, and suspending
mid-phase means recording a provisional envelope so a resumed process can
skip the agent and go straight to the gate. Honest in code, dishonest in the
trace.

**C. A global hook: gate after every agent phase.**

`run.phase()` opens a gate on exit of every `kind="agent"` phase when the run
was told to. Nothing to write in an ADW. But on rejection the runner does not
know how to revise — the ADW owns the loop and the runner cannot re-enter
it — so a global gate can only approve or abort. And it gates `fix_2` and
`revise_1` as eagerly as `plan`.

**Recommendation: A for the primitive, with a helper so an ADW spends three
lines per gate, not fifteen.** The trace is the product; a wait that hides
inside an agent block breaks "the trace shows exactly when code ran and when
an agent was working". Keep C as an opt-in *force* mode (`--hitl every`) that
adds approve/abort checkpoints after every agent phase for the runs where a
human wants to watch each step, and say plainly that those checkpoints cannot
revise.

The helper is the shape the reviewer loop already has, moved into a module
(rule 6). Sketch:

```python
plan = hitl.gated(run, hitl.Gate(
    name="plan",                                  # the gate's id; policy keys on it
    produce=lambda previous: ph_call_plan(previous),   # the agent phase, re-run on reject
    subject=lambda env: env.artifacts,            # what the human is shown
))
```

Whether `produce` takes the phase or the helper opens it is a plan-level
detail. What matters: the revise call is the *same* `AgentCall` (output type,
gates, prompt) with `previous=decision`, in the same agent session, so nothing
new has to be written per agent, and the planner that gets a rejection
already has its own plan in context.

Gates after **code** phases work in the same shape with no revise target:
"approve before `integrate`" is approve/abort only, and it is probably the
second most wanted gate after the plan.

### 2. How does the process wait?

**Block.** The ADW process polls the decision record, with a wall clock. The
simplest thing; the pid stays alive; `just kill` works unchanged. The cost is
a Python process holding a worktree for a day because someone went home.

**Suspend.** The gate phase writes "waiting" to `run.json`, prints how to
answer, and the process exits with its own code (75, `EX_TEMPFAIL`, so cron
and CI can tell "waiting" from "failed"). `just approve <adw_id>` records the
decision and re-launches the same workflow with `--resume`; replay carries the
chain to the gate, which now finds its decision and continues. This is
`just resume` with one new file to read. Everything downstream that a resume
already handles — agent sessions resumed from `agent_map.json`, code phases
re-run, pins kept — is inherited.

**Hybrid (recommended).** Block while attended, suspend when not:

- stdin is a TTY → prompt in the terminal (`[a]pprove / [r]eject / [d]etach`),
  and poll the decision record meanwhile so `just approve` from another
  terminal or the UI is honoured too. `d`, or `hitl.wait_seconds` running out,
  suspends.
- no TTY (cron, an issue-triggered run, `just up`) → suspend immediately, and
  notify (below).

Both paths read **one** decision record, so a decision arriving by any
channel is the same decision.

Suspending is the mechanism; blocking is a convenience over it. That order
matters: a design that starts from blocking and adds suspension later ends up
with two state machines.

### 3. What does the human see, and what do they say back?

**The subject.** The envelope of the phase being gated, its artifacts with
absolute paths, and where the tree is (`run.repo_root`, the branch). For a
build gate the artifact is the diff — `changes.capture()` already writes one
to `context_handoff/`. The console prints the paths; the UI renders `plan.md`
through the markdown renderer it already has; the forge channel posts it as a
comment.

**The decision** — a typed record, the same shape wherever it came from:

```python
class Decision(EnvelopeBase):          # so `previous=decision` works unchanged
    gate: str                          # "plan"
    round: int                         # 1, 2, ...
    verdict: Literal["approve", "reject", "abort"]
    notes: str = ""                    # the human's words; on approve, "approved with remarks"
    by: str                            # engineer name, forge login, or "policy"
    channel: str                       # terminal | cli | ui | forge | auto
    subject_digest: str                # what exactly was approved — see below
```

`notes_for_next_agent` carries the notes forward on approve-with-remarks
without a revise round: the builder gets the plan *and* "approved, but keep
the migration reversible". On reject, `notes` becomes the correction the
agent sees; on the forge channel that text is framed as a request, not an
instruction, the way `pull_requests.HANDOFF_NOTES` already frames review
threads.

**The decision names what it approved.** `subject_digest` is a hash of the
artifacts (plan.md, the diff) at the time the human was asked. A decision for
a subject that has since changed — a stale `just approve` from round 1 landing
on round 2 — is refused rather than honoured. This is the one safety rule the
whole thing rests on.

**Trust is recorded, not silent.** A gate the policy skips still writes a
decision: `verdict=approve, by="policy", channel="auto"`. The trace then shows
every gate a run passed and *who* passed it. Later, when the learning loop
(Phase 4) asks which plans humans rejected and why, the answer is already in
`decisions/`.

### 4. Policy: on, off, forced

Placement is structure and belongs to the ADW (rule: code owns sequencing).
*Whether a placed gate fires* is policy, resolved most-specific-wins:

```yaml
hitl:
  default: off                     # a stamped repo behaves exactly as today
  gates:                           # by gate name — the id the ADW gave it
    plan: on
    integrate: on
    build: off
  wait_seconds: 900                # attended: prompt this long, then suspend
  max_rounds: 0                    # 0 = until the human approves or aborts
  channel: terminal                # terminal | forge; the file record is always there
  notify_command: []               # e.g. ["ntfy", "publish", "sssf"] — a known command is code
  when_unattended: suspend         # suspend | auto — what an issue/cron run does at an on-gate
```

Per run, on the command line every chain already parses: `--hitl all`,
`--hitl none`, `--hitl plan,integrate`, `--hitl every` (the force mode from
shape C). `SSSF_HITL` for the justfile, matching `SSSF_CONFIG`.

Should gates key on the **gate name** or on the **agent**? "After any agent"
reads as agent, but the same agent is called five times in one chain
(`build`, `fix_1`, `revise_1`, …) and nobody wants a gate on each. The gate
name is the ADW's decision of *where a human decision is worth having*;
policy chooses among those. An agent-level default (`agents[].hitl: on`) can
be added later as a shorthand that turns on every gate the agent owns.

**Trigger-aware default.** An engineer at a keyboard and a cron job are
different runs. Proposal: `default` applies to engineer-triggered runs;
issue- and PR-triggered runs read `when_unattended` — `suspend` posts the
subject to the issue and waits for the label or comment, `auto` records a
policy approval and continues. Nothing forces a repo to choose: `off` is
`off` everywhere.

### 5. Channels

All of them write the same record, `sessions/<adw_id>/decisions/<gate>_<round>.json`.
The factory reads files, never the db — the existing rule, kept.

| Channel | Ask | Answer | Notes |
|---|---|---|---|
| Terminal | console prints subject, prompts | keypress + notes | attended runs only |
| CLI | `just pending` lists waiting runs and their subjects | `just approve <id> [-m "..."]`, `just reject <id> -m "..."`, `just abort <id>` | works against a blocked *or* a suspended run; the reject/approve re-launches a suspended one via `resume.py` |
| Trace UI | "waiting for you" on the session card, plan rendered | approve / reject buttons | the server's **second** write path (today it has one, `archive`, `server/index.ts:161`); it would write the decision file, not a db row |
| Forge, issue | the subject as a comment on the issue | label `sssf:approved` / `sssf:rejected` + the comment as notes | reuses `issues.py`, `trusted_authors`; the label state machine gains two states |
| Forge, PR | the plan commit pushed, the PR is the review | approval = approve; an unresolved thread = reject with notes | reuses `pull_requests.py` wholesale — a **build** gate in PR mode is what `adw_pr_review` already is |
| Notify | `notify_command` with the subject on stdin | — | a hook, not a channel; the answer still comes through one of the above |

The forge row is the interesting one: for code, the review loop *already
exists* as `adw_pr_review`. What is genuinely new is the plan gate, and a plan
pushed to a PR gets the same treatment for free once the gate can wait on a
PR's review decision.

**v1 channels:** terminal + CLI + UI. Forge second. Every one of them is a
writer of the same file, so adding one never touches the gate.

---

## State: what changes, and what must not break

**A fourth session status: `waiting`.** In `run.json`, in `sessions.status`,
in `shared/types.ts`. With it, `waiting_for: {gate, round, phase_id, since}`
on `run.json`, so `just pending` and the UI know *what* it waits on.
Everything that asks "is it running?" needs a look:

| Reader | Today | With `waiting` |
|---|---|---|
| `resume.py:110` | refuses when `running` + live pid | a suspended run has no pid; `waiting` is resumable |
| `issue_watch._running_count` | counts live pids | a suspended run holds no agent and must not count against `max_concurrent`; a blocked one does |
| `worktrees.py prune` | prunes ended runs with nothing uncommitted | never prunes a `waiting` run — its tree *is* the subject |
| `pr_watch` reaper | stops a run on a merged branch | a waiting run on a merged branch should be aborted, not left waiting forever |
| `Run.finish()` | `success \| fail` | a suspended run does not call `finish()`; it calls `suspend()`, which closes phases as `waiting`, keeps the worktree, and exits 75 |
| `_finalize_when_killed` | marks `fail` | a killed *waiting* run — fine, it is a fail |
| UI `PhaseDots`, `SessionCard`, `StatusChip` | three states | a fourth colour and the badge |

**The phase status.** A blocked gate phase is `running` like any phase. A
suspended one — the process is gone — should not read as `running` forever
(the bug `_finalize_when_killed` exists to prevent). Either `waiting` joins
`PhaseStatus` too, or the gate phase closes as `success` with the decision
`pending` and the wait lives on the session only. The first is more honest in
the waterfall; the second changes fewer files. Undecided.

**Replay.** The gate phase replays like an agent phase does: name, owner,
and the decision's `subject_digest` must match the subject in front of it
now. A revise phase is an ordinary agent phase named `<gate>_revise_<n>` and
replays as one. Phase names are already unique per run and deterministic per
round, which is what makes this work without a new matching rule.

**Budget and clocks.** A wait spends nothing and has no agent timeout;
`hitl.wait_seconds` is its own clock and only decides when to suspend.
`budget` still applies to the revise rounds — a human who rejects ten times
can spend the ceiling, and the phase after the tenth fails as
`budget_exceeded` like any other.

**Worktree.** A waiting run keeps its worktree, like a failed one. The plan
gate's subject *is* the uncommitted `specs/<id>.md` in that tree, and the
`release` rule "keep anything with uncommitted work" already covers it — but
the rule should be explicit, not incidental.

**Security.** Who may approve: on the terminal and CLI, whoever can run
commands in the checkout — the same trust the prompt has. On the forge,
`trusted_authors` / `trusted_reviewers` as today. Rejection notes reach an
agent holding `bash`; from the terminal they are the operator's words, from
the forge they are framed the way review threads are. `by` is always
recorded.

## What this is not

- **Not a wait inside `agents.execute`.** The agent's clock, spend, and
  permission snapshot bracket the agent's turn, and a human's afternoon does
  not belong inside them.
- **Not a db read.** The visualizer may *write* a decision file; the factory
  reads files.
- **Not a new harness.** A human is not an agent with `harness: terminal`;
  the engineer lane exists for exactly this.
- **Not a gate on every phase by default.** Force mode is a flag.
- **Not a job queue.** A suspended run is a file and a branch, and `just
  pending` is a `ls`. The thing that pokes it back to life is `resume.py`,
  which exists.

## A first slice

Small enough to build in a day and prove the shape:

1. `Decision` type, `decisions/` record, `hitl.gated()` helper, `waiting`
   status on `run.json` and the db, `suspend()` on the run, exit 75.
2. Terminal channel with detach; `just pending / approve / reject / abort`
   over `resume.py`.
3. The plan gate in `adw_plan_build.py` and `adw_simple_sdlc.py`, and an
   integrate gate in `adw_simple_sdlc.py`. Config `hitl:` block with
   `default: off`.
4. Fake-harness tests: approve continues; reject revises in the same session
   and asks again; a stale digest is refused; a suspended run resumes to the
   gate and not to the top.

The UI badge and buttons are slice two; the forge channel slice three.

## Open questions

Asked on 2026-09-10 and answered the same day — see *Decisions* below. Kept
as asked, so the reasoning above still reads against them.

1. **Shape A (own engineer phase + loop) as the primitive, with force mode C
   as approve/abort-only?** The alternative is B, which is more generic and
   less visible.
2. **Suspend as the mechanism, block as the attended convenience?** Or block
   only for v1 and accept a process that waits for a day.
3. **Gate policy keyed by gate name, not agent name?** With an agent-level
   shorthand later.
4. **A fourth session status `waiting`, and does the *phase* get it too?**
5. **Unbounded rounds by default (`max_rounds: 0`)?** The human bounds the
   loop by aborting; the budget bounds it by money.
6. **Issue-triggered runs: `suspend` and post to the issue, or `auto`?** The
   safe default is `suspend`; the useful one for an overnight queue is `auto`.
7. **Trace UI gets a write path for decisions, or stays read-only and the CLI
   is the only writer in v1?**

## Decisions

The recommendation on every question, taken as decided on 2026-09-10.

| # | Decision |
|---|---|
| 1 | Shape **A**: a gate is its own engineer phase plus a chain-level revise loop, behind `hitl.gated()`. Force mode **C** ships as `--hitl every`, approve/abort only. Shape B is not built. |
| 2 | **Suspend** is the mechanism — exit 75, `just approve` re-launches through `--resume`. A terminal prompt blocks only while stdin is a TTY, and detaches into the suspend on `d` or after `wait_seconds`. |
| 3 | Policy is keyed by **gate name**. An agent-level shorthand is a follow-up. |
| 4 | A fourth session status **`waiting`**, on `run.json`, the db and the UI — and the gate **phase** closes as `waiting` too. |
| 5 | **Unbounded** rounds by default (`max_rounds: 0`). |
| 6 | Issue- and PR-triggered runs **suspend** at an on-gate (`when_unattended: suspend`); `auto` is available. Posting the subject to the forge is a follow-up. |
| 7 | The **CLI** is the only writer in v1. The trace UI shows `waiting`; its write path is a follow-up. |

The plan turns these into nine tasks, each test-first on the fake harness.
