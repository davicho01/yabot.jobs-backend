import re
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_PERSONIO_JOBS_URL = "https://{board_key}.jobs.personio.de/xml"
_PERSONIO_JOB_URL = "https://{board_key}.jobs.personio.de/job/{job_id}"
_PERSONIO_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.jobs\.personio\.de", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _PERSONIO_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated XML feed — no key required. Like
    # BambooHR, the feed has no direct URL; each posting's page is a
    # predictable /job/{id} path off the same subdomain.
    response = httpx.get(_PERSONIO_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    return [
        _PERSONIO_JOB_URL.format(board_key=board_key, job_id=job_id)
        for position in root.findall("position")
        if (job_id := position.findtext("id"))
    ]


ADAPTER = AtsAdapter(
    AtsType.PERSONIO,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.jobs.personio.de/",
)
