"""Resolve a posting's US location strings to a Census metro/micro area and a state.

The same place is spelled many ways ("Salt Lake City, UT, US" / "…, Utah" /
"Salt Lake City UT, United States of America" / "USA, UT, Salt Lake City" /
"Home Services - Salt Lake City, …"), so people can't just search "Salt Lake
City". Each entry is reduced to a US city + state, mapped to its county
(GeoNames) and from there to the official CBSA that county belongs to (Census
delineation) — see build_geo_data.py for where the bundled files under
app/data/geo/ come from. Deterministic, offline, no network or LLM.

Three questions, in order, for every entry:
  1. Is there a city?  -> its metro/micro area, and its state.
  2. No city, but a state ("Utah", "Remote - California")?  -> the state.
  3. Neither (a facility name, a street, a non-US place)?  -> nothing; the entry
     stays findable by plain text search.

Words like Remote / Hybrid / Office / HQ aren't places, so they're taken off the
entry before the place is looked for and reported separately as the entry's
work-type hint (Resolution.workplace).

Precision over recall: a place only resolves when it is unambiguous, and a
state is only taken from an entry when nothing suggests another country — "IN"
is Indiana *or* India, "CA" California *or* Canada.
"""

import csv
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "geo"

# A bare city name (no state) is only trusted when the biggest metro area with a
# place of that name is this many times bigger than the runner-up ("Salt Lake
# City" yes, "Springfield" no).
_DOMINANCE_RATIO = 5

_US_COUNTRY_TOKENS = {"us", "usa", "united states", "united states of america", "america"}
_US_SIGNAL_RE = re.compile(r"\b(?:us|usa|united states(?: of america)?)\b")

# Canadian provinces and territories. They count as "another country" for the
# guards below: "Remote - Ontario" is Canada, not Ontario, California.
_CANADIAN_REGIONS = {"ontario", "quebec", "british columbia", "alberta", "manitoba", "saskatchewan", "nova scotia",
                     "new brunswick", "newfoundland and labrador", "newfoundland", "prince edward island", "yukon",
                     "northwest territories", "nunavut"}

# Words that are regions or whole-continent labels rather than places we could file.
_REGION_WORDS = {"asia", "apac", "emea", "europe", "latam", "latin america", "worldwide", "global", "anywhere",
                 "north america", "americas", "international", "nationwide"}

# Words that mean the same thing spelled short or long. Applied to city names
# only (never to state tokens: "MT" is Montana, not "Mount").
_CITY_WORD_EXPANSIONS = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount"}

_FACILITY_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
# "Arden Hills US-MN", "US-CA - Santa Clara": a state code written as US-XX / US XX.
_US_STATE_CODE_RE = re.compile(r"\bUS[- ]([A-Za-z]{2})\b")
# "141278-NC-CIC Customer Information Ctr": a state code inside a store/facility number.
# Hyphen/underscore only — with a space, "1501 NE Medical Center Dr" is a street
# direction, not Nebraska.
_FACILITY_STATE_CODE_RE = re.compile(r"\b\d{2,}[-_]([A-Z]{2})[-_]")

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

# Formatting quirks seen in scraped data: a stray comma inside "United, States".
_SPLIT_COUNTRY_RE = re.compile(r"\bunited\s*,\s*states\b", re.IGNORECASE)

# Workplace words. They aren't part of the place: "Boston or Remote" is Boston.
# ("Home Office" is deliberately not a signal — it usually means a company's
# corporate headquarters, not working from home.)
_HOME_OFFICE_RE = re.compile(r"\bhome\s+office\b", re.IGNORECASE)
# "Nationwide Remote Office (US99)" is a remote posting, not "remote" + "office".
_REMOTE_OFFICE_RE = re.compile(r"\b(?:remote|virtual)\s+office\b", re.IGNORECASE)
_REMOTE_WORDS = r"remote(?:ly)?|virtual|telecommut\w*|tele-?work\w*|work\s+from\s+home|wfh"
# Words that state how the job is worked. HQ / Headquarters / Campus only label a
# site — "Mountain View (HQ)" says nothing about remote vs on-site — so they are
# taken off the place name but are not a work-type signal.
_ONSITE_WORDS = r"on-?site|in-?office|office"
_SITE_LABEL_WORDS = r"hq|headquarters|campus"
_WORKPLACE_RE = re.compile(
    rf"\b(?P<remote>{_REMOTE_WORDS})\b|\b(?P<hybrid>hybrid)\b|\b(?P<onsite>{_ONSITE_WORDS})\b"
    rf"|\b(?P<label>{_SITE_LABEL_WORDS})\b",
    re.IGNORECASE,
)
_DANGLING_CONNECTOR_RE = re.compile(r"^(?:or|and)\b|\b(?:or|and)$", re.IGNORECASE)
# "Chicago Metro", "Greater Boston Area": area words, not part of the city name.
_METRO_WORDS_RE = re.compile(r"\b(?:metropolitan|metro|area|greater)\b", re.IGNORECASE)

