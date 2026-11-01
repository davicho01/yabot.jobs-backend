import re

from app.models.enums import AtsType
from app.services.adapters.base import (
    TIMEOUT,
    AtsAdapter,
    get_with_retry,
    limit_job_urls,
    require_listing_page,
)

# Boards are careers.kula.ai/{slug}; the page is server-rendered with every
# open job as /{slug}/{id}-{title} (verified 2026-11-01, invoca: 22 links,
# robots.txt disallows only /api/ and the portal). Job pages carry JobPosting
# JSON-LD, so scans go through the generic scanner. careers-cdn.kula.ai is the
# static host and doesn't match.
_BOARD_RE = re.compile(r"careers\.kula\.ai/([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE)
_RESERVED = {"api", "_next", "robots", "sitemap"}  # the slug stops at the "."
# A "join our talent community" entry is listed like a job but isn't one.
_NOT_A_JOB_RE = re.compile(r"talent-community", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _BOARD_RE.search(url)
    if match is None or match.group(1).lower() in _RESERVED:
        return None
    return match.group(1)


def _fetch_jobs(slug: str) -> list[str]:
    response = get_with_retry(f"https://careers.kula.ai/{slug}", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    paths = re.findall(rf'href="(/{re.escape(slug)}/\d+-[^"?#]*)"', response.text, re.IGNORECASE)
    paths = [path for path in dict.fromkeys(paths) if not _NOT_A_JOB_RE.search(path)]
    if not paths:
        require_listing_page(response.text, "kula", f"Kula board {slug!r}")
    return limit_job_urls(f"https://careers.kula.ai{path}" for path in paths)


ADAPTER = AtsAdapter(
    AtsType.KULA,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://careers.kula.ai/{key}",
)
