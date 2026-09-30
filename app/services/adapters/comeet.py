import json
import re

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import MAX_LOCATION_LENGTH, clean_text, html_to_formatted_text

# Comeet (rebranded "Spark Hire Recruit") — a multi-tenant ATS hosted at
# www.comeet.com/jobs/{slug}/{uid}/..., {uid} being the real per-tenant
# identifier ({slug} is cosmetic/SEO text a company could in principle
# change). Every page is a client-rendered SPA (verified live: <title> and
# visible body never update server-side), but both the board-level listing
# and each job's own detail page embed the exact data the SPA hydrates from
# as a plain JS object literal assignment — JSON-compatible, just missing
# the outer var declaration — so a JSON decoder anchored right after the
# assignment (ignoring the trailing ";") reads it directly, no HTML
# scraping or browser render needed at all.
_URL_RE = re.compile(r"comeet\.com/jobs/([^/]+)/([^/]+)", re.IGNORECASE)
_BOARD_POSITIONS_MARKER = "COMPANY_POSITIONS_DATA = "
_POSITION_MARKER = "POSITION_DATA = "

_EMPLOYMENT_TYPE_MAP = {
    "full-time": EmploymentType.FULL_TIME,
    "part-time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "internship": EmploymentType.INTERNSHIP,
    "intern": EmploymentType.INTERNSHIP,
    "temporary": EmploymentType.TEMPORARY,
}
_WORKPLACE_TYPE_MAP = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "on-site": WorkplaceType.ONSITE,
    "onsite": WorkplaceType.ONSITE,
}


def _match(url: str) -> str | None:
    match = _URL_RE.search(url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _extract_json_after(text: str, marker: str) -> object | None:
    idx = text.find(marker)
    if idx == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text, idx + len(marker))
    except json.JSONDecodeError:
        return None
    return data


def _fetch_jobs(board_key: str) -> list[str]:
    response = get_with_retry(f"https://www.comeet.com/jobs/{board_key}", timeout=TIMEOUT)
    response.raise_for_status()
    positions = _extract_json_after(response.text, _BOARD_POSITIONS_MARKER)
    if not isinstance(positions, list):
        return []
    return limit_job_urls(
        p["url_comeet_hosted_page"] for p in positions if isinstance(p, dict) and p.get("url_comeet_hosted_page")
    )


def _location_of(data: dict) -> str | None:
    location = data.get("location")
    if not isinstance(location, dict):
        return None
    parts = [location.get("city"), location.get("state"), location.get("country")]
    text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    if not text:
        # Fully-remote postings sometimes carry no city/state/country at
        # all — location["name"] (e.g. "TOLA (US)") is the only thing left,
        # a sales-territory label rather than a real place but still more
        # useful than nothing.
        name = location.get("name")
        text = name.strip() if isinstance(name, str) and name.strip() else ""
    return (text[: MAX_LOCATION_LENGTH - 3] + "...") if len(text) > MAX_LOCATION_LENGTH else (text or None)


def _employment_type_of(data: dict) -> str:
    value = data.get("employment_type")
    return _EMPLOYMENT_TYPE_MAP.get(value.strip().lower(), EmploymentType.UNKNOWN) if isinstance(
        value, str
    ) else EmploymentType.UNKNOWN


def _workplace_type_of(data: dict) -> str:
    value = data.get("workplace_type")
    return _WORKPLACE_TYPE_MAP.get(value.strip().lower(), WorkplaceType.UNKNOWN) if isinstance(
        value, str
    ) else WorkplaceType.UNKNOWN


def _description_of(data: dict) -> str | None:
    custom_fields = data.get("custom_fields")
    details = custom_fields.get("details") if isinstance(custom_fields, dict) else None
    if not isinstance(details, list):
        return None
    ordered = sorted(
        (d for d in details if isinstance(d, dict) and isinstance(d.get("value"), str) and d["value"].strip()),
        key=lambda d: d.get("order", 0),
    )
    html = "".join(f"<h3>{d.get('name', '')}</h3>{d['value']}" if d.get("name") else d["value"] for d in ordered)
    return html_to_formatted_text(html) if html else None


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    data = _extract_json_after(page.text, _POSITION_MARKER)
    if not isinstance(data, dict):
        return None

    description = _description_of(data)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    return ScanResult(
        success=True,
        title=clean_text(data.get("name")),
        description=description,
        company_name=clean_text(data.get("company_name")),
        location=_location_of(data),
        workplace_type=_workplace_type_of(data),
        employment_type=_employment_type_of(data),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        raw_html_excerpt=page.text[:20_000],
        full_html=page.text,
    )


# No to_board_url — {uid} is Comeet's own opaque per-tenant id, not
# something to templatize; board_url is stored verbatim, same reasoning as
# Oracle Fusion/Clinch.
ADAPTER = AtsAdapter(
    AtsType.COMEET,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=scan_job_url,
)
