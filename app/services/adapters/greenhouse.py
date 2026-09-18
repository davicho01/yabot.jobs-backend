import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, is_recent_posting, limit_job_urls, AtsAdapter, candidate_slugs_from_domain, get_with_retry

_GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{board_key}/jobs"
_GREENHOUSE_URL_RE = re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/?]+)", re.IGNORECASE)
# Some companies white-label Greenhouse onto their own domain (e.g.
# harness.io/company/jobs/apply?gh_jid=...) via Greenhouse's embeddable JS
# widget rather than linking out to boards.greenhouse.io directly — the
# board key isn't visible in the URL at all in that case (only a numeric
# job id, in gh_jid), so _GREENHOUSE_URL_RE above can never match it.
# Greenhouse ships more than one embed variant with the same `?for=`
# token — job_board/js (harness.io) and job_app (instacart.careers) both
# verified live — so this matches any `embed/.../...?for=` shape rather
# than one specific script path.
_GH_EMBED_TOKEN_RE = re.compile(r"greenhouse\.io/embed/[a-zA-Z_/]*\?for=([a-zA-Z0-9_-]+)", re.IGNORECASE)
# Some sites instead fetch the job from Greenhouse's API server-side and
# stitch it into their own page (e.g. coalitioninc.com's Next.js-rendered
# job pages) — no embed script appears anywhere in the HTML, but gh_jid
# still leaks through wherever that fetched job data gets serialized into
# the page (its own absolute_url field, in Coalition's case) — verified
# live. Used as a fallback below when the embed-script check above misses.
_GH_EMBED_JOB_ID_RE = re.compile(r"gh_jid=(\d+)")


def _match(url: str) -> str | None:
    match = _GREENHOUSE_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = get_with_retry(_GREENHOUSE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return limit_job_urls(job["absolute_url"] for job in jobs if job.get("absolute_url") and is_recent_posting(job.get("first_published")))


def _board_has_job(slug: str, job_id: str) -> bool:
    try:
        response = get_with_retry(f"{_GREENHOUSE_JOBS_URL.format(board_key=slug)}/{job_id}", timeout=TIMEOUT)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    # Fast path: the embeddable-widget script names the board directly —
    # authoritative, no guessing needed.
    match = _GH_EMBED_TOKEN_RE.search(response.text)
    if match:
        return match.group(1)

    # Fallback: no embed script, but gh_jid leaked through somewhere in the
    # page anyway (see _GH_EMBED_JOB_ID_RE above) — guess-and-verify a board
    # slug from the domain, same two-tier pattern as Ashby's embed detector.
    job_id_match = _GH_EMBED_JOB_ID_RE.search(response.text)
    if not job_id_match:
        return None
    job_id = job_id_match.group(1)
    domain = urlsplit(url).netloc
    for slug in candidate_slugs_from_domain(domain):
        if _board_has_job(slug, job_id):
            return slug
    return None


ADAPTER = AtsAdapter(
    AtsType.GREENHOUSE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://boards.greenhouse.io/{key}",
    embedded_match=_detect_embedded,
)
