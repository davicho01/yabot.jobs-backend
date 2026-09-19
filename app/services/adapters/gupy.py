import json
import re
from urllib.parse import urlsplit

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_GUPY_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_GUPY_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.gupy\.io)", re.IGNORECASE)
# Gupy is a Next.js app: the board root server-renders the full job list
# straight into __NEXT_DATA__ (props.pageProps.jobs, every open role at
# once — verified live: Assaí Atacadista returned all 3347 in one fetch, no
# pagination needed) rather than exposing it as a separate API endpoint.
# Each job object carries only a numeric id, no slug/URL — but
# {host}/jobs/{id} 200s and serves the real posting (verified live), so
# that's built directly instead of scraping a link out of the page.
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)
_JOB_URL = "https://{host}/jobs/{job_id}"


def _match(url: str) -> str | None:
    match = _GUPY_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    match = _NEXT_DATA_RE.search(response.text)
    if match is None:
        return []
    data = json.loads(match.group(1))
    jobs = data.get("props", {}).get("pageProps", {}).get("jobs") or []
    urls = [_JOB_URL.format(host=host, job_id=job["id"]) for job in jobs if job.get("id")]
    return urls[:_GUPY_MAX_JOBS]


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# No to_board_url — {tenant}.gupy.io is already the canonical shared host,
# same reasoning as Avature/iCIMS; board_url is stored verbatim.
ADAPTER = AtsAdapter(
    AtsType.GUPY,
    match=_match,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
)
