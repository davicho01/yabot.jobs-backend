import re
from urllib.parse import urlsplit

import httpx

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry

_ATTRAX_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_ATTRAX_SIGNATURE = "attrax"
# Attrax (a Microsoft-stack career-site CMS, white-labeled onto each
# customer's own domain, e.g. careers.abbvie.com) usually locale-prefixes
# every real page (/en/jobs, /en/job/{slug}-jid-{id}) but the bare domain
# root doesn't redirect to reveal which locale — it just serves content
# based on Accept-Language/geo IP with siteUrlPrefix left as "/". Verified
# live: the root page's own nav still links the jobs search with an
# absolute, locale-prefixed URL (https://{host}/en/jobs?...), so that's
# scraped out of whatever page _detect_embedded already fetched rather than
# guessed. Some tenants (verified live: careers.harvard.edu) have
# siteUrlPrefix genuinely empty instead — no locale segment anywhere, on
# either the listing or job-detail pages, "/jobs" and "/job/...-jid-{id}"
# directly off the root — so an empty locale is a real, distinct case from
# "couldn't detect," not just defaulted away to "en" (which 404s there).
_JOBS_LINK_RE = re.compile(r"https?://[^/\"]+/([a-z]{2}(?:-[a-z]{2})?)/jobs\b", re.IGNORECASE)
_BARE_JOBS_LINK_RE = re.compile(r"""href=['"]/jobs(?:[?'"]|$)""", re.IGNORECASE)
_JOB_LINK_RE = re.compile(r'href="((?:/[a-z]{2}(?:-[a-z]{2})?)?/job/[^"]+-jid-(\d+))"', re.IGNORECASE)


def _detect_embedded(url: str) -> str | None:
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _ATTRAX_SIGNATURE not in response.text.lower():
        return None
    host = urlsplit(str(response.url)).netloc
    locale_match = _JOBS_LINK_RE.search(response.text)
    if locale_match:
        return f"{host}/{locale_match.group(1)}"
    if _BARE_JOBS_LINK_RE.search(response.text):
        return f"{host}/"
    return f"{host}/en"


def _fetch_jobs(board_key: str) -> list[str]:
    host, _, locale = board_key.partition("/")
    jobs_path = f"/{locale}/jobs" if locale else "/jobs"
    urls: list[str] = []
    page = 1
    while len(urls) < _ATTRAX_MAX_JOBS:
        response = get_with_retry(f"https://{host}{jobs_path}", params={"page": page}, timeout=TIMEOUT)
        response.raise_for_status()
        paths = dict.fromkeys(path for path, _job_id in _JOB_LINK_RE.findall(response.text))
        if not paths:
            break
        urls.extend(f"https://{host}{path}" for path in paths)
        page += 1
    return urls[:_ATTRAX_MAX_JOBS]


# No to_board_url — like Oracle Fusion/Clinch/SuccessFactors, Attrax is
# white-labeled with no shared host to canonicalize to; board_url is stored
# verbatim, as submitted.
ADAPTER = AtsAdapter(
    AtsType.ATTRAX,
    fetch_jobs=_fetch_jobs,
    board_key=_detect_embedded,
    embedded_match=_detect_embedded,
)
