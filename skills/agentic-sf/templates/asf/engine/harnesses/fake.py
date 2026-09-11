"""A scripted harness. **Not a production harness** — it never calls a model.

Everything else in this package is a real coding-agent CLI. This one answers
from a script you wrote, which makes it useless for doing work and valuable for
two things that used to cost tokens and minutes each time:

  * **Testing the factory.** Phases, gates, gate corrections, JSON re-prompts,
    envelopes, permissions, budgets, the trace — the whole chain around the
    agent — is deterministic machinery that was only ever exercised by paying
    an agent to run through it. `skills/agentic-sf/tests/` drives it with this.
  * **Developing a new ADW.** The shape of a chain (which phases, in which
    order, with which gates and which envelope types) is settled long before
    the prompts are any good. Run it on `harness: fake` until the shape holds,
    then move the roster back to a real harness. The iteration is free.

It is registered in `harnesses.HARNESSES` like any other harness, so a roster
may name it per agent. It is deliberately NOT in `templates/harnesses/`, so
`install.py` never offers it as the harness a repository runs on: a factory
whose whole roster is fake produces nothing.

**Refuse to mistake it for the real thing.** `credentials()` says so in
`just doctor`, and every run it takes part in has `harness: fake` in the trace.

## The script

The agent's `harness_options` block IS the script (see `Options`):

    - name: planner
      harness: fake
      model: fake
      harness_options:
        replies:
          - writes: {"specs/plan.md": "# Plan\\n"}
            envelope: {status: success, summary: planned, artifacts: [specs/plan.md]}

One reply is consumed per SEND — and a phase can send more than once: a
malformed JSON response is re-prompted, and a gate violation comes back as a
correction. That is exactly what makes a scripted first reply that claims an
artifact it never wrote, followed by one that writes it, a real test of the
gate-correction loop.

The cursor lives in the harness's own session directory (`request.session_dir`),
not in a module global, so it behaves like a real session: a second process
joining the same session continues where the first stopped, and a resumed run
that replays a phase does not consume a reply for it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..data_types import (AgentConfig, AgentRequest, AgentResult, Finding,
                          UsageBreakdown)
from ..limits import AgentTimeout
from ..tool_calls import ToolCallLedger

NAME = "fake"


class Reply(BaseModel):
    """One scripted turn: what it does to the tree, and what it says.

    The order matters and is the order a real agent works in — it uses tools,
    it changes files, and only then does it report. `writes` and `deletes` are
    what let a scripted run drive gates that measure the tree rather than the
    envelope.
    """

    model_config = ConfigDict(extra="forbid")

    # `envelope` is serialized to JSON and becomes the turn's final text, which
    # is what `agents._parse_with_retries` parses. `text` overrides it verbatim
    # — that is how a reply that is NOT valid envelope JSON gets scripted, to
    # exercise the re-prompt path.
    envelope: dict[str, Any] = Field(default_factory=dict)
    text: Optional[str] = None
    # Relative to the run's worktree (`request.cwd`), which is where a real
    # agent's writes land and where the gates and permissions.py look.
    writes: dict[str, str] = Field(default_factory=dict)
    deletes: list[str] = Field(default_factory=list)
    # Each becomes one tool_call event, so the trace and the visualizer see the
    # same shape a real harness produces.
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0
    context_tokens: int = 0
    context_window: int = 0
    returncode: int = 0
    # Wall clock this turn pretends to take. Past `timeout_seconds` it raises
    # `AgentTimeout` — the same exception a real harness raises when
    # `limits.Deadline` fires, so `agents.execute` takes the same path. It does
    # NOT exercise `Deadline` itself: that owns a real child process and is
    # tested directly.
    sleep: float = 0.0


class Options(BaseModel):
    """`harness_options` for a fake agent: the script, and how to run out of it.

    `extra="forbid"` for the reason every harness forbids extras — a key that
    silently does nothing is a test that silently proves nothing.
    """

    model_config = ConfigDict(extra="forbid")

    replies: list[Reply] = Field(default_factory=list)
    # A JSON file holding either a list of replies or `{"replies": [...]}`.
    # Resolved against the run's worktree when relative. Loaded fresh per turn,
    # so a test can rewrite it between phases.
    script: str = ""
    # What a send past the end of the script does. Repeating the last reply is
    # the default because a gate-correction loop is bounded by `retries` and
    # would otherwise need the script to know how many corrections it will take.
    # `strict` turns running out into a loud failure instead.
    strict: bool = False


def _load(options: Options, cwd: str) -> list[Reply]:
    replies = list(options.replies)
    if options.script:
        path = Path(options.script)
        if not path.is_absolute():
            path = Path(cwd) / path
        raw = json.loads(path.read_text())
        rows = raw.get("replies", []) if isinstance(raw, dict) else raw
        replies += [Reply(**row) for row in rows]
    return replies


# ── the contract `harnesses/__init__.py` documents ───────────────────────────

def resolve_model(pattern: str) -> str:
    """Any non-empty name. Nothing resolves it — no model is ever called."""
    model = pattern.strip()
    if not model:
        raise ValueError("model is empty — the fake harness ignores it, but a "
                         "roster entry with no model is a typo either way")
    return model


def reachable() -> None:
    """Always. There is no CLI to be missing, which is half the point."""


def credentials(agent: AgentConfig) -> list[Finding]:
    """Say out loud, in every doctor report, that this agent is not real."""
    return [Finding(
        check=f"credentials: {agent.name}", level="warn",
        detail=f"{agent.name} runs on the FAKE harness — it answers from a script "
               f"and never calls a model, so this agent produces nothing",
        fix="move it to `harness: claude_code` or `harness: pi` before this "
            "roster is used for real work")]


def validate_agent(agent: AgentConfig) -> list[str]:
    """Config problems for one fake agent, as strings."""
    try:
        options = Options(**agent.harness_options)
    except ValueError as error:
        return [f"harness_options: {error}"]
    if not options.replies and not options.script:
        return ["harness_options: the fake harness answers from a script, so it "
                "needs `replies:` or `script:` — with neither, every send fails"]
    return []


def new_session_id(adw_id: str, agent: AgentConfig) -> str:
    """Deterministic, like every other harness's: same run, same session."""
    return f"fake-{adw_id}-{agent.name}"


