import re
from datetime import date, datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ScanResult,
    get_with_retry,
)
from app.services.adapters.text import html_to_formatted_text
from app.services.browser_fetch import fetch_rendered_html

# Best Buy's careers site (careers.bestbuy.com -> jobs.bestbuy.com) runs on
# ServiceNow's Service Portal (Angular app, ng-app="sn.$sp") rather than any
# ATS platform — verified live via the og:image host (bestbuy.service-now.com)
# and the page's own ng-app attribute.
_BBY_URL_RE = re.compile(r"(?:careers|jobs)\.bestbuy\.com", re.IGNORECASE)
_BBY_SITEMAP_URL = "https://bestbuy.service-now.com/api/93622/seo/sitemap"
_BBY_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_BBY_WIDGET_CLASS = "bby-career-job-details"
_BBY_DATE_RE = re.compile(r"\b(\d{2}-[A-Za-z]{3}-\d{4})\b")
_BBY_JOB_DESCRIPTION_HEADING_RE = re.compile(r"^#{0,6}\s*Job Description\s*$", re.MULTILINE)
_BBY_OVERVIEW_HEADING_RE = re.compile(r"^#{0,6}\s*Overview\s*$", re.MULTILINE)


def _match(url: str) -> str | None:
    return "bestbuy" if _BBY_URL_RE.search(url) else None


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    # robots.txt (careers.bestbuy.com/robots.txt) is fully permissive and
    # itself points at this sitemap. Unlike Walmart's sitemap, every job
    # <url> here carries a real <lastmod> — sorting by that and taking the
    # most-recently-updated _BBY_MAX_JOBS keeps every crawl focused on
    # what's actually new/changed, no date-seeded sampling needed.
    response = get_with_retry(_BBY_SITEMAP_URL, timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries: list[tuple[str, str]] = []
    for url_el in root.findall("sm:url", ns):
        loc = url_el.find("sm:loc", ns)
        lastmod = url_el.find("sm:lastmod", ns)
        if loc is None or not loc.text or "id=job_details" not in loc.text:
            continue
        entries.append((loc.text, lastmod.text if lastmod is not None and lastmod.text else ""))
    entries.sort(key=lambda e: e[1], reverse=True)
    return [url for url, _ in entries[:_BBY_MAX_JOBS]]


_BBY_EMPLOYMENT_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("seasonal", EmploymentType.TEMPORARY),
    ("temporary", EmploymentType.TEMPORARY),
    ("intern", EmploymentType.INTERNSHIP),
    ("full time", EmploymentType.FULL_TIME),
    ("part time", EmploymentType.PART_TIME),
]


def _employment_type_of(raw: str) -> str:
    lowered = raw.lower()
    for keyword, employment_type in _BBY_EMPLOYMENT_TYPE_KEYWORDS:
        if keyword in lowered:
            return employment_type
    return EmploymentType.UNKNOWN


def _posted_at_of(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%d-%b-%Y").date()
    except ValueError:
        return None


# The job-details widget's data only ever arrives client-side over
# ServiceNow's AMB pub/sub bus (verified live: a plain fetch's initial HTML
# has no job content at all, only per-req_id <title>/og: meta tags) — no
# plain REST call to reuse, so getting the real body means rendering the
# page. The widget itself has no schema.org JSON-LD, just a deeply nested,
# per-deployment-hashed class soup, so rather than depend on fragile CSS
# selectors this converts the whole rendered widget through the same
# html_to_formatted_text used for every other adapter's HTML descriptions
# and then regexes out the handful of fields that follow a consistent,
# verified-live line order: title, [boilerplate], DD-Mon-YYYY date, city/
# state location, "Overview" heading, a free-text employment-type line,
# then a "Job Description" heading followed by the actual posting body.
def _extract_from_rendered(html: str) -> tuple[str | None, str | None, str | None, date | None]:
    idx = html.find(_BBY_WIDGET_CLASS)
    if idx == -1:
        return None, None, None, None
    end = html.find("sp-widget", idx)
    widget_html = html[idx : end if end != -1 else idx + 20_000]
    text = html_to_formatted_text(widget_html)
    if not text:
        return None, None, None, None

    lines = text.split("\n")
    posted_at: date | None = None
    location: str | None = None
    date_match = _BBY_DATE_RE.search(text)
    if date_match:
        posted_at = _posted_at_of(date_match.group(1))
        date_line_idx = next((i for i, line in enumerate(lines) if date_match.group(1) in line), None)
        if date_line_idx is not None:
            for line in lines[date_line_idx + 1 :]:
                if line.strip():
                    location = line.strip()
                    break

    employment_type_raw: str | None = None
    overview_match = _BBY_OVERVIEW_HEADING_RE.search(text)
    if overview_match:
        after = text[overview_match.end() :].split("\n")
        for line in after:
            if line.strip():
                employment_type_raw = line.strip()
                break

    description: str | None = None
    desc_match = _BBY_JOB_DESCRIPTION_HEADING_RE.search(text)
    if desc_match:
        description = text[desc_match.end() :].strip() or None

    return description, employment_type_raw, location, posted_at


def scan_job_url(url: str) -> ScanResult | None:
    if not _BBY_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    title = base.og_title(html) or base.fallback_title(html)
    description = base.og_description(html) or base.fallback_description(html)
    location: str | None = None
    employment_type = EmploymentType.UNKNOWN
    posted_at: date | None = None

    rendered = fetch_rendered_html(url, wait_for_selector=f".{_BBY_WIDGET_CLASS}")
    if rendered is not None:
        rendered_description, employment_type_raw, rendered_location, rendered_posted_at = _extract_from_rendered(
            rendered
        )
        description = rendered_description or description
        location = rendered_location
        posted_at = rendered_posted_at
        if employment_type_raw:
            employment_type = _employment_type_of(employment_type_raw)

    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=title,
        description=description,
        company_name="Best Buy",
        location=location,
        employment_type=employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=posted_at,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.BESTBUY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://careers.bestbuy.com",
    scan_job_url=scan_job_url,
)
