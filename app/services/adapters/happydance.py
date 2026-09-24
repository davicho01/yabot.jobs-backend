import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_HAPPYDANCE_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_HAPPYDANCE_SIGNATURE = "happydance"
# Every posting's own detail URL carries a UUID job id right before its
# slug (verified live: jobs.dominos.com/us/jobs/{uuid}/{slug}/), locale-
# agnostic unlike Attrax/Phenom's fixed "/job(s)/" path segment — some
# tenants localize that word itself (Domino's Spanish mirror uses
# /es/trabajos/{same uuid}/{slug}/). Matching on the UUID instead of a
# literal path segment works across any tenant's locale scheme.
_JOB_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def _sitemap_job_urls(host: str) -> list[str]:
    response = get_with_retry(f"https://{host}/sitemap.xml", timeout=TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []
    seen_ids: set[str] = set()
    for loc in root.findall(".//sm:loc", ns):
        if not loc.text:
            continue
        match = _JOB_ID_RE.search(loc.text)
        if not match or match.group(0) in seen_ids:
            continue
        seen_ids.add(match.group(0))
        urls.append(loc.text)
    return urls


def _fetch_jobs(host: str) -> list[str]:
    # No public jobs API found, and every page (marketing or job-detail)
    # gets a Cloudflare managed challenge on a plain request (verified live:
    # jobs.dominos.com returns "cf-mitigated: challenge" on every path) — but
    # sitemap.xml sits behind no such protection, same as clinch.py's own
    # tenants that WAF-block their marketing pages but not their sitemap.
    # Job-detail pages do render past the challenge for a real browser
    # (verified live) with full schema.org JobPosting JSON-LD, so the
    # generic scanner's browser-render fallback (base.fetch_html) handles
    # per-job scanning without any adapter-specific scan_job_url here.
    return _sitemap_job_urls(host)[:_HAPPYDANCE_MAX_JOBS]


def _detect_embedded(url: str) -> str | None:
    # Unlike clinch.py's WAF tenants (a soft-blocked 2xx with an empty
    # body), Cloudflare's managed challenge here is a hard 403 (verified
    # live: jobs.dominos.com) — raise_for_status() raises on it, so the
    # signature check below is skipped straight to the sitemap fallback
    # rather than ever seeing an empty body to fall through from.
    host = urlsplit(url).netloc
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        host = urlsplit(str(response.url)).netloc
        if _HAPPYDANCE_SIGNATURE in response.text.lower():
            return host
    except httpx.HTTPError:
        pass
    # A sitemap that actually yields job-shaped (UUID) URLs is confirmation
    # enough on its own, same signal clinch.py/phenom.py fall back to.
    try:
        return host if _sitemap_job_urls(host) else None
    except (httpx.HTTPError, ElementTree.ParseError):
        return None


def _board_key(url: str) -> str | None:
    return urlsplit(url).netloc or None


# No to_board_url — white-labeled with no shared host to canonicalize to,
# same reasoning as Clinch/Phenom/Attrax; board_url is stored verbatim, as
# submitted.
ADAPTER = AtsAdapter(
    AtsType.HAPPYDANCE,
    fetch_jobs=_fetch_jobs,
    board_key=_board_key,
    embedded_match=_detect_embedded,
)
