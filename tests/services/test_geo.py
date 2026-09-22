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
        "Remote - Ontario",  # the province, not Ontario, California
        "210 CITATION DRIVE,L4K 2V2,CONCORD,CA, Canada",  # "CA" is Canada's code here, not California's
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
        ("Nationwide Remote Office (US99), United States of America", "remote", "country"),  # a remote office is remote
        ("Maryland Remote Office (MD99), United States of America", "remote", "state"),
        ("Mountain View, California (HQ)", None, "city"),  # HQ labels a site; it doesn't say how the job is worked
        ("New York, NY HQ USA, United States of America", None, "city"),
        ("Cleveland Clinic Main Campus", None, "other"),
        ("Salt Lake City, UT", None, "city"),
        ("Remote - India", "remote", "country"),
        ("United States", None, "country"),
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


def test_remote_plus_a_bare_state_name_that_is_also_a_city_means_the_state():
    # "Remote - New York" is remote within the state, not a New York City job.
    remote = resolve_entry("Remote - New York, United States of America")
    assert (remote.geo, remote.metro, remote.state.name, remote.workplace) == ("state", None, "New York", "remote")
    # ...while the city on its own is still the city.
    assert resolve_entry("New York, United States of America").metro.name == "New York, NY"


def test_a_us_city_and_state_is_unaffected_by_the_country_code_guard():
    assert resolve_entry("Concord, CA").state.name == "California"
    assert resolve_entry("Concord, CA, United States").state.name == "California"


# ------------------------------------------------------------- radius search


def test_a_city_search_is_by_distance_from_the_city_itself():
    place = geo.search_place("West Bountiful, Utah")

    assert (place.name, place.state) == ("West Bountiful", "UT")
    assert geo.place_label(place) == "West Bountiful, Utah"
    # A city is searched by distance, not by the metro area it happens to be in
    # (Ogden-Clearfield runs 43 miles north to Brigham City).
    assert geo.search_areas("West Bountiful, Utah") is None
    for text in ("Utah", "Utah, United States", "Canada", "Remote", "Salt", "Cleveland Clinic Main Campus"):
        assert geo.search_place(text) is None


def test_a_state_or_the_united_states_searches_states_and_other_text_is_a_plain_search():
    assert geo.search_areas("Utah, United States") == ["UT"]
    assert geo.search_areas("Utah") == ["UT"]
    assert len(geo.search_areas("United States")) == 52  # 50 states + DC + Puerto Rico
    assert geo.search_areas("Bay Area") == ["41860"]  # an area alias: a metro with no single city
    for text in ("Canada", "Remote", "Salt", "Cleveland Clinic Main Campus", "Toronto, Ontario, Canada"):
        assert geo.search_areas(text) is None


def test_nearby_state_codes_are_the_states_within_range_own_state_first():
    bountiful = geo.search_place("Bountiful, Utah")
    assert geo.nearby_state_codes(bountiful, 25) == ("UT",)

    # Kansas City, MO sits on the state line: the Kansas side is well inside 25 miles.
    kansas_city = geo.search_place("Kansas City, Missouri")
    assert geo.nearby_state_codes(kansas_city, 25)[0] == "MO"
    assert "KS" in geo.nearby_state_codes(kansas_city, 25)


def test_nearest_place_label_is_the_closest_known_city_by_coordinates():
    bountiful = geo.search_place("Bountiful, Utah")
    # A point right on Bountiful itself: the exact match wins over anything nearby.
    assert geo.nearest_place_label(bountiful.lat, bountiful.lon) == "Bountiful, Utah, United States"

    west_bountiful = geo.search_place("West Bountiful, Utah")
    # A point right on the smaller neighbor: that one wins instead.
    assert geo.nearest_place_label(west_bountiful.lat, west_bountiful.lon) == "West Bountiful, Utah, United States"


def test_nearest_place_label_is_none_too_far_from_any_known_city():
    # The middle of the Pacific — nowhere near any US place.
    assert geo.nearest_place_label(0.0, -140.0) is None


def test_nearest_default_location_label_prefers_the_metro_area_over_the_town():
    # West Bountiful itself isn't a metro's principal city — its metro is
    # Ogden-Clearfield, not Salt Lake City — so this also checks the right
    # area is picked, not just any area.
    west_bountiful = geo.search_place("West Bountiful, Utah")
    assert geo.search_metro("West Bountiful, Utah (metro area)").name == "Ogden, UT"  # sanity check on the fixture

    assert geo.nearest_default_location_label(west_bountiful.lat, west_bountiful.lon) == "Ogden, Utah (metro area)"


def test_nearest_default_location_label_falls_back_to_the_town_outside_any_metro():
    # Bethel, AK: a real town with no CBSA at all (no metro *or* micro area).
    bethel = geo.search_place("Bethel, Alaska")
    assert bethel is not None

    assert geo.nearest_default_location_label(bethel.lat, bethel.lon) == "Bethel, Alaska, United States"