class ToolCallTracker:
    """Folds this harness's own event vocabulary into tool_calls.py records.

    A scripted tool call is announced and answered by ONE event, because there
    is nothing in between to wait for — so `observe` announces and closes it in
    the same breath and the record comes out with the shape every other tracker
    produces.
    """

    def __init__(self) -> None:
        self._ledger = ToolCallLedger()

    def observe(self, event: dict) -> list[dict]:
        if event.get("type") != "tool_call":
            return []
        call_id = str(event.get("id") or id(event))
        args = event.get("args") or {}
        self._ledger.announce(call_id, event.get("tool", "tool"), args)
        return [self._ledger.close(call_id, ok=bool(event.get("ok", True)),
                                   result_text=str(event.get("result", "")))]


# ── the turn ─────────────────────────────────────────────────────────────────

def _cursor_path(request: AgentRequest) -> Path:
    directory = Path(request.session_dir or ".")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{request.session_id}.cursor"


def _take(request: AgentRequest, replies: list[Reply], strict: bool) -> Reply:
    """The next scripted reply for this session, advancing the cursor on disk."""
    path = _cursor_path(request)
    try:
        index = int(path.read_text().strip())
    except (OSError, ValueError):
        index = 0
    path.write_text(str(index + 1))
    if index < len(replies):
        return replies[index]
    if strict or not replies:
        raise RuntimeError(
            f"fake harness: send #{index + 1} for session {request.session_id} has no "
            f"scripted reply ({len(replies)} in the script). Add one, or drop "
            f"`strict: true` to repeat the last.")
    return replies[-1]


def _apply(reply: Reply, cwd: str) -> None:
    """The turn's effect on the tree, before it reports on it."""
    root = Path(cwd)
    for relative, content in reply.writes.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    for relative in reply.deletes:
        target = root / relative
        if target.exists():
            target.unlink()


class _nullwriter:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def write(self, _text: str) -> None:
        pass


def _emit(events: list[dict], request: AgentRequest,
          on_event: Optional[Callable[[dict], None]]) -> None:
    """Forward the turn's events and record them where a real harness does.

    `raw_output.jsonl` is written for the same reason the real harnesses write
    it: the session directory is the record, and a scripted run that left a
    hole there would not be the thing under test.
    """
    path = Path(request.raw_output_path) if request.raw_output_path else None
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
    with (path.open("a") if path else _nullwriter()) as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")
            if on_event:
                on_event(event)


def run(request: AgentRequest, on_event: Optional[Callable[[dict], None]] = None,
        on_spawn: Optional[Callable[[int], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None) -> AgentResult:
    """One scripted turn: apply the tree changes, emit the events, report.

    `on_spawn`/`on_exit` are deliberately never called. They exist so a hung
    coding agent can be found and killed by pid, and there is no child process
    here — recording this ADW's own pid as a killable agent would make
    `just kill` shoot the run itself.
    """
    options = Options(**request.options)
    replies = _load(options, request.cwd)
    reply = _take(request, replies, options.strict)

    if reply.sleep:
        limit = request.timeout_seconds
        if limit and reply.sleep > limit:
            # The same exception a real harness raises when `limits.Deadline`
            # fires, so `agents.execute` records the same `agent_timeout`. The
            # partial result is what a timed-out turn had already been paid for.
            partial = AgentResult(session_id=request.session_id, tokens=reply.tokens,
                                  cost=reply.cost,
                                  usage=UsageBreakdown(total_tokens=reply.tokens,
                                                       total_cost=reply.cost))
            raise AgentTimeout(
                f"fake agent ran past its {limit}s limit without finishing and was "
                f"terminated (defaults.timeout_seconds)", partial)
        time.sleep(reply.sleep)

    _apply(reply, request.cwd)

    text = reply.text if reply.text is not None else json.dumps(reply.envelope)
    # Copied, not aliased: the script is reused across sends and a default
    # written back into it would make the second turn differ from the first.
    events = [{"type": "tool_call", **call} for call in reply.tool_calls]
    events.append({"type": "result", "text": text})
    _emit(events, request, on_event)

    return AgentResult(
        text=text,
        returncode=reply.returncode,
        session_id=request.native_session_id or request.session_id,
        tokens=reply.tokens,
        cost=reply.cost,
        usage=UsageBreakdown(total_tokens=reply.tokens, total_cost=reply.cost),
        context_tokens=reply.context_tokens,
        context_window=reply.context_window,
    )
