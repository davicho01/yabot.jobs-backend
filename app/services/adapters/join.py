import json
import re

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry, limit_job_urls

# Board: join.com/companies/{slug}; job: join.com/companies/{slug}/{id}-{title-slug}.
# Job pages publish JobPosting JSON-LD, so the generic scanner reads them.
_URL_RE = re.compile(r"^https?://(?:www\.)?join\.com/companies/([A-Za-z0-9_-]+)(?:[/?#]|$)", re.IGNORECASE)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
# Paging cap, a backstop in case pageCount is ever missing.
_MAX_PAGES = 100


def _match(url: str) -> str | None:
    match = _URL_RE.match(url)
    return match.group(1).lower() if match else None


def _fetch_jobs(slug: str) -> list[str]:
    # The company page is server-rendered Next.js with the job list in
    # __NEXT_DATA__ (props.pageProps.initialState.jobs: items + pagination,
    # 5 per page, ?page=N) — verified live on join.com/companies/join.
    # robots.txt only disallows /*/lp/.
    urls: list[str] = []
    page, page_count = 1, 1
    while page <= min(page_count, _MAX_PAGES):
        response = get_with_retry(f"https://join.com/companies/{slug}", params={"page": page}, timeout=TIMEOUT)
        response.raise_for_status()
        next_data = _NEXT_DATA_RE.search(response.text)
        if next_data is None:
            raise ValueError(f"No __NEXT_DATA__ on JOIN board {slug!r}")
        jobs = json.loads(next_data.group(1))["props"]["pageProps"]["initialState"]["jobs"]
        items = jobs.get("items") or []
        if not items:
            break
        urls.extend(f"https://join.com/companies/{slug}/{item['idParam']}" for item in items if item.get("idParam"))
        page_count = (jobs.get("pagination") or {}).get("pageCount") or page
        page += 1
    return limit_job_urls(urls)


# No to_board_url: board_url is stored verbatim, as submitted.
ADAPTER = AtsAdapter(
    AtsType.JOIN,
    match=_match,
    fetch_jobs=_fetch_jobs,
)
