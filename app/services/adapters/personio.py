import re
from xml.etree import ElementTree

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, limit_job_urls, AtsAdapter, get_with_retry

_PERSONIO_JOBS_URL = "https://{board_key}.jobs.personio.de/xml"
_PERSONIO_JOB_URL = "https://{board_key}.jobs.personio.de/job/{job_id}"
# Tenants are published on either TLD (`<slug>.jobs.personio.de` or `.com`)
# and both serve the identical /xml feed (verified live: ohpen, 1nce, stark),
# so match both but always fetch/store the .de form — that way a .com URL
# and a .de URL for the same tenant resolve to the same board_key.
_PERSONIO_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.jobs\.personio\.(?:de|com)(?=[/:?#]|$)", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _PERSONIO_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated XML feed — no key required. Like
    # BambooHR, the feed has no direct URL; each posting's page is a
    # predictable /job/{id} path off the same subdomain.
    response = get_with_retry(_PERSONIO_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    return limit_job_urls(
        _PERSONIO_JOB_URL.format(board_key=board_key, job_id=job_id)
        for position in root.findall("position")
        if (job_id := position.findtext("id"))
    )


ADAPTER = AtsAdapter(
    AtsType.PERSONIO,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.jobs.personio.de/",
)
