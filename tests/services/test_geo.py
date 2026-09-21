"""Resolving location strings to Census metro/micro areas (app.services.geo).

The corpus is real strings from prod and the local database — Salt Lake City
alone shows up in ten spellings — plus regression cases for each bug the
resolver hit while being tuned (see the comments)."""

import pytest

from app.services import geo
from app.services.geo import all_metros, metro_by_code, metro_by_slug, resolve_area_codes, resolve_entry, resolve_metro

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
        "United States",
        "US",
        "Remote - US",
        "Hybrid",
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

    # Each city files the posting under its metro *and* its state; repeats collapse.
    assert resolve_area_codes(entries) == ["41860", "CA", NYC, "NY", "41940", "UT"]
    assert resolve_area_codes([]) == []
    assert resolve_area_codes(["Remote - US", "Hybrid"]) == []


@pytest.mark.parametrize(
    "entry, name",
    [
        ("New York, NY or Remote", "New York, NY"),  # workplace words aren't part of the place
        ("New York, NY Office", "New York, NY"),
        ("New York, NY HQ USA, United States of America", "New York, NY"),
        ("New York, 1 Columbus Circle, United States of America", "New York, NY"),  # "New York" is also a state name
        ("Boston or Remote", "Boston, MA"),
        ("Kansas City", "Kansas City, MO"),  # Missouri and Kansas are one metro area, so not ambiguous
        ("7173 - Kansas City, United States of America", "Kansas City, MO"),
        ("Home Office - Illinois - Chicago Metro, United States of America", "Chicago, IL"),
        ("Home Office - Texas - Dallas/Fort Worth Metro, United States of America", "Dallas, TX"),
        ("08579 Minneapolis Headquarters 901, United States of America", "Minneapolis, MN"),
        ("Greater Boston Area", "Boston, MA"),
        ("Remote City, FL, 33412 USA", "Florida"),  # no city we know: falls back to the state
    ],
)
def test_workplace_words_and_area_words_are_not_part_of_the_place(entry, name):
    assert resolve_metro(entry).name == name


@pytest.mark.parametrize(
    "entry, state",
    [
        ("Utah", "Utah"),
        ("Remote - California", "California"),
        ("California - Remote, United States of America", "California"),
        ("Remote, California, United States", "California"),
        ("Remote - CA, United States of America", "California"),  # abbreviation, backed by "United States"
        ("CA, United States", "California"),
        ("CO, United States", "Colorado"),
        ("141278-NC-CIC Customer Information Ctr, United States of America", "North Carolina"),
        ("(JRA)TN - Elm Hill Pike, United States of America", "Tennessee"),
        ("GEORGIA - VIRTUAL - GA01, United States of America", "Georgia"),  # the state, given the US signal
        ("Somewhere Unknown, TX 75001", "Texas"),  # abbreviation backed by a ZIP
    ],
)
def test_with_no_city_an_entry_falls_back_to_its_state(entry, state):
    resolution = resolve_entry(entry)

    assert resolution.geo == "state"
    assert resolution.metro is None
    assert resolution.state.name == state
    assert resolution.state.kind == "state"


def test_a_real_place_in_a_county_outside_every_metro_still_gets_its_state():
    resolution = resolve_entry("Delta, Utah")  # a town, but its county is in no metro area

    assert resolution.geo == "city"
    assert resolution.metro is None
    assert resolution.state.name == "Utah"


def test_a_city_in_a_metro_area_is_filed_under_the_metro_and_its_state():
    resolution = resolve_entry("Salt Lake City, UT")

    assert (resolution.metro.name, resolution.state.name) == ("Salt Lake City, UT", "Utah")
    assert resolve_area_codes(["Salt Lake City, UT", "Sandy, Utah"]) == [SLC, "UT"]


@pytest.mark.parametrize(
    "entry",
    [
        # Two-letter codes that are US states *and* countries: never read as a state
        # without a US signal or a ZIP, and never when another country is named.
        "IN - Hyderabad_HQ, India",
        "Bengaluru, KA,IN, IN",
        "Pune, MH, IN",
        "Berlin, Berlin, DE",
        "Amsterdam, NH, NL",
        "Panamá, Provincia de Panamá,PA, PA",
        "Vancouver, BC, Canada",
        "Remote - India",
        "Canada (Remote)",
        "Tbilisi, Georgia",  # the country, not the state: no US signal
        "CA",
        "CO",
        "Georgia",
        "Washington",  # the state or the capital: not guessed
        "Washington, United States",
        "Remote",
        "Hybrid",
        "Asia",
        "UAE, Dubai",
        # A country (or a province) sharing its name with a small US town:
        "Belgium",  # Belgium, WI
        "Brazil",  # Brazil, IN
        "Mexico",  # Mexico, MO
        "Mexico - Mexico City - Av. Insurgentes Sur 730 - Remote, Mexico",
        "CAN - Ontario - Toronto, Canada",  # Ontario, CA is a US city
        "AMER - Canada - Ontario - Toronto - University Ave, Canada",
        # "NE" here is a street direction, not Nebraska:
        "Bend 1501 NE Medical Center Dr, United States of America",
    ],
)
def test_no_state_is_invented_for_foreign_or_ambiguous_entries(entry):
    resolution = resolve_entry(entry)

    assert resolution.state is None
    assert resolution.metro is None


@pytest.mark.parametrize(
    "entry, workplace, geo",
    [
        ("Remote", "remote", "empty"),
        ("Hybrid", "hybrid", "empty"),
        ("Remote - California", "remote", "state"),
        ("Remote/Teleworker US", "remote", "country"),  # "US" is what is left after the workplace words
        ("Boston or Remote", "remote", "city"),  # the entry itself says only "remote"; a city + remote is hybrid at posting level
        ("Remote or Office", "hybrid", "empty"),
        ("New York, NY Office", "onsite", "city"),
        ("San Francisco- Hybrid, US", "hybrid", "city"),
        ("Home Office - Illinois", None, "state"),  # "Home Office" is a company's HQ, not a work-from-home signal
        ("Salt Lake City, UT", None, "city"),
        ("Remote - India", "remote", "country"),
        ("United States", None, "country"),
        ("Cleveland Clinic Main Building", None, "other"),
    ],
)
def test_entries_report_their_workplace_word_and_what_kind_of_place_is_left(entry, workplace, geo):
    resolution = resolve_entry(entry)

    assert resolution.workplace == workplace
    assert resolution.geo == geo


def test_generic_words_picked_out_of_an_address_are_not_cities():
    # GeoNames lists "North" as an alternate name of North Ogden, UT — "214 North
    # Tryon Street" must not become Ogden.
    assert resolve_metro("214 North Tryon Street, United States of America") is None
    assert resolve_metro("Charlotte NC - 214 North Tryon Street, United States of America").name == "Charlotte, NC"


def test_an_explicit_us_city_and_state_still_wins_even_when_the_name_is_also_a_country():
    assert resolve_entry("Mexico, MO").state.name == "Missouri"
    assert resolve_entry("Lebanon, Ohio").state.name == "Ohio"
    assert resolve_entry("Ontario, CA").state.name == "California"


def test_a_facility_state_code_must_be_hyphen_or_underscore_delimited():
    assert resolve_entry("111432-TX-Las Colinas Bldg A, Irving Campus, United States of America").state.name == "Texas"
