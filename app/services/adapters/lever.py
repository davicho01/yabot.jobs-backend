import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_LEVER_JOBS_URL = "https://api.lever.co/v0/postings/{board_key}"
_LEVER_URL_RE = re.compile(r"jobs\.lever\.co/([^/?]+)", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _LEVER_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = httpx.get(_LEVER_JOBS_URL.format(board_key=board_key), params={"mode": "json"}, timeout=TIMEOUT)
    response.raise_for_status()
    postings = response.json()
    return [posting["hostedUrl"] for posting in postings if posting.get("hostedUrl")]


ADAPTER = AtsAdapter(
    AtsType.LEVER,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://jobs.lever.co/{key}",
)
