"""Resolving location strings to Census metro/micro areas (app.services.geo).

The corpus is real strings from prod and the local database — Salt Lake City
alone shows up in ten spellings — plus regression cases for each bug the
resolver hit while being tuned (see the comments)."""

import pytest

from app.services import geo
from app.services.geo import all_metros, metro_by_code, metro_by_slug, resolve_metro, resolve_metros

SLC = "41620"
NYC = "35620"


def _code(entry: str) -> str | None:
    metro = resolve_metro(entry)
    return metro.code if metro else None


@pytest.mark.parametrize(
    "entry",
    [
        "Salt Lake City, UT, US",
        "Salt Lake City, Utah",
        "Salt Lake City, UT, United States",
        "Salt Lake City, UT",
        "Salt Lake City, United States of America",
        "Salt Lake City",
        "Salt Lake City, Utah, US",
        "Salt Lake City, Utah, United States",
        "Salt Lake City UT, United States of America",
        "Salt Lake City, UT, United States of America",
        "USA, UT, Salt Lake City",
        "Home Services - Salt Lake City, United States of America",
        "Sandy, UT",  # a suburb: same official metro
    ],
)
def test_every_salt_lake_city_spelling_lands_in_the_same_metro(entry):
    assert _code(entry) == SLC


def test_metros_are_the_official_census_areas_not_radius_clusters():
    # Lehi/Pleasant Grove are Utah County (Provo-Orem), not Salt Lake City; and
    # San Jose is its own metro, not part of San Francisco's.
    assert _code("Lehi, Utah") == _code("Pleasant Grove, Utah, US") == "39340"
    assert _code("Lehi, Utah") != SLC
    assert _code("San Jose, CA") != _code("San Francisco, CA")


@pytest.mark.parametrize(
    "entry, name",
    [
        ("Washington, D.C.", "Washington, DC"),
        ("Washington D.C., United States", "Washington, DC"),
        ("Washington, DC", "Washington, DC"),
        ("USA, DC, Washington (20 M St SE), United States of America", "Washington, DC"),  # "Washington" is also a state
        ("USA, WA, Seattle", "Seattle, WA"),  # Amazon style: country, state, city
        ("Seattle, WA,US, US", "Seattle, WA"),
        ("US, CA, Santa Clara, United States of America", "San Jose, CA"),
        ("United States, Washington, Redmond", "Seattle, WA"),
        ("New York, NY, United States", "New York, NY"),
        ("New York City, NY, USA", "New York, NY"),
        ("New York, New York", "New York, NY"),
        ("Brooklyn, NY", "New York, NY"),
        ("St. George, Utah, United States", "St. George, UT"),
        ("Mt. Pleasant, SC", "Charleston, SC"),
        ("Winston-Salem, NC", "Winston-Salem, NC"),
        ("Delaware, OH", "Columbus, OH"),  # a city named like a state
    ],
)
def test_spellings_and_shapes(entry, name):
    assert resolve_metro(entry).name == name


@pytest.mark.parametrize(
    "entry, name",
    [
        ("1000 Nicollet Mall, Minneapolis,MN 55403-2542, United States of America", "Minneapolis, MN"),
        ("1150 S DANZLER RD, DUNCAN, SC 29334, USA, United States of America", "Spartanburg, SC"),
        ("2260 Watson Way  Vista, CA 92083", "San Diego, CA"),
        ("480 WASHINGTON BOULEVARD JERSEY CITY, United States of America", "New York, NY"),
        ("141753-NC-Three Wells Fargo Center, Charlotte, United States of America", "Charlotte, NC"),
        ("Charlotte NC - 214 North Tryon Street, United States of America", "Charlotte, NC"),
        ("140 West St, New York, NY (NY0466), United States of America", "New York, NY"),
        ("Client Office: Washington, DC, United States of America", "Washington, DC"),
        ("Arden Hills US-MN, United States", "Minneapolis, MN"),
        ("California, Irvine 6001 Oak Canyon STE 100, United States of America", "Los Angeles, CA"),
        ("KBR Tower, USA, Houston, 601 Jefferson Street, Texas, United States of America", "Houston, TX"),
        ("San Francisco Bay Area, CA", "San Francisco, CA"),
        ("New York, United, States (Hybrid)", "New York, NY"),  # stray comma inside "United States"
        ("San Francisco- Hybrid, US", "San Francisco, CA"),  # workplace word glued on with a dash
    ],
)
def test_addresses_and_facility_text_around_a_city(entry, name):
    assert resolve_metro(entry).name == name


