# Phase 9 — Human-in-the-loop Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A chain can stop after a phase, hand its artifact to a human, and continue only on approval. A rejection with notes sends the artifact back to the agent that produced it, in the same coding-agent session, and asks the human again; an abort ends the run as not accepted. Whether a gate fires is policy — per gate in the config, per run on the command line — and a gate the policy trusts still leaves a decision in the record saying so.

**Architecture:** A gate is its own `kind="engineer"` phase (`approve_<gate>`) opened by one helper, `hitl.gated()`, that also owns the revise loop (`<gate>_revise_<n>`, the original `AgentCall` re-sent with `previous=decision`). The wait is a *suspend*: the process records what it is waiting for in `run.json`, exits 75, and `just approve <adw_id>` writes the decision and re-launches the workflow with `--resume`, so the existing replay carries the chain back to the gate. A terminal prompt is the attended convenience over that mechanism, and every channel writes the same file: `sessions/<adw_id>/decisions/<gate>_<round>.json`, carrying a digest of the artifact it decided on. Nothing reads the trace db.

**Tech Stack:** Python 3.11+ (`uv run` with inline script metadata), pydantic v2, git worktrees. Tests are pytest on the `fake` harness. The visualizer is Vue + TypeScript on Bun.

**Spec:** `docs/phase-9-hitl-gates.md` — the brainstorm, with the seven decisions recorded in its *Decisions* section. Read it before Task 1.

## The seven decisions this plan implements

| # | Question | Decision |
|---|---|---|
| 1 | Shape of the gate | **A** — its own engineer phase plus a chain-level revise loop, wrapped in `hitl.gated()`. Force mode **C** (`--hitl every`) adds approve/abort-only checkpoints after every agent phase. Shape B (a gate callable inside the agent phase) is not built. |
| 2 | How the process waits | **Suspend** is the mechanism (exit 75, resume via `--resume`). **Block** with a terminal prompt only while attended (stdin is a TTY), and the prompt detaches into a suspend on `d` or after `wait_seconds`. Both read the same decision file. |
| 3 | Policy key | **Gate name.** `hitl.gates: {plan: on}` and `--hitl plan,integrate`. No agent-level shorthand in this phase. |
| 4 | Session status | A fourth status **`waiting`** on `run.json`, `sessions.status` and the UI. The gate **phase** gets it too — a suspended phase closes as `waiting`, never `running` or `fail`. |
| 5 | Rounds | **Unbounded** by default (`max_rounds: 0`). The human bounds the loop by aborting; the budget bounds it by money. |
| 6 | Unattended runs | `when_unattended: suspend` — an issue- or PR-triggered run stops at an on-gate and waits. `auto` is available and records a policy approval. The forge channel (posting the subject to the issue) is **not** in this plan. |
| 7 | Writers | **CLI only** in v1: `just pending / approve / reject / abort`. The trace UI *shows* `waiting`; it gets no write path here. |

## Global Constraints

- **Repository:** all work happens in `software-factory`, under `skills/sssf/`. Nothing in this plan touches a stamped consumer repo.
- **`templates/adws/` is copied verbatim into consuming repos** by `install.py`. **Never put tests, fixtures or scratch files under `templates/`.** Tests live in `skills/sssf/tests/`, which is not stamped. Operational scripts (`hitl.py`, like `resume.py`) live in `skills/sssf/scripts/` and import the stamped modules from the repo they are run in.
- **The factory never reads the trace db.** `hitl.py` and `artifacts.py` read and write files under the session directory; `tracer.py` mirrors to sqlite. `test_no_db_reads.py` enforces this structurally — a `sqlite3` import anywhere but `tracer.py` fails the suite.
- **SKILL.md hard rule 4 (four-param rule):** `Gate`, `Subject`, `Decision`, `WaitingFor` and `HitlPolicy` exist for exactly this reason.
- **SKILL.md hard rule 6:** ADW scripts stay thin. A gate at a call site is one `hitl.gated(...)` call; the loop, the wait, the record and the policy all live in `adw_modules/hitl.py`.
- **SKILL.md hard rule 7:** every phase this plan opens has a real description. The helper composes them from the gate's own.
- **SKILL.md hard rule 2 (the synced triad)** is not triggered: no agent output type changes. `Decision` is an envelope an agent *receives* as `previous`, never one it emits.
- **No new runtime dependencies.** `pytest` comes from `uv run --with pytest` at test time only.
- **Documentation language is English**, matching the rest of the repository.
- **Test commands, from the repository root, used unchanged in every task:**

  ```bash
  uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests -q
  ruff check .
  ```

---

## File Structure

| File | Responsibility |
|---|---|
| `skills/sssf/templates/adws/adw_modules/hitl.py` | **New.** The gate: `gated()` (the loop), `decide()` (the wait), `digest()`, `record()`/`read_decision()`, `HitlPolicy`, `Suspended`, `Aborted`. The only module that knows a decision file exists. |
| `skills/sssf/templates/adws/adw_modules/data_types.py` | New models `Decision`, `Subject`, `Gate`, `WaitingFor`, `HitlConfig`; `waiting` joins `PhaseStatus`; `RunState.waiting_for`; `SSSFConfig.hitl`. |
| `skills/sssf/templates/adws/adw_modules/artifacts.py` | `suspend_run()`, `clear_waiting()`, `decisions_dir()`, `waiting_sessions()`. |
| `skills/sssf/templates/adws/adw_modules/runner.py` | `PhaseHandle.decide()`; `Run.phase()` handles `Suspended`; `Run.hitl` policy; the `every` checkpoint. |
| `skills/sssf/templates/adws/adw_modules/session.py` | `ensure(cfg, adw_id, resume, hitl="")` builds the policy. |
| `skills/sssf/templates/adws/adw_modules/tracer.py` | `session_waiting()`; `decision` event type. |
| `skills/sssf/templates/adws/adw_modules/console.py` | `waiting()` and `decided()` lines; the `waiting` glyph in `phase_ended`. |
| `skills/sssf/templates/adws/adw_*.py` | `--hitl` on every chain ADW; `hitl.gated()` at three call sites. |
| `skills/sssf/templates/harnesses/{pi,claude_code}/prompt_engineering/planner/user.md` | One paragraph on what a `Decision` in `previous_envelope` means. |
| `skills/sssf/templates/config/base.yaml` | The `hitl:` block. |
| `skills/sssf/templates/justfile` | `pending`, `approve`, `reject`, `abort` recipes; `SSSF_HITL` passthrough. |
| `skills/sssf/scripts/hitl.py` | **New.** The CLI: list what waits, show a subject, write a decision, relaunch. |
| `skills/sssf/scripts/resume.py` | `relaunch()` extracted for reuse; a `waiting` branch. |
| `skills/sssf/scripts/pr_watch.py` | The reaper aborts a waiting run whose pull request has landed. |
| `skills/sssf/scripts/up.py` | `status` lists runs waiting for a human. |
| `skills/sssf/apps/visualizer/{shared/types.ts, src/components/StatusChip.vue, PhaseDots.vue, SessionCard.vue}` | The fourth status, rendered. |
| `skills/sssf/references/config.md`, `SKILL.md`, `cookbooks/update_adw.md`, `cookbooks/run_adw.md` | Documented. |
| `docs/phase-9-hitl-gates.md`, `docs/README.md` | *As built* section; roadmap row. |
| `skills/sssf/tests/test_hitl.py`, `test_e2e_fake.py` | **New / extended, not stamped.** |

---

### Task 1: The record — types, digest, decision files

**Files:**
- Create: `skills/sssf/tests/test_hitl.py`
- Create: `skills/sssf/templates/adws/adw_modules/hitl.py`
- Modify: `skills/sssf/templates/adws/adw_modules/data_types.py` (`PhaseStatus` line 19; after `ReviewOutput` line ~141; `RunState` line ~729; `SSSFConfig` line ~647)
- Modify: `skills/sssf/templates/adws/adw_modules/artifacts.py` (after `update_run`, line ~135)

**Interfaces:**
- Produces:
  - `data_types.Decision(EnvelopeBase)`, `data_types.Subject`, `data_types.WaitingFor`, `data_types.HitlConfig`
  - `hitl.digest(paths: list[Path]) -> str`
  - `hitl.record(session_dir: Path, decision: Decision) -> Path`
  - `hitl.read_decision(session_dir: Path, gate: str, round: int) -> Decision | None`
  - `artifacts.decisions_dir(session_dir) -> Path`, `artifacts.suspend_run(session_dir, waiting: WaitingFor)`, `artifacts.clear_waiting(session_dir)`, `artifacts.waiting_sessions(sessions_dir) -> dict[str, WaitingFor]`

- [ ] **Step 1: Write the failing tests**

`skills/sssf/tests/test_hitl.py`:

