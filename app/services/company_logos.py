"""Company logos: work out each company's own website domain, get its logo
once (logo.dev's API, or an admin's pasted URL / upload) and serve our own
copy (see sync_company_logos, set_manual_logo and app.services.logo_dev).

A posting's URL is usually on a multi-tenant ATS (boards.greenhouse.io,
*.myworkdayjobs.com, ...), which says nothing about the company's domain,
so domains come from two signals collected on every scan/rescan (see
resolve_company, called from app.services.jobs._upsert_posting):

- "jsonld": the posting's own JSON-LD hiringOrganization url/sameAs, the
  company telling us its site directly.
- "site_host": the job page's host, or its CrawlSource's board_url host,
  when that's the company's own careers site (careers.rcwilley.com, or a
  white-labeled Phenom/Paycor/TalentBrew board on the company's domain)
  rather than an ATS platform's.

Either way a domain on PLATFORM_DOMAINS is rejected, and so is one already
claimed by SHARED_DOMAIN_COMPANY_LIMIT other companies: that's a shared host
missing from the list, not anyone's own site (and the companies that got it
first lose their site_host claim on it too). An admin can always set or fix
a domain by hand ("manual", see app.api.routes.admin), which nothing
automatic ever overwrites.

Logos are stored once per company in the static-pages store (S3 behind
CloudFront in prod, a local directory in dev — app.services.page_store) and
served from our own domain, so showing one costs no third-party request.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import tldextract
from PIL import Image, ImageDraw
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.company import Company
from app.models.crawl_source import CrawlSource
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services import logo_dev, logo_images
from app.services.job_dedup import normalize_company_name
from app.services.page_store import PageStore, get_page_store

logger = logging.getLogger(__name__)

SOURCE_MANUAL = "manual"
SOURCE_JSONLD = "jsonld"
SOURCE_SITE_HOST = "site_host"
# Found by name via logo.dev's brand search (see app.services.logo_dev.find_domain)
# for a company none of our own signals gave a domain for.
SOURCE_LOGO_DEV = "logo_dev"
# A site_host claim on a host that turned out to be shared by several
# companies: the domain stays on the row so later claims on it keep being
# counted (and refused), but it's never used for a logo (see
# usable_domain_clause) and anything better may replace it.
SOURCE_SHARED = "shared"
# Higher wins; a source only ever replaces a domain from a lower-ranked one.
_SOURCE_RANK = {None: 0, SOURCE_SHARED: 0, SOURCE_SITE_HOST: 1, SOURCE_LOGO_DEV: 2, SOURCE_JSONLD: 3, SOURCE_MANUAL: 4}

# Where a company's stored logo came from. Manual ones (an admin pasted a URL
# or uploaded a file) are pinned: the automatic sync never replaces them.
ORIGIN_LOGO_DEV = "logo_dev"
ORIGIN_URL = "url"
ORIGIN_UPLOAD = "upload"
PINNED_ORIGINS = (ORIGIN_URL, ORIGIN_UPLOAD)

# A domain this many *other* companies already use is a shared host (an ATS
# or job board not on PLATFORM_DOMAINS), not this company's own site.
SHARED_DOMAIN_COMPANY_LIMIT = 2

# Job boards / aggregators users submit links from: other companies' jobs,
# re-listed. A crawl source on one is never a company's official site (see
# app.services.company_names.is_official_source).
JOB_BOARD_DOMAINS = frozenset(
    {
        "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
        "simplyhired.com", "wellfound.com", "builtin.com", "dice.com", "careerbuilder.com",
        "ycombinator.com", "workatastartup.com", "otta.com", "welcometothejungle.com",
        "usajobs.gov", "nlx.org", "dejobs.org", "jobsyn.org", "echojobs.io", "remoteok.com",
        "weworkremotely.com", "handshake.com", "joinhandshake.com",
    }
)

# Registrable domains that host many companies' postings: ATS platforms (from
# the URL shapes in app.services.adapters), job boards/aggregators users
# submit links from, and our own site. Never a company's logo domain.
PLATFORM_DOMAINS = JOB_BOARD_DOMAINS | frozenset(
    {
        # ATS platforms
        "adp.com", "applicantpro.com", "applytojob.com", "ashbyhq.com", "avature.net", "bamboohr.com",
        "breezy.hr", "clinch.io", "clinchtalent.com", "comeet.com", "comeet.co", "csod.com",
        "eightfold.ai", "gem.com", "greenhouse.io", "gupy.io", "hirehive.com", "hiringroom.com",
        "hrmdirect.com", "icims.com", "jazzhr.com", "jibecdn.com", "jobappnetwork.com", "jobvite.com",
        "lever.co", "myworkdayjobs.com", "myworkdaysite.com", "workday.com", "oraclecloud.com",
        "paradox.ai", "peopleadmin.com", "personio.de", "personio.com", "phenompeople.com",
        "pinpointhq.com", "recruitee.com", "recruitingbypaycor.com", "rippling.com", "saashr.com",
        "selectminds.com", "smartrecruiters.com", "successfactors.com", "successfactors.eu",
        "taleo.net", "talentbrew.com", "talentreef.com", "ultipro.com", "ukg.com", "workable.com",
        "zenats.com", "governmentjobs.com", "schooljobs.com", "service-now.com",
        # Generic hosts that show up in hiringOrganization.sameAs
        "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "schema.org",
        "wikipedia.org", "crunchbase.com",
        "yabot.jobs",
    }
)

# Offline: the snapshot bundled with tldextract, never a network fetch of the
# public suffix list at runtime.
_extract = tldextract.TLDExtract(suffix_list_urls=())


def registrable_domain(url_or_host: str | None) -> str | None:
    """"https://careers.rcwilley.com/jobs/1" -> "rcwilley.com",
    "jobs.bbc.co.uk" -> "bbc.co.uk". None for anything without a real
    public suffix (IPs, localhost, junk)."""
    if not url_or_host or not isinstance(url_or_host, str):
        return None
    value = url_or_host.strip()
    host = urlsplit(value if "//" in value else f"//{value}").hostname
    if not host:
        return None
    parts = _extract(host)
    if not parts.domain or not parts.suffix:
        return None
    return f"{parts.domain}.{parts.suffix}".lower()


def usable_domain_clause():
    """SQL condition for a Company domain that's really the company's own."""
    return Company.domain_source.is_not(None) & (Company.domain_source != SOURCE_SHARED)


def usable_domain(company: Company) -> str | None:
    return company.domain if company.domain_source not in (None, SOURCE_SHARED) else None


def is_platform_domain(domain: str | None) -> bool:
    return domain is None or domain in PLATFORM_DOMAINS


def is_own_platform(domain: str, company_key: str) -> bool:
    """A platform's own company (BambooHR posting its own jobs): its domain
    is on PLATFORM_DOMAINS as other companies' host, but it's this one's."""
    return domain.split(".", 1)[0] == company_key.replace(" ", "")


