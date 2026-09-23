import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any

import httpx

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters.text import OG_TITLE_RE, clean_text, html_to_formatted_text
from app.services.browser_fetch import fetch_rendered_page

TIMEOUT = 30.0

# How far back "recent" reaches for adapters that filter postings by date
# (Workday, Amazon, Apple, Oracle Fusion) rather than paginating a whole
# board every crawl. A single shared constant so every adapter's window
# moves together instead of drifting adapter-by-adapter. Includes today
# and the previous three days, allowing overlap between daily crawls
# while reducing work within the event-driven worker's timeout.
RECENT_WINDOW_DAYS = 7

# Shared result cap for every adapter.
# High-volume boards can hit this before exhausting their recent postings.
DEFAULT_MAX_JOBS_PER_CRAWL = 500


def posting_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        return None


def is_recent_posting(value: object) -> bool:
    posted = posting_date(value)
    if posted is None:
        return False
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=RECENT_WINDOW_DAYS - 1) <= posted <= today


def limit_job_urls(urls: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
            if len(result) >= DEFAULT_MAX_JOBS_PER_CRAWL:
                break
    return result

logger = logging.getLogger(__name__)

# job_scanner.py's own retry helper caps at 3 attempts / 18s max backoff,
# tuned for a single interactive job-page fetch. That budget isn't enough
# here: verified live against Microsoft's Eightfold tenant, whose rate
# limiter keeps tripping every few pages during a ~25-request pagination
# run and needed 6 attempts / a 30s ceiling to ride out (crawl_worker.py has
# no tight time budget, so the extra wall-clock cost here is fine).
_RATE_LIMIT_MAX_ATTEMPTS = 6
_RATE_LIMIT_MAX_WAIT_SECONDS = 60.0
_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)


