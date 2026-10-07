"""Scan-only: Paycom job postings are scanned when a job URL reaches us
(a user submission, an Apply link on another board), never discovered by
crawling — robots.txt on www.paycomonline.net disallows everything but a
short allowlist, and the only way to list a customer's jobs
(/api/ats/job-posting-previews/search) isn't on it. Job pages are:
/v4/ats/web.php/portal/{clientkey}/jobs/{id}. Like stripe.py, ADAPTER has
no match/fetch_jobs/embedded_match, so crawl discovery never sees it.
"""

import re

import httpx

from app.models.enums import AtsType
from app.services.adapters import base
from app.services.adapters.base import AtsAdapter, ScanResult

_PORTAL_JOB_RE = re.compile(
    r"^https?://(?:www\.)?paycomonline\.net/v4/ats/web\.php/portal/([0-9A-F]{32})/jobs/(\d+)", re.IGNORECASE
)
# The older job link shape, /v4/ats/web.php/jobs/ViewJobDetails?job={id}&clientkey={key},
# is disallowed by robots.txt — but job ids are the same on the portal
# (verified live: PeakMade 404418 resolves at /portal/{key}/jobs/404418), so
# it is fetched there instead.
_LEGACY_JOB_RE = re.compile(r"^https?://(?:www\.)?paycomonline\.net/v4/ats/web\.php/jobs/ViewJobDetails\?", re.IGNORECASE)
_LEGACY_PARAM_RE = re.compile(r"[?&](job|clientkey)=([0-9A-Za-z]+)", re.IGNORECASE)
_PAYCOM_HOST_RE = re.compile(r"^https?://(?:www\.)?paycomonline\.net(?:[:/?#]|$)", re.IGNORECASE)


def _portal_url(url: str) -> str | None:
    if match := _PORTAL_JOB_RE.match(url):
        return f"https://www.paycomonline.net/v4/ats/web.php/portal/{match.group(1).upper()}/jobs/{match.group(2)}"
    if _LEGACY_JOB_RE.match(url):
        params = {k.lower(): v for k, v in _LEGACY_PARAM_RE.findall(url)}
        if params.get("job") and params.get("clientkey"):
            return f"https://www.paycomonline.net/v4/ats/web.php/portal/{params['clientkey'].upper()}/jobs/{params['job']}"
    return None


def scan_job_url(url: str) -> ScanResult | None:
    portal_url = _portal_url(url)
    if portal_url is None:
        if _PAYCOM_HOST_RE.match(url):
            # A career page or other Paycom URL: the generic scanner would
            # store its "Job Opportunities" shell as a job.
            return ScanResult(success=False, error="Not a Paycom job posting URL (expected .../portal/{key}/jobs/{id}).")
        return None
    try:
        page = base.fetch_html(portal_url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    postings = base.extract_json_ld_postings(page.text)
    if not postings:
        # Every job page is the same client-rendered "Loading..." shell; a
        # live posting's shell carries its JobPosting JSON-LD, and a removed
        # (or never-existing) id's identical shell carries none — verified
        # live on job 9999999 and a filled City Electric Supply posting.
        return ScanResult(success=False, error="Paycom job page has no posting — removed or filled.", expired=True)
    # How much text a posting carries is up to the customer: some put it all
    # in description, others split responsibilities/qualifications out.
    return base.scan_result_from_job_ld(postings[0], page.text)


ADAPTER = AtsAdapter(AtsType.PAYCOM, scan_job_url=scan_job_url)
