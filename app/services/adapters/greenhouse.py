import json
import re
from datetime import date
from html import unescape
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    candidate_slugs_from_domain,
    get_with_retry,
    limit_job_urls,
)
from app.services.adapters.text import extract_balanced_div, html_to_formatted_text

_GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{board_key}/jobs"
_GREENHOUSE_URL_RE = re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/?]+)", re.IGNORECASE)
# Some companies white-label Greenhouse onto their own domain (e.g.
# harness.io/company/jobs/apply?gh_jid=...) via Greenhouse's embeddable JS
# widget rather than linking out to boards.greenhouse.io directly — the
# board key isn't visible in the URL at all in that case (only a numeric
# job id, in gh_jid), so _GREENHOUSE_URL_RE above can never match it.
# Greenhouse ships more than one embed variant with the same `?for=`
# token — job_board/js (harness.io) and job_app (instacart.careers) both
# verified live — so this matches any `embed/.../...?for=` shape rather
# than one specific script path.
_GH_EMBED_TOKEN_RE = re.compile(r"greenhouse\.io/embed/[a-zA-Z_/]*\?for=([a-zA-Z0-9_-]+)", re.IGNORECASE)
# Some sites instead fetch the job from Greenhouse's API server-side and
# stitch it into their own page (e.g. coalitioninc.com's Next.js-rendered
# job pages) — no embed script appears anywhere in the HTML, but gh_jid
# still leaks through wherever that fetched job data gets serialized into
# the page (its own absolute_url field, in Coalition's case) — verified
# live. Used as a fallback below when the embed-script check above misses.
_GH_EMBED_JOB_ID_RE = re.compile(r"gh_jid=(\d+)")


def _match(url: str) -> str | None:
    match = _GREENHOUSE_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required, and this single
    # call already returns the complete current board, so no recency filter
    # is needed here: limit_job_urls's dedupe/cap is the only shaping done.
    response = get_with_retry(_GREENHOUSE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return limit_job_urls(job["absolute_url"] for job in jobs if job.get("absolute_url"))


def _board_has_job(slug: str, job_id: str) -> bool:
    try:
        response = get_with_retry(f"{_GREENHOUSE_JOBS_URL.format(board_key=slug)}/{job_id}", timeout=TIMEOUT)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    # Fast path: the embeddable-widget script names the board directly —
    # authoritative, no guessing needed.
    match = _GH_EMBED_TOKEN_RE.search(response.text)
    if match:
        return match.group(1)

    # Fallback: no embed script, but gh_jid leaked through somewhere in the
    # page anyway (see _GH_EMBED_JOB_ID_RE above) — guess-and-verify a board
    # slug from the domain, same two-tier pattern as Ashby's embed detector.
    job_id_match = _GH_EMBED_JOB_ID_RE.search(response.text)
    if not job_id_match:
        return None
    job_id = job_id_match.group(1)
    domain = urlsplit(url).netloc
    for slug in candidate_slugs_from_domain(domain):
        if _board_has_job(slug, job_id):
            return slug
    return None


def is_error_redirect(final_url: str) -> bool:
    """Greenhouse redirects an invalid/expired/removed job posting to its
    board's generic landing page with `?error=true` appended, rather than a
    404 — verified against a real removed Anthropic posting, which 200'd
    into https://job-boards.greenhouse.io/anthropic?error=true. Left
    undetected, that landing page's og:title/og:description (the company
    name, and nothing) get extracted as if they were the job's own. Called
    by scan_job_url right after fetching, before any extraction — a
    dedicated error path, not a field this adapter's extract() supplies.
    """
    parts = urlsplit(final_url)
    return "greenhouse.io" in parts.netloc and "error=true" in parts.query


# Companies white-label Greenhouse's embeddable widget onto their own
# domain (e.g. harness.io/company/jobs/apply?gh_jid=...) instead of
# linking out to boards.greenhouse.io — see _GH_EMBED_TOKEN_RE above for
# discovery's use of the same signal. These pages ship no JSON-LD, so
# without this the scanner falls back to whatever's in <title>/og: tags —
# the wrapper page's own SEO copy, not the job's actual title/company/
# location/description. Once both the board slug and job id are known,
# Greenhouse's public per-job API (same one _fetch_jobs's board-level
# endpoint is a sibling of) returns the real structured record directly.
_JOB_API_URL = "https://boards-api.greenhouse.io/v1/boards/{board_key}/jobs/{job_id}"


def _fetch_embedded_job_data(url: str, html: str) -> dict[str, Any] | None:
    job_id_match = _GH_EMBED_JOB_ID_RE.search(url) or _GH_EMBED_JOB_ID_RE.search(html)
    if job_id_match is None:
        return None
    job_id = job_id_match.group(1)

    token_match = _GH_EMBED_TOKEN_RE.search(html)
    # Authoritative when present; otherwise guess-and-verify a board slug
    # from the domain the same way _detect_embedded does, except here
    # "verify" and "fetch" are the same request — a 200 on the job
    # endpoint itself confirms the slug.
    candidates = [token_match.group(1)] if token_match else candidate_slugs_from_domain(urlsplit(url).netloc)
    for slug in candidates:
        try:
            response = httpx.get(
                _JOB_API_URL.format(board_key=slug, job_id=job_id),
                params={"content": "true"},
                timeout=10.0,
            )
        except httpx.HTTPError:
            continue
        if response.status_code == 200:
            return response.json()
    return None


# WorkplaceType isn't a top-level field on Greenhouse's job record — when
# set at all, it's tucked into the free-form `metadata` list as a
# single_select custom field most companies label exactly "Workplace Type"
# (verified against Airbnb's live posting), value one of Greenhouse's own
# fixed options.
_WORKPLACE_TYPE_MAP = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "on-site": WorkplaceType.ONSITE,
    "onsite": WorkplaceType.ONSITE,
}


