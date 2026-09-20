"""Multi-location postings: parsing the joined `location` string into
individual locations, ranking them as search suggestions, and the adapter
helpers that produce the "; "-joined string in the first place."""

from datetime import datetime, timezone

import pytest

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import JobPosting
from app.services import jobs
from app.services.adapters import amazon, base, gem
from app.services.adapters.base import ScanResult
from app.services.job_locations import location_matches, rank_locations, split_locations


@pytest.mark.parametrize("raw", [None, "", "   ", ";", " ; | ; "])
def test_split_locations_of_nothing_is_empty(raw):
    assert split_locations(raw) == []


def test_split_locations_single_location_is_kept_whole():
    # The comma is part of one place — only ";" and "|" separate places.
    assert split_locations("Seattle, WA, United States") == ["Seattle, WA, United States"]


def test_split_locations_splits_on_semicolons_in_order():
    assert split_locations("Austin, TX; Boston, MA; Seattle, WA") == ["Austin, TX", "Boston, MA", "Seattle, WA"]


def test_split_locations_drops_workplace_tags_after_a_pipe():
    # og:description style: "<place> | <workplace type>", chained with ";".
    assert split_locations("Arizona | Remote; Florida | Remote; Utah | Hybrid") == ["Arizona", "Florida", "Utah"]


def test_split_locations_treats_pipe_as_a_separator_between_places_too():
    raw = "Remote-Friendly (Travel-Required) | San Francisco, CA | Washington, DC"
    assert split_locations(raw) == ["San Francisco, CA", "Washington, DC"]


def test_split_locations_keeps_remote_entries_that_name_a_place():
    assert split_locations("Remote - USA; Austin, TX; Remote, United States") == [
        "Remote - USA",
        "Austin, TX",
        "Remote, United States",
    ]


def test_split_locations_dedupes_case_insensitively_keeping_first_seen():
    assert split_locations("Austin, TX; austin, tx; Boston, MA; AUSTIN, TX") == ["Austin, TX", "Boston, MA"]


def test_split_locations_real_world_messy_string():
    raw = (
        "New York City, NY; Remote-Friendly (Travel-Required) | San Francisco, CA | Washington, DC; "
        "San Francisco, CA | New York City, NY"
    )
    assert split_locations(raw) == ["New York City, NY", "San Francisco, CA", "Washington, DC"]


def test_split_locations_drops_a_truncated_last_fragment():
    # Adapters cut `location` at 255 chars and end it with "..." — that tail
    # is a fragment of a place, not a place.
    assert split_locations("Austin, TX; Boston, MA; Seatt...") == ["Austin, TX", "Boston, MA"]


def test_split_locations_caps_entry_count_and_length():
    many = "; ".join(f"City {i}, ST" for i in range(300))
    assert len(split_locations(many)) == 100
    assert len(split_locations("x" * 1000)[0]) == 255


COUNTS = [("Seattle, WA", 30), ("Austin, TX", 20), ("East Seattle", 5), ("Sea Girt, NJ", 2)]


def test_rank_locations_puts_prefix_matches_before_substring_matches():
    assert rank_locations(COUNTS, "sea", 10) == ["Seattle, WA", "Sea Girt, NJ", "East Seattle"]


def test_rank_locations_is_case_insensitive_and_trims():
    assert rank_locations(COUNTS, "  AUSTIN ", 10) == ["Austin, TX"]


def test_rank_locations_without_a_query_returns_most_used_first_up_to_limit():
    assert rank_locations(COUNTS, None, 2) == ["Seattle, WA", "Austin, TX"]
    assert rank_locations(COUNTS, "", 2) == ["Seattle, WA", "Austin, TX"]


def test_rank_locations_with_no_match_is_empty():
    assert rank_locations(COUNTS, "zzz", 10) == []


def _place(locality, region, country):
    return {"address": {"addressLocality": locality, "addressRegion": region, "addressCountry": country}}


def test_job_ld_location_single_place_is_unchanged():
    assert base.job_ld_location({"jobLocation": _place("Seattle", "WA", "US")}) == "Seattle, WA, US"


def test_job_ld_location_keeps_every_place_of_a_multi_location_posting():
    job_ld = {"jobLocation": [_place("Seattle", "WA", "US"), _place("Austin", "TX", "US")]}
    assert base.job_ld_location(job_ld) == "Seattle, WA, US; Austin, TX, US"


