"""What a run may spend, and how long one agent turn may take.

Everything else in this package bounds what an agent may CHANGE — `writes:`,
`protected_files`, the worktree, the gates. Nothing bounded what it may SPEND
or how long it may sit there, and the two failures that follow are the ones
nobody is watching for:

- **The hung turn.** A harness call is `Popen` plus a blocking read of its
  stdout. An agent that stops emitting — a CLI waiting on something that never
  comes, a tool call that never returns — produces no events, no tokens and no
  output, so the read blocks forever. The trace shows a phase that started and
  never ended, and the only cure was an engineer noticing and running
  `just kill`. Nobody notices at 02:00.
- **The loop that pays for itself.** A builder that cannot make a suite pass
  retries through its gate corrections, each one a fresh send against the API.
  Every send is recorded (`sessions.total_cost`) and no send was ever refused
  because of what the ones before it cost.

So: one wall clock per agent turn, one ceiling per session, both configured
(`defaults.timeout_seconds`, the `budget:` block) and both off-switchable with
a `0`. Neither is a sandbox — a run that means to spend $40 says so in the
config. They are the difference between a bad run costing an hour and a night.

**A ceiling stops the NEXT send, never the one in flight.** Spend is only known
after a turn has been paid for, so refusing mid-turn would throw away work
already bought. `agents.execute` checks before each send instead: an overrun
session finishes the turn it is in, keeps its envelope, and dies at the
following one. The wall clock is the opposite — a hung turn has produced
nothing worth keeping, so it is terminated where it stands.

**Why `Deadline` reads the pipe itself**, rather than arming a timer and
leaving `for line in process.stdout` to end on its own: killing the child does
not reliably end that loop. Anyone still holding the write end keeps it open,
and a CLI whose tool subprocess inherited stdout leaves exactly that behind —
the read then blocks on a child that is already dead, which is this file's own
failure arrived at by a longer road. Polling puts the clock on OUR side of the
pipe, where it needs no cooperation from the child, from its children, or from
EOF ever arriving. `drain()` and `wait()` are the same argument for the two
other places a stray holder could block us after the loop ends.
"""

from __future__ import annotations

import codecs
import os
import selectors
import subprocess
import time
from typing import IO, Iterator

from .data_types import BudgetConfig

TERM_GRACE_SECONDS = 5.0    # SIGTERM, then this long, then SIGKILL
POLL_SECONDS = 1.0          # how often the read loop looks up at the clock
CHUNK = 65536


class AgentTimeout(RuntimeError):
    """One agent turn ran past `timeout_seconds` and was terminated.

    Carries whatever the harness had folded out of the stream before the clock
    ran out, because a killed turn still costs money: pi reports usage per
    message, so a hang after ten tool calls has already been paid for.
    `agents.execute` records it against the session on the way out — a spend
    ceiling that ignored timed-out turns would undercount exactly the runs it
    exists to stop. `None` when the harness had nothing (Claude Code reports
    usage only in the final `result` event, which a hang never reaches).
    """

    def __init__(self, message: str, result=None):
        super().__init__(message)
        self.result = result            # partial AgentResult, or None


class BudgetExceeded(RuntimeError):
    """The session hit a `budget:` ceiling, and no further send is made."""


