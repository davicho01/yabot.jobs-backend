"""Tests for app.services.collection_qualification — the score, its gates
and the recommendation — and one_off.collection_report's candidates, which
must be today's hubs."""

import itertools
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.enums import CollectionStatus, JobSector, WorkplaceType
from app.models.job_collection import JobCollection
from app.services import static_hub_pages
from app.services import static_job_pages as sjp
from app.services.collection_qualification import (
    WEIGHTS,
    Recommendation,
    Thresholds,
    evaluate_collection,
    evaluate_jobs,
)
from one_off import collection_report

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)  # 2 pm Eastern
TODAY = date(2026, 10, 26)
SLC = "41620"  # Salt Lake City, UT metro
counter = itertools.count()


def job(**kw) -> sjp.LinkJob:
    n = next(counter)
    base = dict(
        url_id=f"job-{n}", title=f"Job {n}", company_name=None, domain=f"company{n}.com", company_key=f"company {n}",
        title_key=None, sector=JobSector.ENGINEERING_TECH, metros=(SLC, "UT"), location="Salt Lake City, UT",
        workplace_type=WorkplaceType.ONSITE, posted_at=TODAY - timedelta(days=2),
        found_at=NOW - timedelta(days=2), scanned="2026-10-24T00:00:00+00:00",
        salary_min=90_000, salary_max=120_000, last_seen_at=NOW - timedelta(hours=3),
    )
    base.update(kw)
    return sjp.LinkJob(**base)


def strong(n=100, **kw) -> list[sjp.LinkJob]:
    """A healthy collection: n fresh, complete jobs from n companies, found
    steadily over the last four weeks."""
    return [job(found_at=NOW - timedelta(days=(i % 4) * 7 + 1), **kw) for i in range(n)]


# ---------------------------------------------------------------- score


def test_a_healthy_collection_is_indexed():
    e = evaluate_jobs(strong(), NOW)
    assert (e.total, e.recommendation, e.issues) == (100, Recommendation.INDEX, ())
    assert e.demand_score is None  # no search data yet (PR-16)
    assert e.inventory_score == WEIGHTS["inventory"] and e.uniqueness_score == WEIGHTS["uniqueness"]


def test_the_total_leaves_out_what_has_no_data():
    e = evaluate_jobs(strong(50), NOW)
    assert e.inventory_score == 12.5  # half of inventory_full
    points = sum(v for k in WEIGHTS if (v := getattr(e, f"{k}_score")) is not None)
    assert e.total == round(100 * points / (100 - WEIGHTS["demand"]))


def test_closed_jobs_dont_count():
    e = evaluate_jobs(strong(100) + [job(closed="2026-10-20T00:00:00+00:00") for _ in range(50)], NOW)
    assert e.stats.open_jobs == 100


def test_data_quality_is_the_share_of_complete_fields():
    bare = dict(salary_min=None, salary_max=None, workplace_type=WorkplaceType.UNKNOWN, posted_at=None, metros=())
    assert evaluate_jobs(strong(), NOW).data_quality_score == 10
    assert evaluate_jobs(strong(**bare), NOW).data_quality_score == 0
    half = strong(50) + strong(50, **bare)
    assert evaluate_jobs(half, NOW).data_quality_score == 5


def test_freshness_reads_found_posted_and_verified_separately():
    old = dict(found_at=NOW - timedelta(days=40), posted_at=TODAY - timedelta(days=40), last_seen_at=None)
    assert evaluate_jobs([job(**old) for _ in range(30)], NOW).freshness_score == 0
    for recent in (
        dict(found_at=NOW - timedelta(days=1)),
        dict(posted_at=TODAY - timedelta(days=6)),  # within the 7 Eastern days, today included
        dict(last_seen_at=NOW - timedelta(days=1)),  # still listed by a crawl
    ):
        assert evaluate_jobs([job(**{**old, **recent}) for _ in range(30)], NOW).freshness_score == 20
    assert evaluate_jobs([job(**{**old, "posted_at": TODAY - timedelta(days=7)}) for _ in range(30)], NOW).freshness_score == 0


def test_stability_counts_weeks_that_brought_new_jobs():
    assert evaluate_jobs(strong(), NOW).stability_score == 10
    burst = [job(found_at=NOW - timedelta(hours=5)) for _ in range(100)]  # one import, nothing before
    assert evaluate_jobs(burst, NOW).stability_score == 2.5


# ---------------------------------------------------------------- gates


def test_too_few_jobs_never_index():
    e = evaluate_jobs(strong(19), NOW, thresholds=Thresholds(inventory_full=19))
    assert e.total >= 80 and e.issues == ("too_few_jobs",)
    assert e.recommendation == Recommendation.REVIEW


def test_one_employer_dominating():
    jobs = strong(40) + [job(company_key="acme") for _ in range(60)]
    e = evaluate_jobs(jobs, NOW)
    assert "one_employer_dominates" in e.issues and e.recommendation != Recommendation.INDEX
    assert e.employer_diversity_score == pytest.approx(15 * 0.4 / 0.5, abs=0.1)  # docked for 60% at one company


