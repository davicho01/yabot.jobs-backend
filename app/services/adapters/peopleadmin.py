import re

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, max_jobs_per_crawl
from app.services.adapters.text import clean_text, html_to_formatted_text

_PEOPLEADMIN_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.peopleadmin\.com)", re.IGNORECASE)
_JOB_HREF_RE = re.compile(r'href="/postings/(\d+)"')


def _match(url: str) -> str | None:
    match = _PEOPLEADMIN_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(host: str) -> list[str]:
    # Page size is tenant-configured (60 at San Jose Evergreen CCD, 30 at
    # utah/weberstate-sb), so a short page is judged against page 1's size,
    # not a fixed constant — a fixed 60 silently stopped 30-per-page tenants
    # after page 1.
    seen: dict[str, None] = {}
    page_size = None
    page = 1
    while len(seen) < max_jobs_per_crawl():
        response = get_with_retry(f"https://{host}/postings/search", params={"page": page}, timeout=TIMEOUT)
        response.raise_for_status()
        ids = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
        if not ids and page == 1:
            base.require_listing_page(response.text, "peopleadmin", f"PeopleAdmin board {host!r}")
        new_ids = [job_id for job_id in ids if job_id not in seen]
        if not new_ids:
            break
        seen.update(dict.fromkeys(new_ids))
        page_size = page_size or len(ids)
        if len(ids) < page_size:
            break
        page += 1
    return [f"https://{host}/postings/{job_id}" for job_id in seen][:max_jobs_per_crawl()]


def _board_key(url: str) -> str | None:
    match = _PEOPLEADMIN_URL_RE.search(url)
    return match.group(1) if match else None


# A posting page has no JSON-LD and no useful meta description (a generic
# scan stored nothing, or one short field — verified 2026-10-12 on Utah, ECU,
# Winthrop and Sam Houston). The posting is a server-rendered <th>/<td>
# table whose labels each institution names itself ("Details", "Primary
# Purpose of Position", "Duties and Responsibilities", ...), so the
# description is every substantial field in page order under its own label,
# minus the equal-opportunity boilerplate every posting repeats.
_POSTING_URL_RE = re.compile(r"[a-zA-Z0-9-]+\.peopleadmin\.com/postings/\d+", re.IGNORECASE)
_ROW_RE = re.compile(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_MIN_FIELD_CHARS = 60
_BOILERPLATE_LABEL_RE = re.compile(r"(?i)\b(eeo|non-?discrimination|equal (employment )?opportunity)\b")
_LOCATION_LABEL_RE = re.compile(r"(?i)\b(city|location|campus)\b")
_POSTED_LABEL_RE = re.compile(r"(?i)\b(open|posting|posted) date\b")


def _rows(html: str) -> list[tuple[str, str]]:
    return [(clean_text(label) or "", raw) for label, raw in _ROW_RE.findall(html)]


def _description_of(rows: list[tuple[str, str]]) -> str | None:
    parts = []
    for label, raw in rows:
        if _BOILERPLATE_LABEL_RE.search(label) or len(clean_text(raw) or "") < _MIN_FIELD_CHARS:
            continue
        parts.append(f"<h3>{label}</h3>{raw}" if label else raw)
    return html_to_formatted_text("".join(parts))


def _field(rows: list[tuple[str, str]], label_re: re.Pattern[str]) -> str | None:
    return next((clean_text(raw) for label, raw in rows if label_re.search(label) and clean_text(raw)), None)


def scan_job_url(url: str) -> ScanResult | None:
    if not _POSTING_URL_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    html = page.text
    rows = _rows(html)
    description = _description_of(rows) or base.fallback_description(html)
    title_match = _TITLE_RE.search(html)
    posted = _field(rows, _POSTED_LABEL_RE)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=(clean_text(title_match.group(1)) if title_match else None) or base.fallback_title(html),
        description=description,
        location=_field(rows, _LOCATION_LABEL_RE),
        employment_type=EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.parse_month_day_year(posted, month_style="%m/%d/%Y"),
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No to_board_url — {tenant}.peopleadmin.com is already the canonical
# shared host, board_url is stored verbatim.
ADAPTER = AtsAdapter(
    AtsType.PEOPLEADMIN,
    match=_match,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    scan_job_url=scan_job_url,
)
