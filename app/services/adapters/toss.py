import re
from typing import Any

import httpx

from app.models.enums import AtsType, EmploymentType
from app.services.adapters.base import DEFAULT_MAX_JOBS_PER_CRAWL, TIMEOUT, AtsAdapter, ScanResult, get_with_retry
from app.services.adapters.text import clean_text

# Toss's own public API mirrors a Greenhouse-backed job board server-side
# (verified live: each job's absolute_url is
# https://toss.im/career/job-detail?gh_jid={id}, and every posting carries
# Greenhouse-specific fields like data_compliance/internal_job_id/metadata)
# but boards-api.greenhouse.io's public candidate API 404s for every
# plausible board-token guess (toss, vivarepublica, tossbank, ...) — Toss's
# own tenant has that public endpoint disabled and instead proxies through
# this API, which is itself public/unauthenticated and returns every field
# we need directly, so there's no board key to resolve at all.
_TOSS_JOB_GROUPS_URL = "https://api-public.toss.im/api/v3/ipd-eggnog/career/job-groups"
_TOSS_URL_RE = re.compile(r"toss\.im", re.IGNORECASE)
_TOSS_MAX_JOBS = DEFAULT_MAX_JOBS_PER_CRAWL
_JOB_ID_RE = re.compile(r"gh_jid=(\d+)")

# Toss Corp ("토스커뮤니티") operates several subsidiaries under this one
# board, selected per-posting via a custom Greenhouse metadata field —
# verified live: every value seen across the live feed is one of these 8.
_SUBSIDIARY_NAMES = {
    "토스": "Toss",
    "토스커뮤니티": "Toss",
    "토스페이먼츠": "Toss Payments",
    "토스씨엑스": "Toss CX",
    "토스인슈어런스": "Toss Insurance",
    "토스증권": "Toss Securities",
    "토스플레이스": "Toss Place",
    "토스뱅크": "Toss Bank",
}

_EMPLOYMENT_TYPE_MAP_KO = {
    "정규직": EmploymentType.FULL_TIME,
    "계약직": EmploymentType.CONTRACT,
    "인턴": EmploymentType.INTERNSHIP,
    "파트타임": EmploymentType.PART_TIME,
}


def _match(url: str) -> str | None:
    return "toss" if _TOSS_URL_RE.search(url) else None


def _fetch_job_groups() -> list[dict[str, Any]]:
    response = get_with_retry(_TOSS_JOB_GROUPS_URL, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json().get("success") or []


def _fetch_jobs(board_key: str) -> list[str]:  # noqa: ARG001 - single-company board, fixed key, no key needed
    groups = _fetch_job_groups()
    urls = [
        url
        for group in groups
        if (url := (group.get("primary_job") or {}).get("absolute_url"))
    ]
    return urls[:_TOSS_MAX_JOBS]


# Metadata field *names* are the custom Greenhouse question text an admin
# wrote (Korean, with an English word or two embedded) rather than a stable
# machine key — matched by substring/exact-value rather than assuming a
# fixed key, same reasoning as job_ld's loose field matching elsewhere.
def _metadata_value(metadata: list[dict[str, Any]], *, contains: str | None = None, equals: str | None = None) -> Any:
    for field in metadata:
        name = field.get("name") or ""
        if (contains and contains in name) or (equals is not None and name == equals):
            return field.get("value")
    return None


def scan_job_url(url: str) -> ScanResult | None:
    match = _JOB_ID_RE.search(url) if _TOSS_URL_RE.search(url) else None
    if match is None:
        return None
    job_id = match.group(1)
    try:
        groups = _fetch_job_groups()
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    group = next((g for g in groups if str(g.get("id")) == job_id), None)
    if group is None:
        return ScanResult(success=False, error=f"Job {job_id} not found in Toss's current job-groups feed")

    primary = group.get("primary_job") or {}
    metadata = primary.get("metadata") or []
    description = _metadata_value(metadata, contains="Job Description")
    subsidiary = _metadata_value(metadata, contains="소속 자회사")
    employment_type_raw = _metadata_value(metadata, equals="Employment_Type")
    location = (primary.get("location") or {}).get("name")

    return ScanResult(
        success=True,
        title=clean_text(group.get("title")),
        description=description.strip() if isinstance(description, str) and description.strip() else None,
        company_name=_SUBSIDIARY_NAMES.get(subsidiary, "Toss") if isinstance(subsidiary, str) else "Toss",
        location=clean_text(location) if isinstance(location, str) else None,
        employment_type=_EMPLOYMENT_TYPE_MAP_KO.get(employment_type_raw, EmploymentType.UNKNOWN)
        if isinstance(employment_type_raw, str)
        else EmploymentType.UNKNOWN,
    )


ADAPTER = AtsAdapter(
    AtsType.TOSS,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda _key: "https://toss.im/career/jobs",
    scan_job_url=scan_job_url,
)
