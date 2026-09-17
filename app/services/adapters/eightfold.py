import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter

_EIGHTFOLD_SEARCH_PAGE_SIZE = 20
_EIGHTFOLD_MAX_JOBS = 500
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_ROBOTS_SITEMAP_RE = re.compile(r"^Sitemap:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
# Eightfold's white-label career sites (e.g. jobs.twilio.com,
# explore.jobs.netflix.net) all serve from a `/careers/job/{numeric_id}`
# path and share a distinctive, stable string in their page HTML even
# though the listing itself renders via JS — verified against two live
# instances. Unlike Greenhouse/Ashby's embeds, this one carries no board
# key in the URL *or* the page at all: the API just wants the tenant's own
# "domain" value, usually (not always — Netflix's host ends .net but its
# domain value would be .com) the requesting host's registrable domain, so
# the same guess-and-verify approach as Ashby applies, keyed off the known
# job id from the URL path instead of a query param.
_EIGHTFOLD_JOB_URL_RE = re.compile(r"/careers/job/(\d+)")
_EIGHTFOLD_SIGNATURE = "eightfold.ai/privacy-policy"


def _candidate_domains(host: str) -> list[str]:
    """Eightfold's "domain" tenant identifier is usually the requesting
    host's registrable domain, but the career site itself often lives on a
    subdomain (jobs.twilio.com's tenant domain is twilio.com) — try the host
    as-is, then progressively strip leading subdomain labels.
    """
    labels = host.lower().split(".")
    candidates = [host.lower()]
    for strip in (1, 2):
        if len(labels) > strip + 1:
            candidates.append(".".join(labels[strip:]))
    return list(dict.fromkeys(candidates))  # dedupe, keep order


def _domain_is_valid(host: str, domain: str) -> bool:
    # A wrong guess fails outright (e.g. Netflix's 403 "PCSX is not enabled
    # for this user") rather than succeeding with an empty result, so this
    # can't mistake "no open roles right now" for "wrong domain".
    try:
        response = httpx.get(
            f"https://{host}/api/pcsx/search", params={"domain": domain, "start": 0, "num": 1}, timeout=TIMEOUT
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


def _sitemap_job_urls(host: str) -> list[str]:
    # Fallback for tenants that have disabled /api/pcsx/search entirely
    # (Netflix: 403 "PCSX is not enabled for this user" on every domain
    # guess). Some Eightfold instances still publish a sitemap — Netflix's
    # robots.txt carries a "Sitemap:" line pointing at a sitemap_index.xml
    # that's already domain-qualified (no need to guess the tenant "domain"
    # value at all), listing a jobs sitemap.xml (job postings, path
    # /careers/job/...) alongside a sitemap_cat.xml (facet/category pages,
    # no job content) — verified against a live instance. Not every
    # Eightfold tenant has this (Twilio's robots.txt has no Sitemap: line),
    # but that's fine since this is only reached once pcsx has already
    # failed outright.
    robots = httpx.get(f"https://{host}/robots.txt", timeout=TIMEOUT)
    robots.raise_for_status()
    match = _ROBOTS_SITEMAP_RE.search(robots.text)
    if not match:
        return []
    index = httpx.get(match.group(1), timeout=TIMEOUT)
    index.raise_for_status()
    index_root = ElementTree.fromstring(index.content)

    urls: list[str] = []
    for loc in index_root.findall(".//sm:sitemap/sm:loc", _SITEMAP_NS):
        if not loc.text or "_cat" in loc.text:
            continue
        sitemap = httpx.get(loc.text, timeout=TIMEOUT)
        sitemap.raise_for_status()
        sitemap_root = ElementTree.fromstring(sitemap.content)
        urls.extend(
            u.text
            for u in sitemap_root.findall(".//sm:url/sm:loc", _SITEMAP_NS)
            if u.text and urlsplit(u.text).path.startswith("/careers/job/")
        )
    return urls


def _fetch_jobs(host: str) -> list[str]:
    # Free, public API — no key required, but only when the company hasn't
    # disabled it on their instance (Netflix returns 403 "PCSX is not
    # enabled for this user" on the very same endpoint that works fine for
    # Twilio). The "domain" value the API expects to identify the tenant
    # isn't derivable from the URL alone (see _candidate_domains), so it's
    # cheaply re-resolved on every crawl via the same candidate-guessing
    # embedded detection uses, rather than cached anywhere.
    domain = next((d for d in _candidate_domains(host) if _domain_is_valid(host, d)), None)
    if domain is None:
        sitemap_urls = _sitemap_job_urls(host)
        if sitemap_urls:
            return sitemap_urls[:_EIGHTFOLD_MAX_JOBS]
        raise ValueError(f"Couldn't resolve an Eightfold tenant domain for host={host!r}")

    urls: list[str] = []
    start = 0
    total = None
    # The "num" param above is a request, not a promise — verified against
    # a live instance ignoring num=20 and always returning 10 per page
    # regardless — so pagination advances by however many actually came
    # back each time, and stops using the response's own "count" rather
    # than assuming a fixed page size.
    while len(urls) < _EIGHTFOLD_MAX_JOBS and (total is None or start < total):
        response = httpx.get(
            f"https://{host}/api/pcsx/search",
            params={"domain": domain, "start": start, "num": _EIGHTFOLD_SEARCH_PAGE_SIZE},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json().get("data", {})
        positions = data.get("positions", [])
        total = data.get("count", 0)
        if not positions:
            break
        urls.extend(f"https://{host}{p['positionUrl']}" for p in positions if p.get("positionUrl"))
        start += len(positions)

    return urls[:_EIGHTFOLD_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    job_match = _EIGHTFOLD_JOB_URL_RE.search(url)
    if not job_match:
        return None
    job_id = job_match.group(1)
    host = urlsplit(url).netloc
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _EIGHTFOLD_SIGNATURE not in response.text:
        return None

    for domain in _candidate_domains(host):
        try:
            detail = httpx.get(
                f"https://{host}/api/pcsx/position_details",
                params={"position_id": job_id, "domain": domain},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError:
            continue
        if detail.status_code != 200:
            continue
        data = detail.json().get("data")
        if isinstance(data, dict) and str(data.get("id")) == job_id:
            return f"{host}/{domain}"
    return None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


def _board_url(board_key: str) -> str:
    host = board_key.split("/")[0]
    return f"https://{host}"


# Eightfold has no static URL shape either (match=None) — same as Clinch,
# only reachable via embedded_match. Its embedded_match key carries both
# host and domain (needed to name the CrawlSource row), but board_key for
# listing only needs the host — _fetch_jobs re-resolves domain fresh every
# time anyway (see its docstring above), so board_key below is a trivial
# host extraction rather than reusing embedded_match's shape. to_board_url
# drops the domain half and keeps only the host, since that's the
# company's own career-site host — there's no separate ATS-hosted page to
# point at the way there is for every templated adapter.
ADAPTER = AtsAdapter(
    AtsType.EIGHTFOLD,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    to_board_url=_board_url,
    embedded_match=_detect_embedded,
)