def _rate_limit_wait_seconds(response: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying a 429, preferring the site's own
    Retry-After over a guessed backoff — capped so a site advertising an
    hours-long Retry-After can't pin a worker slot on one URL indefinitely.
    Mirrors job_scanner.py's helper of the same name/shape.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = None  # HTTP-date form — not worth parsing, fall back to backoff
        if wait is not None and wait >= 0:
            return min(wait, _RATE_LIMIT_MAX_WAIT_SECONDS)
    return _RATE_LIMIT_BACKOFF_SECONDS[attempt]


def _request_with_retry(request_fn: Callable[[], httpx.Response], url: str) -> httpx.Response:
    """Shared retry loop behind get_with_retry/post_with_retry: retries a
    few times on 429, for tenant APIs hit repeatedly in a tight pagination
    loop (e.g. Eightfold's /api/pcsx/search) — a burst of back-to-back
    requests to the same host can trip the tenant's own rate limiter partway
    through. Does not call raise_for_status(); callers do that themselves,
    same as a plain httpx.get()/httpx.post() call.
    """
    attempt = 0
    while True:
        response = request_fn()
        attempt += 1
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS and attempt < _RATE_LIMIT_MAX_ATTEMPTS:
            wait = _rate_limit_wait_seconds(response, attempt - 1)
            logger.info("Rate limited fetching %s (attempt %d); retrying in %.1fs.", url, attempt, wait)
            time.sleep(wait)
            continue
        return response


def get_with_retry(url: str, *, timeout: float = TIMEOUT, **kwargs) -> httpx.Response:
    return _request_with_retry(lambda: httpx.get(url, timeout=timeout, **kwargs), url)


def post_with_retry(url: str, *, timeout: float = TIMEOUT, **kwargs) -> httpx.Response:
    return _request_with_retry(lambda: httpx.post(url, timeout=timeout, **kwargs), url)


# schema.org JobPosting employmentType -> our enum. Anything not listed
# (VOLUNTEER, PER_DIEM, OTHER, ...) falls back to UNKNOWN. Shared because
# it's schema.org-generic (job_scanner.py's own JSON-LD extraction uses it
# too) and because Gem's own employmentType strings happen to already match
# schema.org's, so its adapter reuses this rather than duplicating it.
EMPLOYMENT_TYPE_MAP = {
    "FULL_TIME": EmploymentType.FULL_TIME,
    "PART_TIME": EmploymentType.PART_TIME,
    "CONTRACTOR": EmploymentType.CONTRACT,
    "TEMPORARY": EmploymentType.TEMPORARY,
    "INTERN": EmploymentType.INTERNSHIP,
}


@dataclass
class ExtractedJobFields:
    """What one ATS-specific scan extractor (see each adapter module's own
    `extract(url, html)`) can pull out of a single job page — a subset of
    job_scanner.ScanResult's fields, since no one adapter necessarily
    supplies all of them (e.g. Gem has no salary field at all; Stripe has
    no employment_type). `None` on any field means "this extractor has no
    opinion" — job_scanner.py's generic JSON-LD/OG fallback (or another
    adapter's extract()) fills it instead, same as today. workplace_type/
    employment_type default to UNKNOWN rather than None since ScanResult's
    own fields use that sentinel already, and job_scanner.py's merge logic
    checks `== UNKNOWN` (not `is None`) before overwriting either one.
    """

    title: str | None = None
    description: str | None = None
    company_name: str | None = None
    location: str | None = None
    workplace_type: str = WorkplaceType.UNKNOWN
    employment_type: str = EmploymentType.UNKNOWN
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_at: date | None = None
    extracted_fields: dict[str, Any] | None = field(default=None)


@dataclass
class ScanResult:
    """What a single job-posting scan produces — either one platform's own
    scan_job_url (see AtsAdapter.scan_job_url), or job_scanner.py's generic
    JSON-LD/OG-tag default for platforms with no adapter-specific scan
    logic. Lives here (not job_scanner.py) so every adapter module can
    construct one without a circular import back into job_scanner.py.
    """

    success: bool
    title: str | None = None
    description: str | None = None
    company_name: str | None = None
    location: str | None = None
    workplace_type: str = WorkplaceType.UNKNOWN
    employment_type: str = EmploymentType.UNKNOWN
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_at: date | None = None
    extracted_fields: dict[str, Any] | None = field(default=None)
    # Capped copy kept for the raw_source audit trail (DB storage). Never
    # persisted directly — see `full_html` for what the LLM extractor reads.
    raw_html_excerpt: str | None = None
    # The complete fetched page, uncapped. JSON-LD extraction already reads
    # the full page (see below); this field lets the LLM extractor do the
    # same instead of being bottlenecked by raw_html_excerpt's storage cap —
    # markup overhead means 20k raw HTML chars can collapse to a couple
    # thousand characters of real text, cutting off the job content before
    # the LLM ever sees it. Not stored anywhere; transient, scan-local only.
    full_html: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AtsAdapter:
    """Everything needed to support one ATS platform, in one place.

    board_key is an internal identifier private to a given adapter — for
    most platforms it's just the board slug/subdomain, but some need more
    than one value (e.g. Workday's "company/instance/site", ADP's
    "cid/ccId") and just join them with "/"; nothing outside this module
    ever needs to know or care about that shape, since match/board_key/
    fetch_jobs/to_board_url are always the ones both producing and
    consuming it.

    match: pure string URL matching, no I/O — None for platforms with no
        static URL shape (Eightfold, Clinch), which only match via
        embedded_match.
    board_key: re-derives board_key from a *stored* board_url for
        listing, when that's cheaper/different than match (Eightfold,
        Clinch only — a trivial host extraction, since their real
        board_key needs guess-and-verify network calls that fetch_jobs
        already does anyway). Defaults to match when unset.
    to_board_url: board_key -> the canonical URL to persist as
        CrawlSource.board_url. None means "verbatim": store whatever URL
        was actually submitted, unchanged (see board_url_for_key in
        app.services.ats_adapters).
    embedded_match: url -> board_key, a slower I/O-based fallback tier for
        platforms white-labeled onto a company's own domain, where the
        board_key isn't visible in the URL string alone.
    fetch_jobs: None for scan-only platforms with no crawl support at all
        (Stripe — job URLs are only ever submitted directly, never
        discovered by crawling a board).
    scan_job_url: url -> a complete ScanResult for one job posting, or None
        if this URL clearly isn't this platform (job_scanner.scan_job_url
        tries each adapter in turn and uses the first non-None result
        verbatim — never merges fields across adapters). None for
        platforms with no platform-specific scan logic, which fall back to
        job_scanner's generic JSON-LD/OG-tag scanner.
    """

    ats_type: str
    fetch_jobs: Callable[[str], list[str]] | None = None
    match: Callable[[str], str | None] | None = None
    board_key: Callable[[str], str | None] | None = None
    to_board_url: Callable[[str], str] | None = None
    embedded_match: Callable[[str], str | None] | None = None
    scan_job_url: Callable[[str], "ScanResult | None"] | None = None


# Shared by every embedded-widget detector that has to guess a board slug
# from a company's own domain (Greenhouse, Ashby) — e.g. "www.anrok.com" ->
# ["anrok"]. Also strips a trailing corporate suffix as a second guess
# (verified against a real board: "coalitioninc.com" -> "coalition") —
# wrong guesses just fail the caller's verification step harmlessly, same
# as any other candidate here.
_COMPANY_SUFFIXES = ("incorporated", "corp", "inc", "llc", "ltd", "group", "co")


def candidate_slugs_from_domain(domain: str) -> list[str]:
    label = domain.lower().removeprefix("www.").split(".")[0]
    candidates = [label]
    for suffix in _COMPANY_SUFFIXES:
        if label.endswith(suffix) and len(label) > len(suffix):
            candidates.append(label[: -len(suffix)])
    return list(dict.fromkeys(candidates))  # dedupe, keep order


def parse_month_day_year(raw: str | None, *, month_style: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(" ".join(raw.split()), month_style).date()
    except ValueError:
        return None


# --- Page fetching for scan_job_url (both per-platform adapters and the
# generic default scanner) ---

# A tighter retry budget than get_with_retry/post_with_retry above: this is
# a single interactive job-page fetch (a user waiting on a submit-a-URL
# flow), not a tight pagination loop against one tenant's API, so there's no
# need for get_with_retry's 6-attempt/60s ceiling tuned for riding out
# Eightfold's rate limiter across ~25 requests.
_SCAN_FETCH_RATE_LIMIT_MAX_ATTEMPTS = 3
_SCAN_FETCH_RATE_LIMIT_MAX_WAIT_SECONDS = 60.0
_SCAN_FETCH_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 6.0, 18.0)


def _scan_fetch_rate_limit_wait_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = None  # HTTP-date form — not worth parsing, fall back to backoff
        if wait is not None and wait >= 0:
            return min(wait, _SCAN_FETCH_RATE_LIMIT_MAX_WAIT_SECONDS)
    return _SCAN_FETCH_RATE_LIMIT_BACKOFF_SECONDS[attempt]


def _fetch_direct(url: str) -> httpx.Response:
    attempt = 0
    while True:
        response = httpx.get(
            url,
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; YabotJobsBot/1.0)"},
        )
        attempt += 1
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS and attempt < _SCAN_FETCH_RATE_LIMIT_MAX_ATTEMPTS:
            wait = _scan_fetch_rate_limit_wait_seconds(response, attempt - 1)
            logger.info("Rate limited fetching %s (attempt %d); retrying in %.1fs.", url, attempt, wait)
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response


