"""The installer against a scratch repo — the path every user hits first.

`install.py` is the only script that runs before anything else exists, and its
output is the thing every other module then assumes: a stamped `adws/`, a
generated `sssf.config.yaml`, a `.gitignore` that keeps the runtime and the
worktrees out of the tree.

It is exercised as a subprocess, not imported, because that is how it runs: it
stamps into `Path.cwd()` and reads `sys.stdin.isatty()` to decide whether it may
ask which harness. Importing it would test neither.

The assertion that matters most is the last one: the generated config must load
and validate through `agents.load_config`. A stamped repo whose own config the
factory cannot read is a broken install that looks like a working one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from adw_modules import agents

from conftest import SKILL_ROOT, git

INSTALL = SKILL_ROOT / "scripts" / "install.py"
CONFIG = "adws/adw_sssf_config/sssf.config.yaml"


def install(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the installer the way a user does: as a script, in their repo.

    Without a terminal to ask at (`stdin` is a pipe here), a missing --harness
    is an error rather than a silent default — which is itself worth testing.
    """
    return subprocess.run([sys.executable, str(INSTALL), *args], cwd=cwd,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


@pytest.fixture(params=["claude_code", "pi"])
def harness(request) -> str:
    return request.param


def test_a_fresh_repo_is_stamped_and_its_config_loads(repo: Path, harness: str):
    result = install(repo, "--harness", harness, "--no-detect-quality")
    assert result.returncode == 0, result.stdout + result.stderr

    for relative in ("adws/adw_modules/agents.py", "adws/adw_modules/harnesses/fake.py",
                     "adws/adw_plan.py", "justfile", CONFIG, ".env.sample",
                     "adws/adw_data/prompt_engineering/planner/system.md"):
        assert (repo / relative).is_file(), f"{relative} was not stamped"

    cfg = agents.load_config(str(repo / CONFIG))
    assert cfg.agents, "the generated roster has no agents"
    assert {a.harness for a in cfg.agents} == {harness}
    for a in cfg.agents:
        assert (repo / a.prompt_engineering.system).is_file()
        assert (repo / a.prompt_engineering.user).is_file()


def test_the_generated_roster_validates_against_its_own_harness_rules(repo: Path,
                                                                     harness: str):
    """Not just parseable — every model pattern and tool name is checked by the
    harness that owns it. A roster that only loads is a roster that fails at the
    first agent call instead of at validation."""
    from adw_modules.harnesses import HARNESSES
    driver = HARNESSES[harness]
    try:
        driver.reachable()
    except RuntimeError as unreachable:
        # pi resolves models against `pi --list-models`, so this half of the
        # check needs that CLI. Skipped rather than weakened: on a machine that
        # has it, it runs and means something.
        pytest.skip(f"{harness}: {unreachable}")
    install(repo, "--harness", harness, "--no-detect-quality")
    cfg = agents.load_config(str(repo / CONFIG))
    for a in cfg.agents:
        driver.resolve_model(a.model)
        assert driver.validate_agent(a) == [], f"{a.name}: {driver.validate_agent(a)}"


def test_the_runtime_and_the_worktrees_are_gitignored(repo: Path):
    """Chains that commit call `git add -A`. Without these entries a run's first
    commit stages the tree it is running in, plus every .pyc beside the modules."""
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    ignored = (repo / ".gitignore").read_text()
    for entry in ("adws/adw_data/sessions/", "adws/adw_data/sssf.db*",
                  ".sssf-worktrees/", "__pycache__/", "*.pyc", ".env"):
        assert entry in ignored

    git(repo, "add", "-A")
    staged = git(repo, "diff", "--cached", "--name-only").splitlines()
    assert not [p for p in staged if p.startswith("adws/adw_data/sessions/")]
    assert not [p for p in staged if p.endswith(".pyc")]


def test_a_second_install_skips_what_is_already_there(repo: Path):
    """Idempotent: a re-install must not overwrite an edited roster."""
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    (repo / CONFIG).write_text((repo / CONFIG).read_text() + "\n# edited by the engineer\n")

    result = install(repo, "--harness", "claude_code", "--no-detect-quality")

    assert result.returncode == 0
    assert "# edited by the engineer" in (repo / CONFIG).read_text()


def test_force_replaces_skill_code(repo: Path):
    """What --force is FOR: an upgrade. Every stamped path is a copy of skill
    code, and replacing it is how a repo receives a fix to `adws/**` or the
    justfile."""
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    module = repo / "adws" / "adw_modules" / "hitl.py"
    module.write_text("# a stale copy\n")
    install(repo, "--harness", "claude_code", "--no-detect-quality", "--force")
    assert "# a stale copy" not in module.read_text()


def test_force_does_not_replace_the_one_file_the_operator_owns(repo: Path):
    """The config is the opposite of skill code: it holds the answers only this
    repository has — `issues.project`, the route map, which gates are on — and
    its own header says it is the operator's the moment it is stamped.

    Re-rendering it made the SOLE upgrade path also the one that destroyed the
    file the installer told them to own, silently, in the same breath as the fix
    they came for. So --force replaces everything else and leaves this."""
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    mine = (repo / CONFIG).read_text().replace('project: ""', 'project: "acme/widgets"')
    (repo / CONFIG).write_text(mine + "\n# and a note I wrote\n")

    result = install(repo, "--harness", "claude_code", "--no-detect-quality", "--force")

    assert result.returncode == 0
    kept = (repo / CONFIG).read_text()
    assert 'project: "acme/widgets"' in kept
    assert "# and a note I wrote" in kept


def test_a_changed_config_arrives_beside_the_operator_s_own(repo: Path):
    """An upgrade still has to DELIVER a config that learned a new block, or the
    operator would never find out it exists. It lands as `.new`, and the notice
    names both paths — merging is theirs, with a diff in front of them."""
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    (repo / CONFIG).write_text("# nothing like the real one\n")

    result = install(repo, "--harness", "claude_code", "--no-detect-quality", "--force")

    proposed = repo / (CONFIG + ".new")
    assert proposed.is_file()
    assert "hitl:" in proposed.read_text()          # a real render, not a stub
    assert (repo / CONFIG).read_text() == "# nothing like the real one\n"
    assert "YOUR CONFIG WAS NOT TOUCHED" in result.stdout
    assert str(proposed) in result.stdout
    assert "make_config.py --force" in result.stdout   # how to ask for a clean one


def test_an_unchanged_config_leaves_no_litter(repo: Path):
    """A repo already current gets no `.new` to wonder about — the file is only
    written when the render actually differs from what is there."""
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    result = install(repo, "--harness", "claude_code", "--no-detect-quality", "--force")
    assert not (repo / (CONFIG + ".new")).exists()
    assert "YOUR CONFIG WAS NOT TOUCHED" not in result.stdout


def test_deleting_the_config_is_how_you_ask_for_a_clean_one(repo: Path):
    """The escape hatch the notice promises, and the reason --force does not
    need to be one: a config that is not there is stamped fresh, as on day one."""
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    (repo / CONFIG).unlink()
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    assert "hitl:" in (repo / CONFIG).read_text()


def test_an_unknown_harness_is_refused_by_name(repo: Path):
    result = install(repo, "--harness", "codex")
    assert result.returncode != 0
    assert "unknown harness" in (result.stdout + result.stderr)


def test_the_fake_harness_is_not_installable(repo: Path):
    """It is selectable per agent in a roster and never as the repository's own
    harness — see harnesses/fake.py."""
    result = install(repo, "--harness", "fake")
    assert result.returncode != 0
    assert "unknown harness" in (result.stdout + result.stderr)


def test_without_a_terminal_a_missing_harness_is_an_error_not_a_default(repo: Path):
    """Which harness a repository runs on is a decision the repository owns."""
    result = install(repo, "--no-detect-quality")
    assert result.returncode != 0
    assert "which harness?" in (result.stdout + result.stderr)
    assert not (repo / "adws").exists(), "nothing may be stamped before the answer"


def test_quality_detection_writes_this_repo_s_real_commands(repo: Path):
    """`quality.py` shipped with placeholders that everybody skipped wiring up,
    so the installer reads the repository and fills them in."""
    (repo / "package.json").write_text(
        '{"scripts": {"test": "vitest run", "lint": "eslint ."}}\n')
    install(repo, "--harness", "claude_code")
    stamped = (repo / "adws" / "adw_modules" / "quality.py").read_text()
    assert 'argv=["npm", "run", "test"]' in stamped
    assert 'argv=["npm", "run", "lint"]' in stamped
    # And what it could NOT find stays a placeholder, which fails loudly when
    # a run reaches it rather than passing as an unperformed check.
    assert '_placeholder("typecheck")' in stamped


def test_no_detect_quality_leaves_every_block_a_placeholder(repo: Path):
    (repo / "package.json").write_text('{"scripts": {"test": "vitest run"}}\n')
    install(repo, "--harness", "claude_code", "--no-detect-quality")
    stamped = (repo / "adws" / "adw_modules" / "quality.py").read_text()
    assert '_placeholder("test")' in stamped


# ── what --force re-detects ─────────────────────────────────────────────────
#
# `--force` replaces `adws/adw_modules/quality.py` and re-reads the repository
# to re-wire it, which makes detection load-bearing on every upgrade rather than
# only on day one. These pin the rule the whole thing rests on.

import importlib.util                                                    # noqa: E402


def _detect_module():
    spec = importlib.util.spec_from_file_location(
        "_detect", SKILL_ROOT / "scripts" / "_detect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_declared_script_beats_an_inference_about_the_same_block(tmp_path: Path):
    """`bun run test` and `bun test` are different programs. The first runs the
    repository's `test` script; the second runs Bun's own test runner, which in
    a vitest repo collects a completely different set of files and fails.

    Step 1 of `detect` takes the repository's own words and step 2 only
    `offer`s, which is a no-op once a block is answered — so a `bun.lock` beside
    a declared `test` script must yield the script, not the runner. Nothing
    pinned that, and an upgrade now re-detects on every `--force`: getting it
    backwards would rewire a working test block into a failing one and report it
    as detected.
    """
    (tmp_path / "bun.lock").write_text("")
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": "vitest run", "lint": "eslint .", "build": "vite build"}}))

    found = _detect_module().detect(tmp_path)

    assert found["test"].argv == ["bun", "run", "test"]
    assert found["lint"].argv == ["bun", "run", "lint"]
    assert found["build"].argv == ["bun", "run", "build"]
    assert "package.json" in found["test"].because


def test_the_bare_runner_is_offered_only_when_nothing_was_declared(tmp_path: Path):
    """The inference is not wrong — it is the answer for a bun repo that really
    has no test script. It just must never outrank one that does."""
    (tmp_path / "bun.lock").write_text("")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))

    found = _detect_module().detect(tmp_path)

    assert found["test"].argv == ["bun", "test"]
    assert found["test"].because == "bun.lock"


def test_the_package_manager_comes_from_the_lockfile(tmp_path: Path):
    """`npm run test` in a pnpm repo is a different resolution and sometimes a
    different script. The lockfile is the only thing that knows."""
    detect = _detect_module().detect
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    for lockfile, runner in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                             ("package-lock.json", "npm")):
        (tmp_path / lockfile).write_text("")
        assert detect(tmp_path)["test"].argv == [runner, "run", "test"]
        (tmp_path / lockfile).unlink()


def test_a_block_with_no_evidence_stays_unanswered(tmp_path: Path):
    """An unanswered block stays a placeholder, and a placeholder FAILS a run —
    which is the point. Guessing one would be the installer inventing a command
    that runs against somebody's repository."""
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
    found = _detect_module().detect(tmp_path)
    assert "build" in found
    assert "lint" not in found and "typecheck" not in found
