"""The stage contract: the closed vocabulary a workflow.yaml composes from.

A workflow is a list of stages. A stage is a Python module under `asf/stages/
<name>/stage.py` that owns everything a chain's author used to write by hand
between two agent calls — the fix loop, the retest condition, the commit that
lands only after the checks came back green. The YAML gets numbers and names;
it never gets a loop or a branch. That split is the whole design: what a
workflow may vary is policy, and what it may not vary is the part that decides
whether the work passed.

A stage module exposes, at module level:

    NAME       the word workflow.yaml uses (must equal the directory name)
    KIND       "agent" | "code" — for the lane, and for what `check` may assume
    OUTPUT     the envelope type this stage hands on, or None to pass the
               previous one through unchanged (a commit changes nothing)
    NEEDS      envelope types an earlier stage must have produced, as a tuple;
               () when the stage can start from the prompt alone
    TASKS      {key: filename} — the task files this stage renders for agents,
               each resolvable by a workflow-local override `tasks/<key>.md`
    Options    a pydantic model of the options workflow.yaml may pass, with
               extra="forbid" so a misspelt key is refused before anything runs
    run        run(ctx: StageContext, opts: Options) -> EnvelopeBase | None
    check      optional: check(opts, earlier) -> list[str] of static problems,
               where `earlier` maps every preceding stage's name to its OUTPUT

`engine.workflow.load` verifies all of that at load time, and refuses a
workflow before a session, a branch or a process record exists.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from pydantic import BaseModel

from .data_types import EnvelopeBase

REQUIRED = ("NAME", "KIND", "OUTPUT", "NEEDS", "TASKS", "Options", "run")
KINDS = ("agent", "code")


class StageStop(Exception):
    """A stage that declines to hand on.

    Not a failure of the stage — a verify stage whose fix loop ran out did
    its job — but the run must not be accepted, and nothing after this stage
    may run: a commit after an exhausted fix loop would land red code. The
    runner catches it, finishes the run as not accepted with this reason, and
    keeps the worktree the way any other unaccepted run does.
    """


@dataclass
class StageModule:
    """One entry of the vocabulary, loaded from its directory."""

    name: str
    directory: Path
    module: ModuleType
    kind: str
    output: Optional[type[EnvelopeBase]]
    needs: tuple[type[EnvelopeBase], ...]
    tasks: dict[str, str]
    options: type[BaseModel]

    def run(self, ctx: "StageContext", opts: BaseModel) -> Optional[EnvelopeBase]:
        return self.module.run(ctx, opts)

    def check(self, opts: BaseModel, earlier: dict[str, Optional[type]]) -> list[str]:
        checker = getattr(self.module, "check", None)
        return list(checker(opts, earlier)) if checker else []


@dataclass
class Step:
    """A stage as one workflow uses it: the module, its parsed options, and the
    task files resolved for it — workflow override first, stage default second."""

    stage: StageModule
    opts: BaseModel
    tasks: dict[str, str] = field(default_factory=dict)   # key -> repo-relative path


class StageContext:
    """What a running stage sees. Built once per run, advanced per step."""

    def __init__(self, run, workflow, prompt: str):
        self.run = run
        self.workflow = workflow
        self.prompt = prompt
        self.previous: Optional[EnvelopeBase] = None
        self.results: dict[str, EnvelopeBase] = {}          # by stage name: what it produced
        self.latest: dict[type, EnvelopeBase] = {}          # by type: the work product as it stands
        self.step: Optional[Step] = None

    def begin(self, step: Step) -> None:
        self.step = step

    def end(self, step: Step, output: Optional[EnvelopeBase]) -> None:
        if output is not None:
            self.results[step.stage.name] = output
            self.latest[type(output)] = output
            self.previous = output
        self.step = None

    def current(self, stage_name: str) -> EnvelopeBase:
        """The work product stage `stage_name` produced, AS IT STANDS NOW.

        A verify stage hands on a BuildOutput too — the build after its fix
        loop — so `commit: {of: build}` must land that one, not the envelope
        the build stage wrote before the checks ran. The lookup is therefore
        by the named stage's output TYPE, latest wins; `results` keeps what
        each stage itself said, for anyone who wants the history.
        """
        for step in self.workflow.steps:
            if step.stage.name == stage_name and step.stage.output is not None:
                return self.latest[step.stage.output]
        raise RuntimeError(f"no stage {stage_name!r} with an output precedes this one")

    def task(self, key: str) -> str:
        """The task file resolved for this step, as a path the agent call renders."""
        assert self.step is not None, "task() outside a running stage"
        try:
            return self.step.tasks[key]
        except KeyError:
            raise RuntimeError(f"stage {self.step.stage.name!r} declares no task "
                               f"{key!r} — its TASKS are {sorted(self.step.tasks)}") from None


def load_registry(stages_dir: Path) -> dict[str, StageModule]:
    """Every stage under `stages_dir`, checked against the contract above."""
    if not stages_dir.is_dir():
        raise SystemExit(f"no stages directory at {stages_dir} — is the factory installed?")
    registry: dict[str, StageModule] = {}
    for directory in sorted(p for p in stages_dir.iterdir() if p.is_dir()):
        source = directory / "stage.py"
        if not source.is_file():
            continue
        registry[directory.name] = _load_one(directory, source)
    if not registry:
        raise SystemExit(f"{stages_dir} holds no stage — every stage is a directory "
                         f"with a stage.py in it")
    return registry


def _load_one(directory: Path, source: Path) -> StageModule:
    # Registered in sys.modules BEFORE it executes: pydantic resolves a
    # model's forward references (`fix: Fix` under `from __future__ import
    # annotations`) through the module's name, and a module that is not there
    # leaves every Options class "not fully defined".
    qualified = f"asf_stage_{directory.name}"
    spec = importlib.util.spec_from_file_location(qualified, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)             # type: ignore[union-attr]
    missing = [name for name in REQUIRED if not hasattr(module, name)]
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    else:
        if module.NAME != directory.name:
            problems.append(f"NAME is {module.NAME!r} but the directory is {directory.name!r}")
        if module.KIND not in KINDS:
            problems.append(f"KIND must be one of {KINDS}, got {module.KIND!r}")
        if module.OUTPUT is not None and not _is_envelope(module.OUTPUT):
            problems.append("OUTPUT must be an EnvelopeBase subclass or None")
        if not isinstance(module.NEEDS, tuple) or not all(_is_envelope(t) for t in module.NEEDS):
            problems.append("NEEDS must be a tuple of EnvelopeBase subclasses")
        if not isinstance(module.TASKS, dict):
            problems.append("TASKS must be a dict of task key -> file name")
        else:
            for key, filename in module.TASKS.items():
                if not (directory / filename).is_file():
                    problems.append(f"TASKS[{key!r}] names {filename}, which is not "
                                    f"beside stage.py")
        if not (isinstance(module.Options, type) and issubclass(module.Options, BaseModel)):
            problems.append("Options must be a pydantic model")
        elif module.Options.model_config.get("extra") != "forbid":
            problems.append("Options must set extra='forbid' — a key that silently does "
                            "nothing is a workflow that silently proves nothing")
        if not callable(module.run):
            problems.append("run must be callable")
    if problems:
        raise SystemExit(f"stage {directory.name} ({source}) does not meet the contract:\n- "
                         + "\n- ".join(problems))
    return StageModule(name=module.NAME, directory=directory, module=module,
                       kind=module.KIND, output=module.OUTPUT, needs=tuple(module.NEEDS),
                       tasks=dict(module.TASKS), options=module.Options)


def _is_envelope(candidate: Any) -> bool:
    return isinstance(candidate, type) and issubclass(candidate, EnvelopeBase)