@dataclass
class FetchedPage:
    text: str
    url: str


def fetch_html(url: str) -> FetchedPage:
    """Fetch a page, falling back to a real headless-browser render (via
    browser_fetch_service, already deployed as yabot-jobs-browser on Cloud
    Run) for sites that block a plain HTTP client — bot-detection
    challenges, 403s, etc. Returns both the HTML and the final, post-redirect
    URL either way, since callers need to inspect the latter too. The single
    fetch path every platform-specific scan_job_url (and the generic
    default scanner) uses — no adapter does its own top-level page fetch.
    """
    try:
        response = _fetch_direct(url)
        if response.text.strip():
            return FetchedPage(text=response.text, url=str(response.url))
        # A 2xx status with an empty body is a WAF JS-challenge, not a real
        # page — raise_for_status() never catches this (verified live on
        # delta.avature.net: every path, including job detail pages, answers
        # a plain httpx request with an empty 202). Left unhandled, this
        # scanned as a "successful" fetch of nothing, silently producing a
        # ScanResult with every field null instead of falling back to a real
        # browser render. avature.py's own _fetch_page_html already guards
        # against this same signature; this mirrors that check here so every
        # caller of fetch_html (the generic default scanner included) gets it.
        logger.info("Direct fetch of %s returned an empty body; retrying via browser_fetch_service.", url)
    except httpx.HTTPError as exc:
        logger.info("Direct fetch of %s failed (%s); retrying via browser_fetch_service.", url, exc)

    rendered = fetch_rendered_page(url)
    if rendered is None:
        raise httpx.HTTPError(f"Failed to fetch {url}: direct fetch blocked and browser-render fallback failed")
    return FetchedPage(text=rendered.html, url=rendered.url)