def domain_named(name: str | None) -> str | None:
    """The domain a company name *is*, when it's written as one ("11x.ai",
    "Super.com") — not a name that merely contains a dot ("Harness.io Inc"
    has a space; "J.P. Morgan" has no real suffix)."""
    if not name or " " in name.strip() or "." not in name:
        return None
    domain = registrable_domain(name)
    return domain if domain and domain == name.strip().lower() and not is_platform_domain(domain) else None


def logo_url_for(logo_key: str | None) -> str | None:
    """The one logo URL every surface uses: our self-hosted copy, or None
    (not crawled yet / nothing usable), which every caller treats as "no
    logo" — the frontend shows its letter avatar."""
    if not logo_key:
        return None
    return f"{settings.seo_pages_base_url.rstrip('/')}/{logo_key}"


def _clear_automatic_logo(company: Company) -> None:
    """The company's domain changed: an automatic logo belonged to the old
    one, so drop it and let the next sync fetch the new domain's. A pinned
    (admin-set) logo stays — it never depended on the domain. Runs at scan
    time, so it leaves the stable alias (logo_alias_key) alone: the company
    is now never-tried, so the next sync rewrites it either way."""
    if company.logo_origin in PINNED_ORIGINS:
        return
    company.logo_key = company.logo_origin = company.logo_etag = company.logo_source_url = None
    company.logo_status = company.logo_domain = company.logo_checked_at = None