def test_job_ld_location_dedupes_and_skips_unusable_places():
    job_ld = {"jobLocation": [_place("Seattle", "WA", "US"), {"address": None}, "junk", _place("Seattle", "WA", "US")]}
    assert base.job_ld_location(job_ld) == "Seattle, WA, US"


@pytest.mark.parametrize("job_ld", [{}, {"jobLocation": None}, {"jobLocation": []}, {"jobLocation": "x"}])
def test_job_ld_location_without_a_usable_place_is_none(job_ld):
    assert base.job_ld_location(job_ld) is None


def test_job_ld_location_long_lists_are_capped_with_a_marker_that_split_drops():
    job_ld = {"jobLocation": [_place(f"City{i}", "ST", "US") for i in range(60)]}

    location = base.job_ld_location(job_ld)

    assert len(location) == 255 and location.endswith("...")
    entries = split_locations(location)
    assert entries[0] == "City0, ST, US"
    assert all(not e.endswith("...") for e in entries)


def test_parse_og_description_joins_multiple_locations_with_semicolons():
    location, workplace_type = base.parse_og_description("Utah | Hybrid; Arizona | Remote")

    assert location == "Utah; Arizona"
    assert split_locations(location) == ["Utah", "Arizona"]


def test_upsert_stores_the_individual_locations(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    result = ScanResult(success=True, title="Engineer", location="Austin, TX; Boston, MA | Hybrid; Seattle, WA")

    posting = jobs._upsert_posting(scan_db, url_row, result, datetime.now(timezone.utc))
    scan_db.commit()

    assert posting.location == "Austin, TX; Boston, MA | Hybrid; Seattle, WA"
    assert posting.locations == ["Austin, TX", "Boston, MA", "Seattle, WA"]


def test_upsert_without_a_location_stores_an_empty_list(scan_db, make_source, make_url):
    url_row = make_url(make_source())

    posting = jobs._upsert_posting(
        scan_db, url_row, ScanResult(success=True, title="Engineer"), datetime.now(timezone.utc)
    )
    scan_db.commit()

    assert posting.locations == []


def test_upsert_keeps_locations_past_the_display_columns_length_limit(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    names = [f"City {i:02d}, ST" for i in range(40)]  # ~440 chars joined, over the 255 column
    result = ScanResult(success=True, title="Engineer", location="; ".join(names))

    posting = jobs._upsert_posting(scan_db, url_row, result, datetime.now(timezone.utc))
    scan_db.commit()

    assert len(posting.location) == 255
    assert posting.locations == names


def test_rescan_replaces_the_locations(scan_db, make_source, make_url):
    url_row = make_url(make_source())
    now = datetime.now(timezone.utc)
    jobs._upsert_posting(scan_db, url_row, ScanResult(success=True, title="T", location="Austin, TX; Boston, MA"), now)
    scan_db.commit()

    posting = jobs._upsert_posting(scan_db, url_row, ScanResult(success=True, title="T", location="Seattle, WA"), now)
    scan_db.commit()

    assert posting.locations == ["Seattle, WA"]


def test_gem_joins_multiple_locations_with_semicolons():
    job = {"locations": [{"name": "Austin, TX"}, {"name": "New York, NY"}, {"name": "Austin, TX"}]}

    location = gem._location_of(job)

    # Each name already contains a comma, so ", " would be unsplittable.
    assert location == "Austin, TX; New York, NY"
    assert split_locations(location) == ["Austin, TX", "New York, NY"]


def test_amazon_joins_multiple_locations_with_semicolons():
    html = (
        '<div class="association location-icon"><ul class="association-content">'
        "<li>US, WA, Seattle</li><li>US, CA, San Francisco</li></ul></div>"
    )

    location = amazon._location_of(html)

    assert location == "US, WA, Seattle; US, CA, San Francisco"
    assert split_locations(location) == ["US, WA, Seattle", "US, CA, San Francisco"]


def test_location_matches_compiles_to_a_correlated_exists_over_the_locations_array():
    # The route's SQL only ever runs on Postgres (jsonb_array_elements_text),
    # which these SQLite-backed tests can't execute — so at least check that it
    # builds and has the shape we rely on: one EXISTS per posting row, reading
    # the *outer* job_postings.locations rather than joining a second copy.
    stmt = select(JobPosting.id).where(location_matches("%Sunnyvale%"))

    sql = " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())  # collapse the line breaks

    assert "EXISTS (SELECT entry FROM jsonb_array_elements_text(job_postings.locations) AS entry" in sql
    assert "entry ILIKE" in sql
    assert sql.count("FROM job_postings") == 1