def test_too_few_employers():
    jobs = [job(company_key=f"c{i % 4}") for i in range(100)]
    assert "too_few_employers" in evaluate_jobs(jobs, NOW).issues


def test_a_company_page_has_no_diversity_to_score():
    jobs = [job(company_key="acme", found_at=NOW - timedelta(days=(i % 4) * 7 + 1)) for i in range(100)]
    e = evaluate_jobs(jobs, NOW, single_employer=True)
    assert e.employer_diversity_score is None
    assert (e.total, e.recommendation) == (100, Recommendation.INDEX)


def test_a_near_copy_of_its_parent():
    jobs = strong()
    e = evaluate_jobs(jobs, NOW, parent_size=105)
    assert "near_duplicate" in e.issues and e.uniqueness_score == pytest.approx(5 * (5 / 105) / 0.5, abs=0.1)
    assert evaluate_jobs(jobs, NOW, parent_size=200).uniqueness_score == 5  # half its parent: full marks


def test_stale():
    jobs = [job(found_at=NOW - timedelta(days=20), posted_at=None, last_seen_at=NOW - timedelta(days=15)) for _ in range(30)]
    assert "stale" in evaluate_jobs(jobs, NOW).issues
    jobs.append(job(found_at=NOW - timedelta(days=20), last_seen_at=NOW - timedelta(days=13)))
    assert "stale" not in evaluate_jobs(jobs, NOW).issues


@pytest.mark.parametrize("total,recommendation", [(80, "index"), (79, "review"), (60, "review"), (59, "noindex")])
def test_recommendation_boundaries(total, recommendation):
    # inventory alone moves the total here: 85 points out of 85 is 100
    t = Thresholds(min_jobs=1)
    n = next(n for n in range(1, 101) if evaluate_jobs(strong(n), NOW, thresholds=t).total == total)
    assert evaluate_jobs(strong(n), NOW, thresholds=t).recommendation == recommendation


def test_empty():
    e = evaluate_jobs([], NOW)
    assert e.recommendation == Recommendation.NOINDEX
    assert "too_few_jobs" in e.issues


def test_as_dict_is_json_ready():
    import json

    d = evaluate_jobs(strong(), NOW).as_dict()
    assert d["recommendation"] == "index" and d["issues"] == [] and d["stats"]["open_jobs"] == 100
    json.dumps(d)


# ----------------------------------------------------- a registered collection


def test_evaluate_collection_uses_its_dimensions_and_parent():
    remote = strong(60, workplace_type=WorkplaceType.REMOTE, standard_titles=("Software Engineer",))
    onsite = strong(60, standard_titles=("Software Engineer",))
    other = strong(30, standard_titles=("Data Engineer",), workplace_type=WorkplaceType.REMOTE)
    collection = JobCollection(key="role=software-engineer;location=remote", role="software-engineer",
                               location="remote", status=CollectionStatus.CANDIDATE)
    e = evaluate_collection(remote + onsite + other, collection, NOW)
    assert e.stats.open_jobs == 60
    # parents: software engineers (120) and remote jobs (90) — the closer one, 90, sets uniqueness
    assert e.uniqueness_score == pytest.approx(5 * (1 - 60 / 90) / 0.5, abs=0.1)


# ------------------------------------------------- the report covers the hubs


def test_report_candidates_are_todays_hubs():
    live = (
        [job(company_key="acme") for _ in range(3)]
        + [job(company_key="solo")]  # one job: no company hub
        + [job(sector=JobSector.HEALTHCARE) for _ in range(5)]
        + [job(workplace_type=WorkplaceType.REMOTE, metros=()) for _ in range(6)]
        + [job(metros=("12420", "TX")) for _ in range(4)]  # under LOCATION_MIN_JOBS
        + [job(standard_titles=("Software Engineer",), workplace_type=WorkplaceType.REMOTE)]
    )
    plan = static_hub_pages.plan_hubs(live)
    found = collection_report.candidates(live)
    kinds = {kind: {key for k, key, *_ in found if k == kind} for kind in {k for k, *_ in found}}
    assert kinds["company"] == set(plan._company_paths)
    assert kinds["location"] == set(plan._metro_paths) | ({"remote"} if None in plan._remote_paths else set())
    assert kinds["category_location"] == {f"{code}/{slug}" for code, slug in plan._metro_sector_paths}
    assert kinds["category_remote"] == {f"remote/{slug}" for slug in plan._remote_paths if slug}
    assert kinds["role"] == {"software-engineer"} and kinds["role_remote"] == {"software-engineer/remote"}
    rows = [collection_report.row(kind, key, name, evaluate_jobs(jobs, NOW, parent_size=p, single_employer=s))
            for kind, key, name, jobs, p, s in found]
    assert all(set(r) == set(collection_report.COLUMNS) for r in rows)
