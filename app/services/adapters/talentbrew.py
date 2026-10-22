import re
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, max_jobs_per_crawl
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

_TALENTBREW_SIGNATURE = "tbcdn.talentbrew.com"
_TALENTBREW_RECORDS_PER_PAGE = 15
# Some tenants' job hrefs carry a locale prefix (Walgreens: "/en/job/..."),
# others don't (Ford: "/job/..." bare) — verified live across five tenants,
# accept either.
_TALENTBREW_JOB_HREF_RE = re.compile(r'href="(/(?:[a-z]{2}/)?job/[^"]+)"')
_TALENTBREW_JOB_PATH_RE = re.compile(r"^/(?:[a-z]{2}/)?job/")
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

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
    # robots.txt on 8 of the 9 tenants crawled as of 2026-10-07 (Cargill,
    # CHS, Ford, Intuit, Mayo Clinic, Spectrum, Tenet, UnitedHealth) says
    # "Disallow: /search-jobs/" — the search-results endpoint below — while
    # the sitemap.xml they all publish lists every job page (with a
    # <lastmod>) and is allowed. So the sitemap is the listing; the search
    # endpoint is only a fallback, and only where robots.txt permits it.
    urls = _sitemap_job_urls(host)
    if urls or not _search_allowed(host):
        return urls[:max_jobs_per_crawl()]
    return _search_job_urls(host)


def _sitemap_job_urls(host: str) -> list[str]:
    # Cargill/Walgreens 301 the bare path to a locale-prefixed one
    # (/en/sitemap.xml), same as their search pages.
    response = get_with_retry(f"https://{host}/sitemap.xml", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    jobs = [
        (entry.findtext("sm:loc", "", _SITEMAP_NS), entry.findtext("sm:lastmod", "", _SITEMAP_NS))
        for entry in ElementTree.fromstring(response.content).findall("sm:url", _SITEMAP_NS)
    ]
    jobs = [(loc, lastmod) for loc, lastmod in jobs if _TALENTBREW_JOB_PATH_RE.match(urlsplit(loc).path)]
    jobs.sort(key=lambda job: job[1], reverse=True)
    return list(dict.fromkeys(loc for loc, _ in jobs))


def _search_allowed(host: str) -> bool:
    try:
        response = httpx.get(f"https://{host}/robots.txt", timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
        return False
    if response.status_code >= 400:
        return True  # no robots.txt: nothing disallowed
    robots = RobotFileParser()
    robots.parse(response.text.splitlines())
    return robots.can_fetch("*", f"https://{host}/search-jobs/results")


def _search_job_urls(host: str) -> list[str]:
    urls: list[str] = []
    page = 1
    while len(urls) < max_jobs_per_crawl():
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
    return urls[:max_jobs_per_crawl()]


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
# A TalentBrew job page lives on the company's own domain at
# /job/{city}/{slug}/{org id}/{job id}. Its JSON-LD is often malformed
# (verified 2026-10-12: Mayo Clinic's fails to parse partway through), so
# the generic scanner fell back to the meta teaser ("Learn more about
# applying for ... at Mayo Clinic"). The posting itself is server-rendered
# into <div class="ats-description">; title and location are the <h1> and
# the .job-location element, and the date/organization are still readable
# as individual JSON-LD fields even when the block as a whole isn't valid.
_JOB_PATH_RE = re.compile(r"/job/[^/?]+/[^/?]+/\d+/\d+")
_DESCRIPTION_MARKER = 'class="ats-description'
# The JSON-LD "title" field is the job title on every tenant checked, while
# the <h1> is a site banner on some ("Explore roles where you can make an
# impact" at NetApp) — so the h1 is only the fallback.
_LD_TITLE_RE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.){1,200})"')
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_LOCATION_RE = re.compile(r'class="job-location[^"]*"[^>]*>(.*?)<', re.IGNORECASE | re.DOTALL)
_DATE_POSTED_RE = re.compile(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})')
_ORGANIZATION_RE = re.compile(r'"hiringOrganization".{0,200}?"name"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_PATH_RE.search(urlsplit(url).path):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text
    blocks = []
    for marker in re.finditer(re.escape(_DESCRIPTION_MARKER), html):
        div_start = html.rfind("<div", 0, marker.start())
        inner = extract_balanced_div(html, div_start) if div_start != -1 else None
        if inner:
            blocks.append(inner)
    if not blocks:
        return None  # not a TalentBrew page after all: the generic scanner takes it
    # Some templates have a short metadata block with the same class ahead
    # of the posting (UnitedHealth Group's: requisition number, category).
    description = html_to_formatted_text(max(blocks, key=len))
    title = _LD_TITLE_RE.search(html) or _H1_RE.search(html)
    location = _LOCATION_RE.search(html)
    posted = _DATE_POSTED_RE.search(html)
    organization = _ORGANIZATION_RE.search(html)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=(clean_text(title.group(1)) if title else None) or base.fallback_title(html),
        description=description,
        company_name=clean_text(organization.group(1)) if organization else None,
        location=clean_text(location.group(1)) if location else None,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.posting_date(posted.group(1)) if posted else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.TALENTBREW,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)

