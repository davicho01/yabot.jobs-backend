import re

from app.models.enums import AtsType
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    get_with_retry,
    limit_job_urls,
    require_listing_page,
)

# Boards are {tenant}.freshteam.com/jobs, server-rendered with every open job
# as /jobs/{id}/{slug} (verified 2026-11-01, sparkmentalhealth). robots.txt is
# "Disallow: /" with "Allow: /jobs", which rules out the /hire/widgets/jobs.json
# API; the listing and job pages are the permitted route. Job pages carry
# JobPosting JSON-LD, so scans go through the generic scanner.
_HOST_RE = re.compile(r"([a-z0-9][a-z0-9-]*)\.freshteam\.com", re.IGNORECASE)
_NOT_TENANTS = {"www", "support", "developers", "api", "app"}
_JOB_HREF_RE = re.compile(r'href="(/jobs/[A-Za-z0-9_-]+/[^"?#]+)"')


def _match(url: str) -> str | None:
    match = _HOST_RE.search(url)
    if match is None or match.group(1).lower() in _NOT_TENANTS:
        return None
    return f"{match.group(1).lower()}.freshteam.com"


def _fetch_jobs(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}/jobs", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    paths = list(dict.fromkeys(_JOB_HREF_RE.findall(response.text)))
    if not paths:
        require_listing_page(response.text, "freshteam", f"Freshteam board {host!r}")
    return limit_job_urls(f"https://{host}{path}" for path in paths)


ADAPTER = AtsAdapter(
    AtsType.FRESHTEAM,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}/jobs",
)
