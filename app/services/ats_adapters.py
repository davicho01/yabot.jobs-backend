import json
import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

from app.models.enums import AtsType
from app.services.browser_fetch import fetch_rendered_html

_GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
_LEVER_JOBS_URL = "https://api.lever.co/v0/postings/{board_token}"
_ASHBY_JOBS_URL = "https://api.ashbyhq.com/posting-api/job-board/{board_token}"
_BAMBOOHR_JOBS_URL = "https://{board_token}.bamboohr.com/careers/list"
_BAMBOOHR_JOB_URL = "https://{board_token}.bamboohr.com/careers/{job_id}"
_PERSONIO_JOBS_URL = "https://{board_token}.jobs.personio.de/xml"
_PERSONIO_JOB_URL = "https://{board_token}.jobs.personio.de/job/{job_id}"
_WORKDAY_JOBS_URL = "https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
_WORKDAY_JOB_BASE_URL = "https://{company}.{instance}.myworkdayjobs.com/{site}"
_JAZZHR_JOBS_URL = "https://{board_token}.applytojob.com/apply/jobs"
# The listing page links to /apply/jobs/details/{id}, but that route serves
# a generic shell page (verified: <title>JazzHR » Job Listings</title>, no
# job-specific content) rather than the real posting. Each job's actual
# canonical page — confirmed via that shell page's own <link rel="canonical">
# — is /apply/{id}/{slug}, and the {slug} part turns out to be cosmetic:
# /apply/{id} alone (no slug) resolves to the same real content.
_JAZZHR_JOB_URL = "https://{board_token}.applytojob.com/apply/{job_id}"
_JAZZHR_JOB_ID_RE = re.compile(r"/apply/jobs/details/([a-zA-Z0-9]+)")
_RECRUITEE_JOBS_URL = "https://{board_token}.recruitee.com/api/offers/"
_BREEZYHR_JOBS_URL = "https://{board_token}.breezy.hr/json"
_WORKABLE_JOBS_URL = "https://apply.workable.com/api/v1/widget/accounts/{board_token}"
# Unlike the other platforms, ADP needs two identifiers, not one — the
# client id (cid) and the career-center id (ccId), both only visible in a
# client's careers URL query string, not a single path segment — encoded as
# "cid/ccId" in board_token, same "/"-joined convention as Workday's
# three-part token.
_ADP_JOBS_URL = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_ADP_JOB_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
    "?cid={cid}&ccId={cc_id}&type=JS&lang=en_US&selectedMenuKey=CareerCenter&jobId={job_id}"
)
# These three are single-company, in-house career sites rather than a
# platform used by many companies, so unlike the ones above there's no
# variable board_token — detect_ats_source always returns a fixed token
# (see _DETECT_PATTERNS) and list_job_urls ignores it.
_AMAZON_JOBS_URL = "https://www.amazon.jobs/en/search.json"
_AMAZON_JOB_BASE_URL = "https://www.amazon.jobs"
_GOOGLE_JOBS_URL = "https://www.google.com/about/careers/applications/jobs/results"
_APPLE_JOBS_URL = "https://jobs.apple.com/en-us/search"
_APPLE_JOB_URL = "https://jobs.apple.com/en-us/details/{position_id}/{slug}"
_APPLE_HYDRATION_RE = re.compile(r'window\.__staticRouterHydrationData = JSON\.parse\("(.*?)"\);', re.DOTALL)
_GOOGLE_JOB_RE = re.compile(r'href="jobs/results/([^"?]+)')

_TIMEOUT = 30.0
# Workday paginates 20 jobs per request; a large company can have 1000+ open
# roles. Cap discovery per crawl rather than fully paginating every run —
# already-known URLs are deduped either way, so a cap just bounds how many
# requests one crawl makes to Workday, not what gets found over time.
_WORKDAY_PAGE_SIZE = 20
_WORKDAY_MAX_JOBS = 200
_AMAZON_PAGE_SIZE = 100
_AMAZON_MAX_JOBS = 200
_APPLE_PAGE_SIZE = 20
_APPLE_MAX_JOBS = 200
# Google has no per-job posting-date field anywhere (listing or detail
# page), unlike Workday/Amazon/Apple, so there's no way to reliably stop at
# "today's postings only" — this just takes the first few pages under
# sort_by=date as a heuristic recent-enough window (already-known URLs are
# deduped either way, same rationale as the safety caps above).
_GOOGLE_PAGE_SIZE = 20
_GOOGLE_MAX_PAGES = 10
_ADP_PAGE_SIZE = 50
# ADP client career sites (one company's own postings) run nowhere near
# Amazon/Google scale — no "today only" early-exit needed, just a safety cap.
_ADP_MAX_JOBS = 500
_EIGHTFOLD_SEARCH_PAGE_SIZE = 20
_EIGHTFOLD_MAX_JOBS = 500
_ORACLE_FUSION_JOBS_URL = "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
_ORACLE_FUSION_JOB_URL = "https://{host}/hcmUI/CandidateExperience/en/sites/{site_number}/job/{job_id}"
_ORACLE_FUSION_PAGE_SIZE = 25
_ORACLE_FUSION_MAX_JOBS = 500
_CLINCH_MAX_JOBS = 500


