"""Pi coding agent backend.

Runs `pi -p --mode json` and tails its JSONL stdout line by line, forwarding
each event to a callback WHILE the agent works (the streaming crack, solved
by construction). `--session-id` creates-or-continues, so running and
continuing an agent are the same call: same session id = same context window.

One of two backends behind the same five names — `NAME`, `resolve_model`,
`reachable`, `validate_agent`, `ToolCallTracker`, `run` — which is all
`agents.py` dispatches on. See `agent_cc.py` for the other.
"""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from .data_types import AgentConfig, AgentRequest, AgentResult, UsageBreakdown
from .tool_calls import ToolCallLedger
from .utils import new_id, operator_env

NAME = "pi"

PI_PATH = os.environ.get("PI_PATH", "pi")
MODELS_JSON = os.environ.get("PI_MODELS_PATH",
                             str(Path.home() / ".pi" / "agent" / "models.json"))

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def _count(value: str) -> int:
    """Parse pi's compact model-list counts (`272K`, `1.0M`)."""
    suffixes = {"K": 1_000, "M": 1_000_000}
    suffix = value[-1:].upper()
    if suffix in suffixes:
        return int(float(value[:-1]) * suffixes[suffix])
    return int(value)


@lru_cache(maxsize=1)
def _pi_catalog() -> list[tuple[str, str, int]]:
    """Read pi's merged catalog, including built-in providers and custom models."""
    try:
        result = subprocess.run(
            [PI_PATH, "--list-models"], capture_output=True, text=True,
            timeout=30, env=operator_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 3:
            continue
        try:
            rows.append((columns[0], columns[1], _count(columns[2])))
        except ValueError:
            continue
    return rows


def resolve_model(pattern: str) -> tuple[str, str]:
    """Resolve a model pattern to an explicit ``(provider, model_id)`` pair.

    Pi's catalog merges built-in models with ``~/.pi/agent/models.json``. Using
    that same merged view lets SSSF target direct providers such as
    ``openai/gpt-5.6-terra`` without re-registering built-in models locally.
    """
    catalog = [(provider, model_id) for provider, model_id, _ in _pi_catalog()]
    if "/" in pattern:
        provider, model_id = pattern.split("/", 1)
        if (provider, model_id) in catalog:
            return provider, model_id
    matches = [(provider, model_id) for provider, model_id in catalog
               if pattern == model_id or pattern in model_id]
    exact = [match for match in matches
             if match[1] == pattern or match[1].endswith("/" + pattern)]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"model pattern {pattern!r} not found in pi --list-models — "
                         "authenticate/register it or fix the config")
    raise ValueError(f"model pattern {pattern!r} is ambiguous: {matches}")


def reachable() -> None:
    """Raise unless the pi CLI can be executed. Checked once per process."""
    if not _pi_catalog():
        raise RuntimeError(
            f"the pi CLI ({PI_PATH!r}) is not reachable, or `pi --list-models` "
            f"returned nothing — install it, put it on PATH, or set PI_PATH")


def validate_agent(agent: AgentConfig) -> list[str]:
    """Backend-specific config problems for one agent. Empty list = fine."""
    problems = []
    if agent.thinking not in THINKING_LEVELS:
        problems.append(f"thinking {agent.thinking!r} is not one of "
                        f"{' | '.join(THINKING_LEVELS)}")
    for extension in agent.harness_engineering:
        if not str(extension).endswith(".ts"):
            problems.append(f"harness_engineering {extension!r}: pi extensions are "
                            f"TypeScript files passed as `pi -e <file.ts>`")
    return problems


def new_session_id(adw_id: str, agent: AgentConfig) -> str:
    """A fresh pi session id. Random, because pi's ids are create-or-continue:
    a deterministic one would silently rejoin a context window from an earlier
    run whenever the agent map went missing."""
    return f"sssf-{adw_id}-{agent.name}-{new_id(4)}"


def _turn_usage(usage: dict, total_tokens: int) -> UsageBreakdown:
    """Pi's `message_end` usage object as a UsageBreakdown.

    `total_tokens` is passed in rather than re-derived: the caller already
    computes it pi's way (totalTokens, else the sum of the parts). Pi is the
    backend that reports cost per component, so all five cost fields are real.
    """
    cost = usage.get("cost") or {}
    return UsageBreakdown(
        input_tokens=usage.get("input") or 0,
        output_tokens=usage.get("output") or 0,
        cache_read_tokens=usage.get("cacheRead") or 0,
        cache_write_tokens=usage.get("cacheWrite") or 0,
        reasoning_tokens=usage.get("reasoning") or 0,
        total_tokens=total_tokens,
        input_cost=cost.get("input") or 0.0,
        output_cost=cost.get("output") or 0.0,
        cache_read_cost=cost.get("cacheRead") or 0.0,
        cache_write_cost=cost.get("cacheWrite") or 0.0,
        total_cost=cost.get("total") or 0.0,
    )


def _context_tokens(usage: dict) -> int:
    """Tokens occupying the window after a turn.

    Mirrors pi's own `calculateContextTokens` (coding-agent
    `core/compaction/compaction.ts`), which is what pi compacts against and
    shows in its footer: prefer the provider's `totalTokens`, else sum the
    parts. Cache reads count — cached prompt is still prompt.
    """
    total = usage.get("totalTokens") or 0
    if total:
        return int(total)
    return int(sum(usage.get(part) or 0
                   for part in ("input", "output", "cacheRead", "cacheWrite")))


