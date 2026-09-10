"""The typed contracts: phase descriptions, envelopes, usage arithmetic.

`PhaseParams.description` is a construction-time error on purpose — it fires
before the phase opens, not after the run is in the trace — so the test for it
is a `pydantic.ValidationError`, not a run that comes back red.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adw_modules.data_types import (BuildOutput, GateReport, Phase, PhaseParams,
                                    PlanOutput, ReviewOutput, UsageBreakdown)


def params(**overrides) -> PhaseParams:
    fields = {"name": "plan", "kind": "agent", "owner": "planner",
              "description": "Turn the request into a spec the builder can follow"}
    fields.update(overrides)
    return PhaseParams(**fields)


# ── rule 7: every phase earns a description ──────────────────────────────────

def test_a_real_description_is_accepted():
    assert params().description.startswith("Turn the request")


def test_a_blank_description_is_rejected():
    with pytest.raises(ValidationError) as caught:
        params(description="")
    assert "description is required" in str(caught.value)


def test_a_whitespace_only_description_is_rejected():
    with pytest.raises(ValidationError):
        params(description="   \n\t ")


def test_a_description_that_only_echoes_the_name_is_rejected():
    with pytest.raises(ValidationError) as caught:
        params(name="commit_plan", description="Commit the plan")
    assert "only restates the phase name" in str(caught.value)


def test_the_echo_check_ignores_case_and_a_trailing_period():
    with pytest.raises(ValidationError):
        params(name="run_tests", description="  RUN   TESTS.  ")


def test_a_description_that_says_more_than_the_name_survives():
    p = params(name="commit_plan",
               description="Commit the plan so the build starts from a clean tree")
    assert "clean tree" in p.description


def test_the_description_is_whitespace_normalized():
    assert params(description="two   spaces\nand a  newline").description == \
        "two spaces and a newline"


def test_the_error_names_the_phase_it_came_from():
    with pytest.raises(ValidationError) as caught:
        params(name="document", description="")
    assert "'document'" in str(caught.value)


# ── envelopes ────────────────────────────────────────────────────────────────

def test_status_is_required_and_closed():
    with pytest.raises(ValidationError):
        PlanOutput(status="maybe")


def test_an_envelope_parses_from_the_json_an_agent_emits():
    envelope = BuildOutput.model_validate_json(
        '{"status": "success", "summary": "done", "changed_files": ["src/app.py"], '
        '"commit_message": "feat: thing"}')
    assert envelope.changed_files == ["src/app.py"]
    assert envelope.artifacts == []          # absent list fields default empty


def test_unknown_fields_in_an_agent_s_json_are_tolerated():
    """An agent that says MORE than the contract has still met the contract."""
    envelope = PlanOutput.model_validate_json(
        '{"status": "success", "confidence": 0.9}')
    assert envelope.status == "success"


def test_a_review_defaults_to_unapproved():
    """Approval must be claimed; `verdict_consistent` then checks the claim."""
    assert ReviewOutput(status="success").approved is False


def test_a_phase_defaults_to_fail():
    """Success is earned by a clean exit, never by construction."""
    assert Phase(phase_id="a_01_plan", adw_id="a", seq=1, params=params()).status == "fail"


# ── GateReport ───────────────────────────────────────────────────────────────

def test_violations_are_derived_from_the_failed_checks_only():
    report = GateReport().check("a.md", True, "exists, 2B").check("b.md", False, "missing")
    assert report.violations == ["b.md: missing"]
    assert not report.passed


def test_a_failed_check_with_no_note_still_reads_as_a_violation():
    assert GateReport().check("a.md", False).violations == ["a.md: failed"]


# ── UsageBreakdown ───────────────────────────────────────────────────────────

def test_merge_sums_every_component():
    """A retried phase pays more than once, and the trace must show all of it."""
    total = UsageBreakdown(input_tokens=10, output_tokens=5, total_tokens=15,
                           total_cost=0.5)
    total.merge(UsageBreakdown(input_tokens=3, output_tokens=1, total_tokens=4,
                               total_cost=0.25))
    assert (total.input_tokens, total.output_tokens) == (13, 6)
    assert total.total_tokens == 19
    assert total.total_cost == 0.75
