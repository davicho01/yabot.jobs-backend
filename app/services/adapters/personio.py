import json
import re
from datetime import date
from typing import Any
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services import geo
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, limit_job_urls, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, html_to_formatted_text

_PERSONIO_JOBS_URL = "https://{board_key}.jobs.personio.de/xml"
_PERSONIO_JOB_URL = "https://{board_key}.jobs.personio.de/job/{job_id}"
# Tenants are published on either TLD (`<slug>.jobs.personio.de` or `.com`)
# and both serve the identical /xml feed (verified live: ohpen, 1nce, stark),
# so match both but always fetch/store the .de form — that way a .com URL
# and a .de URL for the same tenant resolve to the same board_key.
_PERSONIO_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.jobs\.personio\.(?:de|com)(?=[/:?#]|$)", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _PERSONIO_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated XML feed — no key required. Like
    # BambooHR, the feed has no direct URL; each posting's page is a
    # predictable /job/{id} path off the same subdomain.
    response = get_with_retry(_PERSONIO_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    return limit_job_urls(
        _PERSONIO_JOB_URL.format(board_key=board_key, job_id=job_id)
        for position in root.findall("position")
        if (job_id := position.findtext("id"))
    )


# The job page has no JSON-LD and its meta/og description is a teaser, but it
# embeds the whole posting in its Next.js (React Server Components) payload:
# a "job" object whose section values are either inline HTML or "$<id>"
# references to text rows ("<id>:T<hex byte length>,<html>") elsewhere in
# the same payload (verified live 2026-10-13 on jungvonmatt, europ-assistance
# and ohpen). The XML feed isn't used for this: it carries descriptions only
# in some languages, for some positions.
_JOB_ID_RE = re.compile(r"/job/(\d+)")
_RSC_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_RSC_TEXT_ROW_RE = re.compile(rb"([0-9a-f]+):T([0-9a-f]+),")

# employment_type_slug values first, then schedule_slug for a permanent role.
_EMPLOYMENT_TYPES = {
    "intern": EmploymentType.INTERNSHIP,
    "trainee": EmploymentType.INTERNSHIP,
    "working_student": EmploymentType.INTERNSHIP,
    "freelance": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
}
_SCHEDULES = {"full-time": EmploymentType.FULL_TIME, "part-time": EmploymentType.PART_TIME}


def _rsc_payload(html: str) -> str:
    return "".join(json.loads(f'"{chunk}"') for chunk in _RSC_CHUNK_RE.findall(html))


def _rsc_text_rows(payload: str) -> dict[str, str]:
    data = payload.encode()
    rows: dict[str, str] = {}
    end = 0
    for match in _RSC_TEXT_ROW_RE.finditer(data):
        if match.start() < end:  # inside the previous row's text
            continue
        end = match.end() + int(match.group(2), 16)
        rows[match.group(1).decode()] = data[match.end() : end].decode(errors="replace")
    return rows


def _rsc_object(payload: str, key: str) -> dict[str, Any] | None:
    start = payload.find(f'"{key}":{{')
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(payload, start + len(key) + 3)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _description_of(job: dict[str, Any], rows: dict[str, str]) -> str | None:
    sections = []
    for field in job.get("fields") or []:
        value = field.get("value") or ""
        if value.startswith("$"):
            value = rows.get(value[1:], "")
        if clean_text(value):
            sections.append(f"<h3>{field.get('label') or ''}</h3>{value}")
    return html_to_formatted_text("".join(sections)) if sections else None


def _location_of(job: dict[str, Any]) -> str | None:
    places = []
    for address in job.get("office_addresses") or []:
        place = ", ".join(p for p in (clean_text(address.get("city")), geo.country_name(address.get("country"))) if p)
        if place and place not in places:
            places.append(place)
    return "; ".join(places) or clean_text(job.get("main_office"))


def _employment_type_of(job: dict[str, Any]) -> str:
    kind = _EMPLOYMENT_TYPES.get(job.get("employment_type_slug") or "")
    return kind or _SCHEDULES.get(job.get("schedule_slug") or "", EmploymentType.UNKNOWN)


def _posted_at_of(job: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat((job.get("published_at") or job.get("created_at") or "")[:10])
    except ValueError:
        return None


def scan_job_url(url: str) -> ScanResult | None:
    if _match(url) is None or _JOB_ID_RE.search(url) is None:
        return None
    try:
        page = base.fetch_html(url)  # a removed job answers 404: PostingGone, so expired
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    payload = _rsc_payload(page.text)
    job = _rsc_object(payload, "job")
    if job is None:
        return ScanResult(success=False, error="Personio job page has no embedded job data.")
    description = _description_of(job, _rsc_text_rows(payload))
    salary_min, salary_max, salary_currency = base.salary_from_text(description)
    settings = _rsc_object(payload, "careerSiteSettings") or {}
    return ScanResult(
        success=True,
        title=clean_text(job.get("name")),
        description=description,
        company_name=clean_text(settings.get("company_name")),
        location=_location_of(job),
        employment_type=_employment_type_of(job),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=_posted_at_of(job),
    )


ADAPTER = AtsAdapter(
    AtsType.PERSONIO,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.jobs.personio.de/",
    scan_job_url=scan_job_url,
)
