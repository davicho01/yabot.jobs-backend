import re
from datetime import datetime

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import clean_text, extract_balanced_tag, html_to_formatted_text

# Frontline AppliTrack — a multi-tenant K-12/education ATS at
# www.applitrack.com/{client}/onlineapp. Its robots.txt only disallows
# /*/onlineapp/admin (verified live). Every listing renders client-side from
# jobpostings/Output.asp, a JavaScript file of document.write() calls whose
# string literals hold the postings' HTML: ?all=1 is the whole board,
# ?AppliTrackJobID={id} a single posting. The user-facing job page
# (default.aspx?AppliTrackJobID=) only carries the title, plus the district's
# name and address in its footer.
_CLIENT_RE = re.compile(r"applitrack\.com/([A-Za-z0-9_-]+)/onlineapp", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"AppliTrackJobID=(\d+)", re.IGNORECASE)
_BOARD_URL = "https://www.applitrack.com/{client}/onlineapp/default.aspx"
_JOB_URL = "https://www.applitrack.com/{client}/onlineapp/default.aspx?AppliTrackJobID={job_id}"
_FEED_URL = "https://www.applitrack.com/{client}/onlineapp/jobpostings/Output.asp"
_FEED_JOB_ID_RE = re.compile(r"JobID: (\d+)")
_TITLE_RE = re.compile(r"<td id='wrapword'[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_FIELD_RE = re.compile(
    r"<span class=[\"']label[\"'][^>]*>\s*([^<:]+?)\s*:\s*</span>.*?<span class=[\"']normal[\"']>(.*?)</span>",
    re.IGNORECASE | re.DOTALL,
)
# Postings inline their logos/images as base64 data URIs (most of a board's
# multi-megabyte feed, verified live on graniteschools) — dropped before
# parsing so they don't end up in stored HTML.
_DATA_URI_RE = re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_CONTACT_RE = re.compile(r'<ul id="contactInfo"[^>]*>(.*?)</ul>', re.IGNORECASE | re.DOTALL)
_DISTRICT_NAME_RE = re.compile(r"<a [^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_CITY_STATE_RE = re.compile(r"<li>\s*([^<]+?,\s*[A-Z]{2})\s+\d{5}(?:-\d{4})?\s*</li>")
_PAGE_TITLE_RE = re.compile(r"<title>\s*(.*?)\s*-\s*Frontline Recruitment\s*</title>", re.IGNORECASE | re.DOTALL)


def _match(url: str) -> str | None:
    match = _CLIENT_RE.search(url)
    return match.group(1).lower() if match else None


def _feed(client: str, params: dict) -> str:
    response = get_with_retry(_FEED_URL.format(client=client), params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return _DATA_URI_RE.sub("", response.text).replace("\\'", "'")


def _fetch_jobs(client: str) -> list[str]:
    job_ids = dict.fromkeys(_FEED_JOB_ID_RE.findall(_feed(client, {"all": 1})))
    return limit_job_urls(_JOB_URL.format(client=client, job_id=job_id) for job_id in job_ids)


def _posting_block(feed: str, job_id: str) -> str | None:
    start = feed.find(f"id='p{job_id}_'")
    if start == -1:
        return None
    return extract_balanced_tag(feed, feed.rfind("<ul", 0, start), "ul")


def _district(page_html: str) -> tuple[str | None, str | None]:
    # Footer contact block: district link, street, then "City, ST 12345"
    # (verified live on graniteschools, alpineschools, washk12 and
    # atlantapublicschools).
    contact = _CONTACT_RE.search(page_html)
    if contact is None:
        return None, None
    name = _DISTRICT_NAME_RE.search(contact.group(1))
    city = _CITY_STATE_RE.search(contact.group(1))
    return (clean_text(name.group(1)) if name else None), (clean_text(city.group(1)) if city else None)


def _posted_at(value: str | None):
    try:
        return datetime.strptime(value, "%m/%d/%Y").date() if value else None
    except ValueError:
        return None


def scan_job_url(url: str) -> ScanResult | None:
    client = _match(url)
    job_id_match = _JOB_ID_RE.search(url)
    if client is None or job_id_match is None:
        return None
    job_id = job_id_match.group(1)
    try:
        feed = _feed(client, {"AppliTrackJobID": job_id})
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    block = _posting_block(feed, job_id)
    if block is None or f"JobID: {job_id}" not in feed:
        return ScanResult(success=False, error="AppliTrack no longer lists this job.", expired=True)

    fields = {clean_text(label): clean_text(value) for label, value in _FIELD_RE.findall(block)}
    title_match = _TITLE_RE.search(block)
    page_title = _PAGE_TITLE_RE.search(page.text)
    district_name, district_city = _district(page.text)
    return ScanResult(
        success=True,
        title=(clean_text(title_match.group(1)) if title_match else None)
        or (clean_text(page_title.group(1)) if page_title else None),
        description=html_to_formatted_text(block),
        company_name=district_name,
        # "Location" is a school or department within the district (e.g.
        # "Human Resources"), not a place — the district office's city from
        # the footer stands in for it.
        location=district_city,
        posted_at=_posted_at(fields.get("Date Posted")),
        raw_html_excerpt=block[:20_000],
        full_html=block,
    )


ADAPTER = AtsAdapter(
    AtsType.APPLITRACK,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda client: _BOARD_URL.format(client=client),
    scan_job_url=scan_job_url,
)
