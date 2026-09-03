"""Landing a run's branch — merge it, or push it and let a human decide.

A run ends with its work on `sssf/<adw_id>` and nowhere else. Getting it from
there onto the base branch is a known command, not a judgement call, so it is a
`kind="code"` phase over this module rather than an agent (SKILL.md rule 8).

Which command is CONFIGURATION, because repositories genuinely disagree: some
merge, some rebase, some let nothing but a reviewed pull request move the base
branch. `worktree.integration.mode` picks; nothing here is opinionated beyond
refusing to do something unsafe.

Two refusals are deliberate and neither is a bug:

  * **Uncommitted work in the run's worktree** stops integration. Whatever the
    agents left behind is not part of a commit yet, and merging the branch would
    silently ship less than the run produced.
  * **A dirty main checkout with the base branch checked out** stops a merge.
    The engineer has uncommitted work there; a merge would either fail halfway
    or bury it. The run's work is safe on its branch, so waiting costs nothing.

When the base branch is NOT checked out anywhere, the merge is done as a ref
update (`git fetch . <branch>:<base>`) — a fast-forward that moves the branch
without touching any working tree at all. Git refuses that when the branch is
checked out, which is exactly the case that needs the real merge above.
"""

from __future__ import annotations

import subprocess

from . import git_helper
from .data_types import IntegrationRequest, IntegrationResult
from .utils import operator_env


def integrate(run, params: IntegrationRequest) -> IntegrationResult:
    """Land the run's branch per config. Returns evidence, never a claim."""
    config = run.cfg.worktree.integration
    workspace = run.workspace
    mode = params.mode or config.mode

    # An externally triggered run must not be able to move the base branch. The
    # prompt came from whoever can file an issue, not from the engineer's own
    # terminal, so the one thing that cannot be left to config discipline is
    # whether a merge is even reachable on that path. `mode: none` still wins —
    # a repository that wants nothing landed gets nothing landed.
    if (run.trigger == "issue" and run.cfg.issues.force_pr
            and mode == "merge"):
        mode = "pr"

    result = IntegrationResult(mode=mode, branch=workspace.branch,
                               base_ref=workspace.base_ref)
    if mode != (params.mode or config.mode):
        result.notes.append("issue-triggered run: merge downgraded to pr "
                            "(issues.force_pr) — a stranger's prompt does not "
                            "move the base branch")

    if mode == "none":
        result.ok = True
        result.notes.append("integration is disabled (worktree.integration.mode: none)")
        return result
    if not workspace.enabled:
        result.notes.append("this run has no worktree, so it has no branch to land — "
                            "its commits are already on the branch it ran on")
        return result
    if git_helper.is_dirty(workspace.repo_root):
        result.notes.append(f"{workspace.branch} has uncommitted work in "
                            f"{workspace.repo_root} — commit it before integrating, "
                            f"or the merge ships less than the run produced")
        return result

    result.head = git_helper.rev(workspace.repo_root, "HEAD")
    if result.head == workspace.base_commit:
        result.ok = True
        result.notes.append(f"{workspace.branch} has no commits of its own since "
                            f"{workspace.base_ref} — nothing to integrate")
        return result

    return _merge(run, result) if mode == "merge" else _open_pr(run, result, params)


def _merge(run, result: IntegrationResult) -> IntegrationResult:
    """Merge the run's branch into the base branch, in the main checkout."""
    config = run.cfg.worktree.integration
    main = run.workspace.main_root
    base = run.workspace.base_ref

    if not git_helper.branch_exists(main, base):
        result.notes.append(f"base ref {base!r} is not a local branch — a detached "
                            f"or remote base cannot be merged into; land "
                            f"{result.branch} by hand, or use mode: pr")
        return result

    if git_helper.current_branch(main) != base:
        # Nobody has it checked out: move the ref, touch no working tree.
        completed = git_helper.update_branch(main, result.branch, base)
        if completed.returncode != 0:
            result.notes.append(
                f"{base} could not be fast-forwarded to {result.branch}: "
                f"{completed.stderr.strip()} — it has moved on since this run "
                f"started, so the merge needs a human")
            return result
        result.ok = True
        result.merged_into = base
        result.notes.append(f"fast-forwarded {base} to {result.branch} without "
                            f"touching a working tree")
        return result

    if git_helper.is_dirty(main):
        result.notes.append(
            f"{main} is on {base} with uncommitted changes — integrating would "
            f"merge into work in progress. Commit or stash there, then run the "
            f"integration again; {result.branch} keeps the run's work meanwhile")
        return result

    message = f"sssf({run.adw_id}): merge {result.branch} into {base}"
    try:
        git_helper.merge(main, result.branch, config.merge_flags, message)
    except RuntimeError as error:
        git_helper.merge_abort(main)          # never leave a half-merged checkout
        result.notes.append(f"merge stopped and was aborted: {error}")
        return result
    result.ok = True
    result.merged_into = base
    result.notes.append(f"merged {result.branch} into {base} "
                        f"({' '.join(config.merge_flags) or 'default strategy'})")
    return result


def _pr_body(run, template: str, result: IntegrationResult) -> str:
    """Render the configured PR body. Empty template = let pr_command decide.

    `Closes #<n>` in the template is what makes an issue-triggered run close its
    own issue on merge — the forge does it, so nothing here has to.
    """
    if not template:
        return ""
    return template.format(adw_id=run.adw_id, branch=result.branch,
                           base_ref=result.base_ref,
                           issue_number=run.issue_number or "",
                           issue_url=run.issue_url or "")


def _open_pr(run, result: IntegrationResult, params: IntegrationRequest) -> IntegrationResult:
    """Push the branch, and optionally ask the forge CLI to open the PR.

    Both commands run in the MAIN checkout, not in the run's worktree. Refs are
    shared between worktrees, so pushing from either sends the same commits —
    but a remote configured with a relative URL (`../mirror.git`, as a local
    clone gets) resolves against the directory git is run in, and from a
    worktree that is one level deeper and somewhere else entirely. The forge CLI
    is run there for the same reason.
    """
    config = run.cfg.worktree.integration
    tree = run.workspace.main_root

    if not git_helper.has_remote(tree, config.remote):
        result.notes.append(f"no remote named {config.remote!r} — nothing to push to. "
                            f"Add one, or use mode: merge")
        return result

    pushed = git_helper.push(tree, config.remote, result.branch)
    if pushed.returncode != 0:
        result.notes.append(f"push to {config.remote} was rejected: "
                            f"{pushed.stderr.strip()}")
        return result
    result.pushed = True
    result.ok = True
    result.notes.append(f"pushed {result.branch} to {config.remote}")

    if not config.open_pr:
        result.notes.append("open_pr is off — the branch is pushed and the pull "
                            "request is the engineer's to open")
        return result

    argv = [*config.pr_command, "--base", result.base_ref, "--head", result.branch]
    if params.title:
        argv += ["--title", params.title]
    body = params.body or _pr_body(run, config.pr_body_template, result)
    if body:
        argv += ["--body", body]
    # The forge CLI is already authenticated in the engineer's shell, so it runs
    # under their environment rather than the ADW's ephemeral `uv run` venv.
    completed = subprocess.run(argv, cwd=tree, env=operator_env(),
                               capture_output=True, text=True)
    if completed.returncode != 0:
        result.ok = False
        result.notes.append(f"`{' '.join(config.pr_command)}` failed: "
                            f"{(completed.stderr or completed.stdout).strip()[-500:]}")
        return result
    result.pr_url = next((line for line in completed.stdout.split()
                          if line.startswith("http")), "")
    result.notes.append(f"opened a pull request{': ' + result.pr_url if result.pr_url else ''}")
    return result
