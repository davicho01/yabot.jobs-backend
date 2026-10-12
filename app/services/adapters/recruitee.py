import re

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, limit_job_urls, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, html_to_formatted_text

_RECRUITEE_JOBS_URL = "https://{board_key}.recruitee.com/api/offers/"
_RECRUITEE_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.recruitee\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _RECRUITEE_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = get_with_retry(_RECRUITEE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    offers = response.json().get("offers", [])
    return limit_job_urls(offer["careers_url"] for offer in offers if offer.get("careers_url"))


# A job page is {board}.recruitee.com/o/{slug} — or the same path on a
# company's own domain (transperfect.com/o/...). Its HTML is a client-side
# app: a generic scan got Recruitee's own marketing copy ("Tellent Recruitee
# helps you hire faster...") instead of the job (verified 2026-10-12), while
# the board's offers API carries the whole posting. A custom domain doesn't
# serve that API, so its board comes from the crawl source (base.scan_board_url).
_JOB_PAGE_RE = re.compile(r"https?://([^/]+)/o/([a-zA-Z0-9_-]+)", re.IGNORECASE)
_EMPLOYMENT_TYPES = {
    "fulltime": EmploymentType.FULL_TIME,
    "fulltime_permanent": EmploymentType.FULL_TIME,
    "fulltime_fixed_term": EmploymentType.FULL_TIME,
    "parttime": EmploymentType.PART_TIME,
    "parttime_permanent": EmploymentType.PART_TIME,
    "parttime_fixed_term": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "freelance": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "internship": EmploymentType.INTERNSHIP,
}


def _workplace_of(offer: dict) -> str:
    if offer.get("remote"):
        return WorkplaceType.REMOTE
    if offer.get("hybrid"):
        return WorkplaceType.HYBRID
    if offer.get("on_site"):
        return WorkplaceType.ONSITE
    return WorkplaceType.UNKNOWN


def scan_job_url(url: str) -> ScanResult | None:
    page = _JOB_PAGE_RE.search(url)
    if page is None:
        return None
    board_key = _match(page.group(1)) or _match(base.scan_board_url() or "")
    if board_key is None:
        return None
    slug = page.group(2)
    try:
        response = get_with_retry(_RECRUITEE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
        response.raise_for_status()
        offers = response.json().get("offers", [])
    except (httpx.HTTPError, ValueError) as exc:
        return ScanResult(success=False, error=str(exc))
    offer = next((o for o in offers if o.get("slug") == slug), None)
    if offer is None:
        return ScanResult(success=False, error="Recruitee board no longer lists this offer.", expired=True)

    sections = [offer.get("description") or ""]
    if offer.get("requirements"):
        sections.append(f"<h3>Requirements</h3>{offer['requirements']}")
    description = html_to_formatted_text("".join(sections))
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=clean_text(offer.get("title")),
        description=description,
        company_name=clean_text(offer.get("company_name")),
        location=clean_text(offer.get("location")),
        workplace_type=_workplace_of(offer),
        employment_type=_EMPLOYMENT_TYPES.get(offer.get("employment_type_code") or "", EmploymentType.UNKNOWN),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.posting_date((offer.get("published_at") or "").replace(" UTC", "+00:00").replace(" ", "T")),
    )


ADAPTER = AtsAdapter(
    AtsType.RECRUITEE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.recruitee.com/",
    scan_job_url=scan_job_url,
)
