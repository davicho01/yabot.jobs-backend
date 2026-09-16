import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.core.config import settings
from app.models.enums import EmploymentType, WorkplaceType

_SCRAPERAPI_URL = "https://api.scraperapi.com/"

logger = logging.getLogger("app.job_scanner")

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"gclid", "fbclid", "ref", "igshid"}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL
)
_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
# Open Graph fallback for pages with no JobPosting JSON-LD and no standard
# meta description (e.g. Greenhouse's application-form pages) — og:title is
# usually cleaner than the raw <title> tag, and og:description often carries
# a terse "Location | WorkplaceType" (e.g. "Utah | Hybrid") or just a
# workplace type on its own.
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL
)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL
)
# og:site_name is usually the site/company's own display name (e.g. a small
# custom-built or BambooHR-hosted careers page with no other structured
# data at all) — a last-resort company name source, tried only after every
# more specific extractor (JSON-LD, Greenhouse's embedded JSON, the fixed
# single-company domains) has already come up empty.
_OG_SITE_NAME_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")

# Used by _html_to_formatted_text to turn block-level HTML structure into
# readable whitespace instead of collapsing it away (see _clean_text, which
# does the latter and is fine for single-line fields like title/company but
# turns a whole job description into an unreadable wall of text).
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_LI_TRAILING_BR_RE = re.compile(r"<br\s*/?>\s*</li>", re.IGNORECASE)
_LI_OPEN_RE = re.compile(r"<li[^>]*>\s*", re.IGNORECASE)
_LI_CLOSE_RE = re.compile(r"</li\s*>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HEADER_OPEN_RE = re.compile(r"<h([1-6])[^>]*>", re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r"</(?:p|div|ul|ol|h[1-6])\s*>", re.IGNORECASE)
# Converted to Markdown equivalents (**bold**, *italic*, [text](url), #
# headings) rather than dropped outright, so the frontend's Markdown
# renderer can still show the emphasis/structure a plain-text description
# would otherwise lose entirely. Applied before the final catch-all tag
# strip below, which removes whatever's left (spans, divs, ...) with no
# Markdown equivalent to convert to.
_LINK_RE = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_BOLD_OPEN_RE = re.compile(r"<(?:strong|b)\b[^>]*>", re.IGNORECASE)
_BOLD_CLOSE_RE = re.compile(r"</(?:strong|b)\s*>", re.IGNORECASE)
_EM_OPEN_RE = re.compile(r"<(?:em|i)\b[^>]*>", re.IGNORECASE)
_EM_CLOSE_RE = re.compile(r"</(?:em|i)\s*>", re.IGNORECASE)

# schema.org JobPosting employmentType -> our enum. Anything not listed
# (VOLUNTEER, PER_DIEM, OTHER, ...) falls back to UNKNOWN.
_EMPLOYMENT_TYPE_MAP = {
    "FULL_TIME": EmploymentType.FULL_TIME,
    "PART_TIME": EmploymentType.PART_TIME,
    "CONTRACTOR": EmploymentType.CONTRACT,
    "TEMPORARY": EmploymentType.TEMPORARY,
    "INTERN": EmploymentType.INTERNSHIP,
}

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

# $ is ambiguous (USD/CAD/AUD/...) so we default it to USD, which is right
# far more often than not for English-language listings.
_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR"}
_CURRENCY_CODES = "USD|CAD|AUD|NZD|GBP|EUR|CHF|JPY|INR"
# Matches things like "USD $124,000.00 - USD $329,200.00", "$120,000-$160,000",
# "50,000 - 65,000 GBP", or "between $216,200 and $394,000" — two amounts
# joined by a dash/"to"/"and", with a currency code/symbol before either
# amount and/or trailing the range.
# Many pay-transparency-law job descriptions state a salary range in prose
# even when the page's structured data (if any) omits or zeroes it out.
_SALARY_RANGE_RE = re.compile(
    rf"""
    (?:(?P<cur1>{_CURRENCY_CODES})\s*)?(?P<sym1>[\$£€])?\s*
    (?P<min>\d[\d,]*(?:\.\d+)?)
    \s*(?:-|–|\bto\b|\band\b)\s*
    (?:(?P<cur2>{_CURRENCY_CODES})\s*)?(?P<sym2>[\$£€])?\s*
    (?P<max>\d[\d,]*(?:\.\d+)?)
    (?:\s*(?P<cur3>{_CURRENCY_CODES}))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_url(raw_url: str) -> str:
    """Canonicalize a URL so trivial variants (tracking params, fragment,
    host casing) dedupe to the same JobPostingUrl row.
    """
    parts = urlsplit(raw_url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"

    kept_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    kept_params.sort()
    query = urlencode(kept_params)

    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode()).hexdigest()


def domain_of(normalized_url: str) -> str:
    return urlsplit(normalized_url).netloc


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    # Unescape BEFORE stripping tags: some sites (e.g. Mastercard) double-
    # encode their JSON-LD description as HTML-entity-escaped markup
    # ("&lt;p&gt;...&lt;/p&gt;" inside the JSON string, not raw "<p>"). Tag
    # stripping first would miss those entirely, since _TAG_RE only matches
    # literal "<...>" — they'd only turn into real tags *after* stripping,
    # leaking straight into the cleaned text.
    text = _TAG_RE.sub(" ", unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _tighten_bold_markers(text: str) -> str:
    """Move each "**" past any whitespace on its *inner* side (trailing for
    an opener, leading for a closer — the two alternate with each
    occurrence).

    Source HTML that wraps a heading like <strong>Title<br></strong> puts
    the closing tag right after the line break, so the naive tag->marker
    conversion above leaves the closing "**" sitting right after a
    newline: "**Title\\n**Body...". CommonMark won't treat a "**"
    preceded by whitespace as a valid closer, so that would render as a
    literal, unrendered "**" instead of ending the bold run. Must run
    after every other tag has already been substituted/stripped — while
    e.g. a <span> still sits between the newline and the marker, there's
    no whitespace for this to find yet.
    """
    segments = text.split("**")
    if len(segments) < 3:
        return text
    for i in range(1, len(segments)):
        if i % 2 == 1:  # opening marker sits between segments[i-1] and segments[i]
            stripped = segments[i].lstrip()
            segments[i - 1] += segments[i][: len(segments[i]) - len(stripped)]
            segments[i] = stripped
        else:  # closing marker sits between segments[i-1] and segments[i]
            stripped = segments[i - 1].rstrip()
            segments[i] = segments[i - 1][len(stripped) :] + segments[i]
            segments[i - 1] = stripped
    return "**".join(segments)


def _html_to_formatted_text(value: Any) -> str | None:
    """Like _clean_text, but for the `description` field: converts to
    Markdown rather than flattening to bare text, so paragraph breaks,
    headings, bullet lists, links, and bold/italic emphasis all survive
    (the frontend renders this field as Markdown) instead of every bit of
    structure and emphasis just vanishing along with the tags carrying it.
    """
    if not isinstance(value, str):
        return None
    text = _SCRIPT_STYLE_RE.sub(" ", value)
    text = _LINK_RE.sub(r"[\2](\1)", text)
    text = _BOLD_OPEN_RE.sub("**", text)
    text = _BOLD_CLOSE_RE.sub("**", text)
    text = _EM_OPEN_RE.sub("*", text)
    text = _EM_CLOSE_RE.sub("*", text)
    text = _LI_TRAILING_BR_RE.sub("</li>", text)
    text = _LI_OPEN_RE.sub("\n- ", text)
    text = _LI_CLOSE_RE.sub("", text)
    text = _BR_RE.sub("\n", text)
    text = _HEADER_OPEN_RE.sub(lambda m: "\n\n" + "#" * int(m.group(1)) + " ", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = unescape(_TAG_RE.sub("", text))
    text = _tighten_bold_markers(text)

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if line:
            cleaned.append(line)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned) or None


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


def _extract_json_ld_postings(html: str) -> list[dict[str, Any]]:
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


def _location_of(job_ld: dict[str, Any]) -> str | None:
    job_location = job_ld.get("jobLocation")
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
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


def _workplace_type_of(job_ld: dict[str, Any]) -> str:
    location_type = job_ld.get("jobLocationType")
    is_remote = location_type == "TELECOMMUTE" or (
        isinstance(location_type, list) and "TELECOMMUTE" in location_type
    )
    has_onsite_location = _location_of(job_ld) is not None

    if is_remote and has_onsite_location:
        return WorkplaceType.HYBRID
    if is_remote:
        return WorkplaceType.REMOTE
    if has_onsite_location:
        return WorkplaceType.ONSITE
    return WorkplaceType.UNKNOWN


def _employment_type_of(job_ld: dict[str, Any]) -> str:
    employment_type = job_ld.get("employmentType")
    if isinstance(employment_type, list):
        employment_type = employment_type[0] if employment_type else None
    if isinstance(employment_type, str):
        return _EMPLOYMENT_TYPE_MAP.get(employment_type.upper(), EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


def _salary_of(job_ld: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
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


def _salary_from_text(text: str | None) -> tuple[int | None, int | None, str | None]:
    """Regex fallback for when structured salary data is missing or
    zeroed out but the (plain-text, already-cleaned) description states a
    range in prose, e.g. "The base salary range for this job is USD
    $124,000.00 - USD $329,200.00 /Yr." (common under US pay-transparency
    laws).
    """
    if not text:
        return None, None, None

    match = _SALARY_RANGE_RE.search(text)
    if match is None:
        return None, None, None

    cur1, sym1, cur2, sym2, cur3 = match.group("cur1", "sym1", "cur2", "sym2", "cur3")
    if not (cur1 or sym1 or cur2 or sym2 or cur3):
        return None, None, None

    try:
        salary_min = round(float(match.group("min").replace(",", "")))
        salary_max = round(float(match.group("max").replace(",", "")))
    except ValueError:
        return None, None, None
    if salary_min > salary_max:
        salary_min, salary_max = salary_max, salary_min

    currency = cur1 or cur2 or cur3 or _CURRENCY_SYMBOLS.get(sym1 or sym2 or "")
    return salary_min, salary_max, currency.upper() if currency else None


_MAX_LOCATION_LENGTH = 255  # matches JobPosting.location's column width


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


def _parse_og_description(og_description: str) -> tuple[str | None, str]:
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
    segments = [s.strip() for s in og_description.split(";") if s.strip()]
    parsed = [_parse_location_workplace_segment(s) for s in segments]

    locations = list(dict.fromkeys(loc for loc, _ in parsed if loc))  # dedupe, keep order
    location = ", ".join(locations) or None
    if location and len(location) > _MAX_LOCATION_LENGTH:
        location = location[: _MAX_LOCATION_LENGTH - 3] + "..."

    workplace_types = {wt for _, wt in parsed if wt != WorkplaceType.UNKNOWN}
    workplace_type = workplace_types.pop() if len(workplace_types) == 1 else WorkplaceType.UNKNOWN

    return location, workplace_type


def _posted_at_of(job_ld: dict[str, Any]) -> date | None:
    date_posted = job_ld.get("datePosted")
    if not isinstance(date_posted, str):
        return None
    try:
        return date.fromisoformat(date_posted[:10])
    except ValueError:
        return None


# Google's careers pages ship no JSON-LD JobPosting data at all, and their
# <meta name="description"> only carries the "About the job" blurb — the
# Minimum/Preferred qualifications and Responsibilities sections live
# further down the real, server-rendered page body with no structured data
# of their own, just an <h3> heading per section. Anchored on that heading
# text (stable) through the "bE3reb" class (the wrapper for the page's
# legal/EEO boilerplate that immediately follows this content on every
# posting observed) rather than Google's other minified CSS class names,
# which could change without notice. Falls back to no-op (caller keeps the
# meta-description-only text) if this shape isn't found on a given page.
_GOOGLE_QUALIFICATIONS_START_RE = re.compile(r"<h3>\s*Minimum\s+Qualifications", re.IGNORECASE)
_GOOGLE_SECTION_END_MARKER = 'class="bE3reb"'

# Amazon, Google, and Apple's own in-house career sites (unlike a
# multi-tenant ATS such as Greenhouse) each host exactly one company's
# postings, ship no JSON-LD hiringOrganization, and have no on-page field
# naming the company at all — so the company name isn't "extracted", it's
# just known from which site this is. See ats_adapters.py's own
# single-company handling (_DETECT_PATTERNS) for the same distinction.
_SINGLE_COMPANY_DOMAINS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"amazon\.jobs", re.IGNORECASE), "Amazon"),
    (re.compile(r"google\.com/about/careers", re.IGNORECASE), "Google"),
    (re.compile(r"jobs\.apple\.com", re.IGNORECASE), "Apple"),
]


def _single_company_name_for_url(url: str) -> str | None:
    for pattern, company_name in _SINGLE_COMPANY_DOMAINS:
        if pattern.search(url):
            return company_name
    return None


# jobs.apple.com is a client-rendered SPA, but the initial HTML still
# embeds the full job payload (location, posting date, description, ...)
# as a JSON string passed to JSON.parse — the same
# `window.__staticRouterHydrationData` blob ats_adapters.py's Apple
# discovery adapter reads from the *search* page, just under a different
# loaderData key ("jobDetails" instead of "search") on the job detail page.
_APPLE_HYDRATION_RE = re.compile(r'window\.__staticRouterHydrationData = JSON\.parse\("(.*?)"\);', re.DOTALL)


def _extract_apple_job_data(html: str) -> dict[str, Any] | None:
    match = _APPLE_HYDRATION_RE.search(html)
    if match is None:
        return None
    try:
        data = json.loads(json.loads(f'"{match.group(1)}"'))
        job_data = data["loaderData"]["jobDetails"]["jobsData"]
        return job_data if isinstance(job_data, dict) else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _apple_location_of(job_data: dict[str, Any]) -> str | None:
    locations = job_data.get("localeLocation")
    if not isinstance(locations, list):
        return None
    parts = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        bits = [loc.get("city"), loc.get("stateProvince"), loc.get("countryName")]
        parts.append(", ".join(b for b in bits if isinstance(b, str) and b.strip()))
    location = "; ".join(p for p in dict.fromkeys(parts) if p) or None
    if location and len(location) > _MAX_LOCATION_LENGTH:
        location = location[: _MAX_LOCATION_LENGTH - 3] + "..."
    return location


def _apple_posted_at_of(job_data: dict[str, Any]) -> date | None:
    posting_date = job_data.get("postingDateMeta")
    if not isinstance(posting_date, str):
        return None
    try:
        return date.fromisoformat(posting_date[:10])
    except ValueError:
        return None


def _apple_title_of(job_data: dict[str, Any]) -> str | None:
    title = job_data.get("postingTitle")
    return _clean_text(title) if isinstance(title, str) else None


# responsibilities/*Qualifications are plain text, one bullet item per
# line (verified on a live posting) rather than an HTML <ul>/<li> list like
# Oracle's equivalents, so each line needs an explicit "- " marker before
# going through _html_to_formatted_text — otherwise it survives as an
# unbulleted paragraph and the list structure is lost.
def _apple_bulleted(text: str) -> str:
    return "\n".join(f"- {line.strip()}" for line in text.split("\n") if line.strip())


# postingFooters carries the Pay & Benefits / EEO Statement / Accessibility
# / Application Deadline sections that render below Preferred Qualifications
# on the real posting, keyed by postLocationId (one entry per job location;
# taking the first is fine since they're the same boilerplate/comp text
# per-region rather than per-job). Each has its own displayOrder, so sort on
# that rather than trusting dict iteration order.
def _apple_posting_footer_sections(job_data: dict[str, Any]) -> list[tuple[str, str]]:
    footers = job_data.get("postingFooters")
    if not isinstance(footers, list) or not footers:
        return []
    first = footers[0]
    if not isinstance(first, dict):
        return []
    sections = first.get("localizations", {}).get("en_US")
    if not isinstance(sections, list):
        return []
    ordered = sorted(
        (s for s in sections if isinstance(s, dict) and isinstance(s.get("content"), str) and s["content"].strip()),
        key=lambda s: s.get("displayOrder", 0),
    )
    return [(s.get("name") or "", s["content"]) for s in ordered]


def _apple_description_of(job_data: dict[str, Any]) -> str | None:
    sections = [job_data.get("jobSummary"), job_data.get("description")]
    if job_data.get("responsibilities"):
        sections.append("<h3>Responsibilities</h3>" + _apple_bulleted(job_data["responsibilities"]))
    if job_data.get("minimumQualifications"):
        sections.append("<h3>Minimum Qualifications</h3>" + _apple_bulleted(job_data["minimumQualifications"]))
    if job_data.get("preferredQualifications"):
        sections.append("<h3>Preferred Qualifications</h3>" + _apple_bulleted(job_data["preferredQualifications"]))
    for name, content in _apple_posting_footer_sections(job_data):
        sections.append(f"<h3>{name}</h3>" + content if name else content)
    html = "\n\n".join(s for s in sections if isinstance(s, str) and s.strip())
    return _html_to_formatted_text(html) if html else None


# Eightfold-powered white-label career sites (e.g. jobs.twilio.com,
# explore.jobs.netflix.net) serve a `/careers/job/{numeric_id}` page that
# renders the actual posting client-side, but carries a stable signature
# string in its static HTML (same one app.services.ats_adapters uses to
# recognize these sites during discovery) and exposes a free public API for
# the real data — no board token to already know, just the requesting
# host's own "domain" tenant identifier, which is usually its registrable
# domain (not guaranteed: verified this API is sometimes disabled entirely
# for a given company, e.g. Netflix's own instance 403s "PCSX is not
# enabled for this user" on the exact same endpoint that works for Twilio —
# nothing to extract there regardless of how the domain guess goes).
_EIGHTFOLD_JOB_URL_RE = re.compile(r"/careers/job/(\d+)")
_EIGHTFOLD_SIGNATURE = "eightfold.ai/privacy-policy"


def _candidate_eightfold_domains(host: str) -> list[str]:
    labels = host.lower().split(".")
    candidates = [host.lower()]
    for strip in (1, 2):
        if len(labels) > strip + 1:
            candidates.append(".".join(labels[strip:]))
    return list(dict.fromkeys(candidates))  # dedupe, keep order


def _fetch_eightfold_job_data(url: str, html: str) -> dict[str, Any] | None:
    job_match = _EIGHTFOLD_JOB_URL_RE.search(url)
    if job_match is None or _EIGHTFOLD_SIGNATURE not in html:
        return None
    job_id = job_match.group(1)
    host = urlsplit(url).netloc
    for domain in _candidate_eightfold_domains(host):
        try:
            response = httpx.get(
                f"https://{host}/api/pcsx/position_details",
                params={"position_id": job_id, "domain": domain},
                timeout=10.0,
            )
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        data = response.json().get("data")
        if isinstance(data, dict) and str(data.get("id")) == job_id:
            return data
    return None


def _eightfold_posted_at_of(job_data: dict[str, Any]) -> date | None:
    creation_ts = job_data.get("creationTs")
    if not isinstance(creation_ts, (int, float)) or not creation_ts:
        return None
    try:
        return date.fromtimestamp(creation_ts).date()
    except (ValueError, OSError):
        return None


# Oracle Fusion's CandidateExperience job page (see
# app.services.adapters.oracle_fusion, which discovers these same URLs) is
# a client-rendered SPA: its static HTML carries no JSON-LD and nothing
# past title/description/company in og: tags — no location, schedule, or
# full description. But the same public recruitingCEJobRequisitionDetails
# REST API the SPA itself calls client-side is keyed by exactly the
# host/site-number/job-id already sitting in the URL, and returns the full
# requisition record — primary location, job schedule, and the complete
# description/responsibilities/qualifications HTML the og:description
# preview is truncated from.
_ORACLE_FUSION_JOB_URL_RE = re.compile(
    r"([a-zA-Z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([^/]+)/job/(\d+)",
    re.IGNORECASE,
)
_ORACLE_FUSION_DETAIL_URL = "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"

# JobSchedule is a free-text label ("Full time"/"Part time"), not a fixed
# enum — only these two values have been observed on a real tenant, and
# it's often unset entirely, so anything else falls back to UNKNOWN like
# every other adapter's unmapped case.
_ORACLE_FUSION_JOB_SCHEDULE_MAP = {
    "full time": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
}
# WorkplaceTypeCode is unset on every requisition seen on the one tenant
# this was verified against, but the field exists in Oracle's schema —
# mapped defensively so a tenant that does set it isn't left at UNKNOWN.
_ORACLE_FUSION_WORKPLACE_TYPE_MAP = {
    "REMOTE": WorkplaceType.REMOTE,
    "HYBRID": WorkplaceType.HYBRID,
    "ON_SITE": WorkplaceType.ONSITE,
    "ONSITE": WorkplaceType.ONSITE,
}


def _fetch_oracle_fusion_job_data(url: str) -> dict[str, Any] | None:
    match = _ORACLE_FUSION_JOB_URL_RE.search(url)
    if match is None:
        return None
    host, site_number, job_id = match.groups()
    try:
        response = httpx.get(
            _ORACLE_FUSION_DETAIL_URL.format(host=host),
            params={
                "onlyData": "true",
                "expand": "all",
                "finder": f'ById;Id="{job_id}",siteNumber={site_number}',
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    items = response.json().get("items", [])
    return items[0] if items and isinstance(items[0], dict) else None


def _oracle_fusion_location_of(job_data: dict[str, Any]) -> str | None:
    names = [job_data.get("PrimaryLocation")]
    for loc in job_data.get("secondaryLocations") or []:
        if isinstance(loc, dict):
            names.append(loc.get("Name"))
    location = ", ".join(dict.fromkeys(n for n in names if isinstance(n, str) and n.strip())) or None
    if location and len(location) > _MAX_LOCATION_LENGTH:
        location = location[: _MAX_LOCATION_LENGTH - 3] + "..."
    return location


def _oracle_fusion_employment_type_of(job_data: dict[str, Any]) -> str:
    schedule = job_data.get("JobSchedule")
    if isinstance(schedule, str):
        return _ORACLE_FUSION_JOB_SCHEDULE_MAP.get(schedule.strip().lower(), EmploymentType.UNKNOWN)
    return EmploymentType.UNKNOWN


def _oracle_fusion_workplace_type_of(job_data: dict[str, Any]) -> str:
    code = job_data.get("WorkplaceTypeCode")
    if isinstance(code, str) and code:
        return _ORACLE_FUSION_WORKPLACE_TYPE_MAP.get(code.upper(), WorkplaceType.UNKNOWN)
    return WorkplaceType.UNKNOWN


def _oracle_fusion_posted_at_of(job_data: dict[str, Any]) -> date | None:
    posted = job_data.get("ExternalPostedStartDate")
    if not isinstance(posted, str):
        return None
    try:
        return date.fromisoformat(posted[:10])
    except ValueError:
        return None


def _oracle_fusion_description_of(job_data: dict[str, Any]) -> str | None:
    sections = [job_data.get("ExternalDescriptionStr")]
    if job_data.get("ExternalResponsibilitiesStr"):
        sections.append("<h3>Responsibilities</h3>" + job_data["ExternalResponsibilitiesStr"])
    if job_data.get("ExternalQualificationsStr"):
        sections.append("<h3>Qualifications</h3>" + job_data["ExternalQualificationsStr"])
    html = "".join(s for s in sections if isinstance(s, str) and s.strip())
    return _html_to_formatted_text(html) if html else None


def _extract_google_job_body(html: str) -> str | None:
    start_match = _GOOGLE_QUALIFICATIONS_START_RE.search(html)
    if start_match is None:
        return None
    marker = html.find(_GOOGLE_SECTION_END_MARKER, start_match.start())
    if marker == -1:
        return None
    # Cut at the start of that marker's own <div ...> tag, not the marker
    # text itself, so the tag's dangling open bracket doesn't leak into the
    # output as literal "<div" text (it has no closing ">" within the slice).
    end = html.rfind("<div", start_match.start(), marker)
    if end == -1:
        end = marker
    return _html_to_formatted_text(html[start_match.start() : end])


_DIV_TAG_RE = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)


def _extract_balanced_div(html: str, div_start: int) -> str | None:
    """Return the inner HTML of the <div ...> opening at index `div_start`,
    found by tracking nested div depth to its true matching close tag — a
    simple non-greedy regex would stop at the first nested </div> instead,
    truncating the content after only its first child element.
    """
    open_end = html.find(">", div_start)
    if open_end == -1:
        return None
    depth = 1
    for m in _DIV_TAG_RE.finditer(html, open_end + 1):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[open_end + 1 : m.start()]
    return None


# job-boards.greenhouse.io (Greenhouse's newer job-board template, used by
# many companies) server-renders the full posting body into a
# <div class="job__description body"> — a stable, semantic template class,
# not a minified/hashed one — but ships no JSON-LD and only a short
# location string (e.g. "Arizona | Remote") in og:description, so without
# this the "description" ends up being just that location string.
_GREENHOUSE_DESCRIPTION_MARKER = 'class="job__description'


def _extract_greenhouse_job_description(html: str) -> str | None:
    marker = html.find(_GREENHOUSE_DESCRIPTION_MARKER)
    if marker == -1:
        return None
    div_start = html.rfind("<div", 0, marker)
    if div_start == -1:
        return None
    inner = _extract_balanced_div(html, div_start)
    return _html_to_formatted_text(inner) if inner else None


# The same job-boards.greenhouse.io template also server-renders its full
# loader payload into a `window.__remixContext = {...}` JSON blob, which is
# the only place on the page carrying the company name and an unambiguous
# location string — og:description is just "Palo Alto, CA" with no marker
# distinguishing it from arbitrary marketing copy, so the general-purpose
# "Location | WorkplaceType" heuristic below can't safely use it. Matching
# the two fields directly out of that blob is far cheaper than parsing the
# whole (multi-hundred-KB) JSON object.
_GREENHOUSE_COMPANY_NAME_RE = re.compile(r'"company_name":"((?:[^"\\]|\\.)*)"')
_GREENHOUSE_JOB_LOCATION_RE = re.compile(r'"job_post_location":"((?:[^"\\]|\\.)*)"')
# ISO-8601 with an explicit offset, e.g. "2026-05-05T17:45:16-04:00" — no
# escaping to worry about (unlike the two string fields above), so this one
# doesn't need to go through _extract_greenhouse_remix_field's JSON-unescape.
_GREENHOUSE_PUBLISHED_AT_RE = re.compile(r'"published_at":"(\d{4}-\d{2}-\d{2})')


def _extract_greenhouse_remix_field(html: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(html)
    if match is None:
        return None
    try:
        # The matched text is a JSON string body (escapes and all); wrapping
        # it back in quotes and decoding gives proper unescaping for free.
        return json.loads(f'"{match.group(1)}"') or None
    except json.JSONDecodeError:
        return None


@dataclass
class ScanResult:
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


def _fetch_direct(url: str) -> httpx.Response:
    response = httpx.get(
        url,
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; YabotJobsBot/1.0)"},
    )
    response.raise_for_status()
    return response


def _fetch_via_scraperapi(url: str) -> httpx.Response:
    # httpx's exception messages embed the full request URL — which here
    # includes "?api_key=...". Never let one of those propagate as-is: it
    # would land in scan_error, which GET /jobs returns with no auth.
    try:
        response = httpx.get(
            _SCRAPERAPI_URL,
            # render=true runs a real headless browser so JS-rendered sites
            # (React/Next.js job boards that fetch content client-side, e.g.
            # ZipRecruiter) actually return their content instead of a
            # near-empty shell of <script> tags. Costs more ScraperAPI
            # credits per request, but this is already the last-resort
            # fallback path, not the common case.
            params={"api_key": settings.scraperapi_key, "url": url, "render": "true"},
            timeout=90.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # `from None` deliberately drops the original exception as __cause__:
        # it embeds the request URL (including "?api_key=..."), and Python
        # prints the full chain on any uncaught traceback or
        # logger.exception() call — chaining it would undo the sanitization.
        raise httpx.HTTPError(f"ScraperAPI request failed with status {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise httpx.HTTPError(f"ScraperAPI request failed: {type(exc).__name__}") from None
    return response


def _fetch_html(url: str) -> httpx.Response:
    """Fetch a page, falling back to ScraperAPI (when configured) for sites
    that block a plain HTTP client — bot-detection challenges, 403s, etc.
    Returns the full response (not just .text) so the caller can also
    inspect the final, post-redirect URL.
    """
    # ScraperAPI fallback disabled for now — direct fetch only.
    return _fetch_direct(url)

    # try:
    #     return _fetch_direct(url)
    # except httpx.HTTPError as exc:
    #     if not settings.scraperapi_key:
    #         logger.warning("Failed to fetch %s: %s", url, exc)
    #         raise
    #     logger.info("Direct fetch of %s failed (%s); retrying via ScraperAPI.", url, exc)
    #
    # try:
    #     return _fetch_via_scraperapi(url)
    # except httpx.HTTPError as exc:
    #     logger.warning("ScraperAPI fetch of %s also failed: %s", url, exc)
    #     raise


def _is_greenhouse_board_error_redirect(final_url: str) -> bool:
    """Greenhouse redirects an invalid/expired/removed job posting to its
    board's generic landing page with `?error=true` appended, rather than a
    404 — verified against a real removed Anthropic posting, which 200'd
    into https://job-boards.greenhouse.io/anthropic?error=true. Left
    undetected, that landing page's og:title/og:description (the company
    name, and nothing) get extracted as if they were the job's own.
    """
    parts = urlsplit(final_url)
    return "greenhouse.io" in parts.netloc and "error=true" in parts.query


def scan_job_url(url: str) -> ScanResult:
    """Fetch a job posting page and pull structured job info out of it.

    Sites that publish schema.org JobPosting JSON-LD (LinkedIn, Indeed,
    Greenhouse, Lever, and most ATS-hosted listings do) get full structured
    fields — company, location, salary, employment type, etc. — for free.
    Sites without it fall back to a simple <title> / meta-description
    baseline extractor so the submit-a-URL flow still works end-to-end.
    """
    try:
        response = _fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    if _is_greenhouse_board_error_redirect(str(response.url)):
        return ScanResult(
            success=False,
            error="Greenhouse redirected to the board's error page — this posting has likely been removed or filled.",
        )

    html = response.text
    job_postings = _extract_json_ld_postings(html)
    job_ld = job_postings[0] if job_postings else None

    title_match = _TITLE_RE.search(html)
    desc_match = _META_DESC_RE.search(html)
    fallback_title = title_match.group(1).strip() if title_match else None
    fallback_description = _html_to_formatted_text(desc_match.group(1)) if desc_match else None

    if job_ld is None:
        # No JSON-LD and no standard meta description (e.g. Greenhouse's
        # application-form pages) — Open Graph tags are usually the next
        # best source: og:title is cleaner than the raw <title>, and
        # og:description often carries "Location | WorkplaceType".
        og_title_match = _OG_TITLE_RE.search(html)
        og_desc_match = _OG_DESC_RE.search(html)
        og_site_name_match = _OG_SITE_NAME_RE.search(html)
        og_title = _clean_text(og_title_match.group(1)) if og_title_match else None
        og_site_name = _clean_text(og_site_name_match.group(1)) if og_site_name_match else None
        # _parse_og_description needs the raw single-line "Location |
        # WorkplaceType" shape, so it parses the un-formatted match directly
        # rather than the (possibly multi-line) formatted version below.
        og_description_raw = _clean_text(og_desc_match.group(1)) if og_desc_match else None
        og_description = _html_to_formatted_text(og_desc_match.group(1)) if og_desc_match else None

        location, workplace_type = (
            _parse_og_description(og_description_raw) if og_description_raw else (None, WorkplaceType.UNKNOWN)
        )
        gh_company_name = _extract_greenhouse_remix_field(html, _GREENHOUSE_COMPANY_NAME_RE)
        gh_location = _extract_greenhouse_remix_field(html, _GREENHOUSE_JOB_LOCATION_RE)
        gh_published_at_match = _GREENHOUSE_PUBLISHED_AT_RE.search(html)
        apple_job_data = _extract_apple_job_data(html)
        eightfold_job_data = _fetch_eightfold_job_data(str(response.url), html)
        oracle_job_data = _fetch_oracle_fusion_job_data(str(response.url))
        location = (
            gh_location
            or (_apple_location_of(apple_job_data) if apple_job_data else None)
            or (eightfold_job_data.get("location") if eightfold_job_data else None)
            or (_oracle_fusion_location_of(oracle_job_data) if oracle_job_data else None)
            or location
        )
        if workplace_type == WorkplaceType.UNKNOWN and oracle_job_data:
            workplace_type = _oracle_fusion_workplace_type_of(oracle_job_data)
        employment_type = (
            _oracle_fusion_employment_type_of(oracle_job_data) if oracle_job_data else EmploymentType.UNKNOWN
        )
        company_name = gh_company_name or _single_company_name_for_url(str(response.url)) or og_site_name
        posted_at = _apple_posted_at_of(apple_job_data) if apple_job_data else None
        if posted_at is None and eightfold_job_data:
            posted_at = _eightfold_posted_at_of(eightfold_job_data)
        if posted_at is None and oracle_job_data:
            posted_at = _oracle_fusion_posted_at_of(oracle_job_data)
        if posted_at is None and gh_published_at_match is not None:
            try:
                posted_at = date.fromisoformat(gh_published_at_match.group(1))
            except ValueError:
                posted_at = None
        description = (
            _extract_greenhouse_job_description(html)
            or _extract_google_job_body(html)
            or (_html_to_formatted_text(eightfold_job_data.get("jobDescription")) if eightfold_job_data else None)
            or (_oracle_fusion_description_of(oracle_job_data) if oracle_job_data else None)
            or (_apple_description_of(apple_job_data) if apple_job_data else None)
            or fallback_description
            or og_description
        )
        salary_min, salary_max, salary_currency = _salary_from_text(description)
        return ScanResult(
            success=True,
            title=(eightfold_job_data.get("name") if eightfold_job_data else None)
            or (oracle_job_data.get("Title") if oracle_job_data else None)
            or (_apple_title_of(apple_job_data) if apple_job_data else None)
            or og_title
            or fallback_title,
            description=description,
            company_name=_clean_text(company_name),
            location=location,
            workplace_type=workplace_type,
            employment_type=employment_type,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            posted_at=posted_at,
            raw_html_excerpt=html[:20_000],
            full_html=html,
        )

    hiring_org = job_ld.get("hiringOrganization")
    company_name = hiring_org.get("name") if isinstance(hiring_org, dict) else None
    description = _html_to_formatted_text(job_ld.get("description")) or fallback_description

    salary_min, salary_max, salary_currency = _salary_of(job_ld)
    if salary_min is None and salary_max is None:
        # Structured baseSalary was missing/zeroed-out; many listings still
        # state a range in prose (pay-transparency-law disclosures).
        text_min, text_max, text_currency = _salary_from_text(description)
        if text_min is not None:
            salary_min, salary_max = text_min, text_max
            salary_currency = text_currency or salary_currency

    known_keys = {
        "@context",
        "@type",
        "title",
        "description",
        "hiringOrganization",
        "jobLocation",
        "jobLocationType",
        "employmentType",
        "baseSalary",
        "datePosted",
    }
    extra_fields = {k: v for k, v in job_ld.items() if k not in known_keys}

    return ScanResult(
        success=True,
        title=_clean_text(job_ld.get("title")) or fallback_title,
        description=description,
        company_name=_clean_text(company_name),
        location=_location_of(job_ld),
        workplace_type=_workplace_type_of(job_ld),
        employment_type=_employment_type_of(job_ld),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=_posted_at_of(job_ld),
        extracted_fields=extra_fields or None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )
