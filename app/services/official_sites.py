"""Find the official careers site of a company whose jobs we only found
somewhere else (a job board, a site we can't crawl yet, a user submission
with no source), so its own site becomes the source of its jobs.

The official company site is the authority on who owns a job (see
app.services.company_names); a company seen only second-hand has no such
authority yet. For each one this looks up the company's own domain
(Company.domain, else the domain its name is, else logo.dev's brand search),
fetches its homepage and its /careers and /jobs pages — then the careers
pages those link to on its own domain — and takes the first careers board on
a platform we support that one of them points to, in a link or anywhere in
its HTML (the same pure URL-shape match job submissions use,
detect_ats_source). Registered as a *pending*
crawl source named after the company (register_discovered_board with
needs_review): a homepage can link to someone else's board (a partner's, a
parent's), so an agent verifies it belongs to the company before it's
activated and crawled (docs/adapter-playbook.md, "Official-site candidates").

Bounded on purpose: a run stops after TIME_BUDGET_SECONDS (the next run
continues with the companies it didn't reach), at most six fetches per
company, and a company is looked at again only after RECHECK_AFTER, whatever
the outcome — so an unanswerable company isn't retried every day.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus, ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services import logo_dev
from app.services.ats_adapters import detect_ats_source
from app.services.company_logos import domain_named, is_platform_domain, registrable_domain, usable_domain
from app.services.company_names import AUTO, PLACEHOLDER
from app.services.crawl_sources import register_discovered_board
from app.services.job_dedup import brand_from_source_name, normalize_company_name

logger = logging.getLogger("app.official_sites")

LOOKBACK = timedelta(days=60)
RECHECK_AFTER = timedelta(days=30)
# A run checks companies until this much time has passed, then stops; the
# next run picks up the ones not checked yet. Leaves room inside the Cloud
# Function's 540s timeout for the company in progress (at most
# len(_PATHS) + MAX_CAREERS_PAGES fetches of FETCH_TIMEOUT each).
TIME_BUDGET_SECONDS = 420.0
FETCH_TIMEOUT = 10.0
_PATHS = ("", "/careers", "/jobs")
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#\s]+)""", re.IGNORECASE)
_ANCHOR_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#\s]+)["'][^>]*>(.*?)</a>""", re.IGNORECASE | re.DOTALL)
_URL_RE = re.compile(r"""https?://[A-Za-z0-9.-]+(?:/[^\s"'<>\\)]*)?""")
_CAREERS_WORDS_RE = re.compile(r"\b(?:careers?|jobs|join (?:us|our team)|work (?:with|for|at) us|openings)\b", re.IGNORECASE)
# A company's own careers site on a domain of its own ("careers.wabtec.com").
# Job boards' hosts never look like this (theirs is a path: linkedin.com/jobs).
_CAREERS_HOST_RE = re.compile(r"^(?:careers?|jobs)\.", re.IGNORECASE)
# Careers pages followed one level down from the homepage, /careers and /jobs.
MAX_CAREERS_PAGES = 3
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; yabot.jobs official-site finder; +https://yabot.jobs)"}

FOUND = "found"
NONE = "none"
ERROR = "error"


@dataclass(slots=True)
class Outcome:
    company_key: str
    company_name: str
    domain: str | None
    board_url: str | None
    result: str


def _official_keys(db: Session) -> set[str]:
    """Company keys an official source already covers: its name's and its
    sub-brands'."""
    keys: set[str] = set()
    rows = db.execute(
        select(CrawlSource.name, CrawlSource.sub_brands).where(
            CrawlSource.status == CrawlSourceStatus.ACTIVE,
            CrawlSource.is_official.is_(True),
            CrawlSource.name_source != PLACEHOLDER,
        )
    ).all()
    for name, sub_brands in rows:
        for candidate in [brand_from_source_name(name), *(sub_brands or [])]:
            key = normalize_company_name(candidate)
            if key:
                keys.add(key)
    return keys


