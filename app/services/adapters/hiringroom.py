import re

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    DEFAULT_MAX_JOBS_PER_CRAWL,
    TIMEOUT,
    AtsAdapter,
    ExtractedJobFields,
    ScanResult,
    limit_job_urls,
    post_with_retry,
)
from app.services.adapters.text import extract_balanced_div, html_to_formatted_text

# HiringRoom — a Latin American multi-tenant ATS at {tenant}.hiringroom.com.
# The bare tenant host (and even its own /jobs page over a plain GET) only
# ever shows the recruiter login/first page of results; the real listing —
# and its pagination — is server-rendered HTML fetched via a same-origin
# POST, verified live against bimbo/grupombo's tenants.
_HIRINGROOM_HOST_RE = re.compile(r"([a-z0-9-]+)\.hiringroom\.com", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"/jobs/get_vacancy/([a-f0-9]{24})", re.IGNORECASE)
_VACANCIES_URL = "https://{tenant}.hiringroom.com/jobs/getVacanciesForPortal/{page}"
_JOB_URL = "https://{tenant}.hiringroom.com/jobs/get_vacancy/{job_id}"
_HIRINGROOM_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL


def _match(url: str) -> str | None:
    match = _HIRINGROOM_HOST_RE.search(url)
    return match.group(1) if match else None


def _board_url(tenant: str) -> str:
    return f"https://{tenant}.hiringroom.com/jobs"


def _fetch_jobs(tenant: str) -> list[str]:
    ids: list[str] = []
    page = 1
    total = None
    while len(ids) < _HIRINGROOM_MAX_JOBS and (total is None or len(ids) < total):
        response = post_with_retry(
            _VACANCIES_URL.format(tenant=tenant, page=page),
            data={"typePortal": "external"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("result") != "success":
            break
        data = body.get("data", {})
        total = data.get("total_vacancies", 0)
        batch = dict.fromkeys(_JOB_ID_RE.findall(data.get("htmlContent", "")))
        if not batch:
            break
        for job_id in batch:
            if job_id not in ids:
                ids.append(job_id)
        page += 1
    return limit_job_urls(_JOB_URL.format(tenant=tenant, job_id=job_id) for job_id in ids)


_LOCATION_TITLE_RE = re.compile(r'<p[^>]*\btitle="([^"]+)"[^>]*>\s*<span class="hr-Location-pin"', re.IGNORECASE)
_TITLE_RE = re.compile(r'hero__title">\s*<h2[^>]*>\s*([^<]+?)\s*(?:<span|</h2)', re.IGNORECASE | re.DOTALL)
# og:site_name is always the literal "Hiring Room" (the ATS vendor, not the
# tenant) — the real company name only shows up in the page <title>, whose
# template is "...: {job title} en {company}" (verified against bimbo and
# grupombo's live tenants).
_COMPANY_NAME_RE = re.compile(r"<title>.*\ben\s+([^<]+?)\s*</title>", re.IGNORECASE)
# Employment/modality tags render as e.g. `<span class="...hr-Clock..."></span>Full-time<!-- comment --></div>` —
# capturing up to the next "<" (an HTML comment or the closing </div>, varies
# by tag) rather than anchoring on </div> directly, since the comment isn't
# always there.
_TAG_RE = re.compile(r'hr-(Clock|Company|Remote)[^>]*></span>\s*([^<]+)', re.IGNORECASE)
# Substring only (not a full class="..." match) — the div carries several
# other classes ahead of this one (e.g. "hrc-fs-14 m-0 hrc-black
# job-description-content"), verified live.
_DESCRIPTION_MARKER = "job-description-content"

_EMPLOYMENT_TYPE_MAP = {
    "full-time": EmploymentType.FULL_TIME,
    "tiempo completo": EmploymentType.FULL_TIME,
    "part time": EmploymentType.PART_TIME,
    "medio tiempo": EmploymentType.PART_TIME,
    "temporal": EmploymentType.TEMPORARY,
    "pasantia": EmploymentType.INTERNSHIP,
    "pasantía": EmploymentType.INTERNSHIP,
}
_WORKPLACE_TYPE_MAP = {
    "presencial": WorkplaceType.ONSITE,
    "remoto": WorkplaceType.REMOTE,
    "hibrido": WorkplaceType.HYBRID,
    "híbrido": WorkplaceType.HYBRID,
}


def extract(html: str) -> ExtractedJobFields:
    title_match = _TITLE_RE.search(html)
    location_match = _LOCATION_TITLE_RE.search(html)

    workplace_type = WorkplaceType.UNKNOWN
    employment_type = EmploymentType.UNKNOWN
    for icon, text in _TAG_RE.findall(html):
        key = text.strip().lower()
        if icon.lower() == "company" or icon.lower() == "remote":
            workplace_type = _WORKPLACE_TYPE_MAP.get(key, workplace_type)
        elif icon.lower() == "clock":
            employment_type = _EMPLOYMENT_TYPE_MAP.get(key, employment_type)

    description = None
    marker = html.find(_DESCRIPTION_MARKER)
    if marker != -1:
        div_start = html.rfind("<div", 0, marker)
        if div_start != -1:
            inner = extract_balanced_div(html, div_start)
            description = html_to_formatted_text(inner) if inner else None

    return ExtractedJobFields(
        title=title_match.group(1).strip() if title_match else None,
        description=description,
        location=location_match.group(1).strip() if location_match else None,
        workplace_type=workplace_type,
        employment_type=employment_type,
    )


def _company_name_of(html: str) -> str | None:
    match = _COMPANY_NAME_RE.search(html)
    return match.group(1).strip() if match else None


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_ID_RE.search(url):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    fields = extract(html)
    description = fields.description or base.fallback_description(html)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=fields.title or base.fallback_title(html),
        description=description,
        company_name=_company_name_of(html),
        location=fields.location,
        workplace_type=fields.workplace_type,
        employment_type=fields.employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(
    AtsType.HIRINGROOM,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