def hiring_org_url(job_ld: dict[str, Any] | None) -> str | None:
    """The company's own site from a JSON-LD JobPosting's hiringOrganization:
    its `url`, else the first `sameAs` that isn't a platform/social link."""
    if not isinstance(job_ld, dict):
        return None
    org = job_ld.get("hiringOrganization")
    if not isinstance(org, dict):
        return None
    same_as = org.get("sameAs")
    candidates = [org.get("url"), *(same_as if isinstance(same_as, list) else [same_as])]
    for candidate in candidates:
        if isinstance(candidate, str) and not is_platform_domain(registrable_domain(candidate)):
            return candidate.strip()
    return None


def _shared_by_others(db: Session, domain: str, company_key: str) -> bool:
    """Whether other companies' *job-page hosts* already point at this
    domain — the signature of an ATS host. Companies that got the domain
    some other way (an admin, JSON-LD, logo.dev — e.g. a parent's several
    legal entities all on bankofamerica.com) legitimately share it, and
    don't count."""
    others = db.scalar(
        select(func.count())
        .select_from(Company)
        .where(
            Company.domain == domain,
            Company.company_key != company_key,
            Company.domain_source.in_((SOURCE_SITE_HOST, SOURCE_SHARED)),
        )
    )
    return (others or 0) >= SHARED_DOMAIN_COMPANY_LIMIT


def _evict_shared_site_host(db: Session, domain: str) -> None:
    """A third company's postings on the same host means it's a shared
    platform, so the companies that claimed it first (before that was
    knowable) didn't really own it either — demote theirs to "shared"."""
    demoted = select(Company).where(Company.domain == domain, Company.domain_source == SOURCE_SITE_HOST)
    evicted = 0
    for company in db.scalars(demoted):
        company.domain_source = SOURCE_SHARED
        _clear_automatic_logo(company)
        evicted += 1
    if evicted:
        logger.info("Shared host %s: cleared it from %d companies (add it to PLATFORM_DOMAINS?)", domain, evicted)


def _get_or_create(db: Session, company_key: str, display_name: str) -> Company | None:
    company = db.scalar(select(Company).where(Company.company_key == company_key))
    if company is not None:
        return company
    company = Company(company_key=company_key, display_name=display_name)
    try:
        with db.begin_nested():
            db.add(company)
    except IntegrityError:
        # Lost a race with another worker scanning the same company.
        return db.scalar(select(Company).where(Company.company_key == company_key))
    return company


def resolve_company(
    db: Session,
    *,
    company_key: str | None,
    company_name: str | None,
    company_url: str | None,
    site_urls: list[str | None],
) -> Company | None:
    """Make sure `company_key` has a Company row and upgrade its domain if a
    better-ranked signal is available. company_url is the JSON-LD
    hiringOrganization url (see hiring_org_url); site_urls are the job page
    URL and its crawl source's board_url, tried in order."""
    if not company_key or not company_name:
        return None
    company = _get_or_create(db, company_key, company_name[:255])
    if company is None:
        return None
    company.display_name = company_name[:255]

    candidates = [(SOURCE_JSONLD, registrable_domain(company_url))]
    candidates += [(SOURCE_SITE_HOST, registrable_domain(url)) for url in site_urls]
    for source, domain in candidates:
        if _SOURCE_RANK[source] < _SOURCE_RANK[company.domain_source]:
            break  # already have something at least this good (or manual)
        if is_platform_domain(domain):
            continue
        if _shared_by_others(db, domain, company_key):
            if source == SOURCE_SITE_HOST:
                _evict_shared_site_host(db, domain)
            continue
        if domain == company.domain:
            company.domain_source = source  # confirmed by a better-ranked signal
            break
        if _SOURCE_RANK[source] == _SOURCE_RANK[company.domain_source] and company.domain is not None:
            continue  # same-rank signals don't flip-flop an existing domain
        logger.info("Company %s domain %s -> %s (%s)", company_key, company.domain, domain, source)
        company.domain = domain
        company.domain_source = source
        _clear_automatic_logo(company)
        break
    # Sessions here don't autoflush, and the shared-host count above has to
    # see this claim when the same session resolves the next company (the
    # backfill does thousands per batch).
    db.flush()
    return company


