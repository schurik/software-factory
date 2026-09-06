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
    validate_agent    harness-specific config problems for one agent, as strings
    new_session_id    a fresh id for this agent's context window
    ToolCallTracker   folds the CLI's event stream into tool_calls.py records
    run               one non-interactive turn: AgentRequest -> AgentResult

`agents.py` dispatches on those names and knows nothing else about any harness.
Everything downstream of the result — gates, permissions.py, the trace schema,
the visualizer — is harness-agnostic by construction.
"""

from __future__ import annotations

from types import ModuleType

from . import claude_code, pi

# Registration is explicit: an import here is what makes a harness selectable,
# and a directory scan would turn a half-written module into a runtime surprise
# during config validation rather than an import error at startup.
HARNESSES: dict[str, ModuleType] = {pi.NAME: pi, claude_code.NAME: claude_code}

NAMES = sorted(HARNESSES)


def get(name: str) -> ModuleType:
    """The harness module `name` selects. SystemExit if it is not one of ours."""
    try:
        return HARNESSES[name]
    except KeyError:
        raise SystemExit(f"harness {name!r} is not one of {' | '.join(NAMES)}") from None