def _embedded_location_of(job_data: dict[str, Any]) -> str | None:
    location = job_data.get("location")
    return location.get("name") if isinstance(location, dict) else None


def _embedded_workplace_type_of(job_data: dict[str, Any]) -> str:
    for entry in job_data.get("metadata") or []:
        if not isinstance(entry, dict) or str(entry.get("name", "")).strip().lower() != "workplace type":
            continue
        mapped = _WORKPLACE_TYPE_MAP.get(str(entry.get("value", "")).strip().lower())
        if mapped is not None:
            return mapped
    return WorkplaceType.UNKNOWN


def _embedded_posted_at_of(job_data: dict[str, Any]) -> date | None:
    raw = job_data.get("first_published") or job_data.get("updated_at")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _embedded_description_of(job_data: dict[str, Any]) -> str | None:
    content = job_data.get("content")
    # The API JSON-encodes the description as HTML-entity-escaped markup
    # ("&lt;div&gt;...") rather than raw HTML, same double-escaping
    # app.services.adapters.text.clean_text's docstring notes for
    # Mastercard's JSON-LD — unescape before handing it to the
    # HTML-to-Markdown converter, or every tag survives as literal text
    # instead of structure.
    return html_to_formatted_text(unescape(content)) if isinstance(content, str) else None


# job-boards.greenhouse.io (Greenhouse's newer job-board template, used by
# many companies) server-renders the full posting body into a
# <div class="job__description body"> — a stable, semantic template class,
# not a minified/hashed one — but ships no JSON-LD and only a short
# location string (e.g. "Arizona | Remote") in og:description, so without
# this the "description" ends up being just that location string.
_DESCRIPTION_MARKER = 'class="job__description'


def _job_board_description_of(html: str) -> str | None:
    marker = html.find(_DESCRIPTION_MARKER)
    if marker == -1:
        return None
    div_start = html.rfind("<div", 0, marker)
    if div_start == -1:
        return None
    inner = extract_balanced_div(html, div_start)
    return html_to_formatted_text(inner) if inner else None


