"""Concrete data types for the SSSF ADW system.

RULE (four-param rule): any function that takes more than 4 parameters takes
ONE of these objects instead. AgentCall and PhaseParams are the pattern.

Every agent call declares a concrete output type — an EnvelopeBase subclass —
that its final JSON response is parsed against. No untyped handoffs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Optional, Type

from pydantic import BaseModel, Field, ValidationInfo, field_validator

PhaseKind = Literal["engineer", "agent", "code"]
PhaseStatus = Literal["queued", "running", "success", "fail"]


# ── Phases ────────────────────────────────────────────────────────────────────

class PhaseParams(BaseModel):
    """Everything run.phase() needs. Passed as one object, never loose params."""

    name: str                       # short id, unique within the run: "plan", "build"
    kind: PhaseKind                 # which lane the block renders in
    owner: str                      # engineer's name, "git", or an agent name from config
    description: str                # REQUIRED: what this phase does and why — see below
    retries: int = 0                # agent phases: gate-failure retries via continue

    @field_validator("description")
    @classmethod
    def _description_must_be_earned(cls, value: str, info: ValidationInfo) -> str:
        """A phase name identifies; a description explains. Both are required.

        The description is the only sentence the trace, the console, and the
        phase block in the UI ever show about intent — everything else is ids,
        statuses, and timings. `commit_plan: "Commit the plan"` tells a reader
        nothing they could not already see, so an echo is rejected the same way
        a blank one is. This is a construction-time error on purpose: it fires
        before the phase opens, not after a run is already in the trace.
        """
        text = " ".join(value.split())
        name = str(info.data.get("name", "?"))
        if not text:
            raise ValueError(
                f"phase {name!r}: description is required — one sentence on what this "
                f"phase does and why. It is what the trace and the UI show.")
        if text.rstrip(".").casefold() == name.replace("_", " ").casefold():
            raise ValueError(
                f"phase {name!r}: description {text!r} only restates the phase name — "
                f"say what it does and why instead.")
        return text


class Phase(BaseModel):
    """The persisted phase record — PhaseParams plus lifecycle."""

    phase_id: str
    adw_id: str
    seq: int
    params: PhaseParams
    status: PhaseStatus = "fail"    # success must be earned
    attempt: int = 0
    error: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


# ── Envelopes (agent output types) ───────────────────────────────────────────

class EnvelopeBase(BaseModel):
    """Base of every agent's final JSON response. Output types extend this."""

    status: Literal["success", "fail"]
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    notes_for_next_agent: str = ""


class GenericOutput(EnvelopeBase):
    pass


class PlanOutput(EnvelopeBase):
    # Subject for committing the PLAN — the spec file the planner wrote, not the
    # implementation it describes. Each agent's commit_message covers its own
    # work product, so a chain that commits per step never reuses one agent's
    # words for another agent's diff.
    commit_message: str = ""


class BuildOutput(EnvelopeBase):
    changed_files: list[str] = Field(default_factory=list)
    commit_message: str = ""        # consumed by the git commit phase


class ScoutFinding(BaseModel):
    file: str
    note: str = ""


class ScoutOutput(EnvelopeBase):
    findings: list[ScoutFinding] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    """One thing the request (or plan) asked for, and whether it is there."""

    requirement: str                # the ask, in the requester's words
    met: bool
    evidence: str = ""              # where it lives, or what is missing


class ReviewOutput(EnvelopeBase):
    """Confirmation that what was built is what was asked for — not a test run."""

    approved: bool = False
    findings: list[ReviewFinding] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)   # what must change before approval


class DocumentOutput(EnvelopeBase):
    """Where the write-up of a completed change landed."""

    document_path: str = ""         # the doc in the repo, e.g. app_docs/<adw_id>_<slug>.md
    documented_files: list[str] = Field(default_factory=list)
    commit_message: str = ""


# ── Deterministic quality blocks ─────────────────────────────────────────────