def set_manual_domain(db: Session, company: Company, domain_or_url: str | None) -> None:
    """Admin override. A blank value clears it back to unresolved, so the
    next scan can fill it in automatically again."""
    domain = registrable_domain(domain_or_url) if domain_or_url else None
    if domain_or_url and domain is None:
        raise ValueError(f"Not a valid domain: {domain_or_url!r}")
    if domain != company.domain:
        _clear_automatic_logo(company)
    company.domain = domain
    company.domain_source = SOURCE_MANUAL if domain else None


# Re-check cadence: a found logo rarely changes (and an unchanged etag costs
# no download); a miss is worth retrying sooner as logo.dev keeps indexing.
LOGO_RECHECK_OK = timedelta(days=90)
LOGO_RECHECK_MISS = timedelta(days=30)
# An error (timeout, logo.dev outage) says nothing about the company — retry soon.
LOGO_RECHECK_ERROR = timedelta(days=1)
LOGO_SYNC_LIMIT = 300
LOGO_SYNC_WORKERS = 4
# Wall-clock budget per sync: it runs inside the static-pages Cloud Function
# (540s timeout) ahead of the page rendering, so it must leave most of that
# for the pages. Companies not reached in time are simply left for next run.
LOGO_SYNC_BUDGET_SECONDS = 120
# Content-addressed keys never change in place, so caches can keep them forever.
LOGO_CACHE_CONTROL = "public, max-age=31536000, immutable"
# Each company's stable alias: the logo, or a placeholder until there is one,
# overwritten in place. Static job pages are rendered once and point here,
# so a logo arriving later needs no re-render — just this one object and a
# /logos/c/* invalidation (generate_static_job_pages.py). A day in browser
# caches, which nothing can invalidate.
LOGO_ALIAS_PREFIX = "logos/c/"
LOGO_ALIAS_CACHE_CONTROL = "public, max-age=86400"
_PLACEHOLDER_BACKGROUND = (232, 236, 241, 255)
_PLACEHOLDER_MARK = (154, 165, 180, 255)


def _slug(company_key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", company_key.lower()).strip("-")[:80] or "company"


def store_logo(company: Company, png: bytes, *, store: PageStore | None = None) -> str:
    """Store a normalized logo as our own copy and point the company at it.
    The key is content-addressed, so the same image is never re-uploaded.
    The company's stable alias always gets it too."""
    store = store or get_page_store()
    key = f"logos/{_slug(company.company_key)}-{hashlib.sha256(png).hexdigest()[:8]}.png"
    if key != company.logo_key:
        store.put(key, png, "image/png", cache_control=LOGO_CACHE_CONTROL)
    company.logo_key = key
    write_logo_alias(company.company_key, png, store=store)
    return key


def logo_alias_key(company_key: str) -> str:
    return f"{LOGO_ALIAS_PREFIX}{_slug(company_key)}.png"


def logo_alias_url(company_key: str | None) -> str | None:
    return logo_url_for(logo_alias_key(company_key)) if company_key else None


@lru_cache(maxsize=1)
def placeholder_logo_png() -> bytes:
    """A neutral building mark on a light tile, the size of a stored logo."""
    size = logo_images.OUTPUT_SIZE
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 6, fill=_PLACEHOLDER_BACKGROUND)
    draw.rectangle((40, 30, 88, 100), fill=_PLACEHOLDER_MARK)  # the building
    for row in range(3):  # its windows
        for col in range(2):
            x, y = 50 + col * 18, 40 + row * 18
            draw.rectangle((x, y, x + 9, y + 9), fill=_PLACEHOLDER_BACKGROUND)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def write_logo_alias(company_key: str, png: bytes | None, *, store: PageStore | None = None) -> None:
    """Point the company's stable alias at its logo, or at the placeholder
    when it has none. Overwriting is always safe: the alias mirrors the
    database, so writing it twice writes the same thing."""
    (store or get_page_store()).put(
        logo_alias_key(company_key), png or placeholder_logo_png(), "image/png", cache_control=LOGO_ALIAS_CACHE_CONTROL
    )


