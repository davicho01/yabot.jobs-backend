import re

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, ExtractedJobFields, ScanResult, get_with_retry
from app.services.adapters.text import clean_text, html_to_formatted_text

# Oracle SelectMinds — a multi-tenant employee-referral/careers platform at
# {tenant}.referrals.selectminds.com. The bare root and most of the site
# (mysubmissions, profile, ...) are SSO-gated, but individual job postings
# and the /latest-jobs listing are genuinely public (verified live) — no
# login needed to view or scan them. Previously mis-rejected as fully
# login-walled based on the root domain alone.
_HOST_RE = re.compile(r"([a-zA-Z0-9-]+)\.referrals\.selectminds\.com", re.IGNORECASE)
_LATEST_JOBS_URL = "https://{tenant}.referrals.selectminds.com/latest-jobs"
_JOB_LINK_RE = re.compile(r'href="(https://[a-zA-Z0-9.-]+\.referrals\.selectminds\.com/jobs/[a-z0-9-]+-\d+)"')
_JOB_URL_RE = re.compile(r"referrals\.selectminds\.com/jobs/[a-z0-9-]+-\d+", re.IGNORECASE)
# The site's own og:title/og:description are stale once a posting closes
# (verified live: a since-closed Data Engineer posting still advertised
# itself via OG tags as if open) — the real, current state only shows up
# in the rendered page body itself, in this exact phrase.
_CLOSED_SIGNATURE = "this position has been closed"
_TITLE_RE = re.compile(r"<title>(.*?)\s*-\s*[^<]*</title>", re.IGNORECASE | re.DOTALL)
_LOCATION_RE = re.compile(
    r'class="primary_location"[^>]*>.*?<span[^>]*>.*?</span>\s*(.*?)\s*</a>', re.IGNORECASE | re.DOTALL
)
_DESCRIPTION_START_RE = re.compile(r'class="job_description"[^>]*>', re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _HOST_RE.search(url)
    return match.group(1) if match else None


def _board_url(tenant: str) -> str:
    return _LATEST_JOBS_URL.format(tenant=tenant)


def _board_key(url: str) -> str | None:
    return _match(url)


def _fetch_jobs(tenant: str) -> list[str]:
    response = get_with_retry(_board_url(tenant), timeout=TIMEOUT)
    response.raise_for_status()
    return list(dict.fromkeys(_JOB_LINK_RE.findall(response.text)))


def _extract_balanced_div_like(html: str, start: int, tag: str) -> str | None:
    open_end = html.find(">", start)
    if open_end == -1:
        return None
    depth = 1
    for m in re.finditer(rf"<(/?){tag}\b[^>]*>", html[open_end + 1 :], re.IGNORECASE):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[open_end + 1 : open_end + 1 + m.start()]
    return None


def extract(html: str) -> ExtractedJobFields:
    title_match = _TITLE_RE.search(html)
    location_match = _LOCATION_RE.search(html)

    description = None
    desc_match = _DESCRIPTION_START_RE.search(html)
    if desc_match:
        inner = _extract_balanced_div_like(html, desc_match.start(), "div")
        description = html_to_formatted_text(inner) if inner else None

    return ExtractedJobFields(
        title=clean_text(title_match.group(1)) if title_match else None,
        description=description,
        company_name="Zions Bancorporation",
        location=clean_text(location_match.group(1)) if location_match else None,
    )


def scan_job_url(url: str) -> ScanResult | None:
    if not _JOB_URL_RE.search(url):
        return None
    response = get_with_retry(url, timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    html = response.text
    if _CLOSED_SIGNATURE in html.lower():
        # Genuinely closed, not a transient scrape failure — retrying won't
        # ever succeed, but returning a ScanResult (rather than None, which
        # would fall through to job_scanner's generic OG-tag fallback and
        # reproduce the exact stale-title/no-location bug this adapter
        # exists to fix) keeps that stale data from being reported as a
        # successful scan.
        return ScanResult(success=False, error="Position has been closed on the source site.")

    fields = extract(html)
    if fields.title is None:
        return None
    return ScanResult(
        success=True,
        title=fields.title,
        description=fields.description,
        company_name=fields.company_name,
        location=fields.location,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


# No shared host to canonicalize to beyond the tenant subdomain already in
# the submitted URL — board_url points at /latest-jobs, stored verbatim per
# tenant, same reasoning as every other multi-tenant white-label adapter.
ADAPTER = AtsAdapter(
    AtsType.SELECTMINDS,
    match=_match,
    board_key=_board_key,
    fetch_jobs=_fetch_jobs,
    to_board_url=_board_url,
    scan_job_url=scan_job_url,
)