QualityArea = Literal["frontend", "backend"]
QualityOperation = Literal["lint", "typecheck", "build"]


class QualityCheckSpec(BaseModel):
    """One deterministic quality command."""

    name: str
    area: QualityArea
    operation: QualityOperation
    argv: list[str]
    timeout_seconds: int = 120


class QualityCheckResult(BaseModel):
    """Captured evidence from one quality command."""

    name: str
    area: QualityArea
    operation: QualityOperation
    command: str
    returncode: int
    passed: bool
    duration_seconds: float
    output_artifact: str
    # The tail of stdout+stderr, verbatim and unparsed. A failure has to travel
    # back to the builder as an envelope, and the builder cannot open a log file
    # it was never handed — so the evidence rides along. Deliberately raw: every
    # runner formats failures differently and a generic parser would be
    # confidently wrong. The full log is always at output_artifact.
    output_tail: str = ""


class QualityResult(BaseModel):
    """Aggregate result from a quality block: every check it ran, and the verdict."""

    passed: bool
    checks: list[QualityCheckResult] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


# ── Change capture (git diff, deterministic) ─────────────────────────────────

class ChangeCapture(BaseModel):
    """Everything documentation.capture() needs. One object, never loose params."""

    base: str = "main"              # the ref the work is measured against
    max_diff_lines: int = 2000      # the diff artifact is truncated past this
    include_untracked: bool = True  # a brand-new file is part of the change


class BaseRef(BaseModel):
    """The commit a change is measured from, and why that one.

    `reason` is the line the trace shows. A diff is only as trustworthy as the
    thing it was taken against, so the ADW records that choice instead of
    leaving the reader to infer it.
    """

    ref: str                        # what was asked for: "main", or a pinned sha
    commit: str                     # the commit actually diffed against
    reason: str = ""

    @property
    def label(self) -> str:
        """Display form — a named ref as itself, a pinned raw sha shortened."""
        if len(self.ref) == 40 and all(c in "0123456789abcdef" for c in self.ref):
            return self.ref[:7]
        return self.ref


class ChangeSet(BaseModel):
    """What changed since the base commit — pure git facts, no judgement."""

    base: BaseRef
    files: list[str] = Field(default_factory=list)
    untracked: list[str] = Field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    stat: str = ""                  # `git diff --stat` output, verbatim
    diff_path: str = ""             # the full diff, written into context_handoff/
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not (self.files or self.untracked)


class ChangesOutput(EnvelopeBase):
    """A ChangeSet shaped as an envelope so an agent can be handed it directly.

    Same adapter idea as VerifyOutput: code computes the diff, the documenter
    consumes it through the one door every agent handoff uses.
    """

    base: str = ""                  # "<ref> @ <commit> — <reason>"
    changed_files: list[str] = Field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    stat: str = ""
    diff_path: str = ""             # read this for the full diff


class VerifyOutput(EnvelopeBase):
    """A deterministic result, shaped as an envelope so an agent can consume it.

    Agents hand each other typed envelopes; code blocks return QualityResult.
    This is the adapter, so a failing lint or test run flows back into the
    builder through exactly the same door a tester agent's report used to —
    the ADW script is the only thing that knows the difference.
    """

    passed: bool = False
    failures: list[str] = Field(default_factory=list)


class IssueOutput(EnvelopeBase):
    """A tracked work item, shaped as an envelope so the planner can consume it.

    Same adapter idea as VerifyOutput and ChangesOutput: code fetches the issue,
    the planner receives it through the one door every agent handoff uses.

    The BODY IS NOT A FIELD, and that is deliberate twice over. Envelopes are
    persisted whole into `envelopes.payload_json`, and an issue body can be a
    screenshot-laden novel. More importantly, a body that arrives as a path in
    `artifacts` is visibly MATERIAL THE AGENT READS rather than INSTRUCTIONS THE
    AGENT RECEIVED — the reporter is not the operator, and the framing is the
    cheapest part of keeping that true. `issues.as_envelope` says so out loud in
    `notes_for_next_agent`.
    """

    number: int = 0
    url: str = ""
    title: str = ""
    labels: list[str] = Field(default_factory=list)
    author: str = ""


