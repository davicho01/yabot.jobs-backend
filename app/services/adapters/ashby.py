import re
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    ScanResult,
    candidate_slugs_from_domain,
    get_with_retry,
    is_recent_posting,
    limit_job_urls,
)
from app.services.adapters.text import clean_text, html_to_formatted_text
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
    return limit_job_urls(job["jobUrl"] for job in jobs if job.get("jobUrl") and is_recent_posting(job.get("publishedAt")))


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


# Ashby's public job-board API has no per-job field for company name (see
# _fetch_board_job below) — verified against a live board (anrok, jobs.ashbyhq.com/anrok):
# the endpoint's only top-level key besides "jobs" is "apiVersion". The
# wrapper page's own og:site_name is the next best source, same fallback
# greenhouse.py's embedded case uses for the same reason.
_ASHBY_WORKPLACE_TYPE_MAP = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "onsite": WorkplaceType.ONSITE,
    "on-site": WorkplaceType.ONSITE,
    "inoffice": WorkplaceType.ONSITE,
}
_ASHBY_EMPLOYMENT_TYPE_MAP = {
    "fulltime": EmploymentType.FULL_TIME,
    "parttime": EmploymentType.PART_TIME,
    "intern": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
}


def _fetch_board_job(board_key: str, job_id: str) -> dict | None:
    response = get_with_retry(_ASHBY_JOBS_URL.format(board_key=quote(board_key, safe="")), timeout=TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return next((job for job in jobs if job.get("id") == job_id), None)


def scan_job_url(url: str) -> ScanResult | None:
    """White-label Ashby embeds (see _detect_embedded above) build their
    content entirely client-side — verified live against nexusblack.com's
    careers page, whose static HTML has no "ashbyhq" string anywhere at all,
    just an empty `<div id="ashby_embed">` Ashby's own JS fills in after
    load. job_scanner's generic default scanner never sees that, so without
    this it silently "succeeds" with the wrapper page's own <title>/
    og:description — the *company's* name and marketing blurb, not the
    job's actual title/description. Hosted jobs.ashbyhq.com/* postings are
    untouched here (return None, same as before this existed) since those
    already publish real JSON-LD the generic scanner reads fine on its own.
    """
    job_id_match = _ASHBY_EMBED_JOB_ID_RE.search(url)
    if job_id_match is None:
        return None
    job_id = job_id_match.group(1)

    board_key = _detect_embedded(url)
    if board_key is None:
        return None

    try:
        job = _fetch_board_job(board_key, job_id)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))
    if job is None:
        return ScanResult(success=False, error="Ashby board no longer lists this job — likely removed or filled.")

    description = html_to_formatted_text(job.get("descriptionHtml")) or clean_text(job.get("descriptionPlain"))
    workplace_type = _ASHBY_WORKPLACE_TYPE_MAP.get(str(job.get("workplaceType") or "").strip().lower(), WorkplaceType.UNKNOWN)
    employment_type = _ASHBY_EMPLOYMENT_TYPE_MAP.get(str(job.get("employmentType") or "").strip().lower(), EmploymentType.UNKNOWN)
    salary_min, salary_max, salary_currency = base.salary_from_text(description)

    company_name = None
    try:
        page = base.fetch_html(url)
        company_name = base.og_site_name(page.text)
    except httpx.HTTPError:
        pass

    return ScanResult(
        success=True,
        title=clean_text(job.get("title")),
        description=description,
        company_name=company_name,
        location=clean_text(job.get("location")),
        workplace_type=workplace_type,
        employment_type=employment_type,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.posting_date(job.get("publishedAt")),
    )


ADAPTER = AtsAdapter(
    AtsType.ASHBY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://jobs.ashbyhq.com/{key}",
    embedded_match=_detect_embedded,
    scan_job_url=scan_job_url,
)
