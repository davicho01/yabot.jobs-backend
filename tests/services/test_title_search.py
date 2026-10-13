"""Tests for the related-title query lexicon — app.services.title_search —
against the shared test queries in title_search_cases.py."""

import pytest

from app.services.title_search import (
    IC_LADDER,
    LEVEL_BANDS,
    MANAGEMENT,
    interpret_query,
    matches_title,
    parse_level,
    title_roles,
)
from tests.services.title_search_cases import CASES, TECH


def _id(case):
    return case.query


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_query_is_read_as_expected(case):
    intent = interpret_query(case.query, case.sector)

    assert (intent.exact, intent.level, intent.roles, intent.residual) == (
        case.exact,
        case.level,
        case.roles,
        case.residual,
    )


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_query_finds_its_example_titles(case):
    intent = interpret_query(case.query, case.sector)

    missed = [t for t in case.should_match if not matches_title(intent, t, case.sector)]
    wrongly_found = [t for t in case.should_not_match if matches_title(intent, t, case.sector)]

    assert (missed, wrongly_found) == ([], [])


def test_needs_model_titles_are_the_gap_the_title_baseline_leaves():
    # Not failures: titles only the whole posting can settle. Listed so the
    # model's version of this check has something to beat.
    gap = [
        (case.query, title)
        for case in CASES
        for title in case.needs_model
        if not matches_title(interpret_query(case.query, case.sector), title, case.sector)
    ]
    assert gap == [(case.query, t) for case in CASES for t in case.needs_model]


def test_ic_ladder_only_goes_down_and_never_reaches_junior_or_intern():
    for level in IC_LADDER:
        band = LEVEL_BANDS[level]
        assert level in band
        assert all(IC_LADDER.index(other) <= IC_LADDER.index(level) for other in band)
        if level not in ("junior", "intern"):
            assert "junior" not in band and "intern" not in band


def test_management_and_ic_never_mix():
    for level in IC_LADDER:
        assert not set(LEVEL_BANDS[level]) & set(MANAGEMENT)
    for level in MANAGEMENT:
        assert not set(LEVEL_BANDS[level]) & set(IC_LADDER)


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("Software Engineer", None),
        ("Sr. Software Engineer", "senior"),
        ("Senior Software Engineer II", "senior"),
        ("Software Engineer III", "senior"),
        ("Software Engineer I", "junior"),
        ("Staff Engineer", "staff"),
        ("Principal Engineer", "principal"),
        ("Distinguished Engineer", "principal"),
        ("Lead Software Engineer", "senior"),
        ("Senior Manager, Engineering", "senior_manager"),
        ("Senior Director of Engineering", "senior_director"),
        ("Associate Director, Data", "senior_manager"),
        ("Head of Platform", "director"),
        ("VP, Engineering", "vp"),
        ("Vice President of Product", "vp"),
        ("Chief Technology Officer", "executive"),
        ("Chief Architect", "principal"),
        ("Software Engineering Intern", "intern"),
        ("Associate Software Engineer", "junior"),
        ("Product Manager", None),
        ("Senior Product Manager", "senior"),
        ("Staff Nurse", None),
        ("Chief Engineer", None),
        ("Systems Engineer Stf - E4", "staff"),
        ("Engineer Sr Lead, Software", "staff"),
        ("Sr Software Eng Supervisor", "manager"),
        # Level fixes from the precision spot check (2026-10-12)
        ("Software Developer (.NET + Vue 3) - Remote", None),
        ("Python 3 Developer", None),
        ("Full-stack Engineer 4", "staff"),
        ("Engineering Technician III", "senior"),
        ("ENGINEER MECHANICAL 3", "senior"),
        ("Power Engineering Technologist I", "junior"),
        ("Applied Researcher 4", "staff"),
        ("GIS I&C Engineer", None),
        ("Field Service Engineer 1", "junior"),
        ("HIH - Software Engineering Associate Principal", "staff"),
        ("Technical Success Manager", None),
        ("Customer Success Manager", None),
        ("Senior Technical Account Manager", "senior"),
        ("Release Manager", None),
        ("Engineering Manager", "manager"),
        ("3D Artist", None),
        # From the 100-posting accuracy check (2026-10-12)
        ("Software Developer, Network Software Associate (Summer 2027)", "intern"),
        ("Senior DevOps Engineer - Assistant Vice President", "senior"),
        ("AVP, Software Engineer", "senior"),
        ("Vice President, Engineering", "vp"),
    ],
)
def test_parse_level_reads_the_level_a_title_states(title, level):
    assert parse_level(title) == level


@pytest.mark.parametrize(
    ("title", "roles"),
    [
        # From the 100-posting accuracy check (2026-10-12)
        ("Senior Cybersecurity Analyst", {"security"}),
        ("Senior Cybersecurity Recovery Architect", {"security"}),
        ("Software Developer, Network Software Associate (Summer 2027)", {"software_general"}),
        ("Full Stack Software Engineer", {"fullstack"}),
        ("Lead Software Engineer-Golang", {"backend"}),
        ("Senior Observability Engineer (Splunk & APM)", {"devops_sre_platform"}),
        ("Senior DFT Design Engineer, Google Cloud", set()),
        ("Research Engineer, Data Infrastructure", {"data_engineering"}),
        ("Windows Server Administrator - Night Shift", {"it_support_sysadmin"}),
        ("FIREWALL ADMINISTRATOR", {"security"}),
        ("Deployed Engineer (NYC)", {"software_general"}),
        # Sector words still apply when nothing clearer is there
        ("Data Analyst", {"data_science_analytics"}),
        ("Senior Analyst", {"data_science_analytics"}),
        ("Engineer III, Software", {"software_general"}),
        ("Senior Cloud Architect", {"devops_sre_platform"}),
        # Second round, from a fresh 100 postings
        ("Sr. Software Engineer, Infrastructure (Remote)", {"devops_sre_platform"}),
        ("Staff Software Engineer, Machine Learning", {"ml_ai_engineering"}),
        ("Staff Software Engineer - Database Engineering", {"data_engineering"}),
        ("Lead Software Engineer - AWS/SRE Engineer", {"devops_sre_platform"}),
        ("Public Key Infrastructure (PKI) Operations Engineer", {"security"}),
        ("Market Developer", set()),
        ("Content Developer I", set()),
        ("Senior Developer, Growth", {"software_general"}),
    ],
)
def test_title_roles_reads_the_role_a_tech_title_states(title, roles):
    assert title_roles(title, TECH) == roles


def test_quoted_query_is_exact_and_never_expands():
    intent = interpret_query('"senior devops"')

    assert intent.exact and not intent.expands
    assert not matches_title(intent, "Senior Site Reliability Engineer")
    assert matches_title(intent, "Senior DevOps Engineer")


def test_unrecognised_query_keeps_todays_literal_match():
    intent = interpret_query("barista")

    assert not intent.expands
    assert matches_title(intent, "Lead Barista")
    assert not matches_title(intent, "Coffee Server")
