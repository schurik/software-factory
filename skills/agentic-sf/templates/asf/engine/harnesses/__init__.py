"""The harnesses the factory can run an agent on, and nothing else.

A harness is one coding-agent CLI plus everything that is specific to it: how a
model pattern is written, which tool names exist, what `thinking` maps to, how a
session is created versus continued, and which options block it reads. One
harness is ONE module here plus ONE template directory in the skill
(`templates/harnesses/<name>/`) — adding Codex means adding both, and touching
nothing else.

A module qualifies by exposing:

    NAME              the config value (`harness: pi`)
    Options           pydantic model for `harness_options`, owned by the harness
    resolve_model     pattern -> whatever the CLI needs; ValueError if unwritable
    reachable         raise unless the CLI can be executed (cached per process)
    credentials       Findings on whether this agent can authenticate at all —
                      read by preflight.py, never by agents.py. Optional: a
                      harness whose CLI carries its own auth may omit it, and
                      a harness that CAN answer must never read a key's value,
                      only whether the variable naming it is set.
    validate_agent    harness-specific config problems for one agent, as strings
    new_session_id    a fresh id for this agent's context window
    ToolCallTracker   folds the CLI's event stream into tool_calls.py records
    run               one non-interactive turn: AgentRequest -> AgentResult,
                      streaming, and armed with `limits.Deadline` over
                      `request.timeout_seconds` — a harness that can block
                      forever on a silent child is one the factory cannot
                      recover from. See references/harnesses.md.

`agents.py` dispatches on those names and knows nothing else about any harness.
Everything downstream of the result — gates, permissions.py, the trace schema,
the visualizer — is harness-agnostic by construction.
"""

from __future__ import annotations

from types import ModuleType

from . import claude_code, fake, pi

# Registration is explicit: an import here is what makes a harness selectable,
# and a directory scan would turn a half-written module into a runtime surprise
# during config validation rather than an import error at startup.
#
# `fake` is the one that is not a coding agent: it answers from a script and
# never calls a model. It is here so a roster can name it per agent — that is
# what makes the factory's own test suite and a new ADW's first ten iterations
# free — and it is deliberately absent from `templates/harnesses/`, so the
# installer never offers it as the harness a repository runs on. Its
# `credentials()` says what it is in every `just doctor` report.
HARNESSES: dict[str, ModuleType] = {pi.NAME: pi, claude_code.NAME: claude_code,
                                    fake.NAME: fake}

NAMES = sorted(HARNESSES)


def get(name: str) -> ModuleType:
    """The harness module `name` selects. SystemExit if it is not one of ours."""
    try:
        return HARNESSES[name]
    except KeyError:
        raise SystemExit(f"harness {name!r} is not one of {' | '.join(NAMES)}") from None
