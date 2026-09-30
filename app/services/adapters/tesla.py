import json
import re
from html import unescape
from typing import Any

from app.models.enums import AtsType, EmploymentType
from app.services.adapters.base import AtsAdapter, ScanResult, limit_job_urls, salary_from_text
from app.services.adapters.text import clean_text, html_to_formatted_text
from app.services.browser_fetch import fetch_rendered_page

# Every path on tesla.com — including these JSON endpoints, not just the
# rendered search page — sits behind Akamai Bot Manager, which blocks a
# plain httpx request outright (verified live: 403 with no challenge page
# to solve, unlike Cloudflare's JS-challenge tenants elsewhere in this
# codebase) but passes a real headless-browser navigation straight through.
# Both discovery and scanning go through fetch_rendered_page for that
# reason — there is no plain-httpx tier to try first, unlike every adapter
# whose browser fallback is only occasionally needed.
_TESLA_URL_RE = re.compile(r"tesla\.com/careers", re.IGNORECASE)
_STATE_URL = "https://www.tesla.com/cua-api/apps/careers/state"
_JOB_API_URL = "https://www.tesla.com/cua-api/careers/job/{job_id}"
_JOB_URL = "https://www.tesla.com/careers/search/job/{job_id}"
# The slug prefix (from the listing's own title) is cosmetic — verified
# live: .../careers/search/job/224501 (bare id, no slug) resolves to the
# exact same posting as the slugged URL a real search-result link uses.
_JOB_ID_RE = re.compile(r"/careers/search/job/[a-z0-9-]*?(\d+)/?(?:\?.*)?$", re.IGNORECASE)
_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL)

_EMPLOYMENT_TYPE_MAP = {
    "full-time": EmploymentType.FULL_TIME,
    "part-time": EmploymentType.PART_TIME,
    "intern": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
}


def _match(url: str) -> str | None:
    return "tesla" if _TESLA_URL_RE.search(url) else None


def _extract_json(rendered_html: str) -> dict[str, Any] | None:
    # Chromium wraps a JSON response's body in a bare <pre> (HTML-entity-
    # escaped) when navigating straight to it — verified live via
    # document.body.outerHTML on both endpoints below.
    match = _PRE_RE.search(rendered_html)
    text = unescape(match.group(1)) if match else rendered_html
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _fetch_jobs(_board_key: str) -> list[str]:
    # One unauthenticated call returns every open role worldwide (~8,300
    # listings, verified live) as a compact {id, t: title, ...} blob — no
    # pagination, no per-job fetch needed for discovery.
    rendered = fetch_rendered_page(_STATE_URL)
    if rendered is None:
        return []
    data = _extract_json(rendered.html)
    if data is None:
        return []
    listings = data.get("listings")
    if not isinstance(listings, list):
        return []
    return limit_job_urls(
        _JOB_URL.format(job_id=item["id"]) for item in listings if isinstance(item, dict) and item.get("id")
    )


def _description_of(data: dict[str, Any]) -> str | None:
    sections = [data.get("jobDescription")]
    if data.get("jobResponsibilities"):
        sections.append("<h3>Responsibilities</h3>" + data["jobResponsibilities"])
    if data.get("jobRequirements"):
        sections.append("<h3>Requirements</h3>" + data["jobRequirements"])
    if data.get("jobCompensationAndBenefits"):
        sections.append(data["jobCompensationAndBenefits"])
    html = "".join(s for s in sections if isinstance(s, str) and s.strip())
    return html_to_formatted_text(html) if html else None


def _employment_type_of(data: dict[str, Any]) -> str:
    time_type = data.get("timeType")
    return _EMPLOYMENT_TYPE_MAP.get(time_type.strip().lower(), EmploymentType.UNKNOWN) if isinstance(
        time_type, str
    ) else EmploymentType.UNKNOWN


def scan_job_url(url: str) -> ScanResult | None:
    match = _JOB_ID_RE.search(url)
    if match is None:
        return None
    job_id = match.group(1)

    rendered = fetch_rendered_page(_JOB_API_URL.format(job_id=job_id))
    if rendered is None:
        return ScanResult(success=False, error="Browser-render fallback failed fetching Tesla's job API.")
    data = _extract_json(rendered.html)
    if data is None:
        return ScanResult(
            success=False, error=f"Couldn't parse Tesla's job API response. DEBUG: {rendered.html[:500]!r}"
        )

    description = _description_of(data)
    salary_min, salary_max, salary_currency = salary_from_text(description)
    return ScanResult(
        success=True,
        title=clean_text(data.get("title")),
        description=description,
        company_name="Tesla",
        location=clean_text(data.get("location")),
        employment_type=_employment_type_of(data),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=rendered.html[:20_000],
        full_html=rendered.html,
    )


ADAPTER = AtsAdapter(
    AtsType.TESLA,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://www.tesla.com/careers/search/",
    scan_job_url=scan_job_url,
)