# Words that may follow a state abbreviation without changing what it is ("NY USA").
_STATE_TRAILING_NOISE = {"us", "usa", "america", "the"}
# Bare state names that can't be taken as a state on their own: "Washington" is
# the state or the capital; "Georgia" is also a country (needs a US signal).
_NEVER_STATE_ONLY = {"washington"}
_NEEDS_US_SIGNAL = {"georgia"}

# How many trailing/leading words of a street-address token to try as the city
# ("6400 LAS COLINAS BLVD IRVING" -> "irving"; "2260 Watson Way Vista" -> "vista").
_MAX_CITY_WORDS = 4
# Words that show up in addresses and facility names and are also (tiny) place
# names or place-name aliases — never a city on their own when picked out of a
# longer string ("214 North Tryon Street" is not "North").
_GENERIC_WORDS = {"north", "south", "east", "west", "central", "new", "old", "street", "avenue", "road", "drive",
                  "boulevard", "park", "plaza", "center", "centre", "campus", "building", "tower", "square",
                  "suite", "floor", "lane", "way", "court", "circle", "place", "main", "first", "second", "third"}


@dataclass(frozen=True)
class Metro:
    """A searchable area: a Census metro/micro area, or a state."""

    code: str  # CBSA code ("41620") or state abbreviation ("UT")
    slug: str  # url-safe, unique: "salt-lake-city-ut", "utah"
    name: str  # short display name: "Salt Lake City, UT" / "Utah"
    title: str  # official CBSA title, or the state name
    kind: str  # "metro" | "micro" | "state"


@dataclass(frozen=True)
class Resolution:
    """What one location entry says. `geo` is what kind of geography the rest of
    the entry (after workplace words are removed) turned out to be: "city",
    "state", "country" (US, another country, or a region like "Asia"), "empty"
    (nothing left — a bare "Remote"), or "other" (text we couldn't classify)."""

    metro: Metro | None = None  # the metro/micro area, if the city is in one
    state: Metro | None = None  # the state-level area
    geo: str = "other"
    workplace: str | None = None  # "remote" | "hybrid" | "onsite" — explicit words in the entry
    place: "Place | None" = None  # the city itself, when it was matched to one (not for an area alias)

    @property
    def city(self) -> bool:
        return self.geo == "city"


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    county_fips: str
    population: int
    lat: float
    lon: float