# ── Agent calls ──────────────────────────────────────────────────────────────

class GateCheck(BaseModel):
    """One thing a gate looked at, and what it found.

    `note` is the evidence — "exists, 2.1KB", "exit 0", "not in the diff". On a
    failed check it doubles as the reason, so it is what the agent is told.
    """

    item: str                       # what was checked: a path, a command, a test
    ok: bool
    note: str = ""


class GateReport(BaseModel):
    """What every gate returns: the checks it ran. Violations are derived.

    Authoring stays a one-liner per item — `report.check(...)` appends and
    returns self, so a gate is a loop and a return.
    """

    checks: list[GateCheck] = Field(default_factory=list)

    def check(self, item: str, ok: bool, note: str = "") -> "GateReport":
        self.checks.append(GateCheck(item=item, ok=ok, note=note))
        return self

    @property
    def violations(self) -> list[str]:
        return [f"{c.item}: {c.note or 'failed'}" for c in self.checks if not c.ok]

    @property
    def passed(self) -> bool:
        return not self.violations


class AgentCall(BaseModel):
    """One agent invocation: prompt in, typed envelope out, gates verified."""

    model_config = {"arbitrary_types_allowed": True}

    output_type: Type[EnvelopeBase]
    prompt: str
    previous: Optional[EnvelopeBase] = None
    gates: list[Callable] = Field(default_factory=list)   # gate(envelope, run) -> list[str]


# ── Config ───────────────────────────────────────────────────────────────────

class PromptEngineering(BaseModel):
    system: str                     # path to system.md
    user: str                       # path to user.md


class ClaudeCodeConfig(BaseModel):
    """Determinism and permission settings for `coding_agent: claude_code`.

    A default `claude -p` discovers whatever the operator has lying around —
    CLAUDE.md, skills, plugins, hooks, MCP servers — which makes a run depend
    on whose machine it executed on. That is the exact failure the factory
    exists to remove, so the defaults below pin it off. They are configuration
    rather than code because some repositories genuinely do want their own
    CLAUDE.md loaded, and that is their decision to make.
    """

    # --safe-mode: no CLAUDE.md, skills, plugins, hooks, MCP servers, custom
    # agents or commands. Auth, model selection, built-in tools and permissions
    # keep working, so a Claude subscription still authenticates.
    safe_mode: bool = True
    # --bare is stricter still (it also skips LSP and background prefetches),
    # but it forces ANTHROPIC_API_KEY / apiKeyHelper auth and never reads OAuth
    # or the keychain — so turning it on takes a subscription-authenticated
    # roster offline. Off by default for that reason; safe_mode covers the
    # determinism half without the auth cost.
    bare: bool = False
    setting_sources: list[str] = Field(default_factory=list)   # user | project | local
    strict_mcp_config: bool = True                             # only MCP servers we pass
    # A non-interactive run has to answer its own permission prompts. This is
    # only acceptable because two other things are true: permissions.py
    # fingerprints the tree before the call and rolls back every write outside
    # the agent's `writes:` allowlist afterwards, and the run happens in its own
    # worktree. The factory is not careless here; it verifies after the fact.
    # `bypassPermissions` is refused by the CLI when running as root — use
    # `acceptEdits` there, and know that it silently denies whatever it would
    # otherwise have prompted for.
    permission_mode: str = "bypassPermissions"
    add_dirs: list[str] = Field(default_factory=list)          # --add-dir, beyond cwd
    max_budget_usd: float = 0.0                                # 0 = no ceiling


