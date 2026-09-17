import re
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, candidate_slugs_from_domain, get_with_retry
from app.services.browser_fetch import fetch_rendered_html

_ASHBY_JOBS_URL = "https://api.ashbyhq.com/posting-api/job-board/{board_key}"
_ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?]+)", re.IGNORECASE)
# Ashby's white-label embed (e.g. anrok.com/careers?ashby_jid=...) has no
# static equivalent of Greenhouse's embed-script token — verified against a
# real instance (superhuman.com) that the board name only ever appears in a
# client-side-built iframe src, nowhere in the page's initial HTML or
# Next.js props. Two-tier recovery: many companies just register their
# Ashby board under their own plain company name, so first guess a handful
# of candidate slugs from the domain (free, no browser needed) and confirm
# one by checking the known job id actually appears in that board's public
# listing — wrong 100% harmlessly (tries the next candidate), right often
# enough to be worth it (verified against anrok.com, board name literally
# "anrok"). Only when every guess misses (e.g. superhuman.com's real board
# name, "Superhuman Platform Inc", isn't derivable from the domain at all)
# does this fall back to actually rendering the page and reading the iframe
# Ashby itself built, which is authoritative but far more expensive.
_ASHBY_EMBED_JOB_ID_RE = re.compile(r"ashby_jid=([a-zA-Z0-9-]+)")
_ASHBY_IFRAME_SRC_RE = re.compile(r'jobs\.ashbyhq\.com/([^/?"\']+)', re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _ASHBY_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = get_with_retry(_ASHBY_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return [job["jobUrl"] for job in jobs if job.get("jobUrl")]


def _board_has_job(slug: str, job_id: str) -> bool:
    try:
        response = get_with_retry(_ASHBY_JOBS_URL.format(board_key=quote(slug, safe="")), timeout=TIMEOUT)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    jobs = response.json().get("jobs", [])
    return any(job.get("id") == job_id for job in jobs)


def _detect_embedded(url: str) -> str | None:
    match = _ASHBY_EMBED_JOB_ID_RE.search(url)
    if not match:
        return None
    job_id = match.group(1)
    domain = urlsplit(url).netloc
    for slug in candidate_slugs_from_domain(domain):
        if _board_has_job(slug, job_id):
            return slug

    # Every free guess missed — actually render the page and read the
    # board name out of the iframe src Ashby's own embed script builds.
    # Still verified against the known job id before trusting it, same as
    # the guesses above: a rendered page could theoretically show a stale
    # cached iframe from a previous job's board name changes.
    html = fetch_rendered_html(url)
    if html is None:
        return None
    iframe_match = _ASHBY_IFRAME_SRC_RE.search(html)
    if not iframe_match:
        return None
    slug = unquote(iframe_match.group(1))
    if _board_has_job(slug, job_id):
        return slug
    return None


ADAPTER = AtsAdapter(
    AtsType.ASHBY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://jobs.ashbyhq.com/{key}",
    embedded_match=_detect_embedded,
)
