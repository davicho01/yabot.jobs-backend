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
from app.services.jobs import decode_entities

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
        ("Utah", 10),  # a state: suggested as "Utah (statewide)" instead
        ("Salt Lake County Courthouse", 5),  # a facility: no area
    ]
    monkeypatch.setattr(job_locations, "_location_counts", lambda db: counts)

    assert location_suggestions(None, "salt", 10) == [
        "Salt Lake City, UT, US",
        "Salt Lake City, Utah",
        "Salt Lake County Courthouse",
    ]
    assert location_suggestions(None, "salt", 10, unresolved_only=True) == ["Salt Lake County Courthouse"]
    assert location_suggestions(None, None, 10, unresolved_only=True) == ["Salt Lake County Courthouse"]


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

    # de-duplicated, in order: each city's metro and its state; London resolves to nothing
    assert posting.metros == [SLC, "UT", PROVO]


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

    areas = [geo.metro_by_code(c) for c in posting.metros]
    assert [a.name for a in areas if a.kind != "state"] == ["New York, NY", "San Francisco, CA", "Seattle, WA"]
    assert [a.name for a in areas if a.kind == "state"] == ["New York", "California", "Washington"]


def test_upsert_reads_the_work_type_from_the_location_text(scan_db, make_source, make_url):
    now = datetime.now(timezone.utc)

    def stored(location, workplace_type):
        posting = jobs._upsert_posting(
            scan_db, make_url(make_source()), ScanResult(success=True, title="T", location=location, workplace_type=workplace_type), now
        )
        return posting.workplace_type

    assert stored("Remote - United States", "onsite") == "remote"  # the adapter's plain-place guess is overridden
    assert stored("Boston or Remote", "unknown") == "hybrid"
    assert stored("New York, NY Office", "unknown") == "onsite"
    assert stored("New York, NY HQ", "unknown") == "unknown"  # HQ labels a site; it doesn't state the arrangement
    assert stored("Nationwide Remote Office (US99), United States of America", "onsite") == "remote"
    assert stored("Remote - United States", "hybrid") == "hybrid"  # an explicit adapter value is kept
    assert stored("San Francisco, CA", "onsite") == "onsite"  # nothing stated: unchanged
    assert stored("San Francisco, CA", "unknown") == "unknown"
    assert stored(None, "remote") == "remote"


def test_upsert_files_a_state_only_posting_under_its_state(scan_db, make_source, make_url):
    result = ScanResult(success=True, title="Engineer", location="Remote - California")

    posting = jobs._upsert_posting(scan_db, make_url(make_source()), result, datetime.now(timezone.utc))

    assert posting.metros == ["CA"]
    assert posting.workplace_type == "remote"


def test_rank_metros_suggests_states_alongside_metro_areas():
    counts = {SLC: 300, "UT": 500, NYC: 900, "NY": 1000}

    assert [(m.name, m.kind) for m, _ in rank_metros(counts, "utah", 5)] == [("Utah", "state")]
    assert [m.name for m, _ in rank_metros(counts, "new york", 5)] == ["New York, NY", "New York"]
    assert [m.name for m, _ in rank_metros(counts, "salt", 5)] == ["Salt Lake City, UT"]


def test_decode_entities():
    assert decode_entities("Sales &amp; Marketing") == "Sales & Marketing"
    assert decode_entities("United States &gt; California : Remote") == "United States > California : Remote"
    assert decode_entities("Tom&#39;s &amp;amp; Jerry") == "Tom's & Jerry"  # double-encoded too
    assert decode_entities("Plain text, 5 > 3") == "Plain text, 5 > 3"
    assert decode_entities(None) is None
    assert decode_entities("") == ""


def test_upsert_stores_decoded_text_and_places_split_correctly(scan_db, make_source, make_url):
    result = ScanResult(
        success=True,
        title="Sales &amp; Marketing Lead",
        company_name="Bob Office &amp; Warehouse",
        location="1403 - Tacoma &amp; Gordon, Canada; Austin, TX",
    )

    posting = jobs._upsert_posting(scan_db, make_url(make_source()), result, datetime.now(timezone.utc))
    scan_db.commit()

    assert posting.title == "Sales & Marketing Lead"
    assert posting.company_name == "Bob Office & Warehouse"
    assert posting.location == "1403 - Tacoma & Gordon, Canada; Austin, TX"
    assert posting.locations == ["1403 - Tacoma & Gordon, Canada", "Austin, TX"]
    # Only Austin counts: the Canadian entry no longer leaves a "Tacoma" fragment that files it under Washington.
    assert posting.metros == ["12420", "TX"]