def companies_needing_logos(
    db: Session, now: datetime, limit: int, skip_keys: set[str] | frozenset[str] = frozenset()
) -> list[Company]:
    """Companies without a pinned (admin-set) logo that were never tried, or
    are due a re-check — the ones with the most canonical postings first, so
    a capped run covers what users see most. Includes companies with no
    domain yet: logo.dev's name search may find one."""
    posting_count = (
        select(func.count())
        .where(JobPosting.company_key == Company.company_key, JobPosting.primary_posting_id.is_(None))
        .correlate(Company)
        .scalar_subquery()
    )
    due = (
        Company.logo_status.is_(None)
        | ((Company.logo_status == logo_dev.STATUS_OK) & (Company.logo_checked_at < now - LOGO_RECHECK_OK))
        | ((Company.logo_status == logo_dev.STATUS_ERROR) & (Company.logo_checked_at < now - LOGO_RECHECK_ERROR))
        | (
            Company.logo_status.not_in((logo_dev.STATUS_OK, logo_dev.STATUS_ERROR))
            & (Company.logo_checked_at < now - LOGO_RECHECK_MISS)
        )
    )
    not_pinned = Company.logo_origin.is_(None) | Company.logo_origin.not_in(PINNED_ORIGINS)
    stmt = (
        select(Company)
        .where(not_pinned, due, Company.company_key.not_in(skip_keys) if skip_keys else True)
        .order_by(posting_count.desc(), Company.company_key)
        .limit(limit)
    )
    return list(db.scalars(stmt))


@dataclass
class _Lookup:
    result: logo_dev.LogoDevResult
    found_domain: str | None = None  # from the name search, for a domain-less company


def sync_company_logos(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = LOGO_SYNC_LIMIT,
    store: PageStore | None = None,
    client=None,
    budget_seconds: float = LOGO_SYNC_BUDGET_SECONDS,
    attempted: set[str] | None = None,
) -> dict[str, int]:
    """Fetch logos from logo.dev for companies that need one (see
    companies_needing_logos) and store our own copies. Lookups run in a
    small thread pool; every DB write happens here on the caller's session,
    committed at the end. Returns a count per outcome. A no-op without a
    logo.dev secret key."""
    if client is None and not logo_dev.is_configured():
        return {}
    now = now or datetime.now(timezone.utc)
    companies = companies_needing_logos(db, now, limit, attempted or frozenset())
    if attempted is not None:
        # A caller looping until done (one_off/sync_all_logos.py) passes the
        # same set every round, so companies logo.dev is still indexing — left
        # due for the next scheduled run — aren't picked again and again.
        attempted.update(c.company_key for c in companies)
    if not companies:
        return {}
    own_client = client is None
    client = client or logo_dev.new_client()
    deadline = time.monotonic() + budget_seconds
    source_names = _source_names(db, [c.company_key for c in companies if usable_domain(c) is None])
    jobs = [
        (c.company_key, c.display_name, usable_domain(c), c.logo_etag, source_names.get(c.company_key, []))
        for c in companies
    ]
    try:
        with ThreadPoolExecutor(max_workers=LOGO_SYNC_WORKERS) as pool:
            lookups = list(pool.map(lambda job: _lookup(client, deadline, *job), jobs))
    finally:
        if own_client:
            client.close()

    counts: dict[str, int] = {}
    for company, lookup in zip(companies, lookups):
        status = lookup.result.status if lookup else "deferred"
        counts[status] = counts.get(status, 0) + 1
        if lookup is None or lookup.result.status in (logo_dev.STATUS_PENDING, logo_dev.STATUS_RATE_LIMITED):
            continue  # retried next run
        if lookup.found_domain and usable_domain(company) is None:
            company.domain = lookup.found_domain
            company.domain_source = SOURCE_LOGO_DEV
        result = lookup.result
        company.logo_checked_at = now
        company.logo_status = logo_dev.STATUS_OK if result.status == logo_dev.STATUS_UNCHANGED else result.status
        if result.status == logo_dev.STATUS_OK:
            store_logo(company, result.png, store=store)
            company.logo_origin = ORIGIN_LOGO_DEV
            company.logo_etag = result.etag
            company.logo_domain = usable_domain(company)
            company.logo_source_url = f"https://logo.dev/{company.logo_domain}"
        elif result.status == logo_dev.STATUS_NONE:
            company.logo_key = company.logo_origin = company.logo_etag = company.logo_source_url = None
            write_logo_alias(company.company_key, None, store=store)
        # STATUS_ERROR keeps whatever logo was there; STATUS_UNCHANGED too.
    db.commit()
    logger.info("Company logos: %s", ", ".join(f"{n} {status}" for status, n in sorted(counts.items())))
    return counts