class AgentConfig(BaseModel):
    name: str
    coding_agent: Literal["pi", "claude_code"] = "pi"
    model: str = "google/gemini-3.6-flash"
    thinking: str = "medium"        # off | minimal | low | medium | high | xhigh | max
    color: str = ""                 # hex swatch for this agent's lane in the UI
    purpose: str = ""
    prompt_engineering: PromptEngineering
    harness_engineering: list[str] = Field(default_factory=list)
    tools: Optional[list[str]] = None    # allowlist; None = all tools usable
    # What this agent may MODIFY in the repo, enforced in code after every call
    # (see adw_modules/permissions.py). `tools` cannot express this: `bash` runs
    # anything and `write` reaches any path, so an agent's capability list is a
    # statement of intent that nothing checks.
    #   None  -> unrestricted, except the roster-wide `protected_files` paths
    #   []    -> read-only: may modify nothing tracked
    #   [...] -> only these. A trailing "/" means a directory prefix; a "*"
    #            makes it a glob; anything else is an exact path.
    writes: Optional[list[str]] = None
    # Backend-specific knobs. Inert for `coding_agent: pi`.
    claude_code: ClaudeCodeConfig = Field(default_factory=ClaudeCodeConfig)


class ConfigDefaults(BaseModel):
    coding_agent: Literal["pi", "claude_code"] = "pi"
    model: str = "google/gemini-3.6-flash"
    thinking: str = "medium"
    color: str = ""
    harness_engineering: list[str] = Field(default_factory=list)
    tools: Optional[list[str]] = None    # roster-wide allowlist; None = all tools usable
    claude_code: ClaudeCodeConfig = Field(default_factory=ClaudeCodeConfig)
    # Off-limits to every agent that has not named them in its own `writes`.
    # The factory's own code is the default: an agent must not be able to edit
    # the machinery that decides whether its work passed.
    protected_files: list[str] = Field(default_factory=lambda: [
        "adws/adw_modules/", "adws/adw_sssf_config/", "adws/adw_*.py",
    ])
    data_dir: str = "adws/adw_data"


IntegrationMode = Literal["none", "merge", "pr"]


class IntegrationConfig(BaseModel):
    """How a run's branch gets back to the base branch. Convention, not code.

    Repositories disagree about merge vs. rebase, about who is allowed to move
    the base branch, and about whether a machine may do it at all — so this is
    configuration. The integration phase reads it; nothing in it is hard-coded.
    """

    mode: IntegrationMode = "merge"
    merge_flags: list[str] = Field(default_factory=lambda: ["--no-ff"])
    remote: str = "origin"                       # mode: pr — where the branch is pushed
    open_pr: bool = False                        # mode: pr — also run pr_command
    # Left as a command rather than an API call: whichever forge CLI the repo
    # uses is already authenticated in the engineer's shell, and the phase runs
    # under operator_env() so it resolves exactly as it does in their terminal.
    pr_command: list[str] = Field(default_factory=lambda: ["gh", "pr", "create", "--fill"])
    # Rendered with {adw_id}, {branch}, {base_ref} and — for an issue-triggered
    # run — {issue_number} and {issue_url}, then passed as --body. Left empty,
    # nothing is passed and pr_command decides on its own (`--fill` does).
    # NOTE: `--fill` and an explicit body are mutually exclusive in gh; a repo
    # that sets a template here drops --fill from pr_command.
    pr_body_template: str = ""


class WorktreeConfig(BaseModel):
    """One git worktree and one branch per run.

    A run that executes in the engineer's working tree cannot be concurrent, is
    destructive when it goes wrong, and commits whatever else was lying around.
    Isolation — not a sandbox: an agent with bash can still leave the worktree,
    which is what permissions.py is for.
    """

    enabled: bool = True
    dir: str = ".sssf-worktrees"     # relative to the MAIN checkout; gitignored
    branch_prefix: str = "sssf/"     # the run's branch is <prefix><adw_id>
    base_ref: str = ""               # "" = whatever the main checkout has checked out
    # A successful run's worktree is a redundant copy of a branch, so it goes.
    # A failed or killed one is where you go to see what happened, so it stays —
    # and so does any worktree with uncommitted work in it, whatever the outcome.
    keep_on_success: bool = False
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)