@dataclass(frozen=True)
class _Found:
    metro: Metro | None
    state: Metro
    city: bool
    place: Place | None = None


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
        self.metro_by_code: dict[str, Metro] = {}
        self.metro_by_slug: dict[str, Metro] = {}

        self.state_by_abbr: dict[str, str] = {}
        self.state_by_name: dict[str, str] = {}
        self.states: dict[str, Metro] = {}
        with (_DATA_DIR / "us_states.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                abbr, name = row["abbr"], row["name"]
                self.state_by_abbr[abbr.lower()] = abbr
                self.state_by_name[_normalize(name)] = abbr
                state = Metro(code=abbr, slug=_slugify(name), name=name, title=name, kind="state")
                self.states[abbr] = state
                self.metro_by_code[abbr] = state
                self.metro_by_slug[state.slug] = state

        # Within a state a place's real name beats another place's alias:
        # GeoNames lists "Elizabethtown" as an alternate name of Hopkinsville, and
        # by population alone that would outrank the actual Elizabethtown.
        self.places_in_state: dict[tuple[str, str], list[Place]] = {}
        self.aliases_in_state: dict[tuple[str, str], list[Place]] = {}
        self.places_by_name: dict[str, list[Place]] = {}
        self.all_places: list[Place] = []
        self.primary_by_name: dict[str, list[Place]] = {}  # real names only, no aliases
        with (_DATA_DIR / "us_places.tsv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                place = Place(
                    row["name"], row["state"], row["county_fips"], int(row["population"]),
                    float(row["lat"]), float(row["lon"]),
                )
                primary_keys = {_city_key(row["name"]), _city_key(row["ascii"])}
                # An alternate name that is a generic word ("North" for North Salt Lake,
                # "Center" for Tallmadge) would send any "North Campus" or "Main Street"
                # to that town — see _GENERIC_WORDS.
                alias_keys = {_city_key(a) for a in row["aliases"].split("|") if a} - primary_keys - _GENERIC_WORDS
                for key in primary_keys:
                    self.places_in_state.setdefault((place.state, key), []).append(place)
                for key in alias_keys:
                    self.aliases_in_state.setdefault((place.state, key), []).append(place)
                for key in primary_keys | alias_keys:
                    self.places_by_name.setdefault(key, []).append(place)
                for key in primary_keys:
                    self.primary_by_name.setdefault(key, []).append(place)
                self.all_places.append(place)
        for places in (
            *self.places_in_state.values(),
            *self.aliases_in_state.values(),
            *self.places_by_name.values(),
            *self.primary_by_name.values(),
        ):
            places.sort(key=lambda p: -p.population)

        self.world_guard: dict[str, int] = {}
        with (_DATA_DIR / "world_city_guard.tsv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                key = _city_key(row["name"])
                self.world_guard[key] = max(self.world_guard.get(key, 0), int(row["population"]))

        # Names of other countries. Used to refuse to read a two-letter code as a
        # US state when the entry names another country, and to tell a
        # country-only entry from unrecognized text.
        self.foreign_countries: set[str] = set()
        with (_DATA_DIR / "world_countries.tsv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                self.foreign_countries.add(_normalize(row["name"]))
        self.foreign_countries |= _CANADIAN_REGIONS
        self.foreign_countries -= set(self.state_by_name)

        self.county_cbsa: dict[str, str] = {}
        titles: dict[str, tuple[str, str]] = {}
        with (_DATA_DIR / "us_cbsa_counties.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                self.county_cbsa[row["county_fips"]] = row["cbsa_code"]
                titles[row["cbsa_code"]] = (row["cbsa_title"], row["kind"])

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
    """Every searchable area — metro/micro areas and states (see Metro.kind)."""
    return list(_geo().metro_by_code.values())


def metro_by_code(code: str) -> Metro | None:
    return _geo().metro_by_code.get(code)


def metro_by_slug(slug: str) -> Metro | None:
    return _geo().metro_by_slug.get(slug)


# ---------------------------------------------------------------- state tokens


def _state_match(token: str) -> tuple[str, str] | None:
    """(state abbreviation, how it was written) if a token names a US state:
    "abbr" ("ut", or one followed by noise like "ny usa"), "name" ("utah"), or
    "zip" (an abbreviation followed only by a ZIP: "sc 29334")."""
    geo = _geo()
    if len(token) == 2 and token in geo.state_by_abbr:
        return geo.state_by_abbr[token], "abbr"
    if token in geo.state_by_name:
        return geo.state_by_name[token], "name"
    words = token.split()
    if len(words) > 1 and len(words[0]) == 2 and words[0] in geo.state_by_abbr:
        rest = words[1:]
        if all(w.isdigit() for w in rest):
            return geo.state_by_abbr[words[0]], "zip"
        if all(w.isdigit() or w in _STATE_TRAILING_NOISE for w in rest):
            return geo.state_by_abbr[words[0]], "abbr"
    return None


def _state_of(token: str) -> str | None:
    match = _state_match(token)
    return match[0] if match else None


def _state_fallback(tokens: list[str], us_signal: bool) -> _Found | None:
    """The entry has no city we can find — is there at least a state? Only with
    evidence it's really a US state: a full name, or an abbreviation backed by
    a ZIP or a US signal; and never when the entry names another country."""
    geo = _geo()
    if any(t in geo.foreign_countries for t in tokens):
        return None
    for token in tokens:
        match = _state_match(token)
        if match is None:
            continue
        abbr, kind = match
        trusted = (
            kind == "zip"
            or (kind == "abbr" and us_signal)
            or (kind == "name" and token not in _NEVER_STATE_ONLY and (token not in _NEEDS_US_SIGNAL or us_signal))
        )
        if trusted:
            return _Found(metro=None, state=geo.states[abbr], city=False)
    return None


# ------------------------------------------------------------------ city lookup


def _place_found(place: Place) -> _Found:
    geo = _geo()
    code = geo.county_cbsa.get(place.county_fips)
    return _Found(
        metro=geo.metro_by_code.get(code) if code else None, state=geo.states[place.state], city=True, place=place
    )


def _lone_city(key: str, *, primary_only: bool = False) -> _Found | None:
    """A city name with no state to pin it down — trusted only when its metro
    area is clearly the biggest of that name ("Salt Lake City" yes, "Springfield"
    no; "Kansas City", split across Missouri and Kansas, is one metro so yes) and
    no famous non-US city shares it ("Paris", "London"). `primary_only` ignores
    aliases, for names picked out of the middle of a longer string."""
    geo = _geo()
    candidates = (geo.primary_by_name if primary_only else geo.places_by_name).get(key)
    if not candidates:
        return None
    biggest_per_area: dict[str, Place] = {}
    for place in candidates:  # already biggest first
        area = geo.county_cbsa.get(place.county_fips) or f"rural:{place.state}"
        biggest_per_area.setdefault(area, place)
    ranked = list(biggest_per_area.values())
    best = ranked[0]
    runner_up = ranked[1].population if len(ranked) > 1 else 0
    if best.population < _DOMINANCE_RATIO * max(runner_up, 1) or geo.world_guard.get(key, 0) > best.population:
        return None
    return _place_found(best)


def _phrases(token: str, *, ngrams: bool) -> list[str]:
    """The token itself, then (for text with an address or facility name around
    the city) its trailing and leading 1-4 word groups, longest first:
    "2260 watson way vista" -> "vista"; "irvine 6001 oak canyon ste 100" -> "irvine".
    Bare numbers (store codes, street numbers) are ignored."""
    words = [w for w in token.split() if not w.isdigit()] if ngrams else token.split()
    phrases = [token]
    if ngrams:
        for size in range(min(len(words) - 1, _MAX_CITY_WORDS), 0, -1):
            phrases.append(" ".join(words[-size:]))
            phrases.append(" ".join(words[:size]))
        if words:
            phrases.append(" ".join(words))
    return list(dict.fromkeys(phrases))


def _place_in_state(state: str, token: str, *, ngrams: bool) -> Place | None:
    """A place of `state` named by `token`. The state pins the match, so the
    looser n-gram matching can't drift to a same-named place elsewhere."""
    geo = _geo()
    for phrase in _phrases(token, ngrams=ngrams):
        if not phrase or phrase.isdigit():
            continue
        key = (state, _city_key(phrase))
        places = geo.places_in_state.get(key) or geo.aliases_in_state.get(key)
        if places:
            return places[0]
    return None


def _metro_state(metro: Metro) -> Metro:
    return _geo().states[metro.title.rsplit(", ", 1)[1].split("-")[0]]


def _resolve_text(text: str, us_hint: bool, foreign: bool = False) -> _Found | None:
    """`foreign`: the whole entry names another country (and doesn't say United
    States), so a bare town name or a state with no city is not enough to call it
    American — only an explicit "City, State" is ("Lebanon, Ohio" yes, "Toronto -
    Ontario, Canada" no: there is an Ontario, California)."""
    geo = _geo()
    tokens = [t for t in (_normalize(part) for part in text.split(",")) if t]
    us_signal = us_hint or any(t in _US_COUNTRY_TOKENS for t in tokens)
    # Country words and bare numbers (ZIP codes, street numbers) carry no city.
    tokens = [t for t in tokens if t not in _US_COUNTRY_TOKENS and not t.isdigit()]
    if not tokens:
        return None

    for token in tokens:
        if token in _AREA_ALIASES:
            metro = geo.metro_by_code[_AREA_ALIASES[token]]
            return _Found(metro=metro, state=_metro_state(metro), city=True)

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
        state, whole, loose = first_state, [*tokens[1:], tokens[0]], [tokens[1]]
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

        def other_country_named(city_token: str) -> bool:
            # "Concord, CA, Canada": the entry names Canada, so "CA" is its country
            # code, not California. (The city token itself may be a country-named US
            # town — "Mexico, MO", "Lebanon, Ohio" — which is fine.)
            return foreign and any(t in geo.foreign_countries and t != city_token for t in tokens)

        for token in whole:
            place = _place_in_state(state, token, ngrams=False)
            if place is not None and not other_country_named(token):
                return _place_found(place)
        for token in loose:
            place = _place_in_state(state, token, ngrams=True)
            if place is not None and not other_country_named(token):
                return _place_found(place)
        return None if foreign else _state_fallback(tokens, us_signal)

    if foreign:
        return None

    # No state next to a city. A lone city name, or — when the entry says United
    # States — the last thing before it that looks like a city ("Charlotte, United
    # States of America" after a facility code). Never a bare state/country name,
    # and without a US signal never a multi-part entry ("Cambridge, Ontario, Canada").
    if len(tokens) == 1:
        candidates = tokens
    elif us_signal:
        candidates = list(reversed(tokens))
    else:
        candidates = []
    for token in candidates:
        if token in geo.state_by_name and token != "new york":
            continue
        # Trailing-word matching only for street addresses (they start with a
        # number: "480 Washington Boulevard Jersey City") — not facility names,
        # where "Penn State University Park" would read as University Park, TX.
        for phrase in _phrases(token, ngrams=token[0].isdigit()):
            picked_out = phrase != token  # part of a longer string, not the whole token
            if picked_out and phrase in _GENERIC_WORDS:
                continue
            found = _lone_city(_city_key(phrase), primary_only=picked_out)
            if found is not None:
                return found
    return _state_fallback(tokens, us_signal)


# --------------------------------------------------------------- entry cleaning


def _strip_workplace(text: str) -> tuple[str, str | None]:
    """The entry without its workplace words, and what they say. "Boston or
    Remote" -> ("Boston", "hybrid"); "Remote - California" -> ("California",
    "remote"); "New York, NY HQ" -> ("New York, NY", "onsite"). A city with a
    remote option is hybrid, matching how JSON-LD postings are read."""
    text = _HOME_OFFICE_RE.sub(" ", text.replace("_", " "))
    remote_office = bool(_REMOTE_OFFICE_RE.search(text))
    text = _REMOTE_OFFICE_RE.sub(" ", text)
    matches = [(name, hit) for m in _WORKPLACE_RE.finditer(text) for name, hit in m.groupdict().items() if hit]
    found = {name for name, _ in matches if name != "label"}
    if remote_office:
        found.add("remote")
    if not matches and not remote_office:
        return text, None
    parts = []
    for part in text.split(","):
        part = _WORKPLACE_RE.sub(" ", part)
        part = " ".join(part.split()).strip(" -–—/:")
        part = _DANGLING_CONNECTOR_RE.sub("", part).strip(" -–—/:")
        if part:
            parts.append(part)
    if "hybrid" in found or {"remote", "onsite"} <= found:
        workplace = "hybrid"
    elif found:
        workplace = "remote" if "remote" in found else "onsite"
    else:
        workplace = None  # only site labels (HQ, Campus): stripped, but no work-type statement
    return ", ".join(parts), workplace


def _entry_variants(entry: str) -> list[str]:
    """The entry cleaned of noise, plus each part of a "Facility - City" style
    entry, most-specific-to-the-city first — each also without "Metro"/"Area"."""
    cleaned = _PARENTHETICAL_RE.sub(" ", entry)          # "(Headquarters)", "(NY0466)"
    cleaned = _SPLIT_COUNTRY_RE.sub("United States", cleaned)
    cleaned = _US_STATE_CODE_RE.sub(r", \1, US", cleaned)  # "Arden Hills US-MN" -> "Arden Hills , MN, US"
    if ":" in cleaned:
        cleaned = cleaned.rsplit(":", 1)[1]              # "Client Office: Washington, DC"
    segments = _FACILITY_SEPARATOR_RE.split(cleaned)
    variants = [cleaned, *reversed(segments[1:]), *segments[:1]]
    variants += [_METRO_WORDS_RE.sub(" ", v) for v in variants]
    code = _FACILITY_STATE_CODE_RE.search(entry)
    if code and _US_SIGNAL_RE.search(_normalize(entry)):
        variants.append(f"{code.group(1)}, United States")  # last resort: just the state
    return list(dict.fromkeys(v.strip() for v in variants if v.strip(" ,-–—/")))


def _is_bare_state_name(text: str) -> bool:
    tokens = [t for t in (_normalize(part) for part in text.split(",")) if t and t not in _US_COUNTRY_TOKENS]
    return len(tokens) == 1 and tokens[0] in _geo().state_by_name


def _names_foreign_country(entry: str) -> bool:
    """Does the entry name a country other than the US — as a comma part
    ("Toronto, Canada") or a dash-separated one ("Mexico - Mexico City")?"""
    geo = _geo()
    parts = [p for chunk in entry.split(",") for p in _FACILITY_SEPARATOR_RE.split(chunk)]
    return any(_normalize(part) in geo.foreign_countries for part in parts)


def _classify_other(text: str) -> str:
    """"country" if what's left is only country / region names, else "other"."""
    geo = _geo()
    tokens = [t for t in (_normalize(part) for part in _PARENTHETICAL_RE.sub(" ", text).split(",")) if t]
    if tokens and all(t in _US_COUNTRY_TOKENS or t in geo.foreign_countries or t in _REGION_WORDS for t in tokens):
        return "country"
    return "other"


@lru_cache(maxsize=200_000)
def resolve_entry(entry: str) -> Resolution:
    """What one location entry says: the city's metro area and state, or just a
    state, plus any explicit remote/hybrid/onsite word."""
    stripped, workplace = _strip_workplace(entry.strip())
    if not stripped.strip(" ,-–—/"):
        return Resolution(geo="empty", workplace=workplace)
    us_hint = bool(_US_SIGNAL_RE.search(_normalize(entry)))
    foreign = not us_hint and _names_foreign_country(entry)
    state_only: _Found | None = None
    for variant in _entry_variants(stripped):
        found = _resolve_text(variant, us_hint, foreign)
        if found is None:
            continue
        if found.city:
            # "Remote - New York": with a remote word, a bare state name that is
            # also a city means the state ("remote, in New York"), not the city.
            if workplace == "remote" and _is_bare_state_name(stripped):
                return Resolution(state=found.state, geo="state", workplace=workplace)
            return Resolution(metro=found.metro, state=found.state, geo="city", workplace=workplace, place=found.place)
        state_only = state_only or found
    if state_only is not None:
        return Resolution(state=state_only.state, geo="state", workplace=workplace)
    return Resolution(geo=_classify_other(stripped), workplace=workplace)


def resolve_metro(entry: str) -> Metro | None:
    """The most specific area a single entry belongs to — its metro/micro area,
    else its state — or None."""
    resolution = resolve_entry(entry)
    return resolution.metro or resolution.state


def resolve_area_codes(entries: list[str]) -> list[str]:
    """Area codes for a posting's location entries: each entry's metro/micro
    area code and its state code, de-duplicated, in order. A city in a metro area
    files the posting under the metro *and* the state, so a statewide search
    finds everything in the state."""
    codes: list[str] = []
    for entry in entries:
        resolution = resolve_entry(entry)
        for area in (resolution.metro, resolution.state):
            if area is not None and area.code not in codes:
                codes.append(area.code)
    return codes


def resolve_places(entries: list[str]) -> list[list[float]]:
    """[lat, lon] of each entry that names a known city, de-duplicated, in order —
    what a radius search measures against. Entries with no city (a state, "Remote",
    a facility, another country) contribute nothing."""
    points: list[list[float]] = []
    for entry in entries:
        place = resolve_entry(entry).place
        if place is not None and [place.lat, place.lon] not in points:
            points.append([place.lat, place.lon])
    return points


# ------------------------------------------------------------- radius search

# How far around a searched city to look, unless the search asks for more.
RADIUS_MILES = 25

EARTH_RADIUS_MILES = 3958.8
_MILES_PER_DEGREE_LAT = 69.0
_UNITED_STATES_NAMES = {"usa", "united states", "united states of america"}


def _distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((phi2 - phi1) / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


@lru_cache(maxsize=4096)
def nearby_state_codes(place: Place, miles: float = RADIUS_MILES) -> tuple[str, ...]:
    """State codes of the places within `miles` of `place`, its own state first —
    usually just one, more near a state line. Every posting with a resolved place
    carries its state code, so this is what the radius search narrows to (with
    the indexed `metros` column) before measuring distances."""
    geo = _geo()
    lat_span = miles / _MILES_PER_DEGREE_LAT
    lon_span = miles / (_MILES_PER_DEGREE_LAT * max(math.cos(math.radians(place.lat)), 0.01))
    states = {place.state: 0.0}
    for other in geo.all_places:
        if abs(other.lat - place.lat) > lat_span or abs(other.lon - place.lon) > lon_span:
            continue  # cheap box test first: most of the 17k places are nowhere near
        distance = _distance_miles(place.lat, place.lon, other.lat, other.lon)
        if distance <= miles and distance < states.get(other.state, math.inf):
            states[other.state] = distance
    return tuple(sorted(states, key=states.__getitem__))


def _is_united_states(text: str) -> bool:
    parts = [part for part in (_normalize(p) for p in text.split(",")) if part]
    return bool(parts) and all(part in _UNITED_STATES_NAMES for part in parts)


def search_place(text: str) -> Place | None:
    """The city a location search names, if it names one we know — searched by
    distance (see nearby_state_codes and job_locations.radius_filter) rather than
    by area."""
    resolution = resolve_entry(text)
    return resolution.place if resolution.geo == "city" else None


def place_label(place: Place) -> str:
    """"West Bountiful, Utah"."""
    return f"{place.name}, {_geo().states[place.state].name}"


def search_areas(text: str) -> list[str] | None:
    """The area codes a location search covers when it isn't a distance search: a
    state -> that state; "United States" -> every state; an area alias ("Bay
    Area") -> its metro. None for anything else (a city — see search_place —
    partial typing, a facility, another country), which stays a plain text
    search."""
    resolution = resolve_entry(text)
    if resolution.geo == "city":
        if resolution.place is None and resolution.metro is not None:
            return [resolution.metro.code]  # an area alias: a metro, but no single city
        return None
    if resolution.geo == "state" and resolution.state is not None:
        return [resolution.state.code]
    if resolution.geo == "country" and _is_united_states(text):
        return list(_geo().states)
    return None


# ----------------------------------------------------------- place suggestions

_UNITED_STATES = "United States"
# Smaller GeoNames "places" are neighbourhoods and districts ("Barracks Row"),
# not somewhere people search for work; so are the composite names GeoNames gives
# them ("Makiki / Lower Punchbowl", "Fenway/Kenmore", "City of Milford (balance)").
_SUGGESTION_MIN_POPULATION = 2500
_NOT_A_CITY_NAME_RE = re.compile(r"\d| - |/|\(")


@dataclass(frozen=True)
class _Suggestion:
    label: str
    keys: tuple[str, ...]  # normalized spellings a typed query is matched against
    rank: tuple[int, int]  # (0 country / 1 state / 2 city, -population)


@lru_cache(maxsize=1)
def _suggestions() -> tuple[_Suggestion, ...]:
    geo = _geo()
    entries = [_Suggestion(_UNITED_STATES, (_normalize(_UNITED_STATES), "usa"), (0, 0))]
    for abbr, state in geo.states.items():
        entries.append(
            _Suggestion(f"{state.name}, {_UNITED_STATES}", (_normalize(f"{state.name} {_UNITED_STATES}"), _normalize(abbr)), (1, 0))
        )
    biggest: dict[tuple[str, str], Place] = {}
    for place in geo.all_places:
        if place.population < _SUGGESTION_MIN_POPULATION or _NOT_A_CITY_NAME_RE.search(place.name):
            continue
        key = (place.state, _normalize(place.name))
        if key not in biggest or place.population > biggest[key].population:
            biggest[key] = place
    for place in biggest.values():
        state = geo.states[place.state]
        entries.append(
            _Suggestion(
                f"{place.name}, {state.name}, {_UNITED_STATES}",
                (_normalize(f"{place.name} {state.name} {_UNITED_STATES}"), _normalize(f"{place.name} {place.state}")),
                (2, -place.population),
            )
        )
    return tuple(sorted(entries, key=lambda e: e.rank))


def place_suggestions(q: str | None, limit: int) -> list[str]:
    """Places to offer for the text typed so far — "City, State, United States",
    "State, United States" or "United States", never a facility or street. Names
    that start with what was typed come first, then ones with a word that does; states
    before cities, bigger cities first. With nothing typed: the United States,
    then the biggest cities."""
    needle = _normalize(q or "")
    entries = _suggestions()
    if not needle:
        return [e.label for e in entries[:1] + tuple(e for e in entries if e.rank[0] == 2)][:limit]
    starts_with: list[str] = []
    contains: list[str] = []
    for entry in entries:
        if any(key.startswith(needle) for key in entry.keys):
            starts_with.append(entry.label)
        elif any(f" {needle}" in f" {key}" for key in entry.keys):  # at a word start: "usa" isn't in "thousand"
            contains.append(entry.label)
    return (starts_with + contains)[:limit]
