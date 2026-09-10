"""The Run object: config + adw_id + agent_map + tracer + console, bound once.

`run.phase(PhaseParams(...))` is the ONE phase primitive — a context manager
for all three kinds (engineer, agent, code). Success must be earned: every
phase defaults to fail; only a clean exit flips it (agent phases additionally
require a parsed envelope + green gates, enforced inside ph.call).

Two roots, deliberately: `repo_root` is the run's worktree — where agents are
spawned, gates measure, and commits land — while `main_root` is the engineer's
checkout, which owns `data_dir` and everything under it. The run's record has
to outlive the tree the run worked in, because that tree is pruned when the run
is accepted.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager

from . import agents, artifacts, hitl, limits, replay, worktree
from .console import Console
from .data_types import (AgentCall, EnvelopeBase, EventRecord, Phase, PhaseParams,
                         RunSpec)
from .utils import anchor, ensure_dir, now_iso


class PhaseHandle:
    def __init__(self, run: "Run", phase: Phase):
        self.run = run
        self.phase = phase

    def log(self, **payload) -> None:
        self.run.tracer.event(EventRecord(adw_id=self.run.adw_id,
                                          phase_id=self.phase.phase_id,
                                          type="log", name=self.phase.params.name,
                                          payload=payload))
        self.run.console.note(", ".join(f"{k}: {v}" for k, v in payload.items()))
        if self.phase.params.kind == "engineer" and "input" in payload:
            self.run.tracer.session_request(self.run.adw_id, str(payload["input"]))

    def call(self, call: AgentCall) -> EnvelopeBase:
        if self.phase.params.kind != "agent":
            raise RuntimeError("ph.call() is only valid inside an agent phase")
        return agents.execute(self.run, self.phase, call)


class Run:
    def __init__(self, spec: RunSpec, tracer):
        self.cfg = spec.cfg
        self.adw_id = spec.adw_id
        self.tracer = tracer
        self.console = Console(tracer, spec.adw_id)
        self.engineer = spec.engineer
        self.phases: list[Phase] = []
        self.tokens = 0                 # THIS process — what the banner reports
        self.cost = 0.0
        self._seq = 0                   # set below, once session_dir is known
        self.workspace = spec.workspace
        self.repo_root = spec.workspace.repo_root      # the tree agents work in
        self.main_root = spec.workspace.main_root      # the checkout that owns data_dir
        # What asked for this run. 'engineer' until an issue phase says otherwise
        # — and integration reads it, because a prompt written by whoever can
        # file an issue does not get to move the base branch.
        self.trigger = "engineer"
        self.issue_number = 0
        self.issue_url = ""
        # Where this session's work already went. Empty until an integration
        # opens a pull request; read back from the trace by every later process
        # in the session, because a branch that is already a PR gets pushed to,
        # never proposed a second time.
        self.pr_url = ""
        # ...unless the session already knows better. A joined run inherits the
        # provenance the first process recorded; without this every re-entry
        # would claim to be engineer-triggered. session.ensure() fills it in.
        # The runtime is anchored to the MAIN checkout, not to the worktree: one
        # trace db for every concurrent run, one place the visualizer reads, and
        # a record that survives the worktree being pruned. The cost is that
        # context_handoff/ now sits outside the agent's working directory, so
        # the path handed to an agent must be absolute — see agents.execute.
        data_dir = anchor(spec.workspace.main_root, self.cfg.defaults.data_dir)
        self.session_dir = ensure_dir(data_dir / "sessions" / spec.adw_id)
        self.context_handoff_dir = ensure_dir(self.session_dir / "context_handoff")
        # A joined or resumed run continues the phase sequence rather than
        # restarting at 1 — from the session's own event log, so it is right
        # with the db deleted.
        self._seq = artifacts.max_phase_seq(self.session_dir, spec.adw_id)
        # What the SESSION had already spent before this process opened, read
        # off its own run.json — never the db, which the factory must work
        # without. A joined run (`--adw-id`, `just integrate`, a pr-review
        # re-entry) is the same work continuing, so the budget counts from
        # here, while `tokens`/`cost` above stay THIS process's and keep the
        # banner reporting the run in front of the engineer. A new session has
        # no record and answers (0, 0.0).
        spent = artifacts.read_run(self.session_dir)
        self._prior_tokens = spent.total_tokens if spent else 0
        self._prior_cost = spent.total_cost if spent else 0.0
        self._agent_map_path = self.session_dir / "agent_map.json"
        self.agent_map: dict = (json.loads(self._agent_map_path.read_text())
                                if self._agent_map_path.exists() else {})
        # Resuming: replay the agent phases this session already recorded rather
        # than paying for them twice. Inert on a normal run — see replay.py for
        # what is replayed and what is deliberately re-run.
        self.resuming = spec.resume
        self.replay = replay.load(self.session_dir, spec.resume)
        # Which gates stop for a human this run. Built once, asked at every
        # gate with the trigger the run knows THEN — an issue chain learns it is
        # issue-triggered in its first phase, after this constructor ran.
        self.hitl = hitl.HitlPolicy(self.cfg.hitl,
                                    spec.hitl or os.environ.get("SSSF_HITL", ""))
        # Values a run pins once and must not re-derive on the way back in (the
        # documenter's diff baseline is the one that matters). Written every
        # run, read only by a resumed one — see `pin`.
        self._pins_path = self.session_dir / "pins.json"
        self._pins: dict = (json.loads(self._pins_path.read_text())
                            if self._pins_path.exists() else {})

    # ── pinned values (what a resumed run must not re-derive) ───────────────
    def pin(self, key: str, produce) -> str:
        """Remember a value this process derived, or hand back the resumed one.

        `baseline = run.pin("baseline", lambda: git_helper.rev(run.repo_root, "HEAD"))`.

        The problem it solves is one line long: a chain pins its diff baseline
        at HEAD before it commits anything, so a resumed run — whose HEAD now
        carries the commits the FIRST run made — would pin a baseline after its
        own work and hand the documenter an empty diff.

        Read only when resuming, written always. A joined run (`--adw-id`
        without `--resume`) is a new increment of the session and derives its
        own value, which is what makes "plan under one id, then build under it"
        keep documenting the right thing.
        """
        if self.resuming and key in self._pins:
            return str(self._pins[key])
        value = produce()
        self._pins[key] = value
        self._pins_path.write_text(json.dumps(self._pins, indent=2))
        return value

    # ── agent map (adw_id -> per-agent coding-agent session ids) ────────────
    def save_agent_map(self, agent: str, entry: dict) -> None:
        self.agent_map[agent] = entry
        self._agent_map_path.write_text(json.dumps(self.agent_map, indent=2))

    # ── issue provenance (set by an issue phase, read by integration) ──────
    def adopt_provenance(self, trigger: str, issue_url: str, pr_url: str = "") -> None:
        """Take on what the session already recorded, without re-writing it.

        The counterpart to record_issue: that one is a run LEARNING it came from
        an issue, this one is a later process being TOLD. Nothing is written
        back, because nothing changed.

        `pr_url` is the same story told forwards: the first process opened the
        pull request, and every process after it has to know that the branch is
        under review — integration pushes to it instead of opening another one.
        """
        if trigger:
            self.trigger = trigger
        if issue_url:
            self.issue_url = issue_url
            tail = issue_url.rstrip("/").rsplit("/", 1)[-1]
            self.issue_number = int(tail) if tail.isdigit() else 0
        if pr_url:
            self.pr_url = pr_url

    def record_issue(self, context) -> None:
        """Bind this run to the work item that caused it, in memory and in the db.

        Lives here rather than in the ADW script (rule 6) because three
        different things need it afterwards: the trace column, the PR body
        template, and integration's refusal to merge an externally triggered
        run. A script that set them one by one would eventually set only two.
        """
        self.trigger = "issue"
        self.issue_number = context.number
        self.issue_url = context.url
        self.tracer.session_issue(self.adw_id, context.url)
        artifacts.update_run(self.session_dir, trigger="issue", issue_url=context.url)
        # `request` is otherwise only written by an ENGINEER phase (see
        # PhaseHandle.log), and an issue-triggered chain has none — so without
        # this every such run reads as blank in `just sessions` and on its card
        # in the UI. The title is what the request field is for: the one line
        # that says what this run was about.
        self.tracer.session_request(self.adw_id, f"#{context.number} {context.title}")

    def record_pull_request(self, context) -> None:
        """Bind this run to the pull request whose review feedback caused it.

        The counterpart to record_issue one step later, and it is CAREFUL WITH
        THE TRIGGER in a way that one does not have to be. A review run is
        almost always a re-entry into a session that already has provenance: an
        issue-triggered session whose pull request drew comments is still
        issue-triggered, and overwriting that would hand `integration` an
        "engineer" answer for the exact run that must never move a base branch.
        So the trigger is only claimed when nothing better is recorded.

        `pr_url` is written unconditionally, because it can only become MORE
        true: a session learns its pull request here or in `integration`, and
        both are the same url.
        """
        if self.trigger == "engineer":
            self.trigger = "pr_review"
            self.tracer.session_trigger(self.adw_id, self.trigger)
        self.pr_url = context.url or self.pr_url
        self.tracer.session_pr(self.adw_id, context.url)
        artifacts.update_run(self.session_dir, trigger=self.trigger, pr_url=context.url)

    # ── usage (run totals mirror what the tracer accumulates in sqlite) ─────
    def add_usage(self, tokens: int, cost: float) -> None:
        self.tokens += tokens
        self.cost += cost
        self.tracer.session_add_usage(self.adw_id, tokens, cost)
        # ...and into the session's own record, because the budget has to be
        # readable by the next process without the db. Absolute totals, not an
        # increment: this process knows what came before it, so nothing has to
        # read-modify-write a number two runs could race on.
        artifacts.update_run(self.session_dir,
                             total_tokens=self._prior_tokens + self.tokens,
                             total_cost=self._prior_cost + self.cost)

    def overrun(self) -> str:
        """Why this session may spend no more, or "" while it still may.

        Deliberately a question and not an enforcement point: adding usage must
        not raise, or a phase would die between paying for a turn and recording
        it. `agents.execute` asks this before each send — see limits.py on why
        a ceiling stops the next turn rather than the one in flight.
        """
        return limits.overrun(self._prior_tokens + self.tokens,
                              self._prior_cost + self.cost, self.cfg.budget)

    # ── the phase primitive ─────────────────────────────────────────────────
    @contextmanager
    def phase(self, params: PhaseParams):
        self._seq += 1
        phase = Phase(phase_id=f"{self.adw_id}_{self._seq:02d}_{params.name}",
                      adw_id=self.adw_id, seq=self._seq, params=params,
                      status="running", started_at=now_iso())
        self.phases.append(phase)
        self.tracer.phase_upsert(phase)
        self.tracer.event(EventRecord(adw_id=self.adw_id, phase_id=phase.phase_id,
                                      type="phase_start", name=params.name,
                                      payload={"kind": params.kind, "owner": params.owner,
                                               "description": params.description}))
        self.console.phase_started(phase)
        clock = time.monotonic()
        try:
            yield PhaseHandle(self, phase)
        except BaseException as error:
            phase.status = "fail"                      # success must be earned
            phase.error = str(error)[:1000]
            phase.ended_at = now_iso()
            self.tracer.event(EventRecord(adw_id=self.adw_id, phase_id=phase.phase_id,
                                          type="error", name=params.name,
                                          payload={"error": phase.error}))
            self.tracer.event(EventRecord(adw_id=self.adw_id, phase_id=phase.phase_id,
                                          type="phase_end", name=params.name,
                                          payload={"status": "fail"}))
            self.tracer.phase_upsert(phase)
            self.tracer.session_finish(self.adw_id, ok=False)
            artifacts.finish_run(self.session_dir, "fail")
            self.console.phase_ended(phase, time.monotonic() - clock)
            self.console.session_finished(False, self.tokens, self.cost,
                                          self.cfg.observability.db)
            raise
        else:
            phase.status = "success"
            phase.ended_at = now_iso()
            self.tracer.event(EventRecord(adw_id=self.adw_id, phase_id=phase.phase_id,
                                          type="phase_end", name=params.name,
                                          payload={"status": "success"}))
            self.tracer.phase_upsert(phase)
            self.console.phase_ended(phase, time.monotonic() - clock)

    # ── run outcome ─────────────────────────────────────────────────────────
    def finish(self, accepted: bool = True, reason: str = "") -> int:
        """Finalize the run and return its exit code. Call this exactly once.

        Two criteria, not one. Every phase must have passed, AND the ADW's own
        acceptance test must hold. They are different questions on purpose: a
        test phase that ran the suite did its job even when the suite came back
        red, so the PHASE succeeds while the RUN must not.

        This replaces a `succeeded` property that answered only the first
        question — and, being a property with side effects, wrote the session
        status and printed the banner before the caller's `and test.passed` was
        ever evaluated. A run whose suite never passed was recorded green in the
        db, on the terminal, and in the UI while exiting 1. Anyone reading the
        trace saw success; only a CI job checking `$?` saw the truth. One call
        now settles the db, the banner, and the exit code together, so the three
        cannot disagree.
        """
        phases_ok = bool(self.phases) and all(p.status == "success" for p in self.phases)
        ok = phases_ok and accepted
        if phases_ok and not accepted:
            note = reason or "the run's acceptance criterion was not met"
            self.tracer.event(EventRecord(
                adw_id=self.adw_id,
                phase_id=self.phases[-1].phase_id if self.phases else "",
                type="error", name="not_accepted", payload={"reason": note}))
            self.console.note(f"not accepted: {note}")
        self.tracer.session_finish(self.adw_id, ok=ok)
        # The session's own record says the same thing, and it is the one a
        # resume reads: `just resume` must not need the trace db to exist.
        artifacts.finish_run(self.session_dir, "success" if ok else "fail")
        # An accepted run's worktree is a redundant copy of a branch that is
        # kept, so it goes; a failed or killed one is the evidence, so it stays.
        # `release` also keeps anything with uncommitted work in it, whatever
        # the outcome — a plan-only chain never commits, and its plan lives
        # nowhere else.
        if ok and not self.cfg.worktree.keep_on_success:
            self.console.note(f"worktree: {worktree.release(self.workspace)}")
        elif self.workspace.enabled:
            self.console.note(f"worktree: kept {self.repo_root} on {self.workspace.branch}")
        self.console.session_finished(ok, self.tokens, self.cost, self.cfg.observability.db)
        return 0 if ok else 1