class Deadline:
    """A bounded read of one harness child: its stream, its stderr, its exit.

    `seconds <= 0` means no limit, and every method below then behaves exactly
    as the plain blocking call it replaced. That is the contract for a factory
    that shipped without any of this, and the reason `0` is a real setting
    rather than a disclaimer.

    On expiry the child gets SIGTERM, then SIGKILL after `grace`. Only the
    child: killing the process GROUP would catch a grandchild too, but it means
    detaching the child from the terminal's group, and then ctrl-c no longer
    reaches the agent — trading a rare hang for a daily orphan. Nothing here
    needs the grandchild to die anyway; the poll loop stops reading whether or
    not somebody else still holds the pipe.

    `fired` says the factory ended this turn, and it is set BEFORE the signal,
    so a reader that sees the stream stop can tell a timeout from an ordinary
    non-zero exit.
    """

    def __init__(self, process: subprocess.Popen, seconds: int,
                 grace: float = TERM_GRACE_SECONDS):
        self.process = process
        self.seconds = max(0, int(seconds or 0))
        self.grace = grace
        self.fired = False
        self._expires_at = time.monotonic() + self.seconds if self.seconds else 0.0

    # ── the clock ───────────────────────────────────────────────────────────
    def _remaining(self) -> float:
        """Seconds left in this turn. `inf` when nothing is limited."""
        return self._expires_at - time.monotonic() if self.seconds else float("inf")

    def _kill(self) -> None:
        self.fired = True               # before the signal, never after
        try:
            self.process.terminate()
            self.process.wait(timeout=self.grace)
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except OSError:
                pass                    # already gone between the two calls
        except OSError:
            pass

    # ── the three places a child can block us ───────────────────────────────
    def lines(self) -> Iterator[str]:
        """The child's stdout, line by line (newline kept), while the clock holds.

        Yields as data arrives — the harnesses tail NDJSON so events reach the
        trace while the agent is still working, and buffering to completion
        would turn the live UI into a one-step progress bar.

        Bytes come straight off the descriptor once `select` says there are
        some, rather than through the text wrapper's `readline`: a partial line
        with nothing behind it would block that call indefinitely, and a
        stalling agent is exactly where a half-written line shows up. Ends on
        EOF, or on expiry with the child killed and `fired` set.
        """
        stream = self.process.stdout
        if stream is None:
            return
        fd = stream.fileno()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                if self._remaining() <= 0:
                    self._kill()
                    break
                if not selector.select(timeout=min(POLL_SECONDS, self._remaining())):
                    continue            # nothing yet — look up at the clock again
                chunk = os.read(fd, CHUNK)
                if not chunk:
                    break               # EOF: every writer is gone
                buffer += decoder.decode(chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    yield line + "\n"
        if buffer:
            yield buffer                # a last line with no newline behind it

    def drain(self, stream: IO[str] | None) -> str:
        """Whatever is on stderr, without hanging on a holder that outlived the child.

        An unlimited turn keeps the old blocking `read()`: complete stderr is
        worth waiting for when nothing else is bounded either. A limited one
        takes what arrives within the grace period — the message an error needs,
        without being one more place a stray process can stop the run.
        """
        if stream is None:
            return ""
        if not self.seconds:
            return stream.read()
        fd = stream.fileno()
        out = b""
        end = time.monotonic() + self.grace
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                left = end - time.monotonic()
                if left <= 0 or not selector.select(timeout=left):
                    break
                chunk = os.read(fd, CHUNK)
                if not chunk:
                    break
                out += chunk
        return out.decode("utf-8", errors="replace")

    def wait(self) -> int:
        """Reap the child. A limited turn never waits on it forever.

        The stream can end while the process does not — it closed its pipes, or
        somebody else was holding them open — so this is the third door, and it
        gets the same bound as the other two.
        """
        if not self.seconds:
            return self.process.wait()
        try:
            return self.process.wait(timeout=max(self.grace, self._remaining()))
        except subprocess.TimeoutExpired:
            self._kill()
            return self.process.wait()

    def reason(self, harness: str, raw_output_path: str = "") -> str:
        """The message for the AgentTimeout this expiry earns."""
        note = (f"{harness} agent ran past its {self.seconds}s limit without "
                f"finishing and was terminated (defaults.timeout_seconds)")
        return f"{note}; partial output: {raw_output_path}" if raw_output_path else note


def overrun(tokens: int, cost: float, budget: BudgetConfig) -> str:
    """Why this session may spend no more, or "" while it still may.

    Reads as a sentence because it becomes the phase's error — the line an
    engineer sees in the console, in the trace and on the run's card. A ceiling
    that fires without naming itself is a run that looks broken.
    """
    if budget.max_cost_usd and cost >= budget.max_cost_usd:
        return (f"session has spent ${cost:.4f} of its ${budget.max_cost_usd:.2f} "
                f"ceiling (budget.max_cost_usd)")
    if budget.max_tokens and tokens >= budget.max_tokens:
        return (f"session has used {tokens:,} of its {budget.max_tokens:,} token "
                f"ceiling (budget.max_tokens)")
    return ""
