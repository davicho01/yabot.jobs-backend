import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import ADAPTERS, base
from app.services.adapters.base import ScanResult
from app.services.adapters.text import clean_text as _clean_text
from app.services.adapters.text import html_to_formatted_text as _html_to_formatted_text

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"gclid", "fbclid", "ref", "igshid"}


def normalize_url(raw_url: str) -> str:
    """Canonicalize a URL so trivial variants (tracking params, fragment,
    host casing) dedupe to the same JobPostingUrl row.
    """
    parts = urlsplit(raw_url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"

    kept_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    kept_params.sort()
    query = urlencode(kept_params)

    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode()).hexdigest()


def domain_of(normalized_url: str) -> str:
    return urlsplit(normalized_url).netloc


def scan_job_url(url: str) -> ScanResult:
    """Fetch a job posting page and pull structured job info out of it.

    Tries each registered ATS adapter's own scan_job_url in turn (see
    app.services.adapters.*) — the first one that claims this URL owns the
    *entire* result verbatim, no merging fields across platforms. A URL no
    adapter claims (most hosted boards already publish full schema.org
    JobPosting JSON-LD, so they don't need platform-specific scan logic at
    all — Lever, Ashby, BambooHR, ...) falls through to the generic
    JSON-LD/Open-Graph default below.
    """
    for adapter in ADAPTERS:
        if adapter.scan_job_url is None:
            continue
        result = adapter.scan_job_url(url)
        if result is not None:
            return result
    return _default_scan_job_url(url)


def _default_scan_job_url(url: str) -> ScanResult:
    """Generic scanner for any URL no platform-specific adapter claims:
    schema.org JobPosting JSON-LD when present (LinkedIn, Indeed, and most
    ATS-hosted listings publish it), page microdata when it's published
    that way instead (e.g. SmartRecruiters), otherwise a plain <title>/
    meta-description/Open-Graph baseline so the submit-a-URL flow still
    works end-to-end. Deliberately platform-agnostic — never imports or
    calls into any app.services.adapters.* module.
    """
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    job_postings = base.extract_json_ld_postings(html)
    job_ld = job_postings[0] if job_postings else None
    if job_ld is None:
        # Some ATS pages (e.g. SmartRecruiters) publish structured job data
        # as schema.org microdata attributes on the visible page instead of
        # an application/ld+json block — same shape, different source.
        job_ld = base.extract_microdata_posting(html)

    if job_ld is not None and base.job_ld_is_expired(job_ld):
        # e.g. a SmartRecruiters posting past its validThrough date: the
        # title/location/company microdata is still on the page, but the
        # real content is replaced with "This job has expired" and a
        # disabled apply button. Reporting that as a successful scan with
        # an empty description would look like our own extraction broke,
        # not like the posting itself is gone — same treatment Greenhouse's
        # is_error_redirect gives a removed/filled posting.
        return ScanResult(success=False, error=f"Job posting has expired (validThrough {job_ld['validThrough']}).")

    fallback_title = base.fallback_title(html)
    fallback_description = base.fallback_description(html)

    if job_ld is None:
        # No JSON-LD and no standard meta description (e.g. Greenhouse's
        # application-form pages) — Open Graph tags are usually the next
        # best source: og:title is cleaner than the raw <title>, and
        # og:description often carries "Location | WorkplaceType".
        og_title = base.og_title(html)
        og_site_name = base.og_site_name(html)
        og_description_raw = base.og_description_raw(html)
        og_description = base.og_description(html)

        location, workplace_type = (
            base.parse_og_description(og_description_raw) if og_description_raw else (None, WorkplaceType.UNKNOWN)
        )
        description = fallback_description or og_description
        salary_min, salary_max, salary_currency = base.salary_from_text(description)
        return ScanResult(
            success=True,
            title=og_title or fallback_title,
            description=description,
            company_name=_clean_text(og_site_name),
            location=location,
            workplace_type=workplace_type,
            employment_type=EmploymentType.UNKNOWN,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            raw_html_excerpt=html[:20_000],
            full_html=html,
        )

    hiring_org = job_ld.get("hiringOrganization")
    company_name = hiring_org.get("name") if isinstance(hiring_org, dict) else None
    description = _html_to_formatted_text(job_ld.get("description")) or fallback_description

    salary_min, salary_max, salary_currency = base.job_ld_salary(job_ld)
    if salary_min is None and salary_max is None:
        # Structured baseSalary was missing/zeroed-out; many listings still
        # state a range in prose (pay-transparency-law disclosures).
        text_min, text_max, text_currency = base.salary_from_text(description)
        if text_min is not None:
            salary_min, salary_max = text_min, text_max
            salary_currency = text_currency or salary_currency

    known_keys = {
        "@context",
        "@type",
        "title",
        "description",
        "hiringOrganization",
        "jobLocation",
        "jobLocationType",
        "employmentType",
        "baseSalary",
        "datePosted",
    }
    extra_fields = {k: v for k, v in job_ld.items() if k not in known_keys}

    return ScanResult(
        success=True,
        title=_clean_text(job_ld.get("title")) or fallback_title,
        description=description,
        company_name=_clean_text(company_name),
        location=base.job_ld_location(job_ld),
        workplace_type=base.job_ld_workplace_type(job_ld),
        employment_type=base.job_ld_employment_type(job_ld),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.job_ld_posted_at(job_ld),
        extracted_fields=extra_fields or None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )
