"""Gates verify claims against the tree. These verify the gates.

The escape case is the one that cost real money to find (`c76a372`): an agent
with `bash` can write anywhere, so a declared artifact path that resolves
OUTSIDE the run's own two trees must fail rather than pass because the file is
really there.
"""

from __future__ import annotations

import pytest

from adw_modules import gates
from adw_modules.data_types import (BuildOutput, GenericOutput, PlanOutput,
                                    ReviewFinding, ReviewOutput)

from conftest import config


@pytest.fixture
def run(make_run):
    """A run whose repo_root and session_dir are the two trees a gate allows."""
    return make_run(config())


def envelope(**fields) -> GenericOutput:
    return GenericOutput(**{"status": "success", **fields})


# ── _in_tree: the boundary itself ────────────────────────────────────────────

def test_relative_path_resolves_against_the_run_worktree(run):
    """Not against the ADW process's cwd — the bug behind `c76a372`."""
    (run.repo_root / "specs").mkdir()
    (run.repo_root / "specs" / "plan.md").write_text("plan")
    assert gates._in_tree(run, "specs/plan.md") == run.repo_root / "specs/plan.md"


def test_session_dir_is_the_second_allowed_tree(run):
    """context_handoff/ lives outside the worktree and is still legitimate."""
    target = run.context_handoff_dir / "report.md"
    assert gates._in_tree(run, str(target)) is not None


def test_absolute_path_outside_both_trees_is_refused(run, tmp_path):
    escape = tmp_path / "elsewhere" / "plan.md"
    escape.parent.mkdir(parents=True)
    escape.write_text("written by an agent that left the worktree")
    assert gates._in_tree(run, str(escape)) is None


def test_traversal_out_of_the_worktree_is_refused(run):
    """`../` is the same escape spelled relatively, and resolve() sees it."""
    assert gates._in_tree(run, "../outside.md") is None


def test_the_worktree_root_itself_is_in_tree(run):
    assert gates._in_tree(run, str(run.repo_root)) is not None


# ── artifacts_exist ──────────────────────────────────────────────────────────

def test_artifacts_exist_passes_for_a_file_that_is_there(run):
    (run.repo_root / "plan.md").write_text("x")
    report = gates.artifacts_exist(envelope(artifacts=["plan.md"]), run)
    assert report.violations == []
    assert report.checks[0].ok


def test_artifacts_exist_fails_for_a_file_that_is_not(run):
    report = gates.artifacts_exist(envelope(artifacts=["plan.md"]), run)
    assert len(report.violations) == 1
    assert "does not exist" in report.violations[0]


def test_a_real_file_outside_the_tree_still_fails(run, tmp_path):
    """The escape a gate that only asked `exists()` would have legitimized."""
    escape = tmp_path / "outside.md"
    escape.write_text("real, and none of this run's business")
    report = gates.artifacts_exist(envelope(artifacts=[str(escape)]), run)
    assert len(report.violations) == 1
    assert "outside this run's own tree" in report.violations[0]


def test_no_artifacts_is_a_green_report(run):
    assert gates.artifacts_exist(envelope(), run).violations == []


# ── files_non_empty / json_parses ────────────────────────────────────────────

def test_files_non_empty_fails_on_a_zero_byte_artifact(run):
    (run.repo_root / "plan.md").write_text("")
    assert len(gates.files_non_empty(envelope(artifacts=["plan.md"]), run).violations) == 1


def test_files_non_empty_leaves_a_missing_file_to_artifacts_exist(run):
    """Two gates, one complaint: absence is not this gate's finding to make."""
    assert gates.files_non_empty(envelope(artifacts=["gone.md"]), run).violations == []


def test_json_parses_fails_on_malformed_json(run):
    (run.repo_root / "out.json").write_text("{not json")
    assert len(gates.json_parses(envelope(artifacts=["out.json"]), run).violations) == 1


def test_json_parses_ignores_non_json_artifacts(run):
    (run.repo_root / "plan.md").write_text("{not json")
    assert gates.json_parses(envelope(artifacts=["plan.md"]), run).violations == []


# ── diff_matches_claims ──────────────────────────────────────────────────────

def test_claimed_changed_file_must_exist(run):
    build = BuildOutput(status="success", changed_files=["src/app.py"])
    assert len(gates.diff_matches_claims(build, run).violations) == 1


def test_claimed_changed_file_outside_the_tree_is_refused(run, tmp_path):
    outside = tmp_path / "app.py"
    outside.write_text("x")
    build = BuildOutput(status="success", changed_files=[str(outside)])
    violations = gates.diff_matches_claims(build, run).violations
    assert len(violations) == 1 and "outside this run's own tree" in violations[0]


def test_an_envelope_without_changed_files_is_not_this_gate_s_problem(run):
    assert gates.diff_matches_claims(PlanOutput(status="success"), run).violations == []


# ── verdict_consistent ───────────────────────────────────────────────────────

def review(**fields) -> ReviewOutput:
    return ReviewOutput(**{"status": "success", **fields})


def test_approval_with_blocking_items_is_refuted():
    report = gates.verdict_consistent(review(approved=True, blocking=["fix the leak"]), None)
    assert any("blocking item(s) while approved=true" in v for v in report.violations)


def test_approval_with_an_unmet_requirement_is_refuted():
    envelope_ = review(approved=True, findings=[
        ReviewFinding(requirement="handles empty input", met=False)])
    report = gates.verdict_consistent(envelope_, None)
    assert any("unmet requirement(s) while approved=true" in v for v in report.violations)


def test_rejection_that_names_no_problem_is_refuted():
    report = gates.verdict_consistent(review(approved=False), None)
    assert any("no blocking item or unmet requirement" in v for v in report.violations)


def test_a_clean_approval_passes():
    envelope_ = review(approved=True, findings=[
        ReviewFinding(requirement="handles empty input", met=True)])
    assert gates.verdict_consistent(envelope_, None).violations == []


def test_a_supported_rejection_passes():
    assert gates.verdict_consistent(review(approved=False, blocking=["fix it"]),
                                    None).violations == []


# ── tests_pass ───────────────────────────────────────────────────────────────

def test_tests_pass_runs_in_the_run_s_worktree(run):
    """`cwd` is the fix in the gate's own docstring; prove it is still applied."""
    (run.repo_root / "marker.txt").write_text("here")
    gate = gates.tests_pass("test -f marker.txt")
    assert gate(envelope(), run).violations == []


def test_tests_pass_reports_the_exit_code_and_the_output(run):
    gate = gates.tests_pass("echo boom >&2; exit 3")
    violations = gate(envelope(), run).violations
    assert len(violations) == 1
    assert "exit 3" in violations[0] and "boom" in violations[0]


def test_tests_pass_names_itself(run):
    assert gates.tests_pass("pytest -q").__name__ == "tests_pass(pytest -q)"