# --- Generic schema.org JobPosting JSON-LD / Open Graph / <title>/<meta>
# extraction — shared, not platform-specific. Used both as job_scanner.py's
# last-resort default scanner (for any URL no adapter's scan_job_url claims)
# and as a same-tier fallback inside individual adapters' own scan_job_url
# for fields their own structured source doesn't cover (e.g. Gem's board has
# no on-page title at all past its GraphQL API's own fields) — never used to
# borrow data across two different *platforms*.

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# The content attribute's quote is captured (group 1) and back-referenced to
# close it (\1), not just re-matched against ["\'] — otherwise an apostrophe
# inside a double-quoted value (e.g. "we're hiring") closes the match early,
# truncating everything after it. Verified live on a SmartRecruiters posting
# whose meta description ("...Hey, g'day...") was getting cut off right
# before the apostrophe.
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL
)
_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
# Open Graph fallback for pages with no JobPosting JSON-LD and no standard
# meta description (e.g. Greenhouse's application-form pages) — og:title is
# usually cleaner than the raw <title> tag, and og:description often carries
# a terse "Location | WorkplaceType" (e.g. "Utah | Hybrid") or just a
# workplace type on its own.
_OG_DESC_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL
)
# og:site_name is usually the site/company's own display name (e.g. a small
# custom-built or BambooHR-hosted careers page with no other structured
# data at all) — a last-resort company name source, tried only after every
# more specific extractor (JSON-LD, Greenhouse's embedded JSON, the fixed
# single-company domains) has already come up empty.
_OG_SITE_NAME_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL
)

# Workplace-type words as they commonly appear in an og:description tag
# (e.g. Greenhouse's "Utah | Hybrid", or just "Remote" on its own).
_OG_WORKPLACE_TYPE_WORDS = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "on-site": WorkplaceType.ONSITE,
    "onsite": WorkplaceType.ONSITE,
    "in-office": WorkplaceType.ONSITE,
    "in office": WorkplaceType.ONSITE,
}


def fallback_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    return match.group(1).strip() if match else None


def fallback_description(html: str) -> str | None:
    match = _META_DESC_RE.search(html)
    return html_to_formatted_text(match.group(2)) if match else None


def og_title(html: str) -> str | None:
    match = OG_TITLE_RE.search(html)
    return clean_text(match.group(2)) if match else None


