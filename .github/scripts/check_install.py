#!/usr/bin/env python3
"""Read a freshly stamped repository's roster the way a run would. CI only.

Run from inside the stamped repository, with the harness it was stamped for as
the single argument. It asks the three questions that decide whether an install
actually worked, and that a successful `install.py` exit does not answer:

  1. Does `agents.load_config` parse the generated `sssf.config.yaml`?
  2. Is every prompt file the roster names on disk?
  3. Does each agent's config satisfy the rules of the harness it names —
     the model pattern and the tool vocabulary, asked of that harness?

(3) is skipped for a harness whose CLI is not installed on the runner: `pi`
resolves models against `pi --list-models`, so without it the check would be
testing the absence of a binary. It is skipped loudly rather than quietly.

Deliberately not a pytest file. `skills/sssf/tests/test_install.py` covers the
same ground with `sys.executable`; this one runs after the installer was
launched through `uv run`, from the stamped repo, exactly as a user does — and
that is a different thing to have proved.
"""

from __future__ import annotations

import sys
from pathlib import Path

CONFIG = Path("adws/adw_sssf_config/sssf.config.yaml")


def main(harness: str) -> int:
    root = Path.cwd()
    if not CONFIG.is_file():
        print(f"::error::{CONFIG} was not stamped into {root}")
        return 1

    sys.path.insert(0, str(root / "adws"))
    from adw_modules import agents                      # noqa: E402  (path set above)
    from adw_modules.harnesses import HARNESSES         # noqa: E402

    cfg = agents.load_config(str(CONFIG))
    if not cfg.agents:
        print("::error::the generated roster has no agents")
        return 1

    problems: list[str] = []
    for agent in cfg.agents:
        if agent.harness != harness:
            problems.append(f"{agent.name}: stamped for {harness!r} but says "
                            f"{agent.harness!r}")
            continue
        for label, ref in (("system", agent.prompt_engineering.system),
                           ("user", agent.prompt_engineering.user)):
            if not (root / ref).is_file():
                problems.append(f"{agent.name}: {label} prompt not stamped: {ref}")

    driver = HARNESSES[harness]
    try:
        driver.reachable()
    except RuntimeError as unreachable:
        print(f"::notice::skipping the per-harness config rules — {unreachable}")
    else:
        for agent in cfg.agents:
            try:
                driver.resolve_model(agent.model)
            except ValueError as bad_model:
                problems.append(f"{agent.name}: {bad_model}")
            problems += [f"{agent.name}: {p}" for p in driver.validate_agent(agent)]

    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print(f"ok: {len(cfg.agents)} agents on {harness}, every prompt on disk")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: check_install.py <harness>")
    sys.exit(main(sys.argv[1]))
