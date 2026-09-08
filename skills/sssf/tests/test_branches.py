import pytest

from adw_modules import branches
from adw_modules.data_types import WorktreeConfig


@pytest.fixture
def config():
    return WorktreeConfig()


@pytest.mark.parametrize("text,expected", [
    ("Fix the floorEuro rounding", "fix-the-flooreuro-rounding"),
    ("  Add tenant export to CSV  ", "add-tenant-export-to-csv"),
    ("# Fix the floorEuro rounding", "fix-the-flooreuro-rounding"),
    ("## Deep heading\n\nbody text", "deep-heading"),
    ("\n\n\nSecond line is the first", "second-line-is-the-first"),
    ("Mietvertrag: Kaution & Nebenkosten!", "mietvertrag-kaution-nebenkosten"),
    ("---", ""),
    ("", ""),
    ("@{weird}", "weird"),
    ("trailing dot.", "trailing-dot"),
    # A ref may not END in ".lock" — but every "." here has already become a
    # hyphen, so the illegal shape cannot survive to be stripped.
    ("release.lock", "release-lock"),
    ("a..b", "a-b"),
])
def test_slugify_shapes(text, expected):
    assert branches.slugify(text) == expected


def test_slugify_truncates_on_a_hyphen_boundary():
    # 40 chars would land mid-word; the last whole word wins instead.
    slug = branches.slugify("rounding behaviour of the euro formatter helper", limit=40)
    assert slug == "rounding-behaviour-of-the-euro-formatter"
    assert len(slug) <= 40
    assert not slug.endswith("-")


def test_issue_slug_keeps_the_number_when_the_title_slugs_to_nothing():
    assert branches.issue_slug(42, "Fix rounding") == "42-fix-rounding"
    assert branches.issue_slug(42, "!!!") == "42"


def test_branch_for_with_and_without_a_slug(config):
    assert branches.branch_for(config, "a1b2c3d4") == "sssf/a1b2c3d4"
    assert (branches.branch_for(config, "a1b2c3d4", "42-fix-rounding")
            == "sssf/a1b2c3d4-42-fix-rounding")


def test_branch_slug_false_restores_the_old_name(config):
    config.branch_slug = False
    assert branches.branch_for(config, "a1b2c3d4", "42-fix-rounding") == "sssf/a1b2c3d4"


@pytest.mark.parametrize("branch,expected", [
    ("sssf/a1b2c3d4-42-fix-rounding", "a1b2c3d4"),
    ("sssf/a1b2c3d4", "a1b2c3d4"),          # the pre-phase-8 format still resolves
    ("feature/something", ""),
    ("", ""),
])
def test_session_of(branch, expected):
    assert branches.session_of(branch, "sssf/") == expected


def test_session_of_round_trips_every_branch_for(config):
    for slug in ("", "42-fix-rounding", "add-tenant-export-to-csv"):
        branch = branches.branch_for(config, "a1b2c3d4", slug)
        assert branches.session_of(branch, config.branch_prefix) == "a1b2c3d4"
