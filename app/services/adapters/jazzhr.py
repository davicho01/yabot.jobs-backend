import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_JAZZHR_JOBS_URL = "https://{board_key}.applytojob.com/apply/jobs"
# The listing page links to /apply/jobs/details/{id}, but that route serves
# a generic shell page (verified: <title>JazzHR » Job Listings</title>, no
# job-specific content) rather than the real posting. Each job's actual
# canonical page — confirmed via that shell page's own <link rel="canonical">
# — is /apply/{id}/{slug}, and the {slug} part turns out to be cosmetic:
# /apply/{id} alone (no slug) resolves to the same real content.
_JAZZHR_JOB_URL = "https://{board_key}.applytojob.com/apply/{job_id}"
_JAZZHR_JOB_ID_RE = re.compile(r"/apply/jobs/details/([a-zA-Z0-9]+)")
_JAZZHR_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.applytojob\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _JAZZHR_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Unlike the other platforms, JazzHR has no public JSON API for listings
    # (their real API requires a per-company key, which we don't have and
    # can't get without that company's cooperation). The only free option is
    # the server-rendered /apply/jobs HTML page, which links to each posting
    # as /apply/jobs/details/{id} — more fragile than a real API contract
    # since it depends on markup that could change, but deterministic today.
    response = httpx.get(_JAZZHR_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    job_ids = dict.fromkeys(_JAZZHR_JOB_ID_RE.findall(response.text))  # dedupe, keep order
    return [_JAZZHR_JOB_URL.format(board_key=board_key, job_id=job_id) for job_id in job_ids]


ADAPTER = AtsAdapter(
    AtsType.JAZZHR,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.applytojob.com/apply/jobs",
)