def context_window(provider: str, model_id: str) -> int:
    """The model's context ceiling from pi's merged model catalog.

    ``models.json`` is optional — a provider registered entirely by a pi
    extension (no local overrides) has no such file, so a missing file falls
    straight through to ``pi --list-models`` below rather than raising.
    """
    try:
        registry = json.loads(Path(MODELS_JSON).read_text())
    except FileNotFoundError:
        registry = {}
    for model in registry.get("providers", {}).get(provider, {}).get("models", []):
        if model.get("id") == model_id:
            return int(model.get("contextWindow") or 0)
    for listed_provider, listed_model, window in _pi_catalog():
        if listed_provider == provider and listed_model == model_id:
            return window
    return 0


def _text_of(container: dict) -> str:
    """Join the text blocks of anything pi shapes as {content: [...]} — a
    message or a tool result."""
    return "".join(part.get("text", "") for part in container.get("content", []) or []
                   if isinstance(part, dict) and part.get("type") == "text")


class ToolCallTracker:
    """Folds pi's tool stream into ONE normalized record per completed call.

    pi announces a call as a `toolCall` content block, then emits
    tool_execution_start / _update / _end for it. Only the end carries the
    result, so that is where a record is emitted — one trace event per real
    tool call, the moment it returns, instead of three shapeless ones.

    `observe` returns a LIST because the other backend can close several calls
    in one event; the record shape itself lives in tool_calls.py, which is what
    keeps the two backends indistinguishable downstream.
    """

    def __init__(self) -> None:
        self._ledger = ToolCallLedger()

    def observe(self, event: dict) -> list[dict]:
        """Records for whatever tool calls this event finished — usually none."""
        etype = event.get("type", "")
        if etype == "message_end":
            for block in event.get("message", {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "toolCall":
                    self._ledger.announce(block.get("id"), block.get("name"),
                                          block.get("arguments"))
            return []
        if etype == "tool_execution_start":
            self._ledger.announce(event.get("toolCallId"), event.get("toolName"),
                                  event.get("args"))
            return []
        if etype != "tool_execution_end":
            return []
        return [self._ledger.close(event.get("toolCallId"),
                                   tool=str(event.get("toolName") or ""),
                                   args=event.get("args"),
                                   ok=not event.get("isError", False),
                                   result_text=_text_of(event.get("result") or {}))]


def run(request: AgentRequest, on_event: Optional[Callable[[dict], None]] = None,
        on_spawn: Optional[Callable[[int], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None) -> AgentResult:
    """Run one non-interactive pi turn.

    `on_spawn(pid)` and `on_exit(pid)` bracket the child process so the caller
    can record it as killable — a hung coding agent is otherwise a pid you have
    to hunt for in `ps` while the run sits there.
    """
    provider, model_id = resolve_model(request.model)
    cmd = [
        PI_PATH, "-p", "--mode", "json",
        "--provider", provider, "--model", model_id,
        "--thinking", request.thinking,
        "--session-id", request.session_id,
        "--session-dir", request.session_dir,
        "--system-prompt", request.system_prompt,
    ]
    if request.tools:
        cmd += ["--tools", ",".join(request.tools)]
    for extension in request.extensions:
        cmd += ["-e", extension]
    cmd.append(request.prompt)

    raw_path = Path(request.raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    result = AgentResult(session_id=request.session_id,
                         context_window=context_window(provider, model_id))
    # stdin is DEVNULL, deliberately. The prompt travels in argv, so the child
    # never needs stdin — but inheriting the parent's means pi sees a non-TTY
    # and can sit forever waiting for piped input that will never arrive or
    # EOF. That failure is silent and total: no request goes out, no bytes come
    # back, and the ADW blocks on a read loop with nothing to read. Observed as
    # a run that sat idle at 0% CPU with an empty raw_output.jsonl.
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1, cwd=request.cwd,
                               env=operator_env())
    if on_spawn:
        on_spawn(process.pid)
    with raw_path.open("a") as raw:
        assert process.stdout is not None
        for line in process.stdout:
            raw.write(line)
            raw.flush()                      # events land on disk as they happen
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "message_end":
                message = event.get("message", {})
                if message.get("role") == "assistant":
                    text = _text_of(message)
                    if text:
                        result.text = text   # last assistant message wins
                    usage = message.get("usage", {}) or {}
                    turn = _context_tokens(usage)
                    result.tokens += turn
                    result.usage.merge(_turn_usage(usage, turn))
                    # Occupancy is read off the last VALID assistant turn, the
                    # way pi does it — an aborted or errored turn reports usage
                    # you can't trust, so it must not overwrite a good reading.
                    if turn and message.get("stopReason") not in ("aborted", "error"):
                        result.context_tokens = turn
                    result.cost += (usage.get("cost", {}) or {}).get("total", 0.0) or 0.0
            if on_event:
                on_event(event)

    stderr = process.stderr.read() if process.stderr else ""
    result.returncode = process.wait()
    if on_exit:
        on_exit(process.pid)
    if result.returncode != 0 and not result.text:
        raise RuntimeError(f"pi exited {result.returncode}: {stderr.strip()[-800:]}")
    return result