def og_site_name(html: str) -> str | None:
    match = _OG_SITE_NAME_RE.search(html)
    return clean_text(match.group(2)) if match else None


def og_description_raw(html: str) -> str | None:
    """The un-formatted og:description content — parse_og_description needs
    this single-line "Location | WorkplaceType" shape, not the (possibly
    multi-line) Markdown og_description below.
    """
    match = _OG_DESC_RE.search(html)
    return clean_text(match.group(2)) if match else None


def og_description(html: str) -> str | None:
    match = _OG_DESC_RE.search(html)
    return html_to_formatted_text(match.group(2)) if match else None


def _parse_location_workplace_segment(segment: str) -> tuple[str | None, str]:
    """Parse a single "Location | WorkplaceType" (or bare "Remote") chunk.

    Deliberately conservative: only treats the text before "|" as a location
    when the text after it is a recognized workplace-type word — otherwise
    we'd risk mistaking arbitrary marketing copy for a location.
    """
    parts = [p.strip() for p in segment.split("|")]
    if len(parts) == 2 and parts[1].lower() in _OG_WORKPLACE_TYPE_WORDS:
        return (parts[0] or None), _OG_WORKPLACE_TYPE_WORDS[parts[1].lower()]
    if len(parts) == 1 and parts[0].lower() in _OG_WORKPLACE_TYPE_WORDS:
        return None, _OG_WORKPLACE_TYPE_WORDS[parts[0].lower()]
    return None, WorkplaceType.UNKNOWN


_MAX_LOCATION_LENGTH = 255


def parse_og_description(og_description_text: str) -> tuple[str | None, str]:
    """Best-effort extraction of (location, workplace_type) from an
    og:description.

    Greenhouse-style job boards often pack these into one short string —
    "Utah | Hybrid", "San Francisco, CA | On-site", or just "Remote" on its
    own. Multi-location postings (remote-eligible across many states) chain
    several of these with ";", e.g. "Arizona | Remote; Utah | Hybrid" — each
    chunk is parsed individually and the locations combined. Workplace type
    is only set when every chunk agrees; a mix (as in that example) is left
    as UNKNOWN rather than guessing which one applies. Anything that doesn't
    match this shape at all is left as (None, UNKNOWN); the raw string is
    still used as the description text by the caller regardless.
    """
    segments = [s.strip() for s in og_description_text.split(";") if s.strip()]
    parsed = [_parse_location_workplace_segment(s) for s in segments]

    locations = list(dict.fromkeys(loc for loc, _ in parsed if loc))  # dedupe, keep order
    location = "; ".join(locations) or None
    if location and len(location) > _MAX_LOCATION_LENGTH:
        location = location[: _MAX_LOCATION_LENGTH - 3] + "..."

    workplace_types = {wt for _, wt in parsed if wt != WorkplaceType.UNKNOWN}
    workplace_type = workplace_types.pop() if len(workplace_types) == 1 else WorkplaceType.UNKNOWN

    return location, workplace_type


def _iter_job_postings(node: Any) -> list[dict[str, Any]]:
    """Recursively walk a parsed JSON-LD document (which may be a dict, a
    list of dicts, or nested under "@graph") and collect every node whose
    @type is (or includes) "JobPosting".
    """
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_iter_job_postings(item))
    elif isinstance(node, dict):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(isinstance(t, str) and t == "JobPosting" for t in types):
            found.append(node)
        if "@graph" in node:
            found.extend(_iter_job_postings(node["@graph"]))
    return found