class ObservabilityConfig(BaseModel):
    db: str = "adws/adw_data/sssf.db"
    poll_ms: int = 500


class IssueStates(BaseModel):
    """The label state machine. The flip from `queued` IS the lock.

    No queue and no state file: the watcher claims an issue by moving its label,
    which is atomic at the forge and visible to humans in the place they already
    look. Two watchers racing the same issue means one of them loses the flip.
    """

    queued: str = "sssf:queued"
    running: str = "sssf:running"
    done: str = "sssf:done"
    failed: str = "sssf:failed"


class IssuesConfig(BaseModel):
    """Where work items come from, and which chain each label asks for.

    Commands rather than API calls, for the reason `pr_command` already gives:
    whichever forge CLI the repo uses is installed and authenticated in the
    engineer's shell already, and everything here runs under operator_env().
    """

    enabled: bool = False
    # WHICH REPO the watcher watches. Empty resolves ONCE at startup from the
    # origin remote of the main checkout — never left to each command's cwd,
    # because cron has an arbitrary working directory and a watcher that
    # silently polls the wrong project looks exactly like one with nothing to
    # do. A tracker that is not the forge has no remote to infer from and must
    # set this. It is the same normalised identity the trace records.
    project: str = ""
    fetch_command: list[str] = Field(default_factory=lambda: ["gh", "issue", "view"])
    list_command: list[str] = Field(default_factory=lambda: ["gh", "issue", "list"])
    comment_command: list[str] = Field(default_factory=lambda: ["gh", "issue", "comment"])
    state_command: list[str] = Field(default_factory=lambda: ["gh", "issue", "edit"])
    # label -> ADW script. The watcher routes on this; no ADW knows about it.
    route: dict[str, str] = Field(default_factory=dict)
    states: IssueStates = Field(default_factory=IssueStates)
    # Empty = every issue author is accepted, and the human who applied the
    # routing label is the only authorization. Narrow it where anyone can label.
    trusted_authors: list[str] = Field(default_factory=list)
    max_concurrent: int = 2
    # An issue-triggered run must not be able to move the base branch. Enforced
    # in integration.integrate(), not left to whoever edits the config.
    force_pr: bool = True


class SSSFConfig(BaseModel):
    defaults: ConfigDefaults = Field(default_factory=ConfigDefaults)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    issues: IssuesConfig = Field(default_factory=IssuesConfig)
    agents: list[AgentConfig] = Field(default_factory=list)


# ── Workspace (where a run works, and where its record lives) ────────────────

class Workspace(BaseModel):
    """The two roots a run has, kept apart on purpose.

    `repo_root` is the tree the agents are spawned in, the gates measure, the
    permission snapshot fingerprints and the commit phases commit. `main_root`
    is the engineer's checkout, which a run must never modify — but which owns
    the one thing that has to outlive the run: `data_dir`, and with it the trace
    db, the session dir and context_handoff/. A worktree is pruned; the trace of
    what happened in it is not.

    Without a worktree (disabled, or not a git repo) both point at the same
    directory and every path below behaves exactly as it did before.
    """

    main_root: Path
    repo_root: Path
    enabled: bool = False           # False = running directly in the main checkout
    branch: str = ""                # sssf/<adw_id>
    base_ref: str = ""              # what it was cut from, as asked for
    base_commit: str = ""           # ...pinned to a sha at creation
    created: bool = False           # False = re-attached to a worktree that existed

    @property
    def joined(self) -> bool:
        """True when this run re-attached to a worktree an earlier ADW created."""
        return self.enabled and not self.created


class WorktreeRequest(BaseModel):
    """Everything worktree.ensure() needs. One object, never loose params."""

    main_root: Path
    adw_id: str
    config: WorktreeConfig = Field(default_factory=WorktreeConfig)


