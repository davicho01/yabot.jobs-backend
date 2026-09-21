import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_TALENTBREW_SIGNATURE = "tbcdn.talentbrew.com"
_TALENTBREW_RECORDS_PER_PAGE = 15
_TALENTBREW_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
# Some tenants' job hrefs carry a locale prefix (Walgreens: "/en/job/..."),
# others don't (Ford: "/job/..." bare) — verified live across five tenants,
# accept either.
_TALENTBREW_JOB_HREF_RE = re.compile(r'href="(/(?:[a-z]{2}/)?job/[^"]+)"')

# The pagination AJAX endpoint (data-ajax-url on the #search-results
# section) 200s and reports hasJobs=true for almost any query, but silently
# returns an empty "results" string unless every one of these companion
# params is present too — copied verbatim from a real browser's network
# request (captured via read_network_requests while clicking "Next"), not
# reverse-engineered from the minified JS, which turned out to disagree
# with what the server actually expects. No cookie/session needed despite
# looking session-bound at a glance — a prior investigation of this exact
# board concluded pagination required a session-bound POST; that was wrong,
# this is a stateless GET.
#
# SearchResultsModuleName/SearchFiltersModuleName must match the *current*
# module names ("Search Results"/"Search Filters", verified live across all
# five tenants below) — some earlier snapshot of this page apparently used
# "Section 6 - Search Results List"/"Section 6 - Search Filters" instead,
# which still 200s with hasJobs=true but silently empty results, exactly
# the failure mode the paragraph above warns about. Some tenants also
# 301-redirect the bare path to a locale-prefixed one (Cargill and
# Walgreens both do this now, Ford/Mayo/UnitedHealth don't) —
# follow_redirects handles both without needing to detect each tenant's
# locale up front.
_TALENTBREW_RESULTS_PARAMS = {
    "ActiveFacetID": "0",
    "RecordsPerPage": str(_TALENTBREW_RECORDS_PER_PAGE),
    "TotalContentResults": "",
    "Distance": "50",
    "RadiusUnitType": "0",
    "Keywords": "",
    "Location": "",
    "ShowRadius": "False",
    "IsPagination": "False",
    "CustomFacetName": "",
    "FacetTerm": "",
    "FacetType": "0",
    "SearchResultsModuleName": "Search Results",
    "SearchFiltersModuleName": "Search Filters",
    "SortCriteria": "0",
    "SortDirection": "0",
    "SearchType": "5",
    "PostalCode": "",
    "ResultsType": "0",
    "fc": "",
    "fl": "",
    "fcf": "",
    "afc": "",
    "afl": "",
    "afcf": "",
    "TotalContentPages": "NaN",
}


def _fetch_jobs(host: str) -> list[str]:
    urls: list[str] = []
    page = 1
    while len(urls) < _TALENTBREW_MAX_JOBS:
        response = get_with_retry(
            f"https://{host}/search-jobs/results",
            params={**_TALENTBREW_RESULTS_PARAMS, "CurrentPage": str(page)},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        hrefs = _TALENTBREW_JOB_HREF_RE.findall(response.json().get("results") or "")
        urls.extend(f"https://{host}{href}" for href in hrefs)
        if len(hrefs) < _TALENTBREW_RECORDS_PER_PAGE:
            break
        page += 1
    return urls[:_TALENTBREW_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _TALENTBREW_SIGNATURE not in response.text:
        return None
    return urlsplit(str(response.url)).netloc


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# TalentBrew is white-labeled onto each customer's own domain
# (careers.ford.com, ...) with no shared host to statically match — same
# embedded-only shape as Clinch/Oracle Fusion's white-label tier. board_url
# is stored verbatim rather than reconstructed; see AtsAdapter's docstring.
ADAPTER = AtsAdapter(
    AtsType.TALENTBREW,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)