def extract_json_ld_postings(html: str) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    for raw_block in _JSON_LD_RE.findall(html):
        block = raw_block.strip()
        try:
            # The block is valid JSON as-is on most sites, including ones
            # whose string values legitimately contain HTML entities (e.g.
            # "&quot;" inside an HTML description) that must NOT be
            # unescaped before parsing, since doing so can turn an entity
            # into a literal quote and corrupt the JSON structure.
            data = json.loads(block)
        except json.JSONDecodeError:
            try:
                # Fallback for the (rarer) case where the JSON itself was
                # HTML-escaped when templated into the page.
                data = json.loads(unescape(block))
            except json.JSONDecodeError:
                continue
        postings.extend(_iter_job_postings(data))
    return postings


def job_ld_location(job_ld: dict[str, Any]) -> str | None:
    """Every place in the posting's schema.org `jobLocation` (a single Place
    or a list of them), each as "City, Region, Country", joined with "; " —
    the separator downstream code splits multi-location postings on (see
    app.services.job_locations). Capped to the location column's width."""
    job_location = job_ld.get("jobLocation")
    places = job_location if isinstance(job_location, list) else [job_location]
    texts = list(dict.fromkeys(t for t in (_job_ld_place_text(p) for p in places) if t))
    location = "; ".join(texts) or None
    if location and len(location) > _MAX_LOCATION_LENGTH:
        location = location[: _MAX_LOCATION_LENGTH - 3] + "..."
    return location


def _job_ld_place_text(job_location: Any) -> str | None:
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    # schema.org's PostalAddress object is the strict shape, but some sites
    # (verified live: Dynatrace) just put a plain string straight in
    # "address" instead — a looser but common real-world deviation, worth
    # taking as-is rather than dropping the location entirely.
    if isinstance(address, str):
        return clean_text(address)
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    # schema.org allows addressCountry to be either a plain ISO string or a
    # full Country object ({"@type": "Country", "name": "US"}) — verified
    # on a live Eightfold-powered listing (jobs.twilio.com) using the
    # latter, which the plain isinstance(..., str) filter below would
    # otherwise silently drop.
    if isinstance(country, dict):
        country = country.get("name")
    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        country,
    ]
    # Some ATS feeds (e.g. iCIMS, used by GitHub's careers site) fill unset
    # address fields with the literal string "UNAVAILABLE" instead of
    # omitting them.
    parts = [p for p in parts if isinstance(p, str) and p.strip() and p.strip().upper() != "UNAVAILABLE"]
    return ", ".join(parts) if parts else None


def job_ld_workplace_type(job_ld: dict[str, Any]) -> str:
    location_type = job_ld.get("jobLocationType")
    is_remote = location_type == "TELECOMMUTE" or (
        isinstance(location_type, list) and "TELECOMMUTE" in location_type
    )
    has_onsite_location = job_ld_location(job_ld) is not None

    if is_remote and has_onsite_location:
        return WorkplaceType.HYBRID
    if is_remote:
        return WorkplaceType.REMOTE
    if has_onsite_location:
        return WorkplaceType.ONSITE
    return WorkplaceType.UNKNOWN