class WorktreeInfo(BaseModel):
    """One worktree on disk, and whether anything still needs it.

    A killed run leaves its worktree behind deliberately, so "left behind" and
    "orphaned" are not the same thing — the session status is what tells them
    apart, and it lives in the trace db, not in git.
    """

    path: str
    branch: str = ""
    adw_id: str = ""
    dirty: bool = False
    status: str = "unknown"         # the run's session status, from the trace db
    prunable: bool = False          # git says the directory is gone


class RunSpec(BaseModel):
    """Everything the Run object is built from, minus the tracer it writes to."""

    model_config = {"arbitrary_types_allowed": True}

    cfg: SSSFConfig
    adw_id: str
    engineer: str
    workspace: Workspace


# ── Integration (landing a run's branch) ─────────────────────────────────────

class IntegrationRequest(BaseModel):
    """One integration attempt. `mode` empty = whatever the config says."""

    mode: str = ""
    message: str = ""               # merge commit subject; defaults to the branch
    title: str = ""                 # PR title, when the forge CLI takes one
    body: str = ""                  # PR body; overrides config.pr_body_template


class IntegrationResult(BaseModel):
    """What integration actually did — a code phase's evidence, not a claim."""

    mode: IntegrationMode = "none"
    ok: bool = False
    branch: str = ""
    base_ref: str = ""
    head: str = ""                  # the branch tip that was landed or pushed
    merged_into: str = ""
    pushed: bool = False
    pr_url: str = ""
    notes: list[str] = Field(default_factory=list)


# ── Issues (a tracked work item as a run's entry point) ──────────────────────

class IssueRef(BaseModel):
    """Which work item, in which project. `project` empty = whatever config says."""

    number: int
    project: str = ""


class IssueContext(BaseModel):
    """One fetched issue. What the forge said, plus where the body was written.

    `body_path` rather than the body itself for the same reason IssueOutput has
    no body field — see that type. Nothing downstream reads `body` off this
    object; the agent opens the file.
    """

    number: int
    project: str = ""
    url: str = ""
    title: str = ""
    labels: list[str] = Field(default_factory=list)
    author: str = ""
    state: str = ""
    body_path: str = ""             # written into context_handoff/


class IssueUpdate(BaseModel):
    """One write back to the tracker: a comment, a label move, or both."""

    number: int
    project: str = ""
    comment: str = ""
    add_labels: list[str] = Field(default_factory=list)
    remove_labels: list[str] = Field(default_factory=list)


class IssueResult(BaseModel):
    """What a tracker write actually did — evidence, never a claim.

    A failed write-back is NOT a failed run. The work is committed and the
    branch is kept either way; the tracker just did not hear about it, which is
    a thing a human can finish by hand. So this carries `ok` and notes rather
    than raising, exactly like IntegrationResult.
    """

    ok: bool = False
    number: int = 0
    commented: bool = False
    labels_changed: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ── Tracing ──────────────────────────────────────────────────────────────────

class EventRecord(BaseModel):
    """One traced event, always logged against adw_id + phase."""

    adw_id: str
    phase_id: str = ""
    type: str                       # phase_start | agent_start | tool_call | handoff | gate_pass | gate_fail | log | agent_end | phase_end | error
    name: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    parent_id: str = ""
    tokens: Optional[int] = None
    # Spans: set both when an event covers real elapsed time (a tool call), so
    # the UI lays it out on a time axis without parsing payload JSON. Left unset,
    # the tracer stamps started_at with the moment the event was recorded.
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


# ── Coding agent interface (one shape, every backend) ────────────────────────

