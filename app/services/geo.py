"""Resolve a posting's US location strings to a Census metro/micro area (CBSA).

The same place is spelled many ways ("Salt Lake City, UT, US" / "…, Utah" /
"Salt Lake City UT, United States of America" / "USA, UT, Salt Lake City" /
"Home Services - Salt Lake City, …"), so people can't just search "Salt Lake
City". Each entry is reduced to a US city + state, mapped to its county
(GeoNames) and from there to the official CBSA that county belongs to (Census
delineation) — see build_geo_data.py for where the bundled files under
app/data/geo/ come from. Deterministic, offline, no network or LLM.

Precision over recall: an entry only resolves when the place is unambiguous.
States, countries, "Remote", non-US places and facility names it can't parse
resolve to nothing and stay findable by plain text search.
"""

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "geo"

# A bare city name (no state) is only trusted when the biggest US place with
# that name is this many times bigger than the runner-up ("Salt Lake City" yes,
# "Springfield" no).
_DOMINANCE_RATIO = 5

_US_COUNTRY_TOKENS = {"us", "usa", "united states", "united states of america", "america"}

# Words that mean the same thing spelled short or long. Applied to city names
# only (never to state tokens: "MT" is Montana, not "Mount").
_CITY_WORD_EXPANSIONS = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount"}

_FACILITY_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
# "Arden Hills US-MN", "US-CA - Santa Clara": a state code written as US-XX / US XX.
_US_STATE_CODE_RE = re.compile(r"\bUS[- ]([A-Za-z]{2})\b")

# Well-known area names people put in a location field instead of a city.
# Values are CBSA codes (a test asserts every one exists).
_AREA_ALIASES = {
    "san francisco bay area": "41860", "sf bay area": "41860", "bay area": "41860",
    "silicon valley": "41940",
    "greater boston": "14460",
    "greater new york": "35620", "new york metro": "35620", "nyc metro": "35620",
    "dfw": "19100", "dallas fort worth": "19100", "dallas fort worth metroplex": "19100",
    "twin cities": "33460",
    "research triangle": "39580",
}

# Formatting quirks seen in scraped data: a stray comma inside "United, States",
# and a workplace word tacked on with a dash ("San Francisco- Hybrid").
_SPLIT_COUNTRY_RE = re.compile(r"\bunited\s*,\s*states\b", re.IGNORECASE)
_WORKPLACE_SUFFIX_RE = re.compile(r"\s*[-–—]\s*(?:hybrid|remote|on-?site|in-?office)\b", re.IGNORECASE)

# How many trailing words of a street-address token to try as the city
# ("6400 LAS COLINAS BLVD IRVING" -> "irving"; "2260 Watson Way Vista" -> "vista").
_MAX_CITY_WORDS = 4


@dataclass(frozen=True)
class Metro:
    code: str  # CBSA code, e.g. "41620"
    slug: str  # url-safe, unique, e.g. "salt-lake-city-ut"
    name: str  # short display name: principal city + first state, e.g. "Salt Lake City, UT"
    title: str  # official CBSA title, e.g. "Salt Lake City, UT"
    kind: str  # "metro" | "micro"