```python
"""The decision record: what a human said, about exactly which artifact.

Files only. `hitl.py` never touches the db — `test_no_db_reads.py` keeps it so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adw_modules import artifacts, hitl
from adw_modules.data_types import Decision, RunState, WaitingFor


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "sessions" / "a1b2c3d4"
    directory.mkdir(parents=True)
    artifacts.write_run(directory, RunState(adw_id="a1b2c3d4", status="running", pid=0))
    return directory


def test_digest_covers_content_and_order_independent_of_listing(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("plan\n")
    b.write_text("diff\n")
    assert hitl.digest([a, b]) == hitl.digest([b, a])
    before = hitl.digest([a, b])
    a.write_text("plan, revised\n")
    assert hitl.digest([a, b]) != before


def test_digest_of_a_missing_file_is_stable_and_distinct(tmp_path):
    present = tmp_path / "a.md"
    present.write_text("x")
    missing = tmp_path / "gone.md"
    assert hitl.digest([present, missing]) == hitl.digest([present, missing])
    assert hitl.digest([present, missing]) != hitl.digest([present])


def test_a_decision_is_an_envelope_the_next_agent_can_read():
    decision = Decision(gate="plan", round=2, verdict="reject",
                        notes="split the migration", by="alice", channel="cli")
    assert decision.status == "success"           # the DECISION happened
    assert decision.approved is False
    assert "alice" in decision.summary and "reject" in decision.summary
    assert decision.notes_for_next_agent == "split the migration"


def test_record_and_read_round_trip(session_dir):
    decision = Decision(gate="plan", round=1, verdict="approve", by="alice",
                        channel="cli", subject_digest="abc")
    path = hitl.record(session_dir, decision)
    assert path == session_dir / "decisions" / "plan_1.json"
    assert hitl.read_decision(session_dir, "plan", 1) == decision
    assert hitl.read_decision(session_dir, "plan", 2) is None


def test_suspend_and_clear_write_run_json(session_dir):
    waiting = WaitingFor(gate="plan", round=1, phase_id="a1b2c3d4_03_approve_plan",
                         subject_digest="abc", paths=["specs/plan.md"],
                         summary="a plan", since="2026-09-10T00:00:00Z")
    artifacts.suspend_run(session_dir, waiting)
    state = artifacts.read_run(session_dir)
    assert state.status == "waiting"
    assert state.waiting_for == waiting
    assert state.ended_at == ""                    # waiting is not ended
    assert state.pid == 0                          # nothing of it is alive

    artifacts.clear_waiting(session_dir)
    state = artifacts.read_run(session_dir)
    assert state.waiting_for is None
    assert state.status == "waiting"               # status is the RUN's to change


def test_waiting_sessions_lists_only_what_waits(tmp_path, session_dir):
    other = tmp_path / "sessions" / "deadbeef"
    other.mkdir()
    artifacts.write_run(other, RunState(adw_id="deadbeef", status="success"))
    artifacts.suspend_run(session_dir, WaitingFor(gate="plan", round=1))
    assert list(artifacts.waiting_sessions(tmp_path / "sessions")) == ["a1b2c3d4"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests/test_hitl.py -q
```

Expected: collection error — `ImportError: cannot import name 'hitl'`.

- [ ] **Step 3: Add the types**

In `data_types.py`:

```python
PhaseStatus = Literal["queued", "running", "success", "fail", "waiting"]
```

After `ReviewOutput` (a human's verdict sits beside the reviewer's on purpose — the builder already knows how to revise from one):

```python
# ── Human-in-the-loop (adw_modules/hitl.py) ──────────────────────────────────

Verdict = Literal["approve", "reject", "abort"]


class Decision(EnvelopeBase):
    """What a human said at a gate, in the shape the next agent already reads.

    An ENVELOPE, so `previous=decision` hands a rejection to the agent that
    produced the artifact with no new plumbing: `notes_for_next_agent` is the
    human's notes, and `summary` says who decided what. `status` is always
    "success" — the decision happened; whether the WORK passed is `verdict`.

    `subject_digest` names exactly what was decided on: a hash of the artifact
    files at the moment the human was asked. A decision whose digest no longer
    matches the subject in front of the run is refused, so a stale
    `just approve` from an earlier round can never wave a changed plan through.
    """

    status: Literal["success", "fail"] = "success"
    gate: str
    round: int = 1
    verdict: Verdict
    notes: str = ""
    by: str = ""                    # engineer name, forge login, or "policy"
    channel: str = ""               # terminal | cli | auto
    subject_digest: str = ""
    decided_at: str = ""

    def model_post_init(self, _context) -> None:
        if not self.summary:
            who = self.by or "someone"
            self.summary = (f"{self.verdict} by {who}" +
                            (f": {self.notes}" if self.notes else ""))
        if not self.notes_for_next_agent:
            self.notes_for_next_agent = self.notes

    @property
    def approved(self) -> bool:
        return self.verdict == "approve"


class Subject(BaseModel):
    """What a gate shows the human, and what its digest is taken over."""

    gate: str
    round: int = 1
    summary: str = ""               # the envelope's own one-liner
    paths: list[str] = Field(default_factory=list)   # absolute, or relative to repo_root
    notes: str = ""                 # the producing agent's notes_for_next_agent


class Gate(BaseModel):
    """One human gate at a call site: what to show, and how to revise on reject.

    `call` is the agent call to repeat when the human rejects — the SAME output
    type and gates, with `previous=` replaced by the decision. None makes the
    gate approve/abort only, which is what a gate after a code phase is.
    """

    model_config = {"arbitrary_types_allowed": True}

    name: str                       # the id policy keys on: "plan", "integrate"
    owner: str = ""                 # the agent that revises; "" = nobody can
    call: Optional[AgentCall] = None
    retries: int = 1                # gate-correction retries on the revise phase
    description: str = ""           # for the approve phase; a default is composed
    paths: list[str] = Field(default_factory=list)   # subject; default = envelope.artifacts


class WaitingFor(BaseModel):
    """What a suspended session is waiting on — `run.json.waiting_for`."""

    gate: str
    round: int = 1
    phase_id: str = ""
    phase_name: str = ""
    since: str = ""
    subject_digest: str = ""
    paths: list[str] = Field(default_factory=list)
    summary: str = ""
    notes: str = ""
```

`Gate` references `AgentCall`, which is defined later in the file (line ~380). Either move this block below `AgentCall` or keep it here and add `Gate.model_rebuild()` after `AgentCall` — moving is cleaner; put the whole block directly after `class AgentCall`.

In `RunState`, after `pr_url`:

```python
    # Set while a gate waits on a human; cleared when the decision is consumed.
    # `status == "waiting"` says the PROCESS is gone; this says why, and what
    # `just approve` would be approving.
    waiting_for: Optional[WaitingFor] = None
```

Add `HitlConfig` beside `BudgetConfig` and wire it into `SSSFConfig`:

```python
class HitlConfig(BaseModel):
    """Which gates stop for a human, and what a stopped run does.

    Placement is the ADW's (`hitl.gated(...)` at a call site names a gate);
    this decides whether a placed gate FIRES. Most specific wins: the `--hitl`
    flag, then `SSSF_HITL`, then `gates` by name, then `default`.

    Off by default, so a stamped repository behaves exactly as it did.
    """

    default: bool = False                # off | on — YAML booleans; the words are accepted too
    gates: dict[str, bool] = Field(default_factory=dict)   # {"plan": on}
    wait_seconds: int = 900              # attended: prompt this long, then suspend
    max_rounds: int = 0                  # 0 = until the human approves or aborts
    # What an issue- or PR-triggered run does at an on-gate: `suspend` stops
    # and waits (there is no terminal to ask); `auto` records a policy approval
    # and continues. The safe default is the first.
    when_unattended: str = "suspend"     # suspend | auto
    # Run when a gate suspends, with the subject on stdin — a known command is
    # code. [] runs nothing.
    notify_command: list[str] = Field(default_factory=list)
```

```python
class SSSFConfig(BaseModel):
    ...
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    hitl: HitlConfig = Field(default_factory=HitlConfig)
```

- [ ] **Step 4: Add the record functions to `artifacts.py`**

After `update_run`:

```python
# ── run.json: waiting on a human ─────────────────────────────────────────────

DECISIONS_DIR = "decisions"


def decisions_dir(session_dir: Path) -> Path:
    return Path(session_dir) / DECISIONS_DIR


def suspend_run(session_dir: Path, waiting: WaitingFor) -> None:
    """Record that this session stopped for a human, and that nothing of it is alive.

    Not `finish_run`: `ended_at` stays empty, because a waiting run has not
    ended — it will be picked up by `just approve` and continue as the same
    session. The pid is cleared and the process rows closed for the same reason
    `finish_run` closes them: the process IS gone, and a watcher counting live
    runs must not count this one.
    """
    state = read_run(session_dir)
    if state is None:
        return
    state.status = "waiting"
    state.waiting_for = waiting
    state.pid = 0
    try:
        write_run(session_dir, state)
    except OSError:
        pass
    end_all_processes(session_dir)


def clear_waiting(session_dir: Path) -> None:
    """The decision was consumed; the session no longer waits on it."""
    state = read_run(session_dir)
    if state is None or state.waiting_for is None:
        return
    state.waiting_for = None
    try:
        write_run(session_dir, state)
    except OSError:
        pass


def waiting_sessions(sessions_dir: Path) -> dict[str, WaitingFor]:
    """{adw_id: what it waits for} for every session stopped at a gate."""
    return {adw_id: state.waiting_for for adw_id, state in scan(sessions_dir).items()
            if state.status == "waiting" and state.waiting_for is not None}
```

`start_run` must carry `waiting_for` forward the way it carries provenance, or a resumed process would lose the record before the gate reads it: add `state.waiting_for = state.waiting_for or previous.waiting_for` beside the provenance lines. And `session_start` in `session.ensure` writes `status="running"` on re-entry, which is right — the process is alive again; `waiting_for` says what it will ask.

Import `WaitingFor` at the top of `artifacts.py`.

- [ ] **Step 5: Write the record half of `hitl.py`**

Create `skills/sssf/templates/adws/adw_modules/hitl.py`. Its docstring is the module's argument; the rest of the module arrives in Tasks 2–4.