def list_job_urls(ats_type: str, board_url: str) -> list[str]:
    """Discovery only: return every current job-posting URL for a company's
    board. Field extraction (title, salary, etc.) is left entirely to the
    existing scan pipeline (app.services.job_scanner) once each URL is
    submitted via get_or_create_job_posting — this just finds the URLs.

    Takes the board's canonical URL rather than a pre-extracted token —
    every platform's token is fully recoverable from that URL via
    detect_ats_source, so CrawlSource only needs to persist the URL (see
    canonical_board_url below for the reverse direction). Eightfold is the
    one exception: its tenant "domain" identifier isn't derivable by string
    matching alone, so it resolves its own identifier internally instead
    (see _list_eightfold_jobs).
    """
    if ats_type == AtsType.EIGHTFOLD:
        return _list_eightfold_jobs(board_url)
    if ats_type == AtsType.CLINCH:
        return _list_clinch_jobs(board_url)

    _, board_token = detect_ats_source(board_url)

    if ats_type == AtsType.GREENHOUSE:
        return _list_greenhouse_jobs(board_token)
    if ats_type == AtsType.LEVER:
        return _list_lever_jobs(board_token)
    if ats_type == AtsType.ASHBY:
        return _list_ashby_jobs(board_token)
    if ats_type == AtsType.BAMBOOHR:
        return _list_bamboohr_jobs(board_token)
    if ats_type == AtsType.PERSONIO:
        return _list_personio_jobs(board_token)
    if ats_type == AtsType.WORKDAY:
        return _list_workday_jobs(board_token)
    if ats_type == AtsType.JAZZHR:
        return _list_jazzhr_jobs(board_token)
    if ats_type == AtsType.RECRUITEE:
        return _list_recruitee_jobs(board_token)
    if ats_type == AtsType.BREEZYHR:
        return _list_breezyhr_jobs(board_token)
    if ats_type == AtsType.AMAZON:
        return _list_amazon_jobs(board_token)
    if ats_type == AtsType.GOOGLE:
        return _list_google_jobs(board_token)
    if ats_type == AtsType.APPLE:
        return _list_apple_jobs(board_token)
    if ats_type == AtsType.WORKABLE:
        return _list_workable_jobs(board_token)
    if ats_type == AtsType.ADP:
        return _list_adp_jobs(board_token)
    if ats_type == AtsType.ORACLE_FUSION:
        return _list_oracle_fusion_jobs(board_token)
    raise ValueError(f"Unsupported ats_type: {ats_type!r}")


def _list_greenhouse_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = httpx.get(_GREENHOUSE_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return [job["absolute_url"] for job in jobs if job.get("absolute_url")]


def _list_lever_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = httpx.get(
        _LEVER_JOBS_URL.format(board_token=board_token), params={"mode": "json"}, timeout=_TIMEOUT
    )
    response.raise_for_status()
    postings = response.json()
    return [posting["hostedUrl"] for posting in postings if posting.get("hostedUrl")]


def _list_ashby_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = httpx.get(_ASHBY_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return [job["jobUrl"] for job in jobs if job.get("jobUrl")]


def _list_bamboohr_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required. The list response
    # doesn't include a direct URL, but each posting's page is a predictable
    # /careers/{id} path off the same subdomain.
    response = httpx.get(_BAMBOOHR_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    postings = response.json().get("result", [])
    return [
        _BAMBOOHR_JOB_URL.format(board_token=board_token, job_id=posting["id"])
        for posting in postings
        if posting.get("id")
    ]


def _list_personio_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated XML feed — no key required. Like
    # BambooHR, the feed has no direct URL; each posting's page is a
    # predictable /job/{id} path off the same subdomain.
    response = httpx.get(_PERSONIO_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    return [
        _PERSONIO_JOB_URL.format(board_token=board_token, job_id=job_id)
        for position in root.findall("position")
        if (job_id := position.findtext("id"))
    ]


def _list_workday_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required, but unlike the
    # other platforms Workday needs three identifiers, not one: the company
    # slug, the Workday instance number (e.g. "wd12" — not visible in the
    # careers URL, varies per company), and the career site name. Encoded
    # as "company/instance/site" in board_token (e.g.
    # "salesforce/wd12/External_Career_Site") since CrawlSource only has a
    # single board_token column.
    parts = board_token.split("/")
    if len(parts) != 3:
        raise ValueError(
            f"Workday board_token must be 'company/instance/site' (e.g. 'salesforce/wd12/External_Career_Site'), got {board_token!r}"
        )
    company, instance, site = parts

    jobs_url = _WORKDAY_JOBS_URL.format(company=company, instance=instance, site=site)
    job_base_url = _WORKDAY_JOB_BASE_URL.format(company=company, instance=instance, site=site)

    urls: list[str] = []
    offset = 0
    while len(urls) < _WORKDAY_MAX_JOBS:
        response = httpx.post(
            jobs_url,
            json={"appliedFacets": {}, "limit": _WORKDAY_PAGE_SIZE, "offset": offset, "searchText": ""},
            headers={"Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        postings = response.json().get("jobPostings", [])
        if not postings:
            break

        # Workday's default sort is newest-first (verified: offset=0 was
        # entirely "Posted Today", offset=400 was entirely "Posted 10 Days
        # Ago" — no interleaving). Since we only want today's new postings,
        # stop as soon as a page contains anything older instead of always
        # paginating to _WORKDAY_MAX_JOBS — far fewer requests per crawl,
        # and correct regardless of how many jobs the company has total.
        todays_postings = [p for p in postings if p.get("postedOn") == "Posted Today"]
        urls.extend(
            job_base_url + posting["externalPath"] for posting in todays_postings if posting.get("externalPath")
        )
        if len(todays_postings) < len(postings) or len(postings) < _WORKDAY_PAGE_SIZE:
            break  # hit an older posting, or this was the last page
        offset += _WORKDAY_PAGE_SIZE

    # _WORKDAY_MAX_JOBS is a safety net, not the normal stopping point — it
    # only bites if a company posts an unusually large batch in a single day.
    return urls[:_WORKDAY_MAX_JOBS]


def _list_jazzhr_jobs(board_token: str) -> list[str]:
    # Unlike the other platforms, JazzHR has no public JSON API for listings
    # (their real API requires a per-company key, which we don't have and
    # can't get without that company's cooperation). The only free option is
    # the server-rendered /apply/jobs HTML page, which links to each posting
    # as /apply/jobs/details/{id} — more fragile than a real API contract
    # since it depends on markup that could change, but deterministic today.
    response = httpx.get(_JAZZHR_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    job_ids = dict.fromkeys(_JAZZHR_JOB_ID_RE.findall(response.text))  # dedupe, keep order
    return [_JAZZHR_JOB_URL.format(board_token=board_token, job_id=job_id) for job_id in job_ids]


def _list_recruitee_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = httpx.get(_RECRUITEE_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    offers = response.json().get("offers", [])
    return [offer["careers_url"] for offer in offers if offer.get("careers_url")]


def _list_breezyhr_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required.
    response = httpx.get(_BREEZYHR_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    postings = response.json()
    return [posting["url"] for posting in postings if posting.get("url")]


def _list_workable_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required (the same feed
    # that powers Workable's embeddable "jobs widget").
    response = httpx.get(_WORKABLE_JOBS_URL.format(board_token=board_token), timeout=_TIMEOUT)
    response.raise_for_status()
    jobs = response.json().get("jobs", [])
    return [job["url"] for job in jobs if job.get("url")]


def _list_adp_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required (the same feed
    # ADP's own JS-rendered career-center page calls). Each requisition
    # buries its externally-visible job id inside a generic key/value bag
    # (customFieldGroup.stringFields) rather than a top-level field.
    parts = board_token.split("/")
    if len(parts) != 2:
        raise ValueError(f"ADP board_token must be 'cid/ccId', got {board_token!r}")
    cid, cc_id = parts

    urls: list[str] = []
    skip = 0
    while len(urls) < _ADP_MAX_JOBS:
        response = httpx.get(
            _ADP_JOBS_URL,
            params={
                "cid": cid,
                "ccId": cc_id,
                "lang": "en_US",
                "locale": "en_US",
                "$top": _ADP_PAGE_SIZE,
                "$skip": skip,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        requisitions = response.json().get("jobRequisitions", [])
        if not requisitions:
            break
        for req in requisitions:
            job_id = next(
                (
                    field["stringValue"]
                    for field in req.get("customFieldGroup", {}).get("stringFields", [])
                    if field.get("nameCode", {}).get("codeValue") == "ExternalJobID"
                ),
                None,
            )
            if job_id:
                urls.append(_ADP_JOB_URL.format(cid=cid, cc_id=cc_id, job_id=job_id))
        if len(requisitions) < _ADP_PAGE_SIZE:
            break
        skip += _ADP_PAGE_SIZE

    return urls[:_ADP_MAX_JOBS]


def _list_oracle_fusion_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated REST API — the same one Oracle's own
    # candidate-experience UI calls client-side, no key required. Like
    # Workday, needs two identifiers, not one: the tenant host (e.g.
    # eeho.fa.us2.oraclecloud.com) and the site number (a company can run
    # multiple career sites off the same host), encoded as "host/site" in
    # board_token. PostedDate is a per-job field (verified sortBy=
    # POSTING_DATES_DESC returns newest first), so this uses the same
    # "today only" early-exit as Workday/Amazon/Apple.
    host, _, site_number = board_token.partition("/")
    if not site_number:
        raise ValueError(f"Oracle Fusion board_token must be 'host/site_number', got {board_token!r}")

    urls: list[str] = []
    offset = 0
    today = datetime.now(timezone.utc).date()
    while len(urls) < _ORACLE_FUSION_MAX_JOBS:
        response = httpx.get(
            _ORACLE_FUSION_JOBS_URL.format(host=host),
            params={
                "onlyData": "true",
                "expand": "requisitionList",
                "finder": (
                    f"findReqs;siteNumber={site_number},limit={_ORACLE_FUSION_PAGE_SIZE},"
                    f"offset={offset},sortBy=POSTING_DATES_DESC"
                ),
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        requisitions = items[0].get("requisitionList", []) if items else []
        if not requisitions:
            break

        todays = [r for r in requisitions if r.get("PostedDate") == today.isoformat()]
        urls.extend(
            _ORACLE_FUSION_JOB_URL.format(host=host, site_number=site_number, job_id=r["Id"])
            for r in todays
            if r.get("Id")
        )
        if len(todays) < len(requisitions) or len(requisitions) < _ORACLE_FUSION_PAGE_SIZE:
            break
        offset += _ORACLE_FUSION_PAGE_SIZE

    return urls[:_ORACLE_FUSION_MAX_JOBS]


def _list_clinch_jobs(board_url: str) -> list[str]:
    # No public jobs API, but Clinch (a white-label career-site CMS —
    # board_token is the tenant's own domain, there's no shared
    # clinch.io host to point at) publishes a standard sitemap.xml that
    # cleanly separates job postings (/jobs/{slug}) from marketing/blog
    # pages (verified against a live instance). Small volume in practice
    # (~100 jobs), so no "today only" filtering — just a safety cap like
    # every other adapter's _MAX_JOBS.
    host = urlsplit(board_url).netloc
    response = httpx.get(f"https://{host}/sitemap.xml", timeout=_TIMEOUT)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [
        loc.text
        for loc in root.findall(".//sm:loc", ns)
        if loc.text and urlsplit(loc.text).path.startswith("/jobs/")
    ]
    return urls[:_CLINCH_MAX_JOBS]


def _eightfold_domain_is_valid(host: str, domain: str) -> bool:
    # A wrong guess fails outright (e.g. Netflix's 403 "PCSX is not enabled
    # for this user") rather than succeeding with an empty result, so this
    # can't mistake "no open roles right now" for "wrong domain".
    try:
        response = httpx.get(
            f"https://{host}/api/pcsx/search", params={"domain": domain, "start": 0, "num": 1}, timeout=_TIMEOUT
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


def _list_eightfold_jobs(board_url: str) -> list[str]:
    # Free, public API — no key required, but only when the company hasn't
    # disabled it on their instance (Netflix returns 403 "PCSX is not
    # enabled for this user" on the very same endpoint that works fine for
    # Twilio; nothing on our end to do about that). Two identifiers, not
    # one: the host actually serving the career site (e.g. jobs.twilio.com
    # — every company's own domain, not a shared one, recovered directly
    # from board_url) and the "domain" value that host's API expects to
    # identify the tenant (usually, but not always, the host's registrable
    # domain) — not derivable from the URL alone, so it's cheaply
    # re-resolved on every crawl via the same candidate-guessing
    # detect_embedded_ats_source uses at discovery time, rather than
    # cached anywhere.
    host = urlsplit(board_url).netloc
    domain = next((d for d in _candidate_eightfold_domains(host) if _eightfold_domain_is_valid(host, d)), None)
    if domain is None:
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
            timeout=_TIMEOUT,
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


def _parse_month_day_year(raw: str | None, *, month_style: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(" ".join(raw.split()), month_style).date()
    except ValueError:
        return None


def _list_amazon_jobs(board_token: str) -> list[str]:
    # Free, public, unauthenticated API — no key required. sort=recent
    # verified newest-first (offset=0 was entirely today's date, offset=200
    # was already yesterday's) — same "today only" early-exit as Workday,
    # since Amazon has 10,000+ open roles.
    urls: list[str] = []
    offset = 0
    today = datetime.now(timezone.utc).date()
    while len(urls) < _AMAZON_MAX_JOBS:
        response = httpx.get(
            _AMAZON_JOBS_URL,
            params={"result_limit": _AMAZON_PAGE_SIZE, "offset": offset, "sort": "recent"},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
        if not jobs:
            break

        todays_jobs = [
            job for job in jobs if _parse_month_day_year(job.get("posted_date"), month_style="%B %d, %Y") == today
        ]
        urls.extend(_AMAZON_JOB_BASE_URL + job["job_path"] for job in todays_jobs if job.get("job_path"))
        if len(todays_jobs) < len(jobs) or len(jobs) < _AMAZON_PAGE_SIZE:
            break
        offset += _AMAZON_PAGE_SIZE

    return urls[:_AMAZON_MAX_JOBS]


def _list_google_jobs(board_token: str) -> list[str]:
    # No public API — the search-results page server-renders real job links
    # directly into static HTML (no JS execution needed), verified against
    # 3362 live postings, 20 per page. sort_by=date changes result order
    # (verified against the unsorted default) but there's no per-job date
    # anywhere to confirm it's a true chronological cutoff — see
    # _GOOGLE_MAX_PAGES above for why this only takes a bounded number of
    # pages rather than doing a Workday-style exact "today only" stop.
    urls: list[str] = []
    for page in range(1, _GOOGLE_MAX_PAGES + 1):
        response = httpx.get(_GOOGLE_JOBS_URL, params={"page": page, "sort_by": "date"}, timeout=_TIMEOUT)
        response.raise_for_status()
        job_paths = dict.fromkeys(_GOOGLE_JOB_RE.findall(response.text))  # dedupe, keep order
        if not job_paths:
            break
        urls.extend(f"{_GOOGLE_JOBS_URL}/{path}" for path in job_paths)
        if len(job_paths) < _GOOGLE_PAGE_SIZE:
            break
    return urls


def _list_apple_jobs(board_token: str) -> list[str]:
    # No public API — the search page server-renders full job data into a
    # `window.__staticRouterHydrationData = JSON.parse("...")` blob (a React
    # Router hydration payload): a double-escaped JSON string, more fragile
    # than a real endpoint (same caveat as JazzHR) but the data itself is
    # clean structured JSON, not raw HTML to regex-scrape. sort=newest
    # verified newest-first (page 1 was entirely today's postingDate, page
    # 10 was already yesterday's), so the same "today only" early-exit as
    # Workday/Amazon applies — using postingDate (a stable per-job date),
    # NOT postDateInGMT, which turned out to change on every request for
    # the same job (a live response timestamp, not a stored value).
    urls: list[str] = []
    today = datetime.now(timezone.utc).date()
    page = 1
    while len(urls) < _APPLE_MAX_JOBS:
        response = httpx.get(_APPLE_JOBS_URL, params={"sort": "newest", "page": page}, timeout=_TIMEOUT)
        response.raise_for_status()
        match = _APPLE_HYDRATION_RE.search(response.text)
        if not match:
            break
        # The blob is a JS string literal fed to JSON.parse — re-wrapping it
        # in quotes and parsing through json.loads (rather than the
        # `unicode_escape` codec) unescapes it correctly without mangling
        # any real non-ASCII characters already in the text.
        search_data = json.loads(json.loads('"' + match.group(1) + '"'))["loaderData"]["search"]
        postings = search_data.get("searchResults", [])
        if not postings:
            break

        todays_postings = [
            posting for posting in postings if _parse_month_day_year(posting.get("postingDate"), month_style="%b %d, %Y") == today
        ]
        urls.extend(
            _APPLE_JOB_URL.format(position_id=posting["positionId"], slug=posting["transformedPostingTitle"])
            for posting in todays_postings
            if posting.get("positionId") and posting.get("transformedPostingTitle")
        )
        if len(todays_postings) < len(postings) or len(postings) < _APPLE_PAGE_SIZE:
            break
        page += 1

    return urls[:_APPLE_MAX_JOBS]


# Each platform's career-page/board URL has a predictable, detectable shape
# (verified against real companies while building each adapter above), so a
# user can paste any careers URL for a board instead of having to already
# know its ats_type/board_token split. Order doesn't matter — each pattern
# matches a distinct domain suffix. Workday is the one exception: its token
# has three parts, all of which are only visible in the *board* URL (e.g.
# https://salesforce.wd12.myworkdayjobs.com/External_Career_Site) — a
# specific job URL under that board works too, since the same three parts
# still appear in its path.
_DETECT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (AtsType.GREENHOUSE, re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/?]+)", re.IGNORECASE)),
    (AtsType.LEVER, re.compile(r"jobs\.lever\.co/([^/?]+)", re.IGNORECASE)),
    (AtsType.ASHBY, re.compile(r"jobs\.ashbyhq\.com/([^/?]+)", re.IGNORECASE)),
    (AtsType.BAMBOOHR, re.compile(r"([a-zA-Z0-9-]+)\.bamboohr\.com", re.IGNORECASE)),
    (AtsType.PERSONIO, re.compile(r"([a-zA-Z0-9-]+)\.jobs\.personio\.de", re.IGNORECASE)),
    (AtsType.JAZZHR, re.compile(r"([a-zA-Z0-9-]+)\.applytojob\.com", re.IGNORECASE)),
    (AtsType.RECRUITEE, re.compile(r"([a-zA-Z0-9-]+)\.recruitee\.com", re.IGNORECASE)),
    (AtsType.BREEZYHR, re.compile(r"([a-zA-Z0-9-]+)\.breezy\.hr", re.IGNORECASE)),
    # Requires the account-prefixed URL shape (apply.workable.com/{account}/j/...,
    # e.g. as linked from a LinkedIn "Apply" redirect) — the bare shortlink
    # form (apply.workable.com/j/{code}) resolves to the same job but doesn't
    # carry the account slug this needs, so it just won't match.
    (AtsType.WORKABLE, re.compile(r"apply\.workable\.com/([^/?]+)/j/", re.IGNORECASE)),
    # The bare board page (no specific job), e.g. as stored in
    # CrawlSource.board_url — (?!j/) excludes the ambiguous shortlink shape
    # above, which the pattern above already claims.
    (AtsType.WORKABLE, re.compile(r"apply\.workable\.com/(?!j/)([^/?]+)/?(?:\?|$)", re.IGNORECASE)),
]
# These three are single-company sites, so there's no variable token to
# capture from the URL — the pattern just recognizes the domain, and the
# fixed string in each tuple is used as-is instead of a regex group.
_DETECT_FIXED_TOKEN_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    (AtsType.AMAZON, "amazon", re.compile(r"amazon\.jobs", re.IGNORECASE)),
    (AtsType.GOOGLE, "google", re.compile(r"google\.com/about/careers", re.IGNORECASE)),
    (AtsType.APPLE, "apple", re.compile(r"jobs\.apple\.com", re.IGNORECASE)),
]
_DETECT_WORKDAY_RE = re.compile(
    r"([a-zA-Z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?]+)", re.IGNORECASE
)
# ADP's two-part token (cid, ccId) lives in the query string, not the path,
# and query param order isn't guaranteed — a positional regex can't pull
# both out reliably, so this only recognizes the domain and detect_ats_source
# parses the query string properly below.
_DETECT_ADP_RE = re.compile(r"workforcenow\.adp\.com", re.IGNORECASE)
# Oracle Fusion Recruiting Cloud's two-part token (tenant host, site number)
# both live in the path, so unlike ADP a single regex recovers both —
# .search() rather than a full match, so this matches straight out of a
# full job-posting URL (.../sites/{site}/job/{id}) as readily as a bare
# board URL.
_DETECT_ORACLE_FUSION_RE = re.compile(
    r"([a-zA-Z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([^/]+)", re.IGNORECASE
)
# Some companies white-label Greenhouse onto their own domain (e.g.
# harness.io/company/jobs/apply?gh_jid=...) via Greenhouse's embeddable JS
# widget rather than linking out to boards.greenhouse.io directly — the
# board token isn't visible in the URL at all in that case (only a numeric
# job id, in gh_jid), so _DETECT_PATTERNS above can never match it.
# Greenhouse ships more than one embed variant with the same `?for=`
# token — job_board/js (harness.io) and job_app (instacart.careers) both
# verified live — so this matches any `embed/.../...?for=` shape rather
# than one specific script path.
_GH_EMBED_TOKEN_RE = re.compile(r"greenhouse\.io/embed/[a-zA-Z_/]*\?for=([a-zA-Z0-9_-]+)", re.IGNORECASE)
# Some sites instead fetch the job from Greenhouse's API server-side and
# stitch it into their own page (e.g. coalitioninc.com's Next.js-rendered
# job pages) — no embed script appears anywhere in the HTML, but gh_jid
# still leaks through wherever that fetched job data gets serialized into
# the page (its own absolute_url field, in Coalition's case) — verified
# live. Used as a fallback below when the embed-script check above misses.
_GH_EMBED_JOB_ID_RE = re.compile(r"gh_jid=(\d+)")
# Ashby's white-label embed (e.g. anrok.com/careers?ashby_jid=...) has no
# static equivalent of Greenhouse's embed-script token — verified against a
# real instance (superhuman.com) that the board name only ever appears in a
# client-side-built iframe src, nowhere in the page's initial HTML or
# Next.js props. Two-tier recovery: many companies just register their
# Ashby board under their own plain company name, so first guess a handful
# of candidate slugs from the domain (free, no browser needed) and confirm
# one by checking the known job id actually appears in that board's public
# listing — wrong 100% harmlessly (tries the next candidate), right often
# enough to be worth it (verified against anrok.com, board name literally
# "anrok"). Only when every guess misses (e.g. superhuman.com's real board
# name, "Superhuman Platform Inc", isn't derivable from the domain at all)
# does this fall back to actually rendering the page and reading the iframe
# Ashby itself built, which is authoritative but far more expensive.
_ASHBY_EMBED_JOB_ID_RE = re.compile(r"ashby_jid=([a-zA-Z0-9-]+)")
_ASHBY_IFRAME_SRC_RE = re.compile(r'jobs\.ashbyhq\.com/([^/?"\']+)', re.IGNORECASE)
# Eightfold's white-label career sites (e.g. jobs.twilio.com,
# explore.jobs.netflix.net) all serve from a `/careers/job/{numeric_id}`
# path and share a distinctive, stable string in their page HTML even
# though the listing itself renders via JS — verified against two live
# instances. Unlike Greenhouse/Ashby's embeds, this one carries no board
# token in the URL *or* the page at all: the API just wants the tenant's
# own "domain" value, usually (not always — Netflix's host ends .net but
# its domain value would be .com) the requesting host's registrable
# domain, so the same guess-and-verify approach as Ashby applies, keyed off
# the known job id from the URL path instead of a query param.
_EIGHTFOLD_JOB_URL_RE = re.compile(r"/careers/job/(\d+)")
_EIGHTFOLD_SIGNATURE = "eightfold.ai/privacy-policy"


def detect_ats_source(url: str) -> tuple[str, str]:
    """Given any careers/job URL, return (ats_type, board_token) by matching
    it against each platform's known URL shape. Raises ValueError if the URL
    doesn't match a supported platform — the caller can fall back to asking
    for ats_type/board_token explicitly in that case.
    """
    workday_match = _DETECT_WORKDAY_RE.search(url)
    if workday_match:
        company, instance, site = workday_match.groups()
        return AtsType.WORKDAY, f"{company}/{instance}/{site}"

    if _DETECT_ADP_RE.search(url):
        query = parse_qs(urlsplit(url).query)
        cid = query.get("cid", [None])[0]
        cc_id = query.get("ccId", [None])[0]
        if cid and cc_id:
            return AtsType.ADP, f"{cid}/{cc_id}"

    oracle_match = _DETECT_ORACLE_FUSION_RE.search(url)
    if oracle_match:
        host, site_number = oracle_match.groups()
        return AtsType.ORACLE_FUSION, f"{host}/{site_number}"

    for ats_type, fixed_token, pattern in _DETECT_FIXED_TOKEN_PATTERNS:
        if pattern.search(url):
            return ats_type, fixed_token

    for ats_type, pattern in _DETECT_PATTERNS:
        match = pattern.search(url)
        if match:
            return ats_type, match.group(1)

    raise ValueError(
        f"Couldn't detect a supported ATS from url={url!r}. "
        "Provide ats_type and board_token explicitly instead."
    )


# The inverse of detect_ats_source for every platform whose token is a plain
# string substitution into a known URL shape — used by canonical_board_url
# below. Workday, ADP, and Eightfold aren't here since their tokens need
# more than one placeholder filled from a "/"-joined value (or, for
# Eightfold, aren't part of a canonical URL at all — see canonical_board_url).
_CANONICAL_BOARD_URL_TEMPLATES: dict[str, str] = {
    AtsType.GREENHOUSE: "https://boards.greenhouse.io/{token}",
    AtsType.LEVER: "https://jobs.lever.co/{token}",
    AtsType.ASHBY: "https://jobs.ashbyhq.com/{token}",
    AtsType.BAMBOOHR: "https://{token}.bamboohr.com/careers",
    AtsType.PERSONIO: "https://{token}.jobs.personio.de/",
    AtsType.JAZZHR: "https://{token}.applytojob.com/apply/jobs",
    AtsType.RECRUITEE: "https://{token}.recruitee.com/",
    AtsType.BREEZYHR: "https://{token}.breezy.hr/",
    AtsType.WORKABLE: "https://apply.workable.com/{token}/",
    AtsType.AMAZON: "https://www.amazon.jobs",
    AtsType.GOOGLE: "https://www.google.com/about/careers",
    AtsType.APPLE: "https://jobs.apple.com",
}


def canonical_board_url(ats_type: str, board_token: str) -> str:
    """The canonical, human-visitable board URL for (ats_type, board_token)
    — the inverse of detect_ats_source for every platform where the token
    is purely string-derivable. Used to compute CrawlSource.board_url (both
    going forward, in register_discovered_board, and for the one-time
    migration backfilling existing rows) so it always round-trips back
    through detect_ats_source / list_job_urls.

    Eightfold has no such canonical URL — the company's own career-site
    host *is* the board, there's no separate ATS-hosted page to point at —
    so board_token's host segment is used as-is; see _list_eightfold_jobs
    for how the rest of the identifier gets (re-)resolved from that host.
    """
    if ats_type == AtsType.WORKDAY:
        company, instance, site = board_token.split("/")
        return _WORKDAY_JOB_BASE_URL.format(company=company, instance=instance, site=site)
    if ats_type == AtsType.ADP:
        cid, cc_id = board_token.split("/")
        return (
            "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
            f"?cid={cid}&ccId={cc_id}"
        )
    if ats_type == AtsType.EIGHTFOLD:
        host, _, _domain = board_token.partition("/")
        return f"https://{host}"

    template = _CANONICAL_BOARD_URL_TEMPLATES.get(ats_type)
    if template is None:
        raise ValueError(f"No canonical board URL for ats_type={ats_type!r}")
    return template.format(token=board_token)


_COMPANY_SUFFIXES = ("incorporated", "corp", "inc", "llc", "ltd", "group", "co")


def _candidate_slugs_from_domain(domain: str) -> list[str]:
    """A handful of plausible board-name guesses from a company's domain,
    cheapest/most-likely first — e.g. "www.anrok.com" -> ["anrok"]. Also
    strips a trailing corporate suffix as a second guess (verified against
    a real board: "coalitioninc.com" -> "coalition") — wrong guesses just
    fail the caller's verification step harmlessly, same as any other
    candidate here.
    """
    label = domain.lower().removeprefix("www.").split(".")[0]
    candidates = [label]
    for suffix in _COMPANY_SUFFIXES:
        if label.endswith(suffix) and len(label) > len(suffix):
            candidates.append(label[: -len(suffix)])
    return list(dict.fromkeys(candidates))  # dedupe, keep order


def _candidate_eightfold_domains(host: str) -> list[str]:
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


def _greenhouse_board_has_job(slug: str, job_id: str) -> bool:
    try:
        response = httpx.get(f"{_GREENHOUSE_JOBS_URL.format(board_token=slug)}/{job_id}", timeout=_TIMEOUT)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _detect_embedded_greenhouse(url: str) -> tuple[str, str] | None:
    try:
        response = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    # Fast path: the embeddable-widget script names the board directly —
    # authoritative, no guessing needed.
    match = _GH_EMBED_TOKEN_RE.search(response.text)
    if match:
        return AtsType.GREENHOUSE, match.group(1)

    # Fallback: no embed script, but gh_jid leaked through somewhere in the
    # page anyway (see _GH_EMBED_JOB_ID_RE above) — guess-and-verify a board
    # slug from the domain, same two-tier pattern as Ashby's embed detector.
    job_id_match = _GH_EMBED_JOB_ID_RE.search(response.text)
    if not job_id_match:
        return None
    job_id = job_id_match.group(1)
    domain = urlsplit(url).netloc
    for slug in _candidate_slugs_from_domain(domain):
        if _greenhouse_board_has_job(slug, job_id):
            return AtsType.GREENHOUSE, slug
    return None


def _ashby_board_has_job(slug: str, job_id: str) -> bool:
    try:
        response = httpx.get(_ASHBY_JOBS_URL.format(board_token=quote(slug, safe="")), timeout=_TIMEOUT)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    jobs = response.json().get("jobs", [])
    return any(job.get("id") == job_id for job in jobs)


def _detect_embedded_ashby(url: str) -> tuple[str, str] | None:
    match = _ASHBY_EMBED_JOB_ID_RE.search(url)
    if not match:
        return None
    job_id = match.group(1)
    domain = urlsplit(url).netloc
    for slug in _candidate_slugs_from_domain(domain):
        if _ashby_board_has_job(slug, job_id):
            return AtsType.ASHBY, slug

    # Every free guess missed — actually render the page and read the
    # board name out of the iframe src Ashby's own embed script builds.
    # Still verified against the known job id before trusting it, same as
    # the guesses above: a rendered page could theoretically show a stale
    # cached iframe from a previous job's board name changes.
    html = fetch_rendered_html(url)
    if html is None:
        return None
    iframe_match = _ASHBY_IFRAME_SRC_RE.search(html)
    if not iframe_match:
        return None
    slug = unquote(iframe_match.group(1))
    if _ashby_board_has_job(slug, job_id):
        return AtsType.ASHBY, slug
    return None


def _detect_embedded_eightfold(url: str) -> tuple[str, str] | None:
    job_match = _EIGHTFOLD_JOB_URL_RE.search(url)
    if not job_match:
        return None
    job_id = job_match.group(1)
    host = urlsplit(url).netloc
    try:
        response = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _EIGHTFOLD_SIGNATURE not in response.text:
        return None

    for domain in _candidate_eightfold_domains(host):
        try:
            detail = httpx.get(
                f"https://{host}/api/pcsx/position_details",
                params={"position_id": job_id, "domain": domain},
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError:
            continue
        if detail.status_code != 200:
            continue
        data = detail.json().get("data")
        if isinstance(data, dict) and str(data.get("id")) == job_id:
            return AtsType.EIGHTFOLD, f"{host}/{domain}"
    return None


_CLINCH_SIGNATURE = "clinchtalent.com"


def _detect_embedded_clinch(url: str) -> tuple[str, str] | None:
    try:
        response = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if _CLINCH_SIGNATURE not in response.text:
        return None
    return AtsType.CLINCH, urlsplit(str(response.url)).netloc


# Workable's short link form (apply.workable.com/j/{code}) carries no
# account slug, unlike the account-prefixed form _DETECT_PATTERNS above
# matches — but it 301-redirects to that same account-prefixed URL
# (verified live), so following the redirect and re-running the normal
# Workable pattern against the resolved URL recovers the account slug.
_WORKABLE_SHORTLINK_RE = re.compile(r"apply\.workable\.com/j/[a-zA-Z0-9]+", re.IGNORECASE)


def _detect_workable_shortlink(url: str) -> tuple[str, str] | None:
    if not _WORKABLE_SHORTLINK_RE.search(url):
        return None
    try:
        response = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    for ats_type, pattern in _DETECT_PATTERNS:
        if ats_type != AtsType.WORKABLE:
            continue
        match = pattern.search(str(response.url))
        if match:
            return AtsType.WORKABLE, match.group(1)
    return None


_EMBEDDED_DETECTORS = [
    _detect_embedded_greenhouse,
    _detect_embedded_ashby,
    _detect_embedded_eightfold,
    _detect_embedded_clinch,
    _detect_workable_shortlink,
]


def detect_embedded_ats_source(url: str) -> tuple[str, str] | None:
    """Best-effort fallback for when detect_ats_source's pure string match
    fails: some companies white-label an ATS's embeddable widget onto their
    own domain instead of linking out to the ATS's own hosted board, so the
    board token isn't visible in the URL string alone — recovering it takes
    real network I/O (fetching the page, sometimes probing a guessed token
    against the ATS's own API), unlike every pure-string check above.
    Never raises — this is a fallback for a ValueError, so a fetch failure
    here must not break the job submission it's trying to enrich; caller
    treats None as "still couldn't detect."
    """
    for detector in _EMBEDDED_DETECTORS:
        result = detector(url)
        if result is not None:
            return result
    return None
