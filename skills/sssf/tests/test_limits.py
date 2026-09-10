"""`Deadline` bounds a real child; `overrun` bounds a session's spend.

`Deadline` is tested against actual `Popen` children, because the whole point
of the module is that it does NOT need the child's cooperation: a fake process
object would be cooperating by construction, and the failure it exists to
prevent — a read that blocks on a pipe somebody else is holding open — cannot
happen to a mock.

The `seconds=0` case gets as much attention as the limited one. It is the
contract for a factory that shipped without any of this, and a regression there
would change the behaviour of every run that never configured a timeout.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from adw_modules.limits import (AgentTimeout, BudgetExceeded, Deadline,
                                TERM_GRACE_SECONDS, overrun)
from adw_modules.data_types import BudgetConfig


# Long enough to be unambiguously past a 1s limit, short enough that a BROKEN
# Deadline fails these tests in seconds instead of stalling CI — the failure
# mode of this module is a read that never returns, so its own tests must not
# be the thing that hangs.
STALL_SECONDS = 10


def child(code: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-u", "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


# ── Deadline: the unbounded contract ─────────────────────────────────────────

def test_zero_seconds_reads_the_whole_stream():
    process = child("print('a'); print('b')")
    deadline = Deadline(process, seconds=0)
    assert [line.rstrip("\n") for line in deadline.lines()] == ["a", "b"]
    assert deadline.wait() == 0
    assert deadline.fired is False


def test_a_negative_timeout_is_treated_as_no_limit():
    """`max(0, ...)` — a negative would otherwise expire instantly."""
    process = child("print('a')")
    deadline = Deadline(process, seconds=-5)
    assert deadline.seconds == 0
    assert [line.rstrip("\n") for line in deadline.lines()] == ["a"]
    deadline.wait()


def test_a_last_line_without_a_newline_is_still_yielded():
    process = child("import sys; sys.stdout.write('no newline')")
    lines = list(Deadline(process, seconds=0).lines())
    assert lines == ["no newline"]


# ── Deadline: the bounded one ────────────────────────────────────────────────

def test_a_silent_child_is_killed_when_the_clock_runs_out():
    """The hung turn: emits nothing, so nothing downstream would ever notice."""
    process = child(f"import time; time.sleep({STALL_SECONDS})")
    deadline = Deadline(process, seconds=1, grace=0.5)
    clock = time.monotonic()
    assert list(deadline.lines()) == []
    elapsed = time.monotonic() - clock

    assert deadline.fired is True
    assert elapsed < STALL_SECONDS         # killed, not waited out
    assert process.poll() is not None      # and actually dead


def test_a_child_that_emits_then_stalls_keeps_what_it_emitted():
    """A killed turn still produced events, and the trace must keep them."""
    process = child(f"import time; print('first'); time.sleep({STALL_SECONDS})")
    deadline = Deadline(process, seconds=1, grace=0.5)
    assert [line.rstrip("\n") for line in deadline.lines()] == ["first"]
    assert deadline.fired is True


def test_a_child_that_finishes_inside_the_limit_is_not_killed():
    process = child("print('done')")
    deadline = Deadline(process, seconds=30)
    assert [line.rstrip("\n") for line in deadline.lines()] == ["done"]
    assert deadline.fired is False
    assert deadline.wait() == 0


def test_lines_yields_while_the_child_is_still_working():
    """Buffering to completion would turn the live trace into a progress bar."""
    process = child("import time\nfor i in range(3):\n    print(i)\n    time.sleep(0.2)")
    deadline = Deadline(process, seconds=30)
    stream = deadline.lines()
    clock = time.monotonic()
    assert next(stream).rstrip("\n") == "0"
    assert time.monotonic() - clock < 0.2 + 1.0    # the first line, not the last
    list(stream)
    deadline.wait()


def test_ignoring_sigterm_still_ends_in_sigkill():
    """SIGTERM, then `grace`, then SIGKILL — the child does not get a veto."""
    process = child("import signal, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    f"time.sleep({STALL_SECONDS})")
    deadline = Deadline(process, seconds=1, grace=0.5)
    list(deadline.lines())
    assert deadline.fired is True
    assert process.wait(timeout=10) is not None


def test_wait_is_bounded_when_the_turn_is():
    process = child(f"import time; time.sleep({STALL_SECONDS})")
    deadline = Deadline(process, seconds=1, grace=0.5)
    clock = time.monotonic()
    deadline.wait()
    assert time.monotonic() - clock < STALL_SECONDS
    assert process.poll() is not None


def test_drain_returns_stderr():
    process = child("import sys; sys.stderr.write('boom')")
    deadline = Deadline(process, seconds=0)
    list(deadline.lines())
    assert deadline.drain(process.stderr) == "boom"
    deadline.wait()


def test_the_expiry_message_names_the_setting_that_caused_it():
    process = child("print('x')")
    deadline = Deadline(process, seconds=42)
    list(deadline.lines())
    deadline.wait()
    reason = deadline.reason("pi", "/tmp/raw_output.jsonl")
    assert "42s" in reason and "defaults.timeout_seconds" in reason
    assert "/tmp/raw_output.jsonl" in reason


def test_the_default_grace_is_the_module_s_own():
    process = child("print('x')")
    assert Deadline(process, seconds=1).grace == TERM_GRACE_SECONDS
    list(Deadline(process, seconds=0).lines())


# ── AgentTimeout carries what the turn already cost ──────────────────────────

def test_an_agent_timeout_carries_its_partial_result():
    """A killed turn was still paid for; a ceiling that ignored it undercounts."""
    expiry = AgentTimeout("ran past its limit", result="partial")
    assert expiry.result == "partial"


def test_an_agent_timeout_without_a_result_says_none():
    assert AgentTimeout("ran past its limit").result is None


# ── overrun: the spend ceiling ───────────────────────────────────────────────

def test_no_ceiling_never_refuses():
    assert overrun(10_000_000, 999.0, BudgetConfig()) == ""


def test_a_cost_ceiling_fires_at_or_above_the_limit():
    budget = BudgetConfig(max_cost_usd=5.0)
    assert overrun(0, 4.99, budget) == ""
    assert "budget.max_cost_usd" in overrun(0, 5.0, budget)
    assert "budget.max_cost_usd" in overrun(0, 5.01, budget)


def test_a_token_ceiling_fires_at_or_above_the_limit():
    budget = BudgetConfig(max_tokens=1000)
    assert overrun(999, 0.0, budget) == ""
    assert "budget.max_tokens" in overrun(1000, 0.0, budget)


def test_the_message_names_both_the_spend_and_the_ceiling():
    """It becomes the phase's error — a ceiling that fires anonymously reads
    as a broken run."""
    message = overrun(0, 7.5, BudgetConfig(max_cost_usd=5.0))
    assert "$7.5000" in message and "$5.00" in message


def test_cost_is_reported_before_tokens_when_both_are_over():
    message = overrun(2000, 9.0, BudgetConfig(max_cost_usd=5.0, max_tokens=1000))
    assert "max_cost_usd" in message


def test_budget_exceeded_is_a_runtime_error():
    """It aborts a phase; it is not a gate violation an agent can be asked
    to correct."""
    assert issubclass(BudgetExceeded, RuntimeError)


def test_a_zero_ceiling_is_off_not_instantly_exceeded():
    assert overrun(0, 0.0, BudgetConfig(max_cost_usd=0.0, max_tokens=0)) == ""


@pytest.mark.parametrize("tokens,cost", [(0, 0.0), (1, 0.001)])
def test_a_run_under_both_ceilings_is_allowed(tokens, cost):
    assert overrun(tokens, cost, BudgetConfig(max_cost_usd=5.0, max_tokens=1000)) == ""
