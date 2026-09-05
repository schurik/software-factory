"""The normalized tool-call record every backend must produce.

One record per COMPLETED tool call, with the same keys whatever ran it:

    tool, tool_call_id, args, ok, label, result_snippet,
    started_at, ended_at, duration_ms

That shape is the contract `agents._event_forwarder` writes to the trace and
the visualizer reads back, so it lives here rather than inside one backend.
A backend's tracker parses its own event vocabulary and does its bookkeeping
through `ToolCallLedger`; nothing downstream can tell which one ran.
"""

from __future__ import annotations

import time
from typing import Optional

from .utils import now_iso

RESULT_SNIPPET_CHARS = 20_000   # tool output rides along whole; clip only guards pathological cases
ARG_VALUE_CHARS = 20_000        # args too — the UI scrolls, it must not be handed cut-off data
LABEL_CHARS = 80                # "bash: <command>" shown as the event name

# The arg that identifies a call at a glance, in the order tools tend to use.
# Claude Code's names are here alongside pi's: `file_path` is both, `command`
# is Bash on either side, and `pattern` covers Grep/Glob as well as pi's grep.
PRIMARY_ARGS = ("command", "path", "file_path", "pattern", "query", "url", "prompt")


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def label(tool: str, args: dict) -> str:
    """One-line human name for a tool call: `bash: ls -la src`."""
    value = next((args[key] for key in PRIMARY_ARGS
                  if isinstance(args.get(key), str) and args[key].strip()), "")
    if not value:
        value = next((v for v in args.values() if isinstance(v, str) and v.strip()), "")
    value = " ".join(str(value).split())
    return f"{tool}: {clip(value, LABEL_CHARS)}" if value else tool


class ToolCallLedger:
    """Open calls, keyed by the backend's own call id, closed into one record.

    A tool call is announced by one event and answered by a later one, and only
    the answer carries the result — so a record is emitted at `close`, the
    moment the call returns, instead of one shapeless event per sighting. The
    ledger also holds the call's real span (`started_at`/`ended_at`), which the
    tracer writes to columns so the UI can lay tool calls on a time axis
    without parsing every payload.
    """

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}

    def announce(self, call_id, tool, args) -> None:
        """First sighting starts the clock; a later sighting only fills gaps."""
        if not call_id:
            return
        known = self._open.get(str(call_id), {})
        self._open[str(call_id)] = {
            "tool": tool or known.get("tool", ""),
            "args": args or known.get("args", {}),
            "started_at": known.get("started_at") or now_iso(),   # wall clock, for the row
            "clock": known.get("clock") or time.monotonic(),      # monotonic, for duration
        }

    def close(self, call_id, tool: str = "", args: Optional[dict] = None,
              ok: bool = True, result_text: str = "") -> dict:
        """Finish an announced call and return its normalized record."""
        key = str(call_id or "")
        opened = self._open.pop(key, {})
        name = str(tool or opened.get("tool") or "tool")
        arguments = args or opened.get("args") or {}
        record = {
            "tool": name,
            "tool_call_id": key,
            "args": {k: clip(v, ARG_VALUE_CHARS) if isinstance(v, str) else v
                     for k, v in arguments.items()},
            "ok": ok,
            "label": label(name, arguments),
        }
        if result_text:
            record["result_snippet"] = clip(result_text, RESULT_SNIPPET_CHARS)
        record["ended_at"] = now_iso()
        if opened.get("clock"):
            record["duration_ms"] = int((time.monotonic() - opened["clock"]) * 1000)
        if opened.get("started_at"):
            record["started_at"] = opened["started_at"]
        return record