def test_nearest_default_location_label_is_none_too_far_from_any_known_city():
    assert geo.nearest_default_location_label(0.0, -140.0) is None


def test_resolve_places_gives_coordinates_only_for_entries_that_name_a_city():
    points = geo.resolve_places(
        ["West Bountiful, UT", "West Bountiful, Utah, US", "Salt Lake City, UT", "Remote", "Utah", "London, UK",
         "Cleveland Clinic Main Campus"]
    )

    west_bountiful = geo.search_place("West Bountiful, Utah")
    salt_lake = geo.search_place("Salt Lake City, UT")
    # two spellings of one city are one point; a state, "Remote", a foreign city and a facility add none
    assert points == [[west_bountiful.lat, west_bountiful.lon], [salt_lake.lat, salt_lake.lon]]
    assert geo.resolve_places([]) == []


# ------------------------------------------------------------ place suggestions


def test_place_suggestions_are_only_cities_states_and_the_united_states():
    for q in ("", "b", "salt", "cleveland", "utah", "new", "makiki", "fenway", "milford"):
        for label in geo.place_suggestions(q, 50):
            assert label == "United States" or label.endswith((", United States", " (metro area)")), label
            assert not any(ch.isdigit() for ch in label) and " - " not in label, label
            place = label.removesuffix(" (metro area)")
            assert "/" not in place and "(" not in place, label  # GeoNames' composite district names


def test_place_suggestions_rank_prefix_matches_first_states_before_cities():
    assert geo.place_suggestions("bount", 5) == ["Bountiful, Utah, United States", "West Bountiful, Utah, United States"]
    assert geo.place_suggestions("bountiful, ut", 5)[0] == "Bountiful, Utah, United States"  # abbreviations work
    utah = geo.place_suggestions("utah", 3)
    assert utah[0] == "Utah, United States"
    assert utah[1] == "Salt Lake City, Utah, United States"  # then cities that contain it, biggest first
    assert geo.place_suggestions("united", 3)[0] == "United States"
    assert geo.place_suggestions("usa", 3) == ["United States"]  # "usa" is not found inside "thousand"


def test_place_suggestions_with_nothing_typed_lead_with_the_united_states_then_big_cities():
    suggestions = geo.place_suggestions("", 4)

    assert suggestions[0] == "United States"
    assert suggestions[1] == "New York City, New York, United States"
    assert len(suggestions) == 4


def test_a_generic_word_alternate_name_does_not_pull_text_into_a_town():
    # GeoNames lists "North" as an alternate name of North Salt Lake, so anything that
    # reduced to "north" used to land there.
    for entry in ("North Campus, United States of America", "North Skull Valley, UT, USA, United States of America"):
        resolution = resolve_entry(entry)
        assert resolution.metro is None and resolution.place is None, entry
    # the town itself, and a state-only reading of the second entry, still work
    assert resolve_entry("North Salt Lake, UT").place.name == "North Salt Lake"
    assert resolve_entry("North Skull Valley, UT, USA, United States of America").state.name == "Utah"


# ------------------------------------------------------------------ metro-area search


def test_a_metro_area_is_offered_right_after_its_principal_city():
    assert geo.place_suggestions("salt lake", 3) == [
        "Salt Lake City, Utah, United States",
        "Salt Lake City, Utah (metro area)",
        "South Salt Lake, Utah, United States",
    ]
    assert geo.place_suggestions("salt lake city metro", 3) == ["Salt Lake City, Utah (metro area)"]
    assert geo.place_suggestions("salt lake city, ut", 2) == [
        "Salt Lake City, Utah, United States",
        "Salt Lake City, Utah (metro area)",
    ]
    # nothing typed: the big cities, not their metro areas
    assert not any(label.endswith("(metro area)") for label in geo.place_suggestions("", 20))


def test_metro_area_text_is_a_metro_search_and_the_city_alone_is_not():
    metro = geo.search_metro("Salt Lake City, Utah (metro area)")

    assert (metro.name, metro.kind) == ("Salt Lake City, UT", "metro")
    assert geo.search_metro("salt lake city (Metro Area)") == metro  # forgiving about case and the state
    assert geo.search_metro("West Bountiful, Utah (metro area)").name == "Ogden, UT"  # any city, not just a principal one
    assert geo.search_metro("Salt Lake City, Utah") is None  # the city itself is a distance search
    for text in ("Utah (metro area)", "Canada (metro area)", "Remote (metro area)"):
        assert geo.search_metro(text) is None


def test_every_metro_area_label_round_trips_to_its_own_area():
    metros = [m for m in all_metros() if m.kind == "metro"]
    labels = [geo.metro_area_label(m) for m in metros]

    assert len(labels) == len(set(labels))  # no two areas share a label
    assert all(geo.search_metro(label) == metro for label, metro in zip(labels, metros))
    assert {e.label for e in geo._suggestions() if e.kind == "metro"} == set(labels)