def companies_needing_official_site(db: Session, *, now: datetime, limit: int | None) -> list[tuple[str, str]]:
    """[(company_key, company_name)] of companies with recent canonical jobs
    from a non-official place and no official source of their own, most jobs
    first, skipping any looked at within RECHECK_AFTER."""
    rows = db.execute(
        select(JobPosting.company_key, func.max(JobPosting.company_name), func.count())
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .outerjoin(Company, Company.company_key == JobPosting.company_key)
        .where(
            JobPosting.extraction_status == ScanStatus.SUCCESS,
            JobPosting.primary_posting_id.is_(None),
            JobPosting.company_key.is_not(None),
            JobPostingUrl.created_at >= now - LOOKBACK,
            or_(
                CrawlSource.id.is_(None),
                CrawlSource.status != CrawlSourceStatus.ACTIVE,
                CrawlSource.is_official.is_(False),
            ),
            or_(Company.official_site_checked_at.is_(None), Company.official_site_checked_at < now - RECHECK_AFTER),
        )
        .group_by(JobPosting.company_key)
        .order_by(func.count().desc(), JobPosting.company_key)
    ).all()
    covered = _official_keys(db)
    return [(key, name) for key, name, _ in rows if key not in covered][:limit]


def _board_link(url: str) -> str | None:
    """`url` itself if it's a careers board on a platform we support."""
    try:
        detect_ats_source(url)
    except ValueError:
        return None
    return url


def board_in_page(base_url: str, html: str) -> str | None:
    """The first careers board we can crawl that a page points to: in a
    link, else anywhere in its HTML — careers pages often load the board
    from a script, a JSON blob or an iframe rather than linking it."""
    for href in _HREF_RE.findall(html):
        link = urljoin(base_url, href)
        if link.startswith(("http://", "https://")) and _board_link(link):
            return link
    text = html.replace("\\/", "/").replace("&quot;", '"').replace("&amp;", "&")
    for url in _URL_RE.findall(text):
        if _board_link(url):
            return url
    return None


def careers_links(base_url: str, html: str, domain: str) -> list[str]:
    """Links on a page to the company's own careers pages, with careers
    wording in the link's address or text: on its own domain, or a careers
    site of its own on a sibling domain (wabteccorp.com ->
    "careers.wabtec.com"). Never another site's jobs page (linkedin.com/jobs)."""
    own = {registrable_domain(domain), registrable_domain(base_url)}
    links: list[str] = []
    for href, text in _ANCHOR_RE.findall(html):
        link = urljoin(base_url, href)
        if not link.startswith(("http://", "https://")):
            continue
        host = urlsplit(link).hostname or ""
        if registrable_domain(link) not in own and not _CAREERS_HOST_RE.match(host):
            continue
        if (_CAREERS_WORDS_RE.search(href) or _CAREERS_WORDS_RE.search(re.sub(r"<[^>]+>", " ", text))) and link not in links:
            links.append(link)
    return links


def _fetch(client: httpx.Client, url: str) -> httpx.Response | None:
    try:
        return client.get(url)
    except httpx.HTTPError as exc:
        logger.info("Fetching %s failed (%s).", url, exc)
        return None


def _board_from(response: httpx.Response) -> str | None:
    """A board the response is (a redirect straight to it) or points to."""
    if _board_link(str(response.url)):
        return str(response.url)
    return board_in_page(str(response.url), response.text) if response.status_code == 200 else None


