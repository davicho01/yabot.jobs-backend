import re
from html import unescape

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import TIMEOUT, AtsAdapter, ScanResult, get_with_retry, limit_job_urls
from app.services.adapters.text import html_to_formatted_text

# Government agencies' boards on www.jobapscloud.com, two generations:
# /{agency}/ (State of Maryland "MD", Connecticut "CT", Milwaukee "MIL",
# San Joaquin County "SJQ") with postings at /{agency}/sup/bulpreview.asp,
# and /oec/{agency}/ (City of New Haven) with postings at
# /oec/{agency}/Jobs/Bulletin — both keyed by R1/R2/R3. Paths are
# case-insensitive (/ct/ and /CT/ are the same board). No robots.txt (404).
_BOARD_RE = re.compile(r"^https?://(?:www\.)?jobapscloud\.com/((?:oec/)?[A-Za-z0-9]+)(?:[/?#]|$)", re.IGNORECASE)
_JOB_HREF_RE = re.compile(
    r'href="(?:https?://(?:www\.)?jobapscloud\.com)?(/(?:oec/)?[A-Za-z0-9]+/(?:sup/bulpreview\.asp|Jobs/Bulletin))\?([^"]+)"',
    re.IGNORECASE,
)
_PARAM_RE = re.compile(r"(?:^|&)(R[123])=([^&]*)", re.IGNORECASE)
# "Job Announcement: {title} - {agency}" (Milwaukee) or "Announcement: ..."
# (San Joaquin County).
_TITLE_RE = re.compile(r"^(?:Job\s+)?Announcement:\s*(.*?)\s*-\s*([^-]+)$", re.IGNORECASE)
_BULLETIN_TITLE_RE = re.compile(r'class="JobBulletinTitle"[^>]*>\s*([^<]*?)\s*<', re.IGNORECASE)
_BULLETIN_BODY_RE = re.compile(
    r'id="JobBulletinBody"[^>]*>(.*?)(?:<[^>]+id="contentFooter"|</body>)', re.DOTALL | re.IGNORECASE
)


def _match(url: str) -> str | None:
    match = _BOARD_RE.match(url)
    return match.group(1).lower() if match else None


def _is_pseudo_posting(r1: str, r2: str) -> bool:
    # Listed alongside real jobs: "Application On-File"/"Draft Application"
    # (R1=AF...) and "Practice Application" (PR0000 in R1 or R2) — verified
    # live on MD, CT, MIL and SJQ.
    return r1.upper().startswith("AF") or "PR0000" in (r1.upper(), r2.upper())


def _fetch_jobs(board_key: str) -> list[str]:
    response = get_with_retry(f"https://www.jobapscloud.com/{board_key}/", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    urls = []
    for path, query in _JOB_HREF_RE.findall(response.text):
        params = {k.upper(): v for k, v in _PARAM_RE.findall(unescape(query))}
        r1, r2, r3 = params.get("R1"), params.get("R2"), params.get("R3")
        if not (r1 and r2 and r3) or _is_pseudo_posting(r1, r2):
            continue
        # Every posting is linked twice (title and number), with and
        # without a "b=" param — R1/R2/R3 alone identify it.
        urls.append(f"https://www.jobapscloud.com{path}?R1={r1}&R2={r2}&R3={r3}")
    return limit_job_urls(urls)


def _scan_job_url(url: str) -> ScanResult | None:
    # Most agencies' postings publish JobPosting JSON-LD, which the generic
    # scanner reads well (None below). Some don't (verified live: Milwaukee),
    # and their page is only "Job Announcement: {title} - {agency}" plus a
    # #JobBulletinBody. A removed posting, or an unknown R1/R2/R3, still
    # answers 200 with that title's job name empty ("Job Announcement:  -
    # City of Milwaukee") — which the generic scanner would store as a job.
    if _match(url) is None or not re.search(r"bulpreview\.asp|/Jobs/Bulletin", url, re.IGNORECASE):
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    if base.extract_json_ld_postings(page.text):
        return None
    title = _TITLE_RE.match(" ".join(unescape(base.fallback_title(page.text) or "").split()))
    if title is None:
        # Not a page shape seen before — let the generic scanner try rather
        # than guess: a wrong "expired" closes the listing.
        return None
    bulletin_title = _BULLETIN_TITLE_RE.search(page.text)
    job_title = (bulletin_title and " ".join(unescape(bulletin_title.group(1)).split())) or title.group(1)
    if not job_title:
        return ScanResult(success=False, error="JobAps posting page has no job — removed or closed.", expired=True)
    body = _BULLETIN_BODY_RE.search(page.text)
    return ScanResult(
        success=True,
        title=job_title,
        company_name=title.group(2),
        description=html_to_formatted_text(body.group(1)) if body else base.fallback_description(page.text),
    )


# No to_board_url: board_url is stored verbatim, as submitted.
ADAPTER = AtsAdapter(
    AtsType.JOBAPS,
    match=_match,
    fetch_jobs=_fetch_jobs,
    scan_job_url=_scan_job_url,
)
