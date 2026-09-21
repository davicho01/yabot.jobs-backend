"""Individual-location handling for postings that list several locations.

`JobPosting.location` is a single display string; when a posting lists more
than one location the adapters join them with "; " (and the odd source packs
them straight into og:description with "|"). `JobPosting.locations` is the
list parsed back out of that string at scan time (see _upsert_posting), and
is what location filtering and the search-box suggestions match against — so
picking "Seattle, WA" finds every posting that lists it, wherever in a long
multi-location string it sits.
"""

import html
import re
import time
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Float, FromClause, and_, case, cast, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.models.job_posting import JobPosting
from app.services import geo

# ";" is what adapters join locations with. "|" is used both as a
# place|workplace-tag delimiter ("Arizona | Remote") and, by some sources, as
# a location separator ("Remote-Friendly | San Francisco, CA | Washington,
# DC"), so it splits too. Tag chunks like "Remote" are deliberately *kept* as
# entries of their own rather than dropped: people type "remote" into the
# location box, and the old substring filter matched it wherever it appeared in
# the string — dropping tags made ~15% of those postings unfindable.
_SEPARATOR_RE = re.compile(r"[;|]")

# Adapters cap `location` at the column length and end it with "..." — the
# last chunk of such a string is a cut-off fragment, not a real place.
_TRUNCATION_MARKER = "..."

# Scraped text is untrusted — a page listing thousands of places (or one
# enormous one) shouldn't bloat the row or the in-memory suggestion cache.
_MAX_ENTRIES = 100
_MAX_ENTRY_LENGTH = 255


def split_locations(raw: str | None) -> list[str]:
    """The individual locations in a posting's `location` string, in order,
    de-duplicated case-insensitively.

    Migrations hold frozen SQL copies of these rules to backfill existing rows
    (latest: c8e5b3a91d27) — if they change here, new scans and old rows will
    disagree until a follow-up migration re-derives the column.
    """
    if not raw:
        return []
    # Scraped text often still carries HTML entities ("Tacoma &amp; Gordon"), and
    # the ";" inside one would otherwise split a single place in two.
    raw = html.unescape(raw)
    seen: set[str] = set()
    locations: list[str] = []
    for chunk in _SEPARATOR_RE.split(raw):
        entry = chunk.strip()
        if not entry or entry.endswith(_TRUNCATION_MARKER):
            continue
        key = entry.lower()
        if key not in seen:
            seen.add(key)
            locations.append(entry[:_MAX_ENTRY_LENGTH])
            if len(locations) == _MAX_ENTRIES:
                break
    return locations


_LOCATION_COLUMN_LENGTH = JobPosting.location.type.length


def location_matches(pattern: str) -> ColumnElement[bool]:
    """WHERE-clause expression: true when any of the posting's individual
    locations matches the (already %-wrapped) ILIKE `pattern`. Correlates to
    JobPosting in the enclosing query.

    The cheap ILIKE on the display column comes first because it discards
    nearly every row before the per-row array unnest runs (on ~155k postings
    the unnest alone roughly doubled a zero-hit search). It never drops a real
    match: each entry is a substring of `location`, except when that string
    was cut off at the column width — hence the second branch.
    """
    entry = func.jsonb_array_elements_text(JobPosting.locations).column_valued("entry")
    entry_matches = select(entry).where(entry.ilike(pattern)).correlate(JobPosting).exists()
    display_may_match = or_(
        JobPosting.location.ilike(pattern),
        func.char_length(JobPosting.location) >= _LOCATION_COLUMN_LENGTH,
    )
    return and_(display_may_match, entry_matches)


# A city search lists the searched place's own postings first, then those within
# 5 miles, within 15, and the rest out to the search radius; newest first inside
# each band. Coordinates are exact per place, so "the same place" is any distance
# under half a mile.
SAME_PLACE_MILES = 0.5
NEAR_MILES = 5
NEARBY_MILES = 15


def radius_search(
    place: geo.Place, radius: float
) -> tuple[FromClause, ColumnElement[bool], ColumnElement[int]]:
    """A "within `radius` miles of `place`" search over JobPosting:
    (nearest, where, band). Join `nearest` (a lateral subquery giving each posting's
    distance in `miles` to its closest place) on true(), filter with `where`, and
    order by `band` then recency. Correlates to JobPosting.

    `where` narrows with the indexed `metros` column first — a posting with a
    resolved place always carries its state code — so distances are only measured
    for the few thousand postings in the states around the place. Haversine in
    plain SQL (Postgres' built-in trig, no extension); `least(1, ...)` guards
    the asin against rounding just past 1.
    """
    def sql(name: str, *args):
        return getattr(func, name)(*args, type_=Float)  # typed, so "/ 2" stays float division

    point = func.jsonb_array_elements(JobPosting.places).column_valued("point")
    lat = cast(point.op("->>")(literal_column("0")), Float)
    lon = cast(point.op("->>")(literal_column("1")), Float)
    half_chord = sql("power", sql("sin", sql("radians", lat - place.lat) / 2), 2) + (
        sql("cos", sql("radians", place.lat))
        * sql("cos", sql("radians", lat))
        * sql("power", sql("sin", sql("radians", lon - place.lon) / 2), 2)
    )
    miles = 2 * geo.EARTH_RADIUS_MILES * sql("asin", sql("least", 1.0, sql("sqrt", half_chord)))
    nearest = select(sql("min", miles).label("miles")).correlate(JobPosting).lateral("nearest")
    in_states = or_(*[JobPosting.metros.contains([state]) for state in geo.nearby_state_codes(place, radius)])
    band = case(
        (nearest.c.miles < SAME_PLACE_MILES, 0),
        (nearest.c.miles <= NEAR_MILES, 1),
        (nearest.c.miles <= NEARBY_MILES, 2),
        else_=3,
    )
    return nearest, and_(in_states, nearest.c.miles <= radius), band