def find_official_board(domain: str, client: httpx.Client) -> str | None:
    """A crawlable careers board on or linked from the company's own site:
    its homepage, /careers or /jobs, then — one level deeper — the careers
    pages those link to on its own domain ("careers.wabtec.com"). At most
    len(_PATHS) + MAX_CAREERS_PAGES fetches. Raises httpx.HTTPError only if
    every first-level fetch failed."""
    errors = 0
    followed: list[str] = []
    for path in _PATHS:
        response = _fetch(client, f"https://{domain}{path}")
        if response is None:
            errors += 1
            continue
        board = _board_from(response)
        if board:
            return board
        if response.status_code == 200:
            followed += [u for u in careers_links(str(response.url), response.text, domain) if u not in followed]
    if errors == len(_PATHS):
        raise httpx.ConnectError(f"every fetch of {domain} failed")
    for url in followed[:MAX_CAREERS_PAGES]:
        response = _fetch(client, url)
        board = _board_from(response) if response is not None else None
        if board:
            return board
    return None


def _domain_for(db: Session, company_key: str, company_name: str, logo_client: httpx.Client | None) -> str | None:
    company = db.scalar(select(Company).where(Company.company_key == company_key))
    domain = (usable_domain(company) if company else None) or domain_named(company_name)
    if not domain and logo_client is not None:
        domain = logo_dev.find_domain(company_name, company_key, client=logo_client)
    return None if is_platform_domain(domain) else domain


def _record(db: Session, company_key: str, company_name: str, result: str, now: datetime) -> None:
    company = db.scalar(select(Company).where(Company.company_key == company_key))
    if company is None:
        company = Company(company_key=company_key, display_name=company_name[:255])
        db.add(company)
    company.official_site_checked_at = now
    company.official_site_result = result


def run(
    db: Session,
    *,
    dry_run: bool,
    limit: int | None = None,
    time_budget: float = TIME_BUDGET_SECONDS,
    now: datetime | None = None,
    client: httpx.Client | None = None,
    logo_client: httpx.Client | None = None,
    clock=time.monotonic,
) -> list[Outcome]:
    """Look for the official site of companies that need one, most jobs
    first, until `time_budget` seconds have passed (and at most `limit`
    companies, if given). A board found is registered as a crawl source named
    after the company; every company looked at is recorded, so it waits
    RECHECK_AFTER before the next look and the next run starts with the ones
    this one didn't reach. A dry run only logs what it would do and writes
    nothing."""
    now = now or datetime.now(timezone.utc)
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=FETCH_TIMEOUT, headers=_HEADERS)
    own_logo_client = logo_client is None and logo_dev.is_configured()
    if own_logo_client:
        logo_client = logo_dev.new_client()
    outcomes: list[Outcome] = []
    try:
        started = clock()
        candidates = companies_needing_official_site(db, now=now, limit=limit)
        for company_key, company_name in candidates:
            if clock() - started >= time_budget:
                logger.info("Time budget used; %d compan(ies) left for the next run.", len(candidates) - len(outcomes))
                break
            domain = _domain_for(db, company_key, company_name, logo_client)
            board, result = None, NONE
            if domain:
                try:
                    board = find_official_board(domain, client)
                    result = FOUND if board else NONE
                except httpx.HTTPError:
                    result = ERROR
            outcomes.append(Outcome(company_key, company_name, domain, board, result))
            if board:
                logger.info("%s %r: %s (from %s)", "WOULD ADD" if dry_run else "ADDED", company_name, board, domain)
            elif result == ERROR:
                logger.info("ERROR    %r: couldn't fetch %s", company_name, domain)
            else:
                logger.info("NO BOARD %r: %s", company_name, f"none linked from {domain}" if domain else "no domain known")
            if dry_run:
                continue
            if board:
                source = register_discovered_board(db, board, needs_review=True)
                if source is not None and source.name_source == PLACEHOLDER:
                    source.name, source.name_source = company_name[:255], AUTO
            _record(db, company_key, company_name, result, now)
            db.commit()
    finally:
        if own_client:
            client.close()
        if own_logo_client:
            logo_client.close()
    found = sum(o.result == FOUND for o in outcomes)
    logger.info(
        "%s %d of %d compan(ies) looked at.", "Would add a board for" if dry_run else "Added a board for", found, len(outcomes)
    )
    return outcomes
