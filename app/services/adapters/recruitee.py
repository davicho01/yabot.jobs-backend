import re

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_RECRUITEE_JOBS_URL = "https://{board_key}.recruitee.com/api/offers/"
_RECRUITEE_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.recruitee\.com", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _RECRUITEE_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = httpx.get(_RECRUITEE_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    offers = response.json().get("offers", [])
    return [offer["careers_url"] for offer in offers if offer.get("careers_url")]


ADAPTER = AtsAdapter(
    AtsType.RECRUITEE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.recruitee.com/",
)