class AgentRequest(BaseModel):
    """Everything one non-interactive coding-agent turn needs, on any backend.

    The four-param rule already forced a request object, so adding a second
    backend is a couple of fields rather than a second signature. Fields a
    backend does not use are inert, never an error: pi ignores `resume`, and
    Claude Code ignores `extensions` (it validates `harness_engineering` its
    own way — see agent_cc).
    """

    prompt: str
    system_prompt: str
    model: str                      # pi: a registry pattern. claude_code: an alias or model id
    thinking: str = "medium"
    session_id: str                 # the FACTORY's id for this agent's context window
    session_dir: str                # the backend's own session store, if it keeps one
    raw_output_path: str            # JSONL stream lands here
    # The run's session runtime — data_dir/sessions/<adw_id> — which lives in
    # the MAIN checkout, OUTSIDE the worktree the agent is spawned in. It holds
    # context_handoff/, the prompt copies and this agent's envelope, so every
    # agent must be able to write it whatever its `writes:` says. A backend that
    # confines file tools to the working directory has to be told about it.
    runtime_dir: str = ""
    tools: Optional[list[str]] = None
    extensions: list[str] = Field(default_factory=list)
    cwd: str = "."                  # set from run.repo_root — the codebase root agents work in
    # Create-vs-resume. pi's --session-id is create-or-continue, so pi needs
    # neither field; Claude Code's is create-ONLY and errors on a second use,
    # so it needs both — the UUID it knows the session by, and whether that
    # session already exists.
    native_session_id: str = ""     # "" = the backend uses session_id as-is
    resume: bool = False
    claude_code: ClaudeCodeConfig = Field(default_factory=ClaudeCodeConfig)


PiRequest = AgentRequest            # transitional alias; prefer AgentRequest


class AgentSession(BaseModel):
    """One agent's context window within a run, as the agent map records it.

    `session_id` is the factory's name for it and never changes; the two extra
    fields exist because Claude Code's `--session-id` is create-only. The first
    call creates, every later one resumes, and `started` is the flag that says
    which — so it has to survive the process, not just the phase.
    """

    session_id: str
    native_session_id: str = ""     # what the backend calls it; pi echoes session_id back
    started: bool = False           # the session EXISTS — resume it, do not create it


class UsageBreakdown(BaseModel):
    """Tokens and the dollars they cost, per component, summed over a call.

    Mirrors pi's `usage` shape one-for-one so the numbers reconcile with what
    pi itself reports: `input` EXCLUDES cache reads, which bill at their own
    (cheaper) rate — add them to learn the size of the prompt that was sent.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Thinking tokens. NOT a fifth component: measured across every session on
    # disk, reasoning is always <= output and the four components above always
    # sum to totalTokens, so reasoning is the thinking SHARE of output, billed
    # at the output rate. Report it nested under output, never added to it.
    reasoning_tokens: int = 0
    total_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0
    total_cost: float = 0.0

    # No `add_turn` here on purpose. Each backend reports usage in its own
    # vocabulary — pi says `cacheRead`, Claude Code says
    # `cache_read_input_tokens`, and only pi breaks the cost down per component
    # — so each backend owns an adapter that BUILDS one of these and merges it
    # (`agent_pi._turn_usage`, `agent_cc._result_usage`). One function taught
    # two vocabularies is how a silently-zero column happens.

    def merge(self, other: "UsageBreakdown") -> None:
        """Add another call's usage — a phase that retries spends more than once."""
        for field in self.model_fields:
            setattr(self, field, getattr(self, field) + getattr(other, field))


class AgentResult(BaseModel):
    """What one coding-agent turn produced. Identical across backends.

    `session_id` is what the backend says the session was — pi echoes back the
    id it was handed, Claude Code reports the one it created or resumed, and
    `agents.execute` writes it into the agent map either way.
    """

    text: str = ""
    returncode: int = 0
    session_id: str = ""
    tokens: int = 0
    cost: float = 0.0
    usage: UsageBreakdown = Field(default_factory=UsageBreakdown)
    # Context occupancy after the LAST turn — not a sum. `tokens` bills every
    # turn; this is how full the window is right now, which is what the
    # visualizer's context bar measures against `context_window`.
    context_tokens: int = 0
    context_window: int = 0         # 0 when the registry declares no ceiling


PiResult = AgentResult              # transitional alias; prefer AgentResult
