import re

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry

_BREEZYHR_JOBS_URL = "https://{board_key}.breezy.hr/json"
_BREEZYHR_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.breezy\.hr", re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _BREEZYHR_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = get_with_retry(_BREEZYHR_JOBS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    postings = response.json()
    return [posting["url"] for posting in postings if posting.get("url")]


ADAPTER = AtsAdapter(
    AtsType.BREEZYHR,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.breezy.hr/",
)