```python
"""Human-in-the-loop gates: stop after a phase, ask a person, continue or revise.

A gate is its own `kind="engineer"` phase — the lane that until now only ever
logged the request — plus a revise loop that is the reviewer loop in
`adw_build_review.py` with a person where the reviewer is. `gated()` owns both.
An ADW spends one call per gate and never sees the loop, the wait, or the file.

THE WAIT IS A SUSPEND. `decide()` looks for a decision this session already
recorded for the gate and round; finding none, it records what it is waiting
for in `run.json`, exits the process with status 75, and leaves the worktree
where it is. `just approve <adw_id>` writes the decision and re-launches the
same workflow with `--resume`: replay answers every recorded agent phase from
the record, the chain reaches the gate again, and this time the decision is
there. A terminal prompt is a convenience over that — while stdin is a TTY the
run asks in place and polls the same file, and `d` or `wait_seconds` turns the
block into the suspend it would have been anyway.

THE DECISION NAMES WHAT IT DECIDED. `subject_digest` hashes the artifact files
at the moment the human was asked; a decision whose digest does not match the
subject in front of the run now is refused and the run waits again. That is
the one rule everything here rests on, and it is what makes a decision file
safe to write from anywhere.

TRUST IS RECORDED. A gate the policy skips writes `verdict=approve,
by="policy", channel="auto"` to the same directory, so the record of a run
shows every gate it passed and who passed it.

Files only. The trace db mirrors the events; nothing here reads it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from . import artifacts
from .data_types import Decision

EXIT_WAITING = 75          # EX_TEMPFAIL: "try again later", which is exactly it


def digest(paths: list[Path]) -> str:
    """One hash over the subject's files, independent of listing order.

    A missing file hashes as its name plus a marker, so "the plan is gone" is a
    different subject from "the plan is here" and from "there was no plan".
    """
    hasher = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        hasher.update(str(path).encode())
        hasher.update(b"\0")
        try:
            hasher.update(path.read_bytes())
        except OSError:
            hasher.update(b"<missing>")
        hasher.update(b"\0")
    return hasher.hexdigest()


def decision_path(session_dir: Path, gate: str, round: int) -> Path:
    return artifacts.decisions_dir(session_dir) / f"{gate}_{round}.json"


def record(session_dir: Path, decision: Decision) -> Path:
    """Write one decision. Keyed by gate and round; a later write replaces."""
    path = decision_path(session_dir, decision.gate, decision.round)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(decision.model_dump_json(indent=2))
    return path


def read_decision(session_dir: Path, gate: str, round: int) -> Optional[Decision]:
    path = decision_path(session_dir, gate, round)
    if not path.is_file():
        return None
    try:
        return Decision(**json.loads(path.read_text()))
    except (ValueError, OSError):
        return None            # a half-written file is a missing answer, not a crash
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests -q
ruff check .
```

Expected: all PASS, including `test_no_db_reads.py` (nothing new imports sqlite3) and `test_data_types.py`.

- [ ] **Step 7: Commit**

```bash
git add skills/sssf/tests/test_hitl.py skills/sssf/templates/adws/adw_modules/hitl.py \
        skills/sssf/templates/adws/adw_modules/data_types.py \
        skills/sssf/templates/adws/adw_modules/artifacts.py
git commit -m "hitl: the decision record — types, digest, decision files, a waiting run"
```

---

### Task 2: Policy — which gates fire, from config, environment and flag

**Files:**
- Modify: `skills/sssf/tests/test_hitl.py`
- Modify: `skills/sssf/templates/adws/adw_modules/hitl.py`
- Modify: `skills/sssf/templates/adws/adw_modules/runner.py` (`Run.__init__`, line ~48)
- Modify: `skills/sssf/templates/adws/adw_modules/session.py` (`ensure`, line 65)
- Modify: `skills/sssf/templates/adws/adw_modules/data_types.py` (`RunSpec`, line ~768)

**Interfaces:**
- Produces:
  - `hitl.HitlPolicy(config: HitlConfig, override: str = "")` with `.mode(gate: str, trigger: str) -> Literal["off", "on", "auto"]`, `.every: bool`, `.summary() -> str`
  - `session.ensure(cfg, adw_id=None, resume=False, hitl="")`; `RunSpec.hitl: str = ""`; `Run.hitl: HitlPolicy`

- [ ] **Step 1: Write the failing tests**

Append to `test_hitl.py`:

```python
from adw_modules.data_types import HitlConfig
from adw_modules.hitl import HitlPolicy


def test_policy_default_off_means_every_gate_is_auto():
    policy = HitlPolicy(HitlConfig())
    assert policy.mode("plan", "engineer") == "auto"
    assert policy.every is False


def test_policy_gate_entries_win_over_default():
    policy = HitlPolicy(HitlConfig(default="off", gates={"plan": "on"}))
    assert policy.mode("plan", "engineer") == "on"
    assert policy.mode("build", "engineer") == "auto"
    policy = HitlPolicy(HitlConfig(default="on", gates={"build": "off"}))
    assert policy.mode("plan", "engineer") == "on"
    assert policy.mode("build", "engineer") == "auto"


@pytest.mark.parametrize("override,plan,build,every", [
    ("all", "on", "on", False),
    ("none", "auto", "auto", False),
    ("plan", "on", "auto", False),
    ("plan,build", "on", "on", False),
    ("every", "on", "on", True),
    ("", "on", "auto", False),           # no override: the config decides
])
def test_policy_override_forms(override, plan, build, every):
    policy = HitlPolicy(HitlConfig(gates={"plan": "on"}), override)
    assert policy.mode("plan", "engineer") == plan
    assert policy.mode("build", "engineer") == build
    assert policy.every is every


def test_policy_override_rejects_nonsense():
    with pytest.raises(ValueError, match="--hitl"):
        HitlPolicy(HitlConfig(), "sometimes")


def test_policy_unattended_runs_read_when_unattended():
    on = HitlConfig(default="on")
    assert HitlPolicy(on).mode("plan", "issue") == "on"          # suspend and wait
    assert HitlPolicy(HitlConfig(default="on", when_unattended="auto")).mode("plan", "issue") == "auto"
    # A flag is a person at a keyboard saying so, and it wins even for an issue run.
    assert HitlPolicy(HitlConfig(default="on", when_unattended="auto"), "all").mode("plan", "issue") == "on"
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: `ImportError: cannot import name 'HitlPolicy'`.

- [ ] **Step 3: Write the policy**

Append to `hitl.py`:

```python
OVERRIDES = ("all", "none", "every")


class HitlPolicy:
    """Whether a named gate fires, resolved most-specific-first.

    `override` is the `--hitl` flag (or `SSSF_HITL`): `all`, `none`, `every`,
    or a comma-separated list of gate names. It is a person saying so at the
    keyboard, and it wins over everything in the config — including
    `when_unattended`, because a flag on an issue run's argv was put there by
    the operator who launched the watcher.
    """

    def __init__(self, config: HitlConfig, override: str = ""):
        self.config = config
        self.override = (override or "").strip().lower()
        self.every = self.override == "every"
        self._named: set[str] = set()
        if self.override and self.override not in OVERRIDES:
            names = {part.strip() for part in self.override.split(",") if part.strip()}
            if not names or any(not part.replace("_", "").isalnum() for part in names):
                raise ValueError(f"--hitl {override!r}: expected all | none | every | "
                                 f"a comma-separated list of gate names")
            self._named = names

    def mode(self, gate: str, trigger: str) -> str:
        """`on` — stop and ask. `auto` — record a policy approval and go on."""
        if self.override in ("all", "every"):
            return "on"
        if self.override == "none":
            return "auto"
        if self._named:
            return "on" if gate in self._named else "auto"
        wanted = self.config.gates.get(gate, self.config.default) == "on"
        if not wanted:
            return "auto"
        if trigger != "engineer" and self.config.when_unattended == "auto":
            return "auto"
        return "on"

    def summary(self) -> str:
        source = f"--hitl {self.override}" if self.override else "config"
        on = sorted(g for g, v in self.config.gates.items() if v == "on")
        return (f"hitl: {source}" + (f" · every agent phase" if self.every else "") +
                (f" · gates on: {', '.join(on)}" if on and not self.override else "") +
                (f" · default {self.config.default}" if not self.override else ""))
```

Import `HitlConfig` from `.data_types`.

- [ ] **Step 4: Thread it into the run**

`data_types.RunSpec`:

```python
    resume: bool = False
    hitl: str = ""                  # the --hitl flag, or "" for the config's say
```

`runner.Run.__init__`, beside `self.replay`:

```python
        # Which gates stop for a human this run. Built once, asked at every
        # gate with the trigger the run knows THEN — an issue chain learns it is
        # issue-triggered in its first phase, after this constructor ran.
        self.hitl = hitl.HitlPolicy(self.cfg.hitl, spec.hitl or os.environ.get("SSSF_HITL", ""))
```

Import `os` and `hitl` in `runner.py`. `hitl.py` imports `artifacts` and `data_types` only, so there is no cycle.

`session.ensure`:

```python
def ensure(cfg: SSSFConfig, adw_id: str | None = None, resume: bool = False,
           hitl: str = "") -> Run:
    ...
    run = Run(RunSpec(cfg=cfg, adw_id=adw_id, engineer=engineer_name(),
                      workspace=workspace, resume=resume, hitl=hitl), tracer)
    ...
    if resume:
        run.console.note(run.replay.summary())
    if run.hitl.override or cfg.hitl.default == "on" or cfg.hitl.gates:
        run.console.note(run.hitl.summary())
