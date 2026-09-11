"""Task files: where an agent's TASK comes from, and the check that its report
block still matches the envelope type the stage will parse.

The roster used to carry one `user.md` per agent, which meant the builder did
three jobs — build, fix, revise — with one prompt, and the words that told it
which one leaked into Python constants. Here a task belongs to the stage that
calls the agent: `asf/stages/<stage>/<task>.md` is the default, and a workflow
may override it with `asf/workflows/<name>/tasks/<key>.md`. Nothing else: no
template dialect beyond `{{placeholders}}`, no conditionals, for the same reason
workflow.yaml has no loops.

The `## Report` section of a task shows the agent the exact JSON to answer
with. That block and the stage's OUTPUT type are one contract, and a task
edited without the type — or the other way round — used to be found by an
agent's parse failure, mid-run, after the money was spent. So the block is read
here, at load time, and compared field by field with the pydantic model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

REPORT_HEADING = re.compile(r"^##\s+Report\s*$", re.MULTILINE)
JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
PLACEHOLDERS = ("prompt", "previous_envelope", "context_handoff_dir")


def resolve(key: str, default_file: Path, workflow_dir: Path) -> Path:
    """Workflow-local override first, the stage's own file second."""
    override = workflow_dir / "tasks" / f"{key}.md"
    return override if override.is_file() else default_file


def report_block(text: str) -> Optional[dict]:
    """The JSON example under `## Report`, parsed; None when there is no section.

    The example is written for a model to imitate, so its values are prose in
    angle brackets — that is still valid JSON as long as they are strings, and
    the check below only ever looks at the keys.
    """
    match = REPORT_HEADING.search(text)
    if not match:
        return None
    fence = JSON_FENCE.search(text, match.end())
    if not fence:
        raise ValueError("a `## Report` section with no ```json block in it")
    try:
        return json.loads(fence.group(1))
    except json.JSONDecodeError as error:
        raise ValueError(f"the ## Report block is not valid JSON: {error}") from None


def check(path: Path, output_type: type[BaseModel]) -> list[str]:
    """Every way a task file can disagree with the type its stage parses."""
    problems: list[str] = []
    if not path.is_file():
        return [f"task file not found: {path}"]
    text = path.read_text()
    for name in PLACEHOLDERS:
        if "{{" + name + "}}" not in text:
            problems.append(f"{path}: does not mention {{{{{name}}}}} — the agent would "
                            f"work without it")
    try:
        example = report_block(text)
    except ValueError as error:
        return problems + [f"{path}: {error}"]
    if example is None:
        return problems + [f"{path}: has no `## Report` section — the agent is never "
                           f"told what JSON to answer with"]
    if not isinstance(example, dict):
        return problems + [f"{path}: the ## Report block must be a JSON object"]
    fields = output_type.model_fields
    unknown = sorted(set(example) - set(fields))
    required = sorted(name for name, info in fields.items() if info.is_required())
    missing = sorted(set(required) - set(example))
    if unknown:
        problems.append(f"{path}: ## Report names {unknown}, which {output_type.__name__} "
                        f"has no field for — the agent would be told to send what the "
                        f"parser drops")
    if missing:
        problems.append(f"{path}: ## Report omits {missing}, which {output_type.__name__} "
                        f"requires — every reply would fail to parse")
    return problems