@pytest.mark.parametrize(
    "entry",
    [
        "London",  # a famous non-US city beats a small US namesake
        "London, UK",
        "Paris",
        "Toronto, Canada",
        "Cambridge, Ontario, Canada",  # not Cambridge, MA
        "Berlin, Berlin, DE",  # DE is Delaware here, but there's no Berlin, DE
        "Pune, MH, IN",
        "Amsterdam, NH, NL",
        "Springfield",  # too many of them to guess
        "Utah",
        "United States",
        "US",
        "Remote - US",
        "Remote, California, United States",
        "Hybrid",
        "Delta, Utah",  # a real place, but its county is in no metro area
        "Cleveland Clinic Main Campus, United States of America",  # facility, no city
        "Penn State University Park, United States of America",  # must not read as University Park, TX
        "1 Medical Plaza Drive, United States of America",
        "",
        "   ",
    ],
)
def test_ambiguous_foreign_state_only_and_facility_entries_resolve_to_nothing(entry):
    assert resolve_metro(entry) is None


def test_a_places_real_name_beats_another_places_alias():
    # GeoNames lists "Elizabethtown" as an alternate name of Hopkinsville, KY —
    # bigger than the real Elizabethtown by population.
    assert resolve_metro("Elizabethtown, KY").name == "Elizabethtown, KY"
    assert resolve_metro("Hopkinsville, KY").name != "Elizabethtown, KY"


def test_connecticut_and_puerto_rico_resolve():
    # Connecticut needs the 2023 planning regions (GeoNames uses those codes);
    # Puerto Rico is a separate GeoNames country whose municipio code is in admin1.
    assert resolve_metro("Hartford, CT").name == "Hartford, CT"
    assert resolve_metro("Bridgeport, CT") == resolve_metro("Stamford, CT")
    assert resolve_metro("San Juan, PR").name == "San Juan, PR"


def test_every_metropolitan_area_resolves_from_its_principal_city():
    """The all-major-metros sweep: each of the ~390 metropolitan areas, named by
    its principal city in the spellings people actually use."""
    state_names = {abbr: name.title() for name, abbr in geo._geo().state_by_name.items()}
    metros = [m for m in all_metros() if m.kind == "metro"]
    failures = []
    for metro in metros:
        city, state = metro.name.rsplit(", ", 1)
        spellings = [
            f"{city}, {state}",
            f"{city}, {state_names[state]}",
            f"{city}, {state}, US",
            f"{city}, {state_names[state]}, United States",
            f"{city}, {state}, United States of America",
            f"USA, {state}, {city}",
            f"{city} {state}, United States of America",
        ]
        if any(_code(s) != metro.code for s in spellings):
            failures.append(metro.name)
    assert len(metros) > 380
    assert failures == []


def test_metro_slugs_and_codes_are_unique_and_round_trip():
    metros = all_metros()
    assert len({m.slug for m in metros}) == len(metros)
    assert len({m.code for m in metros}) == len(metros)
    assert all(metro_by_slug(m.slug) == m and metro_by_code(m.code) == m for m in metros)
    assert metro_by_slug("salt-lake-city-ut").code == SLC
    assert metro_by_slug("nope") is None


def test_area_name_aliases_point_at_real_metros():
    assert all(metro_by_code(code) for code in geo._AREA_ALIASES.values())


def test_resolve_metros_dedupes_keeps_order_and_ignores_the_unresolvable():
    entries = ["San Francisco, CA", "Remote - US", "New York City, NY", "Palo Alto, CA", "Utah", "Sunnyvale, CA"]

    assert resolve_metros(entries) == [_code("San Francisco, CA"), NYC, _code("Sunnyvale, CA")]
    assert resolve_metros([]) == []
    assert resolve_metros(["Utah", "Hybrid"]) == []
