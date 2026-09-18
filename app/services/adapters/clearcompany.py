import re

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text, extract_balanced_div, html_to_formatted_text

# ClearCompany's white-label career sites all run on a shared *.hrmdirect.com
# domain per tenant (e.g. adhoc.hrmdirect.com) — a leftover from "HRM
# Direct," the product's name before ClearCompany acquired it; the page
# footer still reads "Applicant Tracking System Powered by ClearCompany."
_CLEARCOMPANY_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.hrmdirect\.com", re.IGNORECASE)
_JOB_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.hrmdirect\.com/employment/job-opening\.php\?req=(\d+)", re.IGNORECASE)
_LISTING_JOB_ID_RE = re.compile(r"job-opening\.php\?req=(\d+)", re.IGNORECASE)

# Verified live against several tenants (adhoc, pmic, shareourstrength): the
# plain listing page renders a "not hiring" placeholder even when postings
# exist — the actual results only render with this query param set.
_LISTING_URL = "https://{board_key}.hrmdirect.com/employment/job-openings.php?search=true"
_JOB_URL = "https://{board_key}.hrmdirect.com/employment/job-opening.php?req={job_id}#job"

_TITLE_RE = re.compile(r"jobViewHeader['\"]></div>.*?<h2>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
# The footer copyright line is the one company-name signal verified present
# and consistently formatted on every tenant sampled — unlike the <title>
# tag's suffix, which some tenants customize away from the default "Careers
# At {company}" template (e.g. pmic.hrmdirect.com uses "Career
# Opportunities" instead).
_COPYRIGHT_RE = re.compile(r'footer-copyright"[^>]*>\s*&copy;\s*\d{4}\s*([^<]+?)\s*</p>', re.IGNORECASE)
_VIEW_FIELD_RE = re.compile(
    r'<td class="viewFieldName"[^>]*>\s*(?:<b>)?(.*?)(?:</b>)?\s*:?\s*</td>\s*'
    r'<td class="viewFieldValue"[^>]*>(.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)


def _match(url: str) -> str | None:
    match = _CLEARCOMPANY_URL_RE.search(url)
    return match.group(1) if match else None


def _decode(response: httpx.Response) -> str:
    # None of the tenants sampled ever declare a charset (no Content-Type
    # header param, no <meta charset>), so httpx's default guess falls back
    # to UTF-8 — but the actual bytes are Windows-1252 (verified: curly
    # quotes/apostrophes in job descriptions decode as U+FFFD replacement
    # characters under UTF-8, correctly as "'"/"'" under cp1252). A tenant
    # that *did* declare its own charset would be respected here instead.
    if response.charset_encoding:
        return response.text
    return response.content.decode("cp1252", errors="replace")


def _fetch_jobs(board_key: str) -> list[str]:
    response = get_with_retry(_LISTING_URL.format(board_key=board_key), timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    html = _decode(response)
    job_ids = dict.fromkeys(_LISTING_JOB_ID_RE.findall(html))  # dedupe, keep order
    return limit_job_urls(_JOB_URL.format(board_key=board_key, job_id=job_id) for job_id in job_ids)


def _view_fields(html: str) -> dict[str, str]:
    fields = {}
    for name, value in _VIEW_FIELD_RE.findall(html):
        cleaned_value = clean_text(value)
        if cleaned_value:
            fields[clean_text(name).rstrip(":").strip().lower()] = cleaned_value
    return fields


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
        return None

    try:
        response = get_with_retry(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = _decode(response)

    title_match = _TITLE_RE.search(html)
    title = clean_text(title_match.group(1)) if title_match else None

    copyright_match = _COPYRIGHT_RE.search(html)
    company_name = clean_text(copyright_match.group(1)) if copyright_match else None

    fields = _view_fields(html)
    location = fields.get("location")

    desc_start = html.find('class="jobDesc"')
    description = None
    if desc_start != -1:
        div_start = html.rfind("<div", 0, desc_start)
        inner_html = extract_balanced_div(html, div_start) if div_start != -1 else None
        description = html_to_formatted_text(inner_html) if inner_html else None
    if description is None:
        description = base.fallback_description(html)

    salary_min, salary_max, salary_currency = base.salary_from_text(description)

    return ScanResult(
        success=True,
        title=title or base.fallback_title(html),
        description=description,
        company_name=company_name,
        location=location,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        extracted_fields={k: v for k, v in fields.items() if k != "location"} or None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.CLEARCOMPANY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.hrmdirect.com/employment/job-openings.php",
    scan_job_url=scan_job_url,
)
