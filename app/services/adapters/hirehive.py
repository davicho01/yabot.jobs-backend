import re

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry, max_jobs_per_crawl, require_listing_page

_HIREHIVE_URL_RE = re.compile(r"([a-zA-Z0-9-]+\.hirehive\.com)", re.IGNORECASE)
# The board root server-renders every open posting as a plain anchor tagged
# with the hh-job-row class (verified live: patagonia.hirehive.com, 24
# postings, no "load more"/pagination control on the page) — no API or
# pagination scheme found, so this just scrapes that one page.
_JOB_HREF_RE = re.compile(r'<a href="(/[a-zA-Z0-9-]+)"[^>]*class="[^"]*\bhh-job-row\b[^"]*"')


def _match(url: str) -> str | None:
    match = _HIREHIVE_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}", timeout=TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    paths = dict.fromkeys(_JOB_HREF_RE.findall(response.text))
    if not paths:
        require_listing_page(response.text, "hirehive", f"HireHive board {host!r}")
    return [f"https://{host}{path}" for path in paths][:max_jobs_per_crawl()]


def _board_key(url: str) -> str | None:
    match = _HIREHIVE_URL_RE.search(url)
    return match.group(1) if match else None


# No to_board_url — {tenant}.hirehive.com is already the canonical shared
# host, board_url is stored verbatim.
ADAPTER = AtsAdapter(
    AtsType.HIREHIVE,
    match=_match,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
)