# The same job-boards.greenhouse.io template also server-renders its full
# loader payload into a `window.__remixContext = {...}` JSON blob, which is
# the only place on the page carrying the company name and an unambiguous
# location string — og:description is just "Palo Alto, CA" with no marker
# distinguishing it from arbitrary marketing copy, so job_scanner.py's
# general-purpose "Location | WorkplaceType" heuristic can't safely use it.
# Matching the two fields directly out of that blob is far cheaper than
# parsing the whole (multi-hundred-KB) JSON object.
_COMPANY_NAME_RE = re.compile(r'"company_name":"((?:[^"\\]|\\.)*)"')
_JOB_LOCATION_RE = re.compile(r'"job_post_location":"((?:[^"\\]|\\.)*)"')
# ISO-8601 with an explicit offset, e.g. "2026-05-05T17:45:16-04:00" — no
# escaping to worry about (unlike the two string fields above), so this one
# doesn't need to go through _remix_field's JSON-unescape.
_PUBLISHED_AT_RE = re.compile(r'"published_at":"(\d{4}-\d{2}-\d{2})')


def _remix_field(html: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(html)
    if match is None:
        return None
    try:
        # The matched text is a JSON string body (escapes and all); wrapping
        # it back in quotes and decoding gives proper unescaping for free.
        return json.loads(f'"{match.group(1)}"') or None
    except json.JSONDecodeError:
        return None


def extract(url: str, html: str) -> ExtractedJobFields | None:
    embedded = _fetch_embedded_job_data(url, html)
    if embedded is not None:
        title = embedded.get("title")
        return ExtractedJobFields(
            title=title if isinstance(title, str) else None,
            description=_embedded_description_of(embedded),
            company_name=embedded.get("company_name"),
            location=_embedded_location_of(embedded),
            workplace_type=_embedded_workplace_type_of(embedded),
            posted_at=_embedded_posted_at_of(embedded),
        )

    company_name = _remix_field(html, _COMPANY_NAME_RE)
    location = _remix_field(html, _JOB_LOCATION_RE)
    description = _job_board_description_of(html)
    published_at_match = _PUBLISHED_AT_RE.search(html)
    posted_at = None
    if published_at_match is not None:
        try:
            posted_at = date.fromisoformat(published_at_match.group(1))
        except ValueError:
            posted_at = None
    if company_name is None and location is None and description is None and posted_at is None:
        return None
    return ExtractedJobFields(
        description=description,
        company_name=company_name,
        location=location,
        posted_at=posted_at,
    )


def scan_job_url(url: str) -> ScanResult | None:
    # Cheap, URL-only gate: a real boards.greenhouse.io/job-boards.
    # greenhouse.io link, or a gh_jid already visible in the query string
    # (the common embedded-widget shape — see extract()'s own gh_jid
    # search). A widget whose gh_jid only ever appears inside the fetched
    # page body (no URL signal at all) falls through to the generic default
    # scanner instead of paying for a speculative fetch on every unrelated
    # URL scanned.
    if _match(url) is None and _GH_EMBED_JOB_ID_RE.search(url) is None:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    final_url, html = page.url, page.text
    if is_error_redirect(final_url):
        return ScanResult(
            success=False,
            error="Greenhouse redirected to the board's error page — this posting has likely been removed or filled.",
        )

    fields = extract(final_url, html)
    description = (
        (fields.description if fields else None) or base.fallback_description(html) or base.og_description(html)
    )
    og_description_raw = base.og_description_raw(html)
    og_location, og_workplace_type = (
        base.parse_og_description(og_description_raw) if og_description_raw else (None, WorkplaceType.UNKNOWN)
    )
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=(fields.title if fields else None) or base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name=(fields.company_name if fields else None) or base.og_site_name(html),
        location=(fields.location if fields else None) or og_location,
        workplace_type=(
            fields.workplace_type if fields and fields.workplace_type != WorkplaceType.UNKNOWN else og_workplace_type
        ),
        employment_type=EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=fields.posted_at if fields else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.GREENHOUSE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://boards.greenhouse.io/{key}",
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