```

A bad `--hitl` value raises `ValueError` inside `Run.__init__` — after the worktree exists. Validate earlier: in `ensure`, before `preflight.before_run`, call `hitl.HitlPolicy(cfg.hitl, hitl or os.environ.get("SSSF_HITL", ""))` inside a `try` and turn `ValueError` into `SystemExit(str(error))`. A refused run costs nothing (the same rule `preflight` states).

- [ ] **Step 5: Run the tests, lint, commit**

```bash
uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests -q && ruff check .
git add skills/sssf/tests/test_hitl.py skills/sssf/templates/adws/adw_modules/
git commit -m "hitl: policy — config, SSSF_HITL and --hitl decide which gates fire"
```

---

### Task 3: The wait — `decide()`, suspend, resume to the gate

**Files:**
- Modify: `skills/sssf/tests/test_e2e_fake.py`
- Modify: `skills/sssf/templates/adws/adw_modules/hitl.py`
- Modify: `skills/sssf/templates/adws/adw_modules/runner.py` (`PhaseHandle`, line 27; `Run.phase`, line 230)
- Modify: `skills/sssf/templates/adws/adw_modules/tracer.py` (`session_finish`, line 335)
- Modify: `skills/sssf/templates/adws/adw_modules/console.py` (`phase_ended`, line ~88)

**Interfaces:**
- Produces:
  - `hitl.Suspended(SystemExit)` — code 75, carries `waiting: WaitingFor`
  - `hitl.decide(run, phase, subject: Subject) -> Decision`
  - `PhaseHandle.decide(subject: Subject) -> Decision`
  - `tracer.session_waiting(adw_id, gate)`; `EventRecord.type == "decision"`
  - `console.waiting(waiting: WaitingFor, how: str)`, `console.decided(decision: Decision)`

- [ ] **Step 1: Write the failing tests**

Append to `test_e2e_fake.py`, after the resume tests:

```python
# ── human-in-the-loop: the wait ──────────────────────────────────────────────

from adw_modules import hitl                                               # noqa: E402
from adw_modules.data_types import Decision, Subject                       # noqa: E402


def approve_phase(run, envelope, gate="plan", round=1):
    name = f"approve_{gate}" if round == 1 else f"approve_{gate}_{round}"
    with run.phase(PhaseParams(name=name, kind="engineer", owner=run.engineer,
                               description="Hand the plan to the engineer and wait "
                                           "for a verdict")) as ph:
        return ph.decide(Subject(gate=gate, round=round, summary=envelope.summary,
                                 paths=envelope.artifacts))


def test_a_gate_with_no_decision_suspends_the_run_with_exit_75(factory, repo):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg, hitl="all")
    plan = plan_phase(run)

    with pytest.raises(SystemExit) as stop:
        approve_phase(run, plan)
    assert stop.value.code == hitl.EXIT_WAITING

    from adw_modules import artifacts
    state = artifacts.read_run(run.session_dir)
    assert state.status == "waiting"
    assert state.waiting_for.gate == "plan"
    assert state.waiting_for.round == 1
    assert state.waiting_for.paths == [str(Path(run.repo_root) / "specs/plan.md")]
    assert state.waiting_for.subject_digest == hitl.digest([Path(run.repo_root) / "specs/plan.md"])
    assert state.pid == 0
    # The phase closed as WAITING — neither running forever nor failed.
    assert run.phases[-1].status == "waiting"
    assert db_rows(repo, "select status from sessions") == [("waiting",)]
    assert db_rows(repo, "select status from phases where name='approve_plan'") == [("waiting",)]
    # Its worktree is kept: the uncommitted plan is the subject.
    assert Path(run.repo_root).exists()


def test_a_resumed_run_reaches_the_gate_and_reads_its_decision(factory, repo):
    cfg = factory(planner={"writes": ["specs/"], "strict": True, "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    first = session.ensure(cfg, hitl="all")
    plan = plan_phase(first)
    with pytest.raises(SystemExit):
        approve_phase(first, plan)

    from adw_modules import artifacts
    waiting = artifacts.read_run(first.session_dir).waiting_for
    hitl.record(first.session_dir, Decision(
        gate="plan", round=1, verdict="approve", notes="ship it", by="alice",
        channel="cli", subject_digest=waiting.subject_digest))

    second = session.ensure(cfg, adw_id=first.adw_id, resume=True, hitl="all")
    plan = plan_phase(second)                      # replayed: strict planner has one reply
    decision = approve_phase(second, plan)

    assert decision.approved and decision.by == "alice"
    assert artifacts.read_run(second.session_dir).waiting_for is None
    kinds = [e["type"] for e in events_of(second)]
    assert "decision" in kinds
    assert second.finish() == 0
    assert db_rows(repo, "select status from sessions") == [("success",)]


def test_a_decision_for_a_changed_subject_is_refused(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    first = session.ensure(cfg, hitl="all")
    plan = plan_phase(first)
    with pytest.raises(SystemExit):
        approve_phase(first, plan)
    hitl.record(first.session_dir, Decision(
        gate="plan", round=1, verdict="approve", by="alice", channel="cli",
        subject_digest="not-the-plan-that-was-shown"))

    second = session.ensure(cfg, adw_id=first.adw_id, resume=True, hitl="all")
    plan = plan_phase(second)
    with pytest.raises(SystemExit) as stop:             # waits again, says why
        approve_phase(second, plan)
    assert stop.value.code == hitl.EXIT_WAITING
    notes = [e["payload"].get("message", "") for e in events_of(second) if e["type"] == "log"]
    assert any("stale" in note for note in notes)


def test_an_attended_run_asks_in_place(factory):
    """The terminal path, with the prompt scripted instead of a TTY."""
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda subject: ("approve", "looks right")     # what a keypress would return
    plan = plan_phase(run)
    decision = approve_phase(run, plan)
    assert decision.approved and decision.channel == "terminal"
    assert decision.by == run.engineer
    assert hitl.read_decision(run.session_dir, "plan", 1) == decision


def test_an_attended_detach_suspends(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda subject: ("detach", "")
    plan = plan_phase(run)
    with pytest.raises(SystemExit) as stop:
        approve_phase(run, plan)
    assert stop.value.code == hitl.EXIT_WAITING
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: `AttributeError: 'PhaseHandle' object has no attribute 'decide'`.

- [ ] **Step 3: Write the wait**

Append to `hitl.py`:

```python
import os
import select
import subprocess
import sys
import time

from .data_types import EventRecord, Phase, Subject, WaitingFor
from .utils import now_iso

POLL_SECONDS = 2.0


class Suspended(SystemExit):
    """The run stopped for a human. Exit 75; the record says what it waits for.

    A SystemExit so an ADW's uncaught path is a clean exit with no traceback —
    a run that stopped on purpose must not look like one that crashed — and so
    `Run.phase` can tell it from every other exception and close the phase as
    `waiting` rather than `fail`.
    """

    def __init__(self, waiting: WaitingFor):
        super().__init__(EXIT_WAITING)
        self.waiting = waiting


def resolve_paths(run, paths: list[str]) -> list[Path]:
    """Subject paths as absolute files: absolute stay, relative are the worktree's."""
    return [Path(p) if Path(p).is_absolute() else Path(run.repo_root) / p for p in paths]


def how_to_answer(run, gate: str) -> str:
    return (f"just show {run.adw_id} · just approve {run.adw_id} [-m notes] · "
            f"just reject {run.adw_id} -m \"what to change\" · just abort {run.adw_id}")


def decide(run, phase: Phase, subject: Subject) -> Decision:
    """The decision for this gate and round — from the record, the terminal, or not yet.

    Order: a recorded decision whose digest matches wins, whoever wrote it. An
    attended run then asks in place, polling the record meanwhile so a
    `just approve` from another terminal is honoured too. Everything else
    suspends. The digest is taken ONCE, here, and every later comparison is
    against it — the human is answering about what they were shown.
    """
    paths = resolve_paths(run, subject.paths)
    fingerprint = digest(paths)
    waiting = WaitingFor(gate=subject.gate, round=subject.round, phase_id=phase.phase_id,
                         phase_name=phase.params.name, since=now_iso(),
                         subject_digest=fingerprint, paths=[str(p) for p in paths],
                         summary=subject.summary, notes=subject.notes)
    run.console.note(f"gate {subject.gate} round {subject.round}: {subject.summary}")
    for path in waiting.paths:
        run.console.note(f"subject: {path}")

    recorded = read_decision(run.session_dir, subject.gate, subject.round)
    if recorded is not None:
        if recorded.subject_digest == fingerprint:
            return _consume(run, phase, recorded)
        run.console.note(f"decision {subject.gate}_{subject.round} is stale — it approved "
                         f"a different {subject.gate}; asking again")

    # The blocked-and-polling path. `run.hitl.ask` is None without a TTY; a
    # test injects a scripted answerer the same way a keypress would answer.
    if run.hitl.ask is not None:
        artifacts.update_run(run.session_dir, waiting_for=waiting)   # `just pending` sees it
        answer = _attended(run, waiting)
        if answer is not None:
            return _consume(run, phase, answer)

    _notify(run, waiting)
    raise Suspended(waiting)


def _consume(run, phase: Phase, decision: Decision) -> Decision:
    """Record that a decision was taken, in the trace and by clearing the wait."""
    if not decision.decided_at:
        decision.decided_at = now_iso()
    record(run.session_dir, decision)
    artifacts.clear_waiting(run.session_dir)
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                 type="decision", name=decision.gate,
                                 payload=decision.model_dump()))
    run.console.decided(decision)
    return decision


def _attended(run, waiting: WaitingFor) -> Optional[Decision]:
    """Ask at the terminal, up to `wait_seconds`, polling the record between keypresses.

    `run.hitl.ask(subject) -> (verdict | "detach", notes)`; the default reads
    one line from stdin. None means suspend — the human detached, or the clock
    ran out, which is the same thing with nobody at the keyboard.
    """
    deadline = time.monotonic() + max(0, run.cfg.hitl.wait_seconds)
    run.console.note(f"waiting for you — [a]pprove / [r]eject / [x] abort / [d]etach "
                     f"(suspends; {how_to_answer(run, waiting.gate)})")
    while True:
        recorded = read_decision(run.session_dir, waiting.gate, waiting.round)
        if recorded is not None and recorded.subject_digest == waiting.subject_digest:
            return recorded
        verdict, notes = run.hitl.ask(waiting)
        if verdict == "detach":
            return None
        if verdict in ("approve", "reject", "abort"):
            return Decision(gate=waiting.gate, round=waiting.round, verdict=verdict,
                            notes=notes, by=run.engineer, channel="terminal",
                            subject_digest=waiting.subject_digest)
        if time.monotonic() > deadline:
            run.console.note("no answer within wait_seconds — suspending")
            return None


def read_keypress(waiting: WaitingFor) -> tuple[str, str]:
    """The real `ask`: one line from a TTY, or "" after POLL_SECONDS so the
    caller can look at the record again. Notes are asked for on a reject."""
    ready, _, _ = select.select([sys.stdin], [], [], POLL_SECONDS)
    if not ready:
        return "", ""
    key = sys.stdin.readline().strip().lower()[:1]
    if key == "a":
        return "approve", input("notes for the next agent (optional): ").strip()
    if key == "r":
        notes = ""
        while not notes:
            notes = input("what should change: ").strip()
        return "reject", notes
    if key == "x":
        return "abort", input("reason (optional): ").strip()
    if key == "d":
        return "detach", ""
    return "", ""


def attended() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _notify(run, waiting: WaitingFor) -> None:
    """Run `notify_command` with the subject on stdin. Never raises."""
    argv = run.cfg.hitl.notify_command
    if not argv:
        return
    env = {**os.environ, "SSSF_ADW_ID": run.adw_id, "SSSF_GATE": waiting.gate,
           "SSSF_ROUND": str(waiting.round)}
    try:
        subprocess.run(argv, input=waiting.model_dump_json(indent=2), text=True,
                       env=env, timeout=30, capture_output=True)
    except (OSError, subprocess.SubprocessError) as error:
        run.console.note(f"notify_command failed: {error}")
```

`HitlPolicy` gains one attribute in `__init__`: `self.ask = read_keypress if attended() else None`. It lives on the policy because that is the run-scoped object a test already reaches (`run.hitl.ask = ...`), and "is anyone at the keyboard" is a policy input.

`artifacts.update_run` skips falsy values and sets attributes by name; `waiting_for=waiting` is a truthy model, so it works unchanged.

- [ ] **Step 4: `PhaseHandle.decide` and the `waiting` close**

`runner.py`:

```python
    def decide(self, subject: Subject) -> Decision:
        if self.phase.params.kind != "engineer":
            raise RuntimeError("ph.decide() is only valid inside an engineer phase")
        return hitl.decide(self.run, self.phase, subject)
```

In `Run.phase`, before the generic `except BaseException` branch:

```python
        except hitl.Suspended as stop:
            # Not a failure. The process ends here on purpose, the session says
            # what it waits for, and `just approve` brings it back to THIS phase
            # by name. The worktree is kept — its uncommitted work is the subject.
            phase.status = "waiting"
            phase.ended_at = now_iso()
            self.tracer.event(EventRecord(adw_id=self.adw_id, phase_id=phase.phase_id,
                                          type="phase_end", name=params.name,
                                          payload={"status": "waiting",
                                                   "gate": stop.waiting.gate,
                                                   "round": stop.waiting.round}))
            self.tracer.phase_upsert(phase)
            self.tracer.session_waiting(self.adw_id, stop.waiting.gate)
            artifacts.suspend_run(self.session_dir, stop.waiting)
            self.console.phase_ended(phase, time.monotonic() - clock)
            self.console.waiting(stop.waiting, hitl.how_to_answer(self, stop.waiting.gate))
            raise
```

`tracer.py`:

```python
    def session_waiting(self, adw_id: str, gate: str) -> None:
        """The run stopped at a gate. Its process is gone; its session is not over."""
        self.conn.execute("UPDATE sessions SET status='waiting' WHERE adw_id=?", (adw_id,))
        self.processes_end_all(adw_id)
```

Add `"decision"` to the event-type comment in `tracer.py` and to `references/observability.md`'s event list (Task 8).

`console.py`:

```python
    def waiting(self, waiting, how: str) -> None:
        rows = [f" [dim]gate[/dim]     {escape(waiting.gate)} · round {waiting.round}",
                f" [dim]subject[/dim]  {escape(_clip(waiting.summary))}",
                *[f" [dim]file[/dim]     {escape(p)}" for p in waiting.paths],
                f" [dim]answer[/dim]   {escape(how)}"]
        panel = Panel(Text.from_markup("\n".join(rows)),
                      title="[bold]waiting for you[/bold]", border_style="cyan", expand=False)
        self._emit(escape(f"session {self.adw_id} waiting at gate {waiting.gate}"),
                   renderable=panel)

    def decided(self, decision) -> None:
        color = {"approve": "green", "reject": "yellow", "abort": "red"}[decision.verdict]
        self._emit(f"  [{color}]⚑ {escape(decision.verdict)}[/{color}] [dim]by "
                   f"{escape(decision.by)} · {escape(decision.channel)}[/dim]"
                   + (f"  {escape(_clip(decision.notes))}" if decision.notes else ""))
```

In `phase_ended`, a `waiting` phase prints `⏸` in cyan rather than `✗`: compute `mark = {"success": "[green]✓[/green]", "waiting": "[cyan]⏸[/cyan]"}.get(phase.status, "[red]✗[/red]")`.

`session_finished` is not called on suspend — the panel above is the closing line.

- [ ] **Step 5: Run the tests, lint, commit**

```bash
uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests -q && ruff check .
git add skills/sssf/tests/test_e2e_fake.py skills/sssf/templates/adws/adw_modules/
git commit -m "hitl: the wait — a gate suspends with exit 75, and a resumed run reads its decision"
```

---

### Task 4: The loop — `gated()`, revise in the same session, abort

**Files:**
- Modify: `skills/sssf/tests/test_e2e_fake.py`
- Modify: `skills/sssf/templates/adws/adw_modules/hitl.py`
- Modify: `skills/sssf/templates/harnesses/pi/prompt_engineering/planner/user.md`, `skills/sssf/templates/harnesses/claude_code/prompt_engineering/planner/user.md`

**Interfaces:**
- Produces:
  - `hitl.gated(run, gate: Gate, envelope: EnvelopeBase) -> EnvelopeBase`
  - `hitl.Aborted(SystemExit)`
  - Phase names: `approve_<gate>`, `approve_<gate>_<n>` (n ≥ 2), `<gate>_revise_<n>`

- [ ] **Step 1: Write the failing tests**

```python
# ── human-in-the-loop: the loop ──────────────────────────────────────────────

from adw_modules.data_types import Gate                                     # noqa: E402


def plan_gate(prompt="do the thing"):
    return Gate(name="plan", owner="planner",
                call=AgentCall(output_type=PlanOutput, prompt=prompt,
                               gates=[gates.artifacts_exist, gates.files_non_empty]))


def test_a_trusted_gate_records_a_policy_approval_and_opens_no_phase(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg)                          # config default: off
    plan = hitl.gated(run, plan_gate(), plan_phase(run))
    assert plan.artifacts == ["specs/plan.md"]
    assert [p.params.name for p in run.phases] == ["plan"]
    recorded = hitl.read_decision(run.session_dir, "plan", 1)
    assert recorded.by == "policy" and recorded.channel == "auto" and recorded.approved
    assert run.finish() == 0


def test_a_rejection_revises_in_the_same_session_and_asks_again(factory):
    """The planner has TWO scripted replies in ONE session: the plan, then the
    revision. A revise that started a fresh session would have no second reply."""
    cfg = factory(planner={"writes": ["specs/"], "strict": True, "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"], summary="v1")},
        {"writes": {"specs/plan.md": "# Plan, split migration\n"},
         "envelope": envelope(artifacts=["specs/plan.md"], summary="v2")}]})
    run = session.ensure(cfg, hitl="all")
    answers = iter([("reject", "split the migration"), ("approve", "")])
    run.hitl.ask = lambda waiting: next(answers)

    plan = hitl.gated(run, plan_gate(), plan_phase(run))

    assert plan.summary == "v2"
    assert [p.params.name for p in run.phases] == [
        "plan", "approve_plan", "plan_revise_1", "approve_plan_2"]
    assert all(p.status == "success" for p in run.phases)
    # The revise phase's prompt carried the human's words, via previous_envelope.
    sent = (run.session_dir / "planner" / "prompts" / "user.md").read_text()
    assert "split the migration" in sent and '"verdict": "reject"' in sent
    assert hitl.read_decision(run.session_dir, "plan", 2).approved
    assert run.finish() == 0


def test_approve_with_remarks_carries_them_to_the_next_agent(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"],
                              notes_for_next_agent="use the existing helper")}]})
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("approve", "keep the migration reversible")
    plan = hitl.gated(run, plan_gate(), plan_phase(run))
    assert "use the existing helper" in plan.notes_for_next_agent
    assert "keep the migration reversible" in plan.notes_for_next_agent


def test_an_abort_ends_the_run_as_not_accepted(factory, repo):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("abort", "wrong feature")
    with pytest.raises(SystemExit) as stop:
        hitl.gated(run, plan_gate(), plan_phase(run))
    assert stop.value.code != 0 and stop.value.code != hitl.EXIT_WAITING
    assert run.phases[-1].params.name == "approve_plan"
    assert run.phases[-1].status == "fail"
    assert "wrong feature" in run.phases[-1].error
    assert db_rows(repo, "select status from sessions") == [("fail",)]
    assert Path(run.repo_root).exists()                # a failed run keeps its tree


def test_a_gate_without_a_revise_target_cannot_be_rejected(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg, hitl="all")
    run.hitl.ask = lambda waiting: ("reject", "no")
    with pytest.raises(SystemExit):
        hitl.gated(run, Gate(name="integrate"), plan_phase(run))
    assert "approve/abort" in run.phases[-1].error


def test_max_rounds_bounds_the_loop_when_set(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]},
        hitl={"default": "on", "max_rounds": 1})
    run = session.ensure(cfg)
    run.hitl.ask = lambda waiting: ("reject", "again")
    with pytest.raises(SystemExit):
        hitl.gated(run, plan_gate(), plan_phase(run))
    assert "max_rounds" in run.phases[-1].error
```

The `factory` fixture's `roster()` needs to accept a top-level `hitl` mapping: add `hitl = agent_scripts.pop("hitl", None)` and include it in the returned dict when given.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: `AttributeError: module 'adw_modules.hitl' has no attribute 'gated'`.

- [ ] **Step 3: Write the loop**

Append to `hitl.py`:

```python
from .data_types import EnvelopeBase, Gate, PhaseParams

REVISE_PROMPT = (
    "{prompt}\n\n"
    "The engineer reviewed what you produced and asked for changes — their notes are "
    "`notes_for_next_agent` in previous_envelope, a Decision with verdict \"reject\". "
    "Revise your existing work along those notes in this same session: update the "
    "artifacts you already wrote rather than starting over, keep the same paths, and "
    "report the same Report JSON shape as before."
)


class Aborted(SystemExit):
    """The human ended the run at a gate. Exit 1, with the reason as the message."""


def gated(run, gate: Gate, envelope: EnvelopeBase) -> EnvelopeBase:
    """Stop at a gate, or pass it by policy; loop on reject; return what was approved.

    Phases: `approve_<gate>` (round 1), `approve_<gate>_<n>` (later rounds),
    `<gate>_revise_<n>` between them. Every one replays on `--resume` by its
    name, so a suspended run re-enters the exact round it left.
    """
    mode = run.hitl.mode(gate.name, run.trigger)
    if mode == "auto":
        return _pass_by_policy(run, gate, envelope)

    round = 1
    while True:
        paths = gate.paths or envelope.artifacts
        name = f"approve_{gate.name}" if round == 1 else f"approve_{gate.name}_{round}"
        description = gate.description or (
            f"Hand the {gate.name} to the engineer and wait for a verdict")
        with run.phase(PhaseParams(name=name, kind="engineer", owner=run.engineer,
                                   description=description)) as ph:
            decision = ph.decide(Subject(gate=gate.name, round=round,
                                         summary=envelope.summary, paths=paths,
                                         notes=envelope.notes_for_next_agent))
            if decision.verdict == "abort":
                raise Aborted(f"aborted by {decision.by} at gate {gate.name}"
                              + (f": {decision.notes}" if decision.notes else ""))
            if decision.verdict == "reject" and gate.call is None:
                raise Aborted(f"gate {gate.name} is approve/abort only — nothing can "
                              f"revise it; rejected by {decision.by}: {decision.notes}")
            if decision.verdict == "reject" and run.cfg.hitl.max_rounds \
                    and round >= run.cfg.hitl.max_rounds:
                raise Aborted(f"gate {gate.name} rejected {round} time(s) — "
                              f"hitl.max_rounds reached")
        if decision.approved:
            if decision.notes:
                envelope.notes_for_next_agent = (
                    f"{envelope.notes_for_next_agent}\n\n"
                    f"Engineer's remarks at the {gate.name} gate: {decision.notes}").strip()
            return envelope

        with run.phase(PhaseParams(name=f"{gate.name}_revise_{round}", kind="agent",
                                   owner=gate.owner, retries=gate.retries,
                                   description=f"Rework the {gate.name} along the "
                                               f"engineer's notes, in the same session")) as ph:
            envelope = ph.call(gate.call.model_copy(update={
                "previous": decision,
                "prompt": REVISE_PROMPT.format(prompt=gate.call.prompt)}))
        round += 1


def _pass_by_policy(run, gate: Gate, envelope: EnvelopeBase) -> EnvelopeBase:
    """Trust, written down: the gate was passed, and the record says by whom."""
    paths = resolve_paths(run, gate.paths or envelope.artifacts)
    decision = Decision(gate=gate.name, round=1, verdict="approve", by="policy",
                        channel="auto", subject_digest=digest(paths), decided_at=now_iso())
    record(run.session_dir, decision)
    run.tracer.event(EventRecord(adw_id=run.adw_id,
                                 phase_id=run.phases[-1].phase_id if run.phases else "",
                                 type="decision", name=gate.name,
                                 payload=decision.model_dump()))
    run.console.note(f"gate {gate.name}: passed by policy (hitl off for it)")
    return envelope
```

Both `Aborted` raises happen INSIDE the approve phase on purpose: `Run.phase` closes the phase as `fail` with the reason, finalizes the session as `fail`, keeps the worktree, and prints the banner — exactly what a `GateFailure` gets. Raising after the phase closed would leave the session reading `running` with no process behind it, which is the bug `_finalize_when_killed` exists to prevent.

`AgentCall.model_copy(update=...)` works because `AgentCall` is a pydantic model; `gates` is copied by reference, which is what we want.

- [ ] **Step 4: Tell the planner what a rejection is**

In both harnesses' `planner/user.md`, after the `### previous_envelope` variable and before `## Task`:

```markdown
If `previous_envelope` is a `Decision` with `"verdict": "reject"`, an engineer has
read your plan and asked for changes: `notes_for_next_agent` is what to change.
Revise `plan.md` in place and refresh the copy under `specs/` you already wrote
(same path — this is the same plan, corrected, not a new one), then report both
paths again.
```

The `specs/` naming rule ("never overwrite an existing spec") is about a *session that plans twice*; a revise round is one plan corrected, and the sentence above says so. `PlanOutput` is unchanged, so rule 2's triad is untouched.

- [ ] **Step 5: Run the tests, lint, commit**

```bash
uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests -q && ruff check .
git add skills/sssf/tests/test_e2e_fake.py skills/sssf/templates/adws/adw_modules/hitl.py \
        skills/sssf/templates/harnesses/*/prompt_engineering/planner/user.md
git commit -m "hitl: gated() — approve continues, reject revises in the same session, abort ends the run"
```

---

### Task 5: Force mode — `--hitl every` checkpoints after each agent phase

**Files:**
- Modify: `skills/sssf/tests/test_e2e_fake.py`
- Modify: `skills/sssf/templates/adws/adw_modules/runner.py` (`PhaseHandle.call`, `Run.phase`)

**Interfaces:**
- `Run.checkpoint(phase: Phase, envelope: EnvelopeBase)` — opens `approve_<phase name>` with `Gate(name=phase.params.name)` (no revise target).

- [ ] **Step 1: Write the failing test**

```python
def test_hitl_every_checkpoints_after_each_agent_phase(factory):
    cfg = factory(planner={"writes": ["specs/"], "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]},
        builder={"replies": [{"envelope": envelope()}]})
    run = session.ensure(cfg, hitl="every")
    asked = []
    run.hitl.ask = lambda waiting: asked.append(waiting.gate) or ("approve", "")

    plan_phase(run)
    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement what the plan asked for")) as ph:
        ph.call(AgentCall(output_type=BuildOutput, prompt="build", previous=None))

    assert asked == ["plan", "build"]
    assert [p.params.name for p in run.phases] == ["plan", "approve_plan", "build", "approve_build"]
    assert run.finish() == 0


def test_hitl_every_does_not_checkpoint_a_revise_or_an_engineer_phase(factory):
    cfg = factory(planner={"writes": ["specs/"], "strict": True, "replies": [
        {"writes": {"specs/plan.md": "# Plan\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])},
        {"writes": {"specs/plan.md": "# Plan 2\n"},
         "envelope": envelope(artifacts=["specs/plan.md"])}]})
    run = session.ensure(cfg, hitl="every")
    answers = iter([("reject", "again"), ("approve", "")])
    run.hitl.ask = lambda waiting: next(answers)
    hitl.gated(run, plan_gate(), plan_phase(run))
    # `plan` was checkpointed by `every`; gated() then saw the round-1 decision
    # already recorded (same gate, same digest) and used it — one ask, not two.
    assert [p.params.name for p in run.phases] == [
        "plan", "approve_plan", "plan_revise_1", "approve_plan_2"]
```

The second test pins a subtlety: with `every`, the checkpoint after `plan` and the gate `gated()` opens are the SAME gate (`name == phase name`), and the checkpoint's decision is read back by `gated()`'s round 1 through the digest — so a `reject` at the checkpoint reaches the revise loop instead of aborting. That is the reason a checkpoint is keyed by the phase name and not by `checkpoint_<name>`.

- [ ] **Step 2: Implement**

`PhaseHandle.call` keeps the envelope: `self.envelope = agents.execute(...)` then returns it. `Run.phase`'s `else:` branch, after `phase_upsert` and the console line:

```python
            if (self.hitl.every and params.kind == "agent"
                    and "_revise_" not in params.name
                    and handle.envelope is not None):
                self._checkpoint(phase, handle.envelope)
```

A revise phase is skipped because the gate that follows it is the one `gated()` opens itself. `handle` is the `PhaseHandle` the `with` yielded — bind it (`handle = PhaseHandle(self, phase)`) before the `yield` so the `else:` branch can read it.

```python
    def _checkpoint(self, phase: Phase, envelope: EnvelopeBase) -> None:
        """`--hitl every`: an approve/abort gate after this agent phase, named for it."""
        hitl.gated(self, Gate(name=phase.params.name,
                              description=f"Checkpoint after {phase.params.name}: the "
                                          f"engineer asked to see every agent's work"),
                   envelope)
```

`gated()` with `mode == "on"` (every forces it) and `call is None`: approve continues, reject or abort raises `Aborted` inside the approve phase — the message says the gate is approve/abort only. For the checkpoint, make the reject message clearer: `_checkpoint` passes a `Gate` whose `owner` is the phase's owner but `call=None`; the `Aborted` text already says "nothing can revise it". Good enough; the doc says checkpoints cannot revise.

A checkpoint that suspends raises `Suspended` from inside `_checkpoint`, which is inside the outer phase's `else:` branch — after the outer phase was already closed as `success` and upserted. `Suspended` propagates out of the outer `with` as a `SystemExit`; the ADW exits 75. Nothing re-marks the outer phase: it did succeed. Verify with the first test by making `ask` return `("detach", "")` and asserting phase `plan` is `success` and `approve_plan` is `waiting`.

- [ ] **Step 3: Run the tests, lint, commit**

```bash
git commit -am "hitl: --hitl every — an approve/abort checkpoint after every agent phase"
```

---

### Task 6: The CLI — `pending`, `show`, `approve`, `reject`, `abort`; resume, status and the reaper learn `waiting`

**Files:**
- Create: `skills/sssf/scripts/hitl.py`
- Modify: `skills/sssf/scripts/resume.py` (extract `relaunch()`; a `waiting` branch at line ~110)
- Modify: `skills/sssf/scripts/up.py` (`status`, line ~433)
- Modify: `skills/sssf/scripts/pr_watch.py` (the reaper, line ~268)
- Modify: `skills/sssf/templates/justfile`
- Modify: `skills/sssf/tests/test_hitl.py`

**Interfaces:**
- `resume.relaunch(adw_id, config, dry_run=False, passthrough=()) -> int` — everything `resume.main` did after parsing.
- `scripts/hitl.py`: `pending`, `show <adw_id>`, `approve <adw_id> [-m NOTES] [--no-resume]`, `reject <adw_id> -m NOTES [--no-resume]`, `abort <adw_id> [-m NOTES] [--no-resume]`, all `--config`.
- justfile: `pending`, `show`, `approve`, `reject`, `abort`.

- [ ] **Step 1: Write the failing tests**

The script is thin over two module functions that Task 1 and 3 already test, plus one it adds to `hitl.py`: `answer(session_dir, verdict, notes, by) -> Decision`, which reads `waiting_for` from `run.json`, refuses when nothing waits, and records a decision carrying the digest the run asked about.

```python
def test_answer_copies_the_digest_the_run_asked_about(session_dir):
    artifacts.suspend_run(session_dir, WaitingFor(gate="plan", round=2, subject_digest="abc"))
    decision = hitl.answer(session_dir, "reject", "split it", by="alice")
    assert decision.subject_digest == "abc" and decision.round == 2
    assert decision.channel == "cli"
    assert hitl.read_decision(session_dir, "plan", 2) == decision


def test_answer_refuses_a_session_that_is_not_waiting(session_dir):
    with pytest.raises(RuntimeError, match="not waiting"):
        hitl.answer(session_dir, "approve", "", by="alice")


def test_answer_requires_notes_on_a_reject(session_dir):
    artifacts.suspend_run(session_dir, WaitingFor(gate="plan", round=1, subject_digest="abc"))
    with pytest.raises(RuntimeError, match="notes"):
        hitl.answer(session_dir, "reject", "", by="alice")
```

- [ ] **Step 2: Implement `answer()` in `hitl.py`**

```python
def answer(session_dir: Path, verdict: str, notes: str, by: str) -> Decision:
    """A decision from OUTSIDE the run — the CLI. Takes the digest from the wait record.

    A blocked run (attended, still polling) has `waiting_for` set with
    `status == "running"`; a suspended one has it with `status == "waiting"`.
    Both are answered the same way, and the run — polling or resumed — picks
    the file up.
    """
    state = artifacts.read_run(session_dir)
    if state is None or state.waiting_for is None:
        raise RuntimeError(f"{session_dir.name} is not waiting at a gate — "
                           f"`just pending` lists the sessions that are")
    if verdict == "reject" and not notes.strip():
        raise RuntimeError("a reject needs notes — they are what the agent revises from")
    waiting = state.waiting_for
    decision = Decision(gate=waiting.gate, round=waiting.round, verdict=verdict,
                        notes=notes.strip(), by=by, channel="cli",
                        subject_digest=waiting.subject_digest, decided_at=now_iso())
    record(session_dir, decision)
    return decision
```

- [ ] **Step 3: Extract `relaunch()` from `resume.py`**

Move the body of `resume.main` after argument parsing into:

```python
def relaunch(adw_id: str, config: str, dry_run: bool = False,
             passthrough: tuple[str, ...] = ()) -> int:
```

with `main()` calling it. Add, beside the `still running` check:

```python
    if state.status == "waiting" and state.waiting_for is not None:
        w = state.waiting_for
        if hitl.read_decision(session_dir, w.gate, w.round) is None:
            print(f"{adw_id}: waiting at gate {w.gate} (round {w.round}) with no decision "
                  f"recorded — `just approve {adw_id}` or `just reject {adw_id} -m ...` "
                  f"first; resuming now would stop at the same gate")
            return 1
```

Because `_rebuild` keeps every argv token it does not strip, a `--hitl` from the original invocation survives into the relaunch.

- [ ] **Step 4: Write `scripts/hitl.py`**

Same shape as `resume.py` — `sys.path.insert(0, "adws")`, `CONFIG`, argparse subcommands. `pending` prints one line per waiting session from `artifacts.waiting_sessions()`: adw_id, gate, round, since, summary. `show` prints the subject: summary, notes, and each file's contents (cap at 400 lines, say where it was cut). `approve`/`reject`/`abort` call `hitl.answer(session_dir, verdict, notes, by=engineer_name())`, print the decision path, then — unless `--no-resume`, and only when the session's `status == "waiting"` (a blocked run is polling and needs nothing) — `relaunch(adw_id, config)` and return its code. Print `exit 75` runs as `waiting again at gate …` by reading `run.json` afterwards.

Import `relaunch` from `resume.py` by path: both scripts live in the same directory, so `sys.path.insert(0, str(Path(__file__).parent))` then `from resume import relaunch`.

- [ ] **Step 5: `up.py status` and the reaper**

`up.py` after `runs in flight`:

```python
    waiting = artifacts.waiting_sessions(sessions)
    print(f"waiting for a human: {len(waiting)}" + ("   (`just pending`)" if waiting else ""))
    for adw_id, w in sorted(waiting.items()):
        print(f"  {adw_id}  gate {w.gate} round {w.round}  since {_age(w.since)}")
```

`pr_watch.py` reaper: a session whose pull request has landed and whose `run.json` says `waiting` has nobody to answer it any more. Before `_release`:

```python
        state = artifacts.read_run(Path(sessions) / adw_id)
        if state is not None and state.status == "waiting" and state.waiting_for:
            hitl.record(Path(sessions) / adw_id, Decision(
                gate=state.waiting_for.gate, round=state.waiting_for.round,
                verdict="abort", notes=f"#{number} {context.state.lower()} while waiting",
                by="policy", channel="auto", subject_digest=state.waiting_for.subject_digest))
            artifacts.finish_run(Path(sessions) / adw_id, "fail")
            print(f"    was waiting at gate {state.waiting_for.gate} — aborted, recorded")
```

`worktree.ENDED` stays `{"success", "fail"}` so `reclaimable()` refuses a waiting run's tree and `just worktrees` labels it `waiting`; no change needed there beyond a docstring line. `issue_watch._running_count` and `pr_watch._running_count` read `artifacts.running_pids()`, which filters on `status == "running"`; a suspended run is not counted, which is right. `kill_run.py` on a waiting session finds no live process and says so; add one line to its "nothing to kill" message: `if state.status == "waiting": print("… it is waiting at a gate, not running — `just abort <id>` ends it")`.

- [ ] **Step 6: justfile**

Under a new `# ── answer a gate ──` section, after `resume`:

```make
# runs stopped at a gate, waiting for you: just pending
pending:
    @uv run {{skill}}/scripts/hitl.py pending --config {{config}}

# what a waiting run wants you to look at: just show <adw_id>
show ADW_ID:
    @uv run {{skill}}/scripts/hitl.py show {{ADW_ID}} --config {{config}}

# approve the artifact and continue the run: just approve <adw_id> [-m "remarks"]
approve ADW_ID *ARGS:
    uv run {{skill}}/scripts/hitl.py approve {{ADW_ID}} --config {{config}} {{ARGS}}

# send it back with notes; the agent revises, then asks again: just reject <adw_id> -m "..."
reject ADW_ID *ARGS:
    uv run {{skill}}/scripts/hitl.py reject {{ADW_ID}} --config {{config}} {{ARGS}}

# end the run at the gate, not accepted: just abort <adw_id> [-m "why"]
abort ADW_ID *ARGS:
    uv run {{skill}}/scripts/hitl.py abort {{ADW_ID}} --config {{config}} {{ARGS}}
```

And a comment beside `config :=` that `SSSF_HITL=all just sdlc "..."` turns every gate on for one run, the way `SSSF_CONFIG` swaps the roster (the env var is read by `session.ensure`, so no recipe changes).

- [ ] **Step 7: Run the tests, lint, and try the script by hand on the fixture**

```bash
uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests -q && ruff check .
```

Then, in a scratch repo stamped with `install.py` and a fake-harness roster: `SSSF_HITL=all just plan-build "x"` should exit 75 with the *waiting for you* panel; `just pending` lists it; `just reject <id> -m "…"` relaunches, the planner revises, the run stops again; `just approve <id>` finishes it. Record the transcript in the commit message.

- [ ] **Step 8: Commit**

```bash
git add skills/sssf/scripts skills/sssf/templates/justfile skills/sssf/templates/adws/adw_modules/hitl.py skills/sssf/tests/test_hitl.py
git commit -m "hitl: just pending / show / approve / reject / abort, and resume, status, the reaper learn 'waiting'"
```

---

### Task 7: Gates in the chains, and `--hitl` on every chain ADW

**Files:**
- Modify: `skills/sssf/templates/adws/adw_plan_build.py`, `adw_plan_build_test.py`, `adw_plan_build_test_quality.py`, `adw_simple_sdlc.py` (gates)
- Modify: `adw_build_review.py`, `adw_build_test.py`, `adw_issue_sdlc.py`, `adw_pr_review.py` (flag only)
- Modify: `skills/sssf/tests/test_e2e_fake.py` (one chain-shaped test)

- [ ] **Step 1: The flag, on every chain that takes `--resume`**

In each `if __name__ == "__main__":` block:

```python
    parser.add_argument("--hitl", default="",
                        help="which gates stop for you: all | none | every | "
                             "gate,names — over the config's hitl: block and SSSF_HITL")
```

and pass it: `main(..., args.resume, args.hitl)` with `main(prompt, config=..., adw_id=None, resume=False, hitl="")` calling `session.ensure(cfg, adw_id, resume, hitl)`.

- [ ] **Step 2: The plan gate**

`adw_plan_build.py`, `adw_plan_build_test.py`, `adw_plan_build_test_quality.py`, `adw_simple_sdlc.py` — replace the plan phase's assignment with the phase plus the gate:

```python
    plan_call = AgentCall(output_type=PlanOutput, prompt=prompt,
                          gates=[gates.artifacts_exist, gates.files_non_empty])
    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                               description="Turn the request into an implementable plan")) as ph:
        plan = ph.call(plan_call)
    # A human may stop here. Off unless `hitl:` or --hitl says otherwise; on
    # reject the planner reworks its plan in the same session and asks again.
    plan = hitl.gated(run, Gate(name="plan", owner="planner", call=plan_call), plan)
```

In `adw_simple_sdlc.py` the gate sits BEFORE `commit_plan`, so the plan that is committed is the approved one.

- [ ] **Step 3: The integrate gate**

`adw_simple_sdlc.py`, immediately before the `integrate` phase, inside `if verified:`:

```python
        # The last thing a human may want to see before a machine moves a
        # branch. Approve/abort only — there is nothing to revise here.
        hitl.gated(run, Gate(name="integrate",
                             description="Show the engineer what is about to land, and wait",
                             paths=[changeset.diff_path]),
                   document)
```

The subject is the run's diff (`changeset.diff_path`, already written by the `changes` phase), so the digest is over what would land. `document` is passed as the envelope for its summary; on approve-with-remarks the remarks land in `document.notes_for_next_agent`, which nothing downstream reads — acceptable, and the decision file has them.

- [ ] **Step 4: Update each chain's `Phases:` docstring line**

e.g. `adw_plan_build.py`: `Phases: engineer(request) -> planner [-> engineer(approve_plan) -> planner(revise) ... until approved] -> builder -> git(commit)`. SKILL.md's startup step reads exactly these lines.

- [ ] **Step 5: A chain-shaped test**

Run `adw_plan_build.main()` itself on the fake harness — `test_e2e_fake.py` already imports chain modules? It does not; add one test that imports `adw_plan_build` (it is on `sys.path` via `conftest`) and calls `main("x", config=CONFIG_PATH, hitl="all")` with `run.hitl.ask` unreachable — so instead pre-seed the decision by running once (expect `SystemExit(75)`), answering with `hitl.answer(...)`, then calling `main(..., adw_id=..., resume=True, hitl="all")` and asserting it returns 0 and the plan commit exists. This is the whole slice, end to end, in one test.

- [ ] **Step 6: Run the tests, lint, commit**

```bash
git commit -am "adws: a plan gate in the four planning chains, an integrate gate in simple_sdlc, --hitl on every chain"
```

---

### Task 8: Config template and documentation

**Files:**
- Modify: `skills/sssf/templates/config/base.yaml` (after `budget:`)
- Modify: `skills/sssf/references/config.md` (a `### hitl` section after `### budget`; a `## Human-in-the-loop gates` section after `## Limits`)
- Modify: `skills/sssf/references/observability.md` (the `decision` event; `waiting` in the status vocabulary)
- Modify: `skills/sssf/references/handoff.md` (`Decision` beside `ReviewOutput`; `decisions/` in the session directory layout)
- Modify: `skills/sssf/SKILL.md` (one bullet under *Where a run's work lands*; one routing row: "stop a run for review / approve a plan" → the config reference section)
- Modify: `skills/sssf/cookbooks/update_adw.md` (`## Add a human gate`), `cookbooks/run_adw.md` (`## When a run is waiting for you`), `cookbooks/create_config.md` (one line on `hitl.default`)
- Modify: `docs/phase-9-hitl-gates.md` (*As built* section), `docs/README.md` (row 9 → **built**)

- [ ] **Step 1: `base.yaml`**

```yaml
# Where a human may stop a run. A chain names its gates (`hitl.gated(...)` at a
# call site — `plan` in every planning chain, `integrate` in simple_sdlc); this
# says which of them FIRE. Off by default: a stamped repository behaves exactly
# as it did. `--hitl all|none|every|plan,integrate` on any chain, or SSSF_HITL,
# overrides all of it for one run.
#
# A gate that fires stops the run: the process exits 75, `just pending` lists
# it, `just show <id>` prints what it wants you to read, and `just approve`,
# `just reject -m "..."` or `just abort` answer it. A reject goes back to the
# agent that produced the artifact, in the same session, and the run asks
# again. A gate the policy skips still writes a decision saying so.
hitl:
  default: off                     # off | on — for gates not named below
  gates: {}                        # by gate name: {plan: on, integrate: on}
  wait_seconds: 900                # attended: prompt this long, then suspend
  max_rounds: 0                    # 0 = until you approve or abort
  when_unattended: suspend         # issue/PR-triggered runs: suspend | auto
  notify_command: []               # run on suspend, subject on stdin; e.g. ["ntfy", "publish", "sssf"]
```

- [ ] **Step 2: `references/config.md`**

A field table for `hitl` in the same shape as `budget`'s, and a prose section covering: the gate names the starter chains define; resolution order (`--hitl` > `SSSF_HITL` > `gates` > `default`; `when_unattended` applies only when the config, not a flag, turned the gate on); what exit 75 means; the decision file and the digest rule; that `waiting` keeps its worktree and is not counted by the watchers; the phase names to look for in `just phases`; `--hitl every`.

- [ ] **Step 3: `SKILL.md`**

Under *Where a run's work lands*:

> - **A run can stop and wait for you.** A gate the config or `--hitl` turns on ends the process with exit 75 and the session reading `waiting`; `just pending` lists them, `just show <adw_id>` prints the artifact, and `just approve` / `just reject -m "…"` / `just abort` answer it — a reject goes back to the agent that made the artifact, in the same session, and the run asks again. Nothing spends while it waits. `references/config.md#hitl`.

Routing row: `stop a run for a human, approve or reject a plan | references/config.md#hitl, then cookbooks/run_adw.md#when-a-run-is-waiting-for-you`.

- [ ] **Step 4: Cookbooks**

`update_adw.md` — `## Add a human gate`: the four-line pattern from Task 7, when a gate needs a `call` (revise) and when it must not (after code), naming rules, and that `--hitl every` exists so a gate is not needed for "let me watch each step".

`run_adw.md` — `## When a run is waiting for you`: what the orchestrator does when a launched chain exits 75 — report the gate and the subject path, do not read the db for it (`run.json` and `just show` are the record), and wait for the engineer's verdict rather than approving on their behalf.

- [ ] **Step 5: `docs/phase-9-hitl-gates.md` *As built***

Record what was built against the plan, and the follow-ups: the trace UI write path (decision 7), the forge channel (decision 6), an agent-level policy shorthand (decision 3).

- [ ] **Step 6: Commit**

```bash
git commit -am "hitl: config block, references, SKILL.md, cookbooks, phase 9 as built"
```

---

### Task 9: The trace UI shows `waiting`

**Files:**
- Modify: `skills/sssf/apps/visualizer/shared/types.ts` (`SessionStatus`, `PhaseStatus`, `EventType`)
- Modify: `skills/sssf/apps/visualizer/src/components/StatusChip.vue`, `PhaseDots.vue`, `SessionCard.vue`

- [ ] **Step 1: Types**

```ts
/** sessions.status — a run is running until it earns success; `waiting` means it stopped at a human gate and no process is alive. */
export type SessionStatus = "running" | "success" | "fail" | "waiting";
export type PhaseStatus = "queued" | "running" | "success" | "fail" | "waiting";
export type EventType = ... | "replay" | "decision" | "log" | "error";
```

- [ ] **Step 2: StatusChip**

Add `waiting: Hourglass` (lucide) to `ICONS` and a `.chip.waiting` rule in the amber family (`--yellow` if the palette has one; otherwise `rgba(250, 204, 21, …)` at the same three alphas the other chips use). No spin.

- [ ] **Step 3: PhaseDots and SessionCard**

`glyph.waiting = '◔'` with `.d.waiting` coloured like the chip. `SessionCard`: `.card.waiting` border; the polling `computed(running)` already stops on any non-`running` status, which is correct — a waiting run changes nothing until a human does.

- [ ] **Step 4: Build and eyeball**

```bash
cd skills/sssf/apps/visualizer && bun install && bun run build
```

Point it at the scratch repo from Task 6 step 7 (`--db …/sssf.db`) and check a suspended session shows the chip, the dot and the `decision` event in the phase detail.

- [ ] **Step 5: Commit**

```bash
git commit -am "visualizer: a fourth status, waiting — chip, dots, card, and the decision event"
```

---

## Verification, end to end

After Task 9, from the repository root:

```bash
uv run --with pytest --with pydantic --with python-dotenv --with pyyaml --with rich pytest skills/sssf/tests -q
ruff check .
```

Then the hand-run from Task 6 step 7 once more, on the pi or Claude Code harness with a real planner, to see the terminal prompt, a detach, `just pending`, a reject with notes, the planner's revised plan, and an approve that finishes the chain and commits the approved plan.

## Out of scope, named

- Approve/reject buttons in the trace UI (decision 7).
- Posting the subject to the issue or pull request and reading the verdict back (decision 6's forge channel).
- `agents[].hitl` as a shorthand for "every gate this agent owns" (decision 3).
- Shape B — a human gate as a gate callable inside the agent phase.
