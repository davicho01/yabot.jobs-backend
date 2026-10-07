import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry, limit_job_urls

# Each company's board is its own {company}.homerun.co subdomain; the rest
# are Homerun's own (marketing site, feed, app).
_HOST_RE = re.compile(r"^https?://((?!www\.|feed\.|api\.|app\.)[a-z0-9-]+\.homerun\.co)(?:[:/?#]|$)", re.IGNORECASE)
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# The "Open application" pseudo-vacancy every board lists.
_NOT_JOBS = {"open"}


def _match(url: str) -> str | None:
    match = _HOST_RE.match(url)
    return match.group(1).lower() if match else None


def _fetch_jobs(host: str) -> list[str]:
    # The board page renders its job list client-side, but every board's
    # sitemap.xml (robots.txt: no rules, just the Sitemap line) lists each
    # posting as /{slug}, plus /{slug}/{locale} variants and /apply forms —
    # verified live on jobs.homerun.co. One path segment = one posting.
    response = get_with_retry(f"https://{host}/sitemap.xml", timeout=TIMEOUT)
    response.raise_for_status()
    urls = []
    for loc in ElementTree.fromstring(response.content).findall("sm:url/sm:loc", _SITEMAP_NS):
        segments = [s for s in urlsplit(loc.text or "").path.split("/") if s]
        if len(segments) == 1 and segments[0] not in _NOT_JOBS:
            urls.append(f"https://{host}/{segments[0]}")
    return limit_job_urls(urls)


# No to_board_url: board_url is stored verbatim, as submitted. Job pages
# publish JobPosting JSON-LD, so the generic scanner reads them.
ADAPTER = AtsAdapter(
    AtsType.HOMERUN,
    match=_match,
    fetch_jobs=_fetch_jobs,
)
