"""Metro-area search: ranking the area suggestions, hiding places that belong to an
area from the raw-location suggestions, the indexed filter's SQL, and storing
`metros` on scan."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import JobPosting
from app.services import geo, job_locations, jobs
from app.services.adapters.base import ScanResult
from app.services.job_locations import location_suggestions, rank_metros

SLC, PROVO, NYC, SF = "41620", "39340", "35620", "41860"


def test_rank_metros_puts_name_prefix_first_then_title_matches_by_count():
    counts = {NYC: 700, SF: 500, SLC: 300, PROVO: 100, "19100": 50}  # ordered most postings first

    assert [m.name for m, _ in rank_metros(counts, "salt", 10)] == ["Salt Lake City, UT"]
    assert [m.name for m, _ in rank_metros(counts, "SAN fran", 10)] == ["San Francisco, CA"]
    # "fort worth" is in the official title "Dallas-Fort Worth-Arlington", not the short name.
    assert [m.name for m, _ in rank_metros(counts, "fort worth", 10)] == ["Dallas, TX"]
    assert [m.name for m, _ in rank_metros(counts, "", 3)] == ["New York, NY", "San Francisco, CA", "Salt Lake City, UT"]
    assert rank_metros(counts, "zzzz", 10) == []


def test_rank_metros_ignores_codes_that_are_no_longer_metros_and_reports_counts():
    ranked = rank_metros({"99999": 5, SLC: 3}, None, 10)

    assert [(m.name, n) for m, n in ranked] == [("Salt Lake City, UT", 3)]


def test_rank_metros_matches_punctuation_insensitively():
    assert [m.name for m, _ in rank_metros({"41180": 4}, "st louis", 5)] == ["St. Louis, MO"]


def test_unresolved_only_hides_places_that_belong_to_a_metro(monkeypatch):
    counts = [
        ("Salt Lake City, UT, US", 30),
        ("Salt Lake City, Utah", 20),
        ("Utah", 10),  # a state: no metro
        ("Salt Lake County Courthouse", 5),  # a facility: no metro
    ]
    monkeypatch.setattr(job_locations, "_location_counts", lambda db: counts)

    assert location_suggestions(None, "salt", 10) == [
        "Salt Lake City, UT, US",
        "Salt Lake City, Utah",
        "Salt Lake County Courthouse",
    ]
    assert location_suggestions(None, "salt", 10, unresolved_only=True) == ["Salt Lake County Courthouse"]
    assert location_suggestions(None, None, 10, unresolved_only=True) == ["Utah", "Salt Lake County Courthouse"]


def test_metro_filter_compiles_to_an_indexable_containment_check():
    stmt = select(JobPosting.id).where(JobPosting.metros.contains([SLC]))

    sql = " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())

    assert "job_postings.metros @>" in sql  # the operator ix_job_postings_metros (GIN, jsonb_path_ops) serves


def test_upsert_stores_the_metro_areas_of_every_location(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    result = ScanResult(
        success=True,
        title="Engineer",
        # two spellings of one metro, a suburb of another, a state, a tag, a foreign city
        location="Salt Lake City, UT; Salt Lake City, Utah, US; Lehi, Utah; Utah | Remote; London, UK",
    )

    posting = jobs._upsert_posting(scan_db, url_row, result, datetime.now(timezone.utc))
    scan_db.commit()

    assert posting.metros == [SLC, PROVO]  # de-duplicated, in order; the rest resolve to nothing


def test_upsert_without_a_resolvable_location_stores_no_metros(scan_db, make_source, make_url):
    url_row = make_url(make_source())

    posting = jobs._upsert_posting(
        scan_db, url_row, ScanResult(success=True, title="Engineer", location="Remote"), datetime.now(timezone.utc)
    )
    scan_db.commit()

    assert posting.metros == []


def test_a_multi_location_posting_carries_every_area_it_lists(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    result = ScanResult(success=True, title="Engineer", location="New York City, NY; San Francisco, CA; Seattle, WA")

    posting = jobs._upsert_posting(scan_db, url_row, result, datetime.now(timezone.utc))

    assert [geo.metro_by_code(c).name for c in posting.metros] == ["New York, NY", "San Francisco, CA", "Seattle, WA"]
