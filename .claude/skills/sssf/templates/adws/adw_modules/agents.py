"""Config loading/validation and agent execution.

Every ADW validates its agents before running (fail fast, nothing spawns
against a half-valid config). Every agent call parses against a concrete
output type; parse failures and gate violations re-prompt the SAME session
with a correction — context intact, bounded retries. Agent proposes, code
disposes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from . import agent_cc, agent_pi, git_helper, permissions, prompts
from .data_types import (AgentCall, AgentConfig, AgentRequest, AgentResult,
                         AgentSession, EnvelopeBase, EventRecord, GateCheck,
                         GateReport, Phase, SSSFConfig, UsageBreakdown)
from .utils import anchor

JSON_FIX_ATTEMPTS = 2      # continue-with-correction attempts for malformed JSON

# The whole of the backend seam. A module qualifies by exposing NAME,
# resolve_model, reachable, validate_agent, new_session_id, ToolCallTracker and
# run — nothing else in the factory knows which one is running.
BACKENDS = {agent_pi.NAME: agent_pi, agent_cc.NAME: agent_cc}


class GateFailure(RuntimeError):
    pass


def backend(agent: AgentConfig):
    """The coding-agent module this agent runs on."""
    try:
        return BACKENDS[agent.coding_agent]
    except KeyError:
        raise SystemExit(f"agent {agent.name!r}: coding_agent "
                         f"{agent.coding_agent!r} is not one of "
                         f"{' | '.join(sorted(BACKENDS))}") from None


# ── config ───────────────────────────────────────────────────────────────────

def load_config(path: str = "adws/adw_sssf_config/sssf.config.yaml") -> SSSFConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    defaults = raw.get("defaults", {}) or {}
    for agent in raw.get("agents", []) or []:
        for key in ("coding_agent", "model", "thinking", "color", "tools", "writes",
                    "claude_code"):
            if key in defaults:
                agent.setdefault(key, defaults[key])
        agent.setdefault("harness_engineering", defaults.get("harness_engineering", []))
    return SSSFConfig(**raw)


def resolve(cfg: SSSFConfig, name: str) -> AgentConfig:
    for agent in cfg.agents:
        if agent.name == name:
            return agent
    raise SystemExit(f"agent {name!r} is not defined in the config — "
                     f"available: {[a.name for a in cfg.agents]}")


def validate(cfg: SSSFConfig, required: list[str]) -> None:
    """Fail fast: every required name must resolve to a usable agent.

    Prompt files are looked for in the MAIN checkout, which is where the roster
    and its prompts live — not in the run's worktree, and not in whatever
    directory the ADW was launched from.

    Everything backend-specific is asked of the backend: pi resolves a model
    against its catalog and Claude Code accepts an alias, pi's tool names are
    lowercase and Claude Code's are not, and only one of the two can load a
    TypeScript extension. The shape here — collect every problem, raise one
    SystemExit — is unchanged, and every ADW depends on it.

    A model is checked for being WRITTEN correctly, not for being reachable.
    Nothing here confirms the provider answers or that its key is set, so a
    missing credential still surfaces partway into a chain.
    """
    root = git_helper.main_root()
    problems = []
    for name in required:
        try:
            agent = resolve(cfg, name)
        except SystemExit as e:
            problems.append(str(e))
            continue
        driver = BACKENDS.get(agent.coding_agent)
        if driver is None:
            problems.append(f"agent {name!r}: coding_agent {agent.coding_agent!r} is not "
                            f"one of {' | '.join(sorted(BACKENDS))}")
            continue
        for label, ref in (("system", agent.prompt_engineering.system),
                           ("user", agent.prompt_engineering.user)):
            if not anchor(root, ref).is_file():
                problems.append(f"agent {name!r}: {label} prompt not found: {ref}")
        # Model and tool vocabularies belong to the backend: pi resolves against
        # its catalog, Claude Code takes an alias. Applying either rule to the
        # other backend is how a valid roster gets rejected — or worse, a
        # nonsense one accepted.
        try:
            driver.resolve_model(agent.model)
        except ValueError as e:
            problems.append(f"agent {name!r}: {e}")
        problems += [f"agent {name!r}: {problem}"
                     for problem in driver.validate_agent(agent)]
        try:
            driver.reachable()      # cached per backend; one probe per process
        except RuntimeError as e:
            problems.append(f"agent {name!r}: {e}")
    if problems:
        raise SystemExit("config validation failed:\n- " + "\n- ".join(problems))


# ── execution ────────────────────────────────────────────────────────────────

def execute(run, phase: Phase, call: AgentCall) -> EnvelopeBase:
    """One agent call: render prompts -> backend run -> typed parse -> gates -> envelope."""
    agent = resolve(run.cfg, phase.params.owner)
    agent_dir = run.session_dir / agent.name
    agent_dir.mkdir(parents=True, exist_ok=True)

    variables = {
        "prompt": call.prompt,
        "previous_envelope": call.previous.model_dump_json(indent=2) if call.previous else "(none)",
        # Absolute, and it has to be. One worktree per RUN, not per agent: every
        # agent in a session is spawned in the same `run.repo_root`, and hands
        # its work to the next one through this directory — which lives under
        # data_dir in the MAIN checkout, outside that worktree.
        #
        # So a relative path would not lose the agents each other; they would
        # all resolve it identically, to a directory inside the worktree. It
        # would lose them the CODE. `changes.capture` and the quality blocks
        # write to `run.context_handoff_dir` and the trace records it, so the
        # two halves of one handoff would be in different trees. Worse, the
        # agents' half would then sit inside the tree that `commit_all` stages
        # with `git add -A` and that permissions.py fingerprints — a scout with
        # `writes: []` would breach its own boundary by filing its report — and
        # it would be deleted with the worktree when the run is released.
        "context_handoff_dir": str(run.context_handoff_dir),
    }
    # The roster's prompts live beside the config, in the main checkout.
    system_text = prompts.render(anchor(run.main_root, agent.prompt_engineering.system), variables)
    user_text = prompts.render(anchor(run.main_root, agent.prompt_engineering.user), variables)
    prompts.save(agent_dir / "prompts", "system.md", system_text)
    prompts.save(agent_dir / "prompts", "user.md", user_text)

    driver = backend(agent)
    session = _agent_session(run, agent, driver)
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                 type="agent_start", name=agent.name,
                                 payload={"model": agent.model, "thinking": agent.thinking,
                                          "color": agent.color,
                                          "session_id": session.session_id,
                                          "coding_agent": agent.coding_agent,
                                          "purpose": agent.purpose,
                                          "tools": agent.tools,  # None = all tools
                                          "harness_engineering": agent.harness_engineering}))
    run.console.agent_started(agent.name, agent.model, session.session_id)

    # Parse retries and gate corrections re-enter the SAME agent session, so the
    # last send is the one whose context occupancy is current — while spend is
    # the opposite: every send costs, so usage accumulates across all of them.
    latest: AgentResult | None = None
    spent = UsageBreakdown()
    forward = _event_forwarder(run, phase, agent.name, driver)

    def send(prompt_text: str) -> AgentResult:
        nonlocal latest
        request = AgentRequest(
            prompt=prompt_text,
            system_prompt=system_text,
            model=agent.model,
            thinking=agent.thinking,
            session_id=session.session_id,
            # absolute: these are read by the coding-agent subprocess, which
            # runs in repo_root
            session_dir=str((agent_dir / f"{agent.coding_agent}_sessions").resolve()),
            raw_output_path=str((agent_dir / "raw_output.jsonl").resolve()),
            runtime_dir=str(run.session_dir.resolve()),
            tools=agent.tools,
            extensions=agent.harness_engineering,
            cwd=str(run.repo_root),
            native_session_id=session.native_session_id,
            resume=session.started,
            claude_code=agent.claude_code,
        )
        result = driver.run(
            request,
            on_event=forward,
            on_spawn=lambda pid: run.tracer.process_start(
                run.adw_id, "agent", agent.name, pid,
                f"{agent.coding_agent} {agent.name} {agent.model}"),
            on_exit=lambda pid: run.tracer.process_end(run.adw_id, pid))
        run.add_usage(result.tokens, result.cost)
        spent.merge(result.usage)
        latest = result
        # The session now EXISTS, and the next send in this phase must continue
        # it rather than create it again. Persisted immediately, not at the end
        # of the phase: a Claude Code session survives the process, so a run
        # that dies mid-phase would otherwise leave a map claiming a session it
        # can no longer create and cannot resume.
        session.native_session_id = result.session_id or session.native_session_id
        session.started = True
        _remember(run, agent, session)
        return result

    # What the tree looked like before this agent got its hands on it. Every
    # send in this phase — first prompt, JSON retries, gate corrections — is
    # measured against this one baseline.
    tree_before = permissions.snapshot(run)

    result = send(user_text)
    envelope, attempt = _parse_with_retries(run, phase, call, result, send)

    # claim gates — violations flow back into the SAME session as corrections
    for gate_attempt in range(1, max(1, phase.params.retries + 1) + 1):
        violations = []
        for gate in call.gates:
            report = _as_report(gate(envelope, run))
            found = report.violations
            run.tracer.gate_row(phase, gate.__name__, report, gate_attempt)
            run.tracer.event(EventRecord(
                adw_id=run.adw_id, phase_id=phase.phase_id,
                type="gate_fail" if found else "gate_pass", name=gate.__name__,
                payload={"attempt": gate_attempt, "violations": found,
                         "checks": [c.model_dump() for c in report.checks]}))
            run.console.gate_result(gate.__name__, report)
            violations.extend(found)
        if not violations:
            break
        if gate_attempt > phase.params.retries:
            raise GateFailure(f"{agent.name} failed gates after {gate_attempt} attempt(s):\n- "
                              + "\n- ".join(violations))
        phase.attempt = gate_attempt
        run.console.retry(agent.name, gate_attempt, phase.params.retries,
                          f"{len(violations)} gate violation(s)")
        correction = ("Your previous response failed validation:\n- "
                      + "\n- ".join(violations)
                      + "\n\nFix these problems, then re-emit ONLY your Report JSON.")
        result = send(correction)
        envelope, attempt = _parse_with_retries(run, phase, call, result, send)

    # Permission is checked after every send is done, and before the envelope is
    # accepted: an agent does not get to report success on a phase in which it
    # wrote somewhere it was not allowed to.
    try:
        touched = permissions.enforce(run, phase, agent, tree_before)
    except permissions.PermissionBreach as breach:
        run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                     type="error", name="permission_breach",
                                     payload={"agent": agent.name, "error": str(breach),
                                              "writes": agent.writes,
                                              "protected_files": run.cfg.defaults.protected_files}))
        raise
    if touched:
        run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                     type="log", name="paths_touched",
                                     payload={"agent": agent.name, "paths": touched}))

    _persist_envelope(run, phase, agent.name, call, envelope, attempt, valid=True)
    run.console.envelope_summary(envelope)
    context = latest or result
    run.tracer.agent_session_row(run.adw_id, agent, session.session_id,
                                 context_tokens=context.context_tokens,
                                 context_window=context.context_window)
    _remember(run, agent, session)
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                 type="handoff", name=agent.name,
                                 payload={"artifacts": envelope.artifacts,
                                          "summary": envelope.summary}))
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                 type="agent_end", name=agent.name,
                                 # Phase totals, not the last send's: a retried
                                 # phase paid for every attempt.
                                 tokens=spent.total_tokens,
                                 payload={"cost": spent.total_cost,
                                          "usage": spent.model_dump(),
                                          "context_tokens": context.context_tokens,
                                          "context_window": context.context_window}))
    run.console.agent_finished(agent.name, spent.total_tokens, spent.total_cost)
    if envelope.status != "success":
        raise RuntimeError(f"{agent.name} reported status={envelope.status!r}: {envelope.summary}")
    return envelope


# ── internals ────────────────────────────────────────────────────────────────

def _as_report(result) -> GateReport:
    """Accept a GateReport, or a legacy gate that returned a violations list."""
    if isinstance(result, GateReport):
        return result
    return GateReport(checks=[GateCheck(item=str(v), ok=False) for v in (result or [])])


def _agent_session(run, agent: AgentConfig, driver) -> AgentSession:
    """This agent's context window in this run: rejoined, or freshly minted.

    The pre-existing rule is that a session is reused only while the MODEL is
    unchanged — a context window built by one model is not one another model
    should inherit. The backend is now part of that identity for the same
    reason, and more bluntly: a pi session id is not a UUID and Claude Code
    would refuse it outright.
    """
    entry = run.agent_map.get(agent.name) or {}
    if entry.get("model") == agent.model and \
            entry.get("coding_agent", "pi") == agent.coding_agent:
        return AgentSession(session_id=entry["session_id"],
                            native_session_id=entry.get("native_session_id", ""),
                            started=bool(entry.get("started")))
    session_id = driver.new_session_id(run.adw_id, agent)
    return AgentSession(session_id=session_id, native_session_id=session_id)


def _remember(run, agent: AgentConfig, session: AgentSession) -> None:
    """Write this agent's session state into the run's agent map."""
    run.save_agent_map(agent.name, {"session_id": session.session_id,
                                    "model": agent.model,
                                    "coding_agent": agent.coding_agent,
                                    "native_session_id": session.native_session_id,
                                    "started": session.started})


def _event_forwarder(run, phase: Phase, agent_name: str, driver):
    """One tool_call event per real tool call, with its exact args and result.

    The tracker comes from the backend; the record shape does not (it is
    tool_calls.py's, identical for both), which is what keeps the tracer, the
    trace schema and the visualizer out of this phase entirely.
    """
    tracker = driver.ToolCallTracker()

    def forward(event: dict) -> None:
        for record in tracker.observe(event):
            # The call's span rides the columns; duration_ms stays in the
            # payload as the coding agent's own authoritative number.
            run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase.phase_id,
                                         type="tool_call", name=record.pop("label"),
                                         started_at=record.pop("started_at", None),
                                         ended_at=record.pop("ended_at", None),
                                         payload={**record, "agent": agent_name}))
    return forward


def _extract_json(text: str) -> dict:
    candidate = text
    if "```" in text:
        for block in text.split("```")[1::2]:
            block = block.removeprefix("json").strip()
            if block.startswith("{"):
                candidate = block
                break
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the response")
    return json.loads(candidate[start:end + 1])


def _parse_with_retries(run, phase: Phase, call: AgentCall, result, send):
    """Parse the final response against the declared output type; on failure,
    continue the SAME session with a correction (bounded)."""
    for attempt in range(1, JSON_FIX_ATTEMPTS + 2):
        try:
            payload = _extract_json(result.text)
            return call.output_type.model_validate(payload), attempt
        except Exception as error:
            _persist_envelope(run, phase, phase.params.owner, call, None, attempt,
                              valid=False, raw=result.text)
            if attempt > JSON_FIX_ATTEMPTS:
                raise RuntimeError(
                    f"{phase.params.owner} never produced valid "
                    f"{call.output_type.__name__} JSON: {error}") from error
            run.console.retry(phase.params.owner, attempt, JSON_FIX_ATTEMPTS,
                              f"invalid {call.output_type.__name__} JSON: {error}")
            fields = ", ".join(call.output_type.model_fields.keys())
            result = send(
                f"Your response was not valid JSON for the required structure "
                f"({error}). Respond again with ONLY a JSON object with these "
                f"fields: {fields}. No prose, no code fences.")


def _persist_envelope(run, phase: Phase, agent_name: str, call: AgentCall,
                      envelope: Optional[EnvelopeBase], attempt: int,
                      valid: bool, raw: str = "") -> None:
    payload_json = envelope.model_dump_json(indent=2) if envelope else json.dumps({"raw": raw[-2000:]})
    run.tracer.envelope_row(phase, agent_name, call.output_type.__name__,
                            payload_json, valid, attempt)
    if envelope:
        record = {"agent_name": agent_name, "purpose": resolve(run.cfg, agent_name).purpose,
                  "output_type": call.output_type.__name__, "attempt": attempt,
                  **envelope.model_dump()}
        (run.session_dir / agent_name / "envelope.json").write_text(json.dumps(record, indent=2))


def load_envelope(run, agent_name: str, output_type: type[EnvelopeBase]) -> EnvelopeBase:
    """Reload an agent's persisted envelope as a typed object — the read side
    of `_persist_envelope`, for an ADW that resumes work an EARLIER run already
    produced under the same `--adw-id`, rather than calling that agent again.

    Ignores the four bookkeeping keys `_persist_envelope` writes alongside the
    envelope's own fields (`agent_name`, `purpose`, `output_type`, `attempt`) —
    pydantic drops unknown fields on `model_validate` by default.
    """
    path = run.session_dir / agent_name / "envelope.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no envelope for {agent_name!r} in session {run.adw_id} ({path}) — "
            f"it has not produced one in this session yet")
    return output_type.model_validate(json.loads(path.read_text()))