@dataclass(frozen=True)
class _Place:
    state: str
    county_fips: str
    population: int


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Lowercase, accent/punctuation-free, single-spaced: "D.C." -> "dc"."""
    text = _strip_accents(text).casefold().replace("&", " and ")
    text = re.sub(r"[.'’`]", "", text)
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


def _city_key(text: str) -> str:
    return " ".join(_CITY_WORD_EXPANSIONS.get(word, word) for word in _normalize(text).split())


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _strip_accents(text).lower()).strip("-")


class _Geo:
    def __init__(self) -> None:
        self.state_by_abbr: dict[str, str] = {}
        self.state_by_name: dict[str, str] = {}
        with (_DATA_DIR / "us_states.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                self.state_by_abbr[row["abbr"].lower()] = row["abbr"]
                self.state_by_name[_normalize(row["name"])] = row["abbr"]

        # Within a state a place's real name beats another place's alias:
        # GeoNames lists "Elizabethtown" as an alternate name of Hopkinsville, and
        # by population alone that would outrank the actual Elizabethtown.
        self.places_in_state: dict[tuple[str, str], list[_Place]] = {}
        self.aliases_in_state: dict[tuple[str, str], list[_Place]] = {}
        self.places_by_name: dict[str, list[_Place]] = {}
        with (_DATA_DIR / "us_places.tsv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                place = _Place(row["state"], row["county_fips"], int(row["population"]))
                primary_keys = {_city_key(row["name"]), _city_key(row["ascii"])}
                alias_keys = {_city_key(a) for a in row["aliases"].split("|") if a} - primary_keys
                for key in primary_keys:
                    self.places_in_state.setdefault((place.state, key), []).append(place)
                for key in alias_keys:
                    self.aliases_in_state.setdefault((place.state, key), []).append(place)
                for key in primary_keys | alias_keys:
                    self.places_by_name.setdefault(key, []).append(place)
        for places in (*self.places_in_state.values(), *self.aliases_in_state.values(), *self.places_by_name.values()):
            places.sort(key=lambda p: -p.population)

        self.world_guard: dict[str, int] = {}
        with (_DATA_DIR / "world_city_guard.tsv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                key = _city_key(row["name"])
                self.world_guard[key] = max(self.world_guard.get(key, 0), int(row["population"]))

        self.county_cbsa: dict[str, str] = {}
        titles: dict[str, tuple[str, str]] = {}
        with (_DATA_DIR / "us_cbsa_counties.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                self.county_cbsa[row["county_fips"]] = row["cbsa_code"]
                titles[row["cbsa_code"]] = (row["cbsa_title"], row["kind"])

        self.metro_by_code: dict[str, Metro] = {}
        self.metro_by_slug: dict[str, Metro] = {}
        for code, (title, kind) in sorted(titles.items(), key=lambda kv: kv[1][0]):
            name = self._short_name(title)
            slug = _slugify(name)
            if slug in self.metro_by_slug:  # two areas sharing a principal city + first state
                slug = f"{slug}-{code}"
            metro = Metro(code=code, slug=slug, name=name, title=title, kind=kind)
            self.metro_by_code[code] = metro
            self.metro_by_slug[slug] = metro

    def _short_name(self, title: str) -> str:
        """"New York-Newark-Jersey City, NY-NJ-PA" -> "New York, NY": the first
        principal city and first state. Census joins principal cities with "-"
        (or "--" when a city's own name has a hyphen), so a title like
        "Winston-Salem, NC" is a single hyphenated city, not two."""
        cities, _, states = title.rpartition(", ")
        first_state = states.split("-")[0]
        if (first_state, _city_key(cities)) in self.places_in_state or (first_state, _city_key(cities)) in self.aliases_in_state:
            principal = cities
        else:
            principal = cities.split("--")[0].split("-")[0]
        return f"{principal}, {first_state}"


@lru_cache(maxsize=1)
def _geo() -> _Geo:
    return _Geo()


def search_key(text: str) -> str:
    """Case/accent/punctuation-insensitive form of `text` for matching what a
    user typed against area names ("st louis" finds "St. Louis, MO")."""
    return _normalize(text)


def all_metros() -> list[Metro]:
    return list(_geo().metro_by_code.values())


def metro_by_code(code: str) -> Metro | None:
    return _geo().metro_by_code.get(code)


def metro_by_slug(slug: str) -> Metro | None:
    return _geo().metro_by_slug.get(slug)


def _state_of(token: str) -> str | None:
    """The US state a token names: "ut", "utah", or an abbreviation followed
    only by a ZIP ("sc 29334", "mn 55403 2542")."""
    geo = _geo()
    if len(token) == 2 and token in geo.state_by_abbr:
        return geo.state_by_abbr[token]
    if token in geo.state_by_name:
        return geo.state_by_name[token]
    words = token.split()
    if len(words) > 1 and words[0] in geo.state_by_abbr and len(words[0]) == 2 and all(w.isdigit() for w in words[1:]):
        return geo.state_by_abbr[words[0]]
    return None


def _place_metro(place: _Place | None) -> Metro | None:
    if place is None:
        return None
    geo = _geo()
    code = geo.county_cbsa.get(place.county_fips)
    return geo.metro_by_code.get(code) if code else None


def _lone_city_metro(key: str) -> Metro | None:
    """A city name with no state to pin it down — trusted only when it's clearly
    the biggest US place of that name ("Salt Lake City" yes, "Springfield" no)
    and no famous non-US city shares it ("Paris", "London")."""
    geo = _geo()
    candidates = geo.places_by_name.get(key)
    if not candidates:
        return None
    best = candidates[0]
    runner_up = candidates[1].population if len(candidates) > 1 else 0
    if best.population < _DOMINANCE_RATIO * max(runner_up, 1) or geo.world_guard.get(key, 0) > best.population:
        return None
    return _place_metro(best)


def _phrases(token: str, *, ngrams: bool) -> list[str]:
    """The token itself, then (for text with an address or facility name around
    the city) its trailing and leading 1-4 word groups, longest first:
    "2260 watson way vista" -> "vista"; "irvine 6001 oak canyon ste 100" -> "irvine"."""
    words = token.split()
    phrases = [token]
    if ngrams:
        for size in range(min(len(words) - 1, _MAX_CITY_WORDS), 0, -1):
            phrases.append(" ".join(words[-size:]))
            phrases.append(" ".join(words[:size]))
    return list(dict.fromkeys(phrases))


def _city_in_state(state: str, token: str, *, ngrams: bool) -> Metro | None:
    """A city of `state` named by `token`. The state pins the match, so the
    looser n-gram matching can't drift to a same-named place elsewhere."""
    geo = _geo()
    for phrase in _phrases(token, ngrams=ngrams):
        if phrase.isdigit():
            continue
        key = (state, _city_key(phrase))
        places = geo.places_in_state.get(key) or geo.aliases_in_state.get(key)
        if places:
            return _place_metro(places[0])
    return None


def _resolve_text(text: str) -> Metro | None:
    geo = _geo()
    tokens = [t for t in (_normalize(part) for part in text.split(",")) if t]
    us_signal = any(t in _US_COUNTRY_TOKENS for t in tokens)
    # Country words and bare numbers (ZIP codes, street numbers) carry no city.
    tokens = [t for t in tokens if t not in _US_COUNTRY_TOKENS and not t.isdigit()]
    if not tokens:
        return None

    for token in tokens:
        if token in _AREA_ALIASES:
            return geo.metro_by_code[_AREA_ALIASES[token]]

    state: str | None = None
    whole: list[str] = []  # tokens to try as an exact city name, most likely first
    loose: list[str] = []  # tokens that may have address/facility text around the city
    first_state = _state_of(tokens[0]) if len(tokens) >= 2 else None
    if first_state and (
        _state_of(tokens[1]) is None
        or (first_state, _city_key(tokens[1])) in geo.places_in_state
        or (first_state, _city_key(tokens[1])) in geo.aliases_in_state
    ):
        # State first, city after: "USA, WA, Seattle", "United States, Washington,
        # Redmond", "USA, DC, Washington" (where "Washington" is also a state name).
        state, whole, loose = first_state, tokens[1:], [tokens[1]]
    else:
        # The rightmost state token wins: in "…, New York, NY" the city "New York"
        # is also a state name, but the abbreviation after it is the real state.
        for index in range(len(tokens) - 1, 0, -1):
            state = _state_of(tokens[index])
            if state:
                # Usually the token right before the state ("1000 Nicollet Mall,
                # Minneapolis, MN 55403"); then anything earlier ("Salt Lake City,
                # Salt Lake County, UT"), and last the state's own name as a city
                # ("New York, New York").
                whole = [*reversed(tokens[:index]), *tokens[index + 1 :], tokens[index]]
                loose = [tokens[index - 1], tokens[0]]
                break
        if state is None:
            words = tokens[0].split()
            if len(words) > 1 and len(words[-1]) == 2 and words[-1] in geo.state_by_abbr:
                # "Salt Lake City UT" — the state glued onto the city.
                state, whole = geo.state_by_abbr[words[-1]], [" ".join(words[:-1])]
                loose = whole

    if state is not None:
        for token in whole:
            metro = _city_in_state(state, token, ngrams=False)
            if metro is not None:
                return metro
        for token in loose:
            metro = _city_in_state(state, token, ngrams=True)
            if metro is not None:
                return metro
        return None

    # No state anywhere. A lone city name, or — when the entry says United
    # States — the last thing before it that looks like a city ("Charlotte, United
    # States of America" after a facility code). Never a bare state/country name,
    # and without a US signal never a multi-part entry ("Cambridge, Ontario, Canada").
    if len(tokens) == 1:
        candidates = tokens
    elif us_signal:
        candidates = list(reversed(tokens))
    else:
        return None
    for token in candidates:
        if token in geo.state_by_name and token != "new york":
            continue
        # Trailing-word matching only for street addresses (they start with a
        # number: "480 Washington Boulevard Jersey City") — not facility names,
        # where "Penn State University Park" would read as University Park, TX.
        for phrase in _phrases(token, ngrams=token[0].isdigit()):
            metro = _lone_city_metro(_city_key(phrase))
            if metro is not None:
                return metro
    return None


def _entry_variants(entry: str) -> list[str]:
    """The entry cleaned of noise, plus each part of a "Facility - City" style
    entry, most-specific-to-the-city first."""
    cleaned = _PARENTHETICAL_RE.sub(" ", entry)          # "(Headquarters)", "(NY0466)"
    cleaned = _SPLIT_COUNTRY_RE.sub("United States", cleaned)
    cleaned = _WORKPLACE_SUFFIX_RE.sub("", cleaned)      # "San Francisco- Hybrid"
    cleaned = _US_STATE_CODE_RE.sub(r", \1", cleaned)     # "Arden Hills US-MN" -> "Arden Hills , MN"
    if ":" in cleaned:
        cleaned = cleaned.rsplit(":", 1)[1]              # "Client Office: Washington, DC"
    segments = _FACILITY_SEPARATOR_RE.split(cleaned)
    variants = [cleaned, *reversed(segments[1:]), *segments[:1]]
    return list(dict.fromkeys(v.strip() for v in variants if v.strip()))


@lru_cache(maxsize=200_000)
def resolve_metro(entry: str) -> Metro | None:
    """The metro/micro area a single location entry belongs to, or None."""
    if not entry.strip() or _normalize(entry).startswith(("remote", "hybrid")):
        return None
    for variant in _entry_variants(entry):
        metro = _resolve_text(variant)
        if metro is not None:
            return metro
    return None


def resolve_metros(entries: list[str]) -> list[str]:
    """CBSA codes for a posting's location entries — de-duplicated, in order."""
    codes: list[str] = []
    for entry in entries:
        metro = resolve_metro(entry)
        if metro is not None and metro.code not in codes:
            codes.append(metro.code)
    return codes