def job_ld_employment_type(job_ld: dict[str, Any]) -> str:
    employment_type = job_ld.get("employmentType")
    if isinstance(employment_type, list):
        employment_type = employment_type[0] if employment_type else None
    if isinstance(employment_type, str):
        # schema.org's enum tokens are strictly "FULL_TIME"-style, but some
        # sites (verified live: Dynatrace's "Full-time") use a human-
        # readable, hyphenated variant instead — normalizing separators
        # before the uppercase lookup catches both without growing
        # EMPLOYMENT_TYPE_MAP's keys, since no real schema.org token itself
        # contains a hyphen or space to collide with.
        normalized = employment_type.strip().upper().replace("-", "_").replace(" ", "_")
        return EMPLOYMENT_TYPE_MAP.get(normalized, EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


def job_ld_salary(job_ld: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    base_salary = job_ld.get("baseSalary")
    if not isinstance(base_salary, dict):
        return None, None, None
    currency = base_salary.get("currency")
    value = base_salary.get("value")
    if not isinstance(value, dict):
        return None, None, currency if isinstance(currency, str) else None

    def _as_int(x: Any) -> int | None:
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return None

    single = _as_int(value.get("value"))
    salary_min = _as_int(value.get("minValue")) or single
    salary_max = _as_int(value.get("maxValue")) or single
    # Some ATS feeds (e.g. iCIMS) report 0/0/0 rather than omitting the
    # field when salary isn't disclosed — treat that as "no data", not
    # "unpaid".
    if not salary_min and not salary_max:
        return None, None, currency if isinstance(currency, str) else None
    return salary_min, salary_max, currency if isinstance(currency, str) else None


def job_ld_posted_at(job_ld: dict[str, Any]) -> date | None:
    date_posted = job_ld.get("datePosted")
    if not isinstance(date_posted, str):
        return None
    try:
        return date.fromisoformat(date_posted[:10])
    except ValueError:
        return None


# $ is ambiguous (USD/CAD/AUD/...) so we default it to USD, which is right
# far more often than not for English-language listings.
_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR"}
_CURRENCY_CODES = "USD|CAD|AUD|NZD|GBP|EUR|CHF|JPY|INR"
# Matches things like "USD $124,000.00 - USD $329,200.00", "$120,000-$160,000",
# "50,000 - 65,000 GBP", "between $216,200 and $394,000", or "224,000 USD -
# 356,500 USD" — two amounts joined by a dash/"to"/"and", with a currency
# code/symbol before either amount, trailing the min amount, and/or trailing
# the range.
# Many pay-transparency-law job descriptions state a salary range in prose
# even when the page's structured data (if any) omits or zeroes it out.
# The amount groups require proper thousands-grouping (\d{1,3}(,\d{3})*)
# rather than a loose \d[\d,]* — verified live on an NVIDIA/Workday posting
# whose per-level bands ("...356,500 USD for Level 5, and 272,000 USD...")
# otherwise let the loose pattern swallow the trailing digit of "Level 5"
# and the "and" before the next band as a bogus "5 - 272,000" range.
# The optional trailing k/K (min_k/max_k) handles compact shorthand like
# "$145k-$163k" (verified live on a DispatchHealth/NLX posting) — a plain
# "145" would otherwise still satisfy \d{1,3} on its own and just fail to
# find a dash immediately after, silently dropping the whole range instead
# of erroring.
_SALARY_RANGE_RE = re.compile(
    rf"""
    (?:(?P<cur1>{_CURRENCY_CODES})\s*)?(?P<sym1>[\$£€])?\s*
    (?<!\d)(?P<min>\d{{1,3}}(?:,\d{{3}})*(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)(?P<min_k>[kK])?
    \s*(?:(?P<cur1b>{_CURRENCY_CODES})\s*)?
    \s*(?:-|–|—|\bto\b|\band\b)\s*
    (?:(?P<cur2>{_CURRENCY_CODES})\s*)?(?P<sym2>[\$£€])?\s*
    (?<!\d)(?P<max>\d{{1,3}}(?:,\d{{3}})*(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)(?P<max_k>[kK])?
    (?:\s*(?P<cur3>{_CURRENCY_CODES}))?
    """,
    re.IGNORECASE | re.VERBOSE,
)
# The comma-grouped alternative above is tried first so "120,000" still
# consumes as one number; the bare \d+ fallback only kicks in (via
# backtracking, once the comma-grouped alternative's shorter partial match
# fails to find the range separator immediately after) for amounts with no
# thousands-separator at all, e.g. Oracle Fusion/Amex's "$103750 - $174750"
# — verified live, previously silently mis-parsed as (174, 750) because the
# comma-grouped pattern alone accepted just the first 3 digits ("103") as a
# complete match, and moving the search past the (correctly rejected)
# non-dash tail let it latch onto the trailing "750"/"174" of each number
# instead. The (?<!\d) lookbehind on both amount groups is what closes that
# hole: it stops either alternative from starting mid-digit-run, so a
# uncomma'd number can now only be matched as a whole from its true start.
# Some listings (e.g. amazon.jobs, verified live) state pay as a single
# figure rather than a range, e.g. "Austin, TX, USA - 116,100.00 USD
# Annually" — same currency-code-before-or-after-the-amount shape as the
# range regex above, but anchored on a trailing pay-period word so it
# doesn't fire on arbitrary standalone numbers in the description.
_SALARY_SINGLE_RE = re.compile(
    rf"""
    (?:(?P<cur1>{_CURRENCY_CODES})\s*)?(?P<sym1>[\$£€])?\s*
    (?P<amount>\d[\d,]*(?:\.\d+)?)(?P<amount_k>[kK])?
    \s*(?:(?P<cur2>{_CURRENCY_CODES}))?
    \s*(?:annually|per\s+year|/\s*yr\b|per\s+annum|hourly|per\s+hour|/\s*hr\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def salary_from_text(text: str | None) -> tuple[int | None, int | None, str | None]:
    """Regex fallback for when structured salary data is missing or
    zeroed out but the (plain-text, already-cleaned) description states a
    range in prose, e.g. "The base salary range for this job is USD
    $124,000.00 - USD $329,200.00 /Yr." (common under US pay-transparency
    laws).
    """
    if not text:
        return None, None, None

    # .search() alone would settle for the *first* number-dash-number shape
    # in the text and bail — verified live on an amazon.jobs posting whose
    # description opens with an unrelated "8-10" (years of experience) that
    # has no currency marker, well before the real "26.25 - 29.75 USD
    # hourly" pay range further down. Walk every candidate instead and skip
    # any that doesn't actually carry a currency signal.
    #
    # Some listings (e.g. big-tech postings open to multiple levels) state a
    # separate band per level rather than one overall range — take the
    # lowest min and highest max across every currency-bearing band found,
    # so a posting like "224,000 - 356,500 USD for Level 5, and 272,000 -
    # 431,250 USD for Level 6" reports the full 224,000-431,250 span.
    overall_min: int | None = None
    overall_max: int | None = None
    overall_currency: str | None = None
    for match in _SALARY_RANGE_RE.finditer(text):
        cur1, sym1, cur1b, cur2, sym2, cur3 = match.group(
            "cur1", "sym1", "cur1b", "cur2", "sym2", "cur3"
        )
        if not (cur1 or sym1 or cur1b or cur2 or sym2 or cur3):
            continue

        try:
            salary_min = round(float(match.group("min").replace(",", "")))
            salary_max = round(float(match.group("max").replace(",", "")))
        except ValueError:
            continue
        if match.group("min_k"):
            salary_min *= 1000
        if match.group("max_k"):
            salary_max *= 1000
        if salary_min > salary_max:
            salary_min, salary_max = salary_max, salary_min

        currency = cur1 or cur1b or cur2 or cur3 or _CURRENCY_SYMBOLS.get(sym1 or sym2 or "")
        overall_min = salary_min if overall_min is None else min(overall_min, salary_min)
        overall_max = salary_max if overall_max is None else max(overall_max, salary_max)
        overall_currency = overall_currency or (currency.upper() if currency else None)

    if overall_min is not None:
        return overall_min, overall_max, overall_currency

    # No range found — some listings (see _SALARY_SINGLE_RE) state a single
    # flat figure instead of a range.
    match = _SALARY_SINGLE_RE.search(text)
    if match is None:
        return None, None, None

    cur1, sym1, cur2 = match.group("cur1", "sym1", "cur2")
    try:
        amount = round(float(match.group("amount").replace(",", "")))
    except ValueError:
        return None, None, None
    if match.group("amount_k"):
        amount *= 1000

    currency = cur1 or cur2 or _CURRENCY_SYMBOLS.get(sym1 or "")
    return amount, amount, currency.upper() if currency else None
