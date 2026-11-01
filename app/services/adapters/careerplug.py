import re

from app.models.enums import AtsType
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    get_with_retry,
    limit_job_urls,
    max_jobs_per_crawl,
    require_listing_page,
)

# Boards are {tenant}.careerplug.com/jobs, server-rendered 30 jobs a page
# (?page=N; verified 2026-11-01, boost-home-health-careers: pages 1-2 full,
# page 4 empty). robots.txt disallows /apps/ and /j/, not the listing or
# /jobs/{id}. A job link redirects to its canonical page, which carries
# JobPosting JSON-LD, so scans go through the generic scanner. app.careerplug.com
# is the vendor's own site, not a board.
_HOST_RE = re.compile(r"([a-z0-9][a-z0-9-]*)\.careerplug\.com", re.IGNORECASE)
_NOT_TENANTS = {"www", "app", "api", "help", "support"}
_JOB_HREF_RE = re.compile(r'href="/jobs/(\d+)"')


def _match(url: str) -> str | None:
    match = _HOST_RE.search(url)
    if match is None or match.group(1).lower() in _NOT_TENANTS:
        return None
    return f"{match.group(1).lower()}.careerplug.com"


def _fetch_jobs(host: str) -> list[str]:
    seen: dict[str, None] = {}
    page = 1
    while len(seen) < max_jobs_per_crawl():
        response = get_with_retry(f"https://{host}/jobs", params={"page": page}, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        new_ids = [job_id for job_id in dict.fromkeys(_JOB_HREF_RE.findall(response.text)) if job_id not in seen]
        if not new_ids:
            if page == 1:
                require_listing_page(response.text, "careerplug", f"CareerPlug board {host!r}")
            break
        seen.update(dict.fromkeys(new_ids))
        page += 1
    return limit_job_urls(f"https://{host}/jobs/{job_id}" for job_id in seen)


ADAPTER = AtsAdapter(
    AtsType.CAREERPLUG,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}/jobs",
)