def rank_locations(counts: Sequence[tuple[str, int]], q: str | None, limit: int) -> list[str]:
    """Locations to suggest for the text typed so far: ones that start with
    it first, then ones that merely contain it; within each group, most
    postings first (`counts` is already ordered that way).
    """
    needle = (q or "").strip().lower()
    if not needle:
        return [name for name, _ in counts[:limit]]
    starts_with: list[str] = []
    contains: list[str] = []
    for name, _ in counts:
        lowered = name.lower()
        if lowered.startswith(needle):
            starts_with.append(name)
        elif needle in lowered:
            contains.append(name)
    return (starts_with + contains)[:limit]


# The distinct-location set changes slowly, and the aggregate over every
# posting is far too heavy to run per keystroke on the small Cloud SQL
# instance — so each API process keeps one copy for a few minutes and filters
# it in memory. New locations from fresh scans show up after the TTL.
_COUNTS_TTL_SECONDS = 600
_COUNTS_MAX_ENTRIES = 5000
_counts_cache: tuple[float, list[tuple[str, int]]] | None = None


def clear_location_cache() -> None:
    global _counts_cache, _metro_counts_cache
    _counts_cache = None
    _metro_counts_cache = None


def _location_counts(db: Session) -> list[tuple[str, int]]:
    global _counts_cache
    now = time.monotonic()
    if _counts_cache is not None and now - _counts_cache[0] < _COUNTS_TTL_SECONDS:
        return _counts_cache[1]

    entry = func.jsonb_array_elements_text(JobPosting.locations).column_valued("entry")
    posting_count = func.count(JobPosting.id.distinct())
    stmt = (
        select(entry, posting_count)
        .group_by(entry)
        .order_by(posting_count.desc(), entry)
        .limit(_COUNTS_MAX_ENTRIES)
    )
    counts = [(name, count) for name, count in db.execute(stmt).all()]
    _counts_cache = (now, counts)
    return counts


def location_suggestions(db: Session, q: str | None, limit: int) -> list[str]:
    return rank_locations(_location_counts(db), q, limit)


_metro_counts_cache: tuple[float, dict[str, int]] | None = None


def _metro_counts(db: Session) -> dict[str, int]:
    """{CBSA code: posting count}, most postings first, cached like the
    location counts above."""
    global _metro_counts_cache
    now = time.monotonic()
    if _metro_counts_cache is not None and now - _metro_counts_cache[0] < _COUNTS_TTL_SECONDS:
        return _metro_counts_cache[1]

    code = func.jsonb_array_elements_text(JobPosting.metros).column_valued("code")
    posting_count = func.count(JobPosting.id.distinct())
    stmt = select(code, posting_count).group_by(code).order_by(posting_count.desc(), code)
    counts = {c: n for c, n in db.execute(stmt).all()}
    _metro_counts_cache = (now, counts)
    return counts


def rank_metros(counts: dict[str, int], q: str | None, limit: int) -> list[tuple[geo.Metro, int]]:
    """Metro areas matching what was typed: ones whose name starts with it first
    ("salt" -> Salt Lake City), then ones containing it anywhere in the official
    title ("fort worth" -> Dallas-Fort Worth-Arlington); within each, the most
    postings first. `counts` is already ordered most-postings-first."""
    needle = geo.search_key(q or "")
    starts_with: list[tuple[geo.Metro, int]] = []
    contains: list[tuple[geo.Metro, int]] = []
    for code, count in counts.items():
        metro = geo.metro_by_code(code)
        if metro is None:
            continue
        if not needle or geo.search_key(metro.name).startswith(needle):
            starts_with.append((metro, count))
        elif needle in geo.search_key(metro.title):
            contains.append((metro, count))
    return (starts_with + contains)[:limit]


def metro_suggestions(
    db: Session, q: str | None, limit: int, *, slug: str | None = None
) -> list[tuple[geo.Metro, int]]:
    counts = _metro_counts(db)
    if slug is not None:
        metro = geo.metro_by_slug(slug)
        return [(metro, counts.get(metro.code, 0))] if metro else []
    return rank_metros(counts, q, limit)
