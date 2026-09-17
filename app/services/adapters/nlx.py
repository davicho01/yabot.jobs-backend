import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_NLX_API = "https://prod-search-api.jobsyn.org/api/v1/solr/search"
# Present in every NLX-templated career page's server-rendered HTML (favicon/
# og:image point at seo.nlx.org) even on a plain fetch — verified against both
# a listing page and a job detail page live, no JS execution needed.
_NLX_SIGNATURE = "nlx.org"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")


def _job_url(host: str, job: dict) -> str | None:
    # No "url" field in the API response at all — verified live. The site's
    # own job-detail links are {location-slug}/{title_slug}/{guid}/job/,
    # where location-slug is just location_exact slugified (matches both a
    # "City, ST" shape and placeholders like "Virtual, USA" — verified
    # against live listing-page hrefs for several jobs of each shape).
    guid = job.get("guid")
    title_slug = job.get("title_slug")
    location = job.get("location_exact")
    if not (guid and title_slug and location):
        return None
    return f"https://{host}/{_slugify(location)}/{title_slug}/{guid}/job/"


def _fetch_jobs(host: str) -> list[str]:
    # NLX (National Labor Exchange, seo.nlx.org/jobsyn.org) is a career-site
    # SEO layer white-labeled onto the company's own domain — every tenant
    # looks like any other company's careers page without fetching it first,
    # same reasoning as clinch.py/eightfold.py. Its job search API is one
    # shared multi-tenant host with no board-key query param at all: the
    # tenant is selected purely by the X-Origin header matching the
    # requesting company's own hostname (verified live). Page size is fixed
    # server-side at 15 regardless of any num_items requested (verified
    # live), so pagination just walks `page` until the response says there's
    # no more.
    urls: list[str] = []
    page = 1
    while len(urls) < DEFAULT_MAX_JOBS_PER_CRAWL:
        response = get_with_retry(
            _NLX_API,
            params={"page": page},
            headers={"X-Origin": host, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        urls.extend(url for job in data.get("jobs", []) if (url := _job_url(host, job)))
        if not data.get("pagination", {}).get("has_more_pages"):
            break
        page += 1
    return urls[:DEFAULT_MAX_JOBS_PER_CRAWL]


def _detect_embedded(url: str) -> str | None:
    host = urlsplit(url).netloc
    if not host:
        return None
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return host if _NLX_SIGNATURE in response.text else None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# NLX has no static URL shape (match=None) — the company's own domain *is*
# the board, only distinguishable via _detect_embedded's signature check,
# same as Clinch/Eightfold. board_key for listing is just the host, trivially
# recoverable from any stored board_url. No to_board_url — the board lives on
# the company's own domain, not a shared NLX-hosted host, so board_url is
# stored verbatim (same reasoning as Oracle Fusion/Clinch).
ADAPTER = AtsAdapter(
    AtsType.NLX,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