def source_search_name(source_name: str) -> str:
    """A CrawlSource name as a brand name to search: "lever/aledade" (a
    board slug some sources are named by) -> "aledade"."""
    name = source_name.rsplit("/", 1)[-1] if "/" in source_name else source_name
    return re.sub(r"[-_]+", " ", name).strip()


def _source_names(db: Session, company_keys: list[str]) -> dict[str, list[str]]:
    """{company_key: names of the crawl sources its postings came from, most
    postings first} — a company's legal-entity name ("FMC Freedom Mortgage
    Corporation", "94-1687665 Bank of America, National Association") often
    doesn't match logo.dev, but the source it was crawled from ("Freedom
    Mortgage", "Bank of America") is named after the brand."""
    if not company_keys:
        return {}
    rows = db.execute(
        select(JobPosting.company_key, CrawlSource.name, func.count())
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .join(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPosting.company_key.in_(company_keys))
        .group_by(JobPosting.company_key, CrawlSource.name)
        .order_by(func.count().desc())
    ).all()
    names: dict[str, list[str]] = {}
    for key, source_name, _ in rows:
        names.setdefault(key, []).append(source_search_name(source_name))
    return names


def _lookup(
    client, deadline: float, company_key: str, name: str, domain: str | None, etag: str | None, source_names: list[str]
) -> _Lookup | None:
    if time.monotonic() > deadline:
        return None  # out of time this run; still due next run
    try:
        found = None
        if domain is None and (named := domain_named(name)):
            domain = found = named  # "11x.ai", "Super.com": the name is the domain
        if domain is None:
            # The company's own name first (as scraped, then without legal
            # endings — "Scribd, Inc." finds nothing, "scribd" does), then
            # the brand its crawl source is named after (see _source_names).
            attempts = [(name, company_key)]
            if company_key != name.strip().lower():
                attempts.append((company_key, company_key))
            for source_name in source_names[:2]:
                source_key = normalize_company_name(source_name)
                if source_key and source_key != company_key:
                    attempts.append((source_name, source_key))
            for attempt_name, attempt_key in attempts:
                found = logo_dev.find_domain(attempt_name, attempt_key, client=client)
                if found is not None and (not is_platform_domain(found) or is_own_platform(found, attempt_key)):
                    break
                found = None
            if found is None:
                return _Lookup(logo_dev.LogoDevResult(logo_dev.STATUS_NONE))
            domain = found
        return _Lookup(logo_dev.fetch_logo(domain, known_etag=etag, client=client), found_domain=found)
    except logo_dev.RateLimited:
        return _Lookup(logo_dev.LogoDevResult(logo_dev.STATUS_RATE_LIMITED))
    except Exception:  # one bad lookup must never sink the whole run
        logger.exception("Logo lookup failed for %s", company_key)
        return _Lookup(logo_dev.LogoDevResult(logo_dev.STATUS_ERROR))


# ---------------------------------------------------------------------------
# Manual logos (admin) — see app.api.routes.admin
# ---------------------------------------------------------------------------


def get_or_create_company(db: Session, company_key: str, display_name: str) -> Company:
    company = _get_or_create(db, company_key, (display_name or company_key)[:255])
    if company is None:  # only on a lost insert race that then vanished — not expected
        raise RuntimeError(f"Couldn't create company {company_key!r}")
    return company


def set_manual_logo(db: Session, company: Company, png: bytes, *, origin: str, source_url: str | None) -> None:
    """Pin an admin-provided logo (already normalized). The automatic sync
    leaves it alone until clear_manual_logo."""
    store_logo(company, png)
    company.logo_origin = origin
    company.logo_source_url = source_url
    company.logo_etag = None
    db.flush()


def clear_manual_logo(db: Session, company: Company) -> None:
    """Back to automatic: drop the pinned logo; the next sync fetches one."""
    company.logo_key = company.logo_origin = company.logo_etag = company.logo_source_url = None
    company.logo_status = company.logo_checked_at = company.logo_domain = None
    write_logo_alias(company.company_key, None)
    db.flush()
