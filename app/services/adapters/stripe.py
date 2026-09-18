"""Scan-only: Stripe's careers site has no crawl support (job URLs are only
ever submitted directly, never discovered by crawling a board) — ADAPTER
below has fetch_jobs=None/match=None/embedded_match=None, so it's invisible
to app.services.ats_adapters's crawl-discovery (detect_ats_source skips
match=None adapters; detect_embedded_ats_source only looks at adapters with
embedded_match set), and only ever reached through scan_job_url.
"""

import json
import re
from urllib.parse import urljoin, urlsplit

import httpx

from app.models.enums import AtsType, EmploymentType, WorkplaceType
from app.services.adapters import base
from app.services.adapters.base import AtsAdapter, ExtractedJobFields, ScanResult
from app.services.adapters.text import LINK_RE, MAX_LOCATION_LENGTH, clean_text, extract_balanced_div, html_to_formatted_text

_STRIPE_HOSTS = {"stripe.com", "www.stripe.com"}
_SECTION_MARKER_RE = re.compile(
    r'<div\b[^>]*class=["\'](?:careers-listing-details__body|careers-listing-closing|'
    r'careers-listing-disclaimer|careers-listing-details__sidebar-content)["\'][^>]*>'
)
_NEXT_DATA_RE = re.compile(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL)


def extract(url: str, html: str) -> ExtractedJobFields | None:
    if urlsplit(url).hostname not in _STRIPE_HOSTS:
        return None

    def description_link(link: re.Match[str]) -> str:
        target = urljoin(url, link.group(1))
        parts = urlsplit(target)
        if parts.hostname in _STRIPE_HOSTS and parts.path.startswith("/careers/apply/"):
            return ""
        return f'<a href="{target}">{link.group(2)}</a>'

    # Stripe renders policy, benefits, and applicant notices outside the
    # JSON-LD description. Keep the job sections without navigation/footer.
    sections = []
    for match in _SECTION_MARKER_RE.finditer(html):
        content = extract_balanced_div(html, match.start())
        if content:
            content = LINK_RE.sub(description_link, content)
            sections.append(content)
    description = html_to_formatted_text("\n".join(sections))

    # The Next.js payload includes remote countries as well as every office;
    # JSON-LD's first jobLocation only identifies one of those offices.
    match = _NEXT_DATA_RE.search(html)
    location = None
    if match:
        try:
            listing = json.loads(match.group(1))["props"]["pageProps"]["listing"]
            names = [loc["name"] for loc in listing["locations"] if isinstance(loc.get("name"), str)]
            location = "; ".join(dict.fromkeys(names)) or None
            if location and len(location) > MAX_LOCATION_LENGTH:
                location = location[: MAX_LOCATION_LENGTH - 3] + "..."
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    return ExtractedJobFields(description=description, location=location)


# Stripe's own JobPosting JSON-LD carries title/hiringOrganization/
# employmentType/datePosted/baseSalary correctly — only description (policy/
# benefits text lives outside it) and location (JSON-LD only names one
# office, see extract() above) need Stripe-specific overrides. Everything
# else is the same generic schema.org reading job_scanner.py's default
# scanner would do for any other JSON-LD-carrying page.
def scan_job_url(url: str) -> ScanResult | None:
    if urlsplit(url).hostname not in _STRIPE_HOSTS:
        return None
    try:
        page = base.fetch_html(url)
    except httpx.HTTPError as exc:
        return ScanResult(success=False, error=str(exc))

    html = page.text
    stripe_fields = extract(url, html)
    job_postings = base.extract_json_ld_postings(html)
    job_ld = job_postings[0] if job_postings else None

    hiring_org = job_ld.get("hiringOrganization") if job_ld else None
    company_name = hiring_org.get("name") if isinstance(hiring_org, dict) else None
    description = (
        (stripe_fields.description if stripe_fields else None)
        or (html_to_formatted_text(job_ld.get("description")) if job_ld else None)
        or base.fallback_description(html)
    )
    location = (stripe_fields.location if stripe_fields else None) or (
        base.job_ld_location(job_ld) if job_ld else None
    )
    salary_min, salary_max, salary_currency = base.job_ld_salary(job_ld) if job_ld else (None, None, None)
    if salary_min is None and salary_max is None:
        text_min, text_max, text_currency = base.salary_from_text(description)
        if text_min is not None:
            salary_min, salary_max = text_min, text_max
            salary_currency = text_currency or salary_currency

    return ScanResult(
        success=True,
        title=(clean_text(job_ld.get("title")) if job_ld else None) or base.og_title(html) or base.fallback_title(html),
        description=description,
        company_name=clean_text(company_name) or base.og_site_name(html),
        location=location,
        workplace_type=base.job_ld_workplace_type(job_ld) if job_ld else WorkplaceType.UNKNOWN,
        employment_type=base.job_ld_employment_type(job_ld) if job_ld else EmploymentType.UNKNOWN,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=base.job_ld_posted_at(job_ld) if job_ld else None,
        raw_html_excerpt=html[:20_000],
        full_html=html,
    )


ADAPTER = AtsAdapter(AtsType.STRIPE, scan_job_url=scan_job_url)
