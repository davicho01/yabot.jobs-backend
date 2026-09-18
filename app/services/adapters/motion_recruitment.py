import re

from app.models.enums import AtsType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, get_with_retry, limit_job_urls

# Motion Recruitment's own site (motionrecruitment.com), not a multi-tenant
# platform other companies point at — same single-company shape as
# fullstack.py. It's built on Bullhorn's staffing CRM under the hood
# (embedded job records carry Bullhorn's own field names/shapes —
# "instance": "agency", owner/categories/employmentType.originalName, etc. —
# verified live), but that's an implementation detail of their own
# Next.js site, not a shared hosted board URL like Greenhouse/Lever.
_COMPANY_NAME = "Motion Recruitment"
_MOTION_URL_RE = re.compile(r"motionrecruitment\.com", re.IGNORECASE)
# Every job listing page (server component, no client-side fetch needed)
# embeds up to `count` full job records as JSON directly in the initial
# HTML response via Next.js's RSC payload — each carrying its own
# absolute, canonical `url`. /tech-jobs with no category slug is the
# unfiltered "all open roles" listing (verified live: 910 total vs. a few
# dozen under any single /tech-jobs/<discipline> category page).
_LISTING_URL = "https://motionrecruitment.com/tech-jobs"
_PAGE_SIZE = 100
_JOB_URL_RE = re.compile(r'\\"url\\":\\"(https://motionrecruitment\.com/tech-jobs/[^\\"]*?/\d+)\\"')


def _match(url: str) -> str | None:
    return "motionrecruitment" if _MOTION_URL_RE.search(url) else None


def _fetch_jobs(_board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    urls: list[str] = []
    start = 0
    while len(urls) < DEFAULT_MAX_JOBS_PER_CRAWL:
        response = get_with_retry(_LISTING_URL, params={"start": start, "count": _PAGE_SIZE}, timeout=TIMEOUT)
        response.raise_for_status()
        page_urls = _JOB_URL_RE.findall(response.text)
        if not page_urls:
            break
        urls.extend(page_urls)
        if len(page_urls) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE
    return limit_job_urls(urls)


# No adapter-specific extract()/scan_job_url — each job page carries a
# complete, standard schema.org JobPosting JSON-LD block (title,
# description, datePosted, jobLocation, employmentType, baseSalary all
# present — verified live), so job_scanner.py's generic JSON-LD default
# scanner already covers this without any Motion-specific field mapping.
ADAPTER = AtsAdapter(
    AtsType.MOTION_RECRUITMENT,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://motionrecruitment.com/tech-jobs",
)
