"""Which platforms start with a daily full read (app.services.ats_adapters)."""

import importlib.util
from pathlib import Path

import pytest

from app.services.ats_adapters import (
    BROWSER_RENDERED_ATS_TYPES,
    UNCAPPED_BY_DEFAULT_ATS_TYPES,
    can_crawl_uncapped,
    uncapped_by_default,
)

# Platforms whose capped crawl lists only a slice of the board, so a job that
# came down is never seen missing and never closes unless the board is read
# in full. Dropping one from the default list silently stops its jobs closing.
RECENT_ONLY_ATS_TYPES = {"workday", "oracle_fusion", "amazon", "apple", "talentreef", "talentbrew", "icims", "eightfold"}


def test_recent_only_platforms_are_uncapped_by_default():
    assert UNCAPPED_BY_DEFAULT_ATS_TYPES == RECENT_ONLY_ATS_TYPES


@pytest.mark.parametrize("ats_type", sorted(RECENT_ONLY_ATS_TYPES))
def test_uncapped_by_default_is_true_for_recent_only_platforms(ats_type):
    assert uncapped_by_default(ats_type)


@pytest.mark.parametrize("ats_type", ["greenhouse", "lever", "ashby", "", None])
def test_other_platforms_and_unknown_types_start_capped(ats_type):
    assert not uncapped_by_default(ats_type)


def test_no_default_uncapped_platform_is_browser_rendered():
    # The crawl worker downgrades an uncapped board of a browser-rendered
    # platform to a capped crawl (with a warning), which would never close jobs.
    assert not UNCAPPED_BY_DEFAULT_ATS_TYPES & BROWSER_RENDERED_ATS_TYPES
    assert all(can_crawl_uncapped(ats_type) for ats_type in UNCAPPED_BY_DEFAULT_ATS_TYPES)


def test_talentbrew_icims_eightfold_migration_matches_the_default_list():
    # The migration hardcodes its platforms ("keep in step with ...").
    path = next(Path(__file__).parents[2].glob("migrations/versions/f7c2a9d4b6e1_*.py"))
    spec = importlib.util.spec_from_file_location("migration_f7c2a9d4b6e1", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert set(migration._ATS_TYPES) == {"talentbrew", "icims", "eightfold"}
    assert set(migration._ATS_TYPES) <= UNCAPPED_BY_DEFAULT_ATS_TYPES
