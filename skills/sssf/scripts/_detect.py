"""Guess this repository's real quality commands, so the stamp is not a stub.

WHY THIS EXISTS. `quality.py` ships four blocks — test, lint, typecheck, build —
and a stamped repo cannot know any of them, so they land as placeholders that
now fail rather than pass. That is honest, but it makes the first `adw_*_test`
run fail for a reason that has nothing to do with the work: nobody had written
the command down yet.

Most repositories already answered the question somewhere a file can see:
`package.json` names its own scripts, a lockfile names the package manager, a
`pyproject.toml` names its test runner. So the installer reads those and writes
what it finds into the stamped `quality.py`, marking every line it filled in.

Two rules, because a wrong command that runs is worse than no command:

1. **A repository's own declaration beats an inference.** A `test` script in
   package.json is the author saying what testing this project means; `pytest`
   being importable is not. Declarations are taken first, always.
2. **Nothing is guessed twice.** A block nothing can answer for stays a
   placeholder — which fails loudly, names itself in `just doctor`, and is
   therefore impossible to mistake for a wired-up check.

Detection only ever runs against a `quality.py` this install just stamped. A
file that already existed is never rewritten, whatever it contains.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Which lockfile means which JavaScript package manager, most specific first.
# The runner matters: `bun run test` and `npm run test` are not interchangeable
# in a repo whose scripts assume one of them.
JS_MANAGERS = [
    ("bun.lock", ["bun", "run"]),
    ("bun.lockb", ["bun", "run"]),
    ("pnpm-lock.yaml", ["pnpm", "run"]),
    ("yarn.lock", ["yarn", "run"]),
    ("package-lock.json", ["npm", "run"]),
]

BLOCKS = ("test", "lint", "typecheck", "build")


class Detected:
    """One block's answer: the argv, and the evidence it came from."""

    def __init__(self, argv: list[str], because: str):
        self.argv = argv
        self.because = because

    @property
    def literal(self) -> str:
        """The argv as it should read in the source — a list, never a string."""
        return "[" + ", ".join(json.dumps(part) for part in self.argv) + "]"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _js(root: Path) -> tuple[list[str], dict]:
    """This repo's JS runner prefix and its declared scripts, if it is one."""
    package = _read_json(root / "package.json")
    if not package:
        return [], {}
    runner = next((prefix for lockfile, prefix in JS_MANAGERS
                   if (root / lockfile).is_file()), ["npm", "run"])
    scripts = package.get("scripts") or {}
    return runner, scripts if isinstance(scripts, dict) else {}


def _python(root: Path) -> tuple[list[str], str]:
    """This repo's Python runner prefix, and the text of its pyproject."""
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text() if pyproject.is_file() else ""
    # `uv run` only makes sense where uv is what manages the project; elsewhere
    # the bare binary is what the engineer's own shell would resolve.
    prefix = ["uv", "run"] if (root / "uv.lock").is_file() or "[tool.uv]" in text else []
    return prefix, text


def detect(root: Path) -> dict[str, Detected]:
    """Everything this repository says about how it is checked. Never guesses
    a block it has no evidence for — an unanswered block stays a placeholder."""
    found: dict[str, Detected] = {}
    js_runner, scripts = _js(root)
    py_runner, pyproject = _python(root)

    # 1. Declared scripts. The repository's own words, so they win outright.
    for block, names in (("test", ("test",)),
                         ("lint", ("lint",)),
                         ("typecheck", ("typecheck", "check-types", "tsc")),
                         ("build", ("build",))):
        for name in names:
            if name in scripts:
                found[block] = Detected(js_runner + [name],
                                        f"package.json scripts.{name}")
                break

    # 2. Inference, only where nothing was declared.
    def offer(block: str, argv: list[str], because: str) -> None:
        if block not in found:
            found[block] = Detected(argv, because)

    if (root / "bun.lock").is_file() or (root / "bun.lockb").is_file():
        offer("test", ["bun", "test"], "bun.lock")
    if (root / "tsconfig.json").is_file():
        offer("typecheck", (["bunx"] if js_runner[:1] == ["bun"] else ["npx"])
              + ["tsc", "--noEmit"], "tsconfig.json")

    if pyproject or (root / "pytest.ini").is_file() or (root / "tox.ini").is_file():
        if "pytest" in pyproject or (root / "pytest.ini").is_file():
            offer("test", py_runner + ["pytest", "-q"], "pytest in pyproject.toml")
        if "[tool.ruff]" in pyproject or (root / "ruff.toml").is_file():
            offer("lint", py_runner + ["ruff", "check", "."], "ruff config")
        if "[tool.mypy]" in pyproject or (root / "mypy.ini").is_file():
            offer("typecheck", py_runner + ["mypy", "."], "mypy config")

    if (root / "Cargo.toml").is_file():
        offer("test", ["cargo", "test"], "Cargo.toml")
        offer("lint", ["cargo", "clippy"], "Cargo.toml")
        offer("build", ["cargo", "build"], "Cargo.toml")
    if (root / "go.mod").is_file():
        offer("test", ["go", "test", "./..."], "go.mod")
        offer("lint", ["go", "vet", "./..."], "go.mod")
        offer("build", ["go", "build", "./..."], "go.mod")

    return {block: found[block] for block in BLOCKS if block in found}


def apply(quality_py: Path, found: dict[str, Detected]) -> list[str]:
    """Write the detected argv into a freshly stamped quality.py.

    Rewrites exactly the `_placeholder("<block>")` call and the `# e.g. …` hint
    beside it, and nothing else — the marker is unique per block, so there is no
    way to hit a line that was not the placeholder. Returns one line per
    substitution, for the installer to print.

    Every rewritten line is marked as detected rather than authored, because a
    guess the engineer never read is the same trap the placeholders were.
    """
    if not found or not quality_py.is_file():
        return []
    source = quality_py.read_text()
    notes = []
    for block, detected in found.items():
        pattern = re.compile(rf'_placeholder\("{block}"\)(,)?([ \t]*#[^\n]*)?')
        replacement = (f"{detected.literal},"
                       f"   # detected at install ({detected.because}) — verify it")
        # A lambda, not a string: a replacement that goes through re's escape
        # processing would mangle any backslash a real command carries.
        source, count = pattern.subn(lambda _match: replacement, source, count=1)
        if count:
            notes.append(f"{block}: {' '.join(detected.argv)}   ({detected.because})")
    quality_py.write_text(source)
    return notes
