"""Cross-source deduplication: the same job posted at two different URLs
(a company's own careers page and a job board, say) still gets two
JobPostingUrl/JobPosting rows — one per URL, since url_id is unique and
that identity is load-bearing (UserJobApplication, tailored resumes,
cover letters, and scores all key off a specific job_posting_id). Rather
than merging those rows, a duplicate is linked to an existing (older)
"primary" one via JobPosting.primary_posting_id; GET /jobs only lists
primary rows, and a duplicate's also_posted_count shows how many other
places it was found. A duplicate stays fully reachable at its own
url_id for applying, scoring, and tailoring.

Matching is computed once, at write time (in app.services.jobs._upsert_posting
on every successful scan/rescan), not per-request: company_key/title_key are
cheap, normalized-for-matching forms of company_name/title (see below), and
find_duplicate_primary compares only within same-company candidates. At this
product's scale (a few dozen distinct companies, thousands of postings) that
keeps the whole thing to stdlib string work — no fuzzy-search Postgres
extension (pg_trgm) needed.
"""

import re
import uuid
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job_posting import JobPosting

# Common legal suffixes scraped company names carry inconsistently
# ("Acme Inc.", "Acme, LLC", "Acme Corp") — stripped so all three match.
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|plc|gmbh)\.?\s*$",
    re.IGNORECASE,
)
_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")

# Careers-page wording that employers put in their og:site_name or JSON-LD
# hiringOrganization instead of their name: "Careers at Marriott", "SBH
# Careers", "WTW External Careers Site", "Corporate Careers | Trueblue".
_CAREERS_WORDS = r"(?:careers?|jobs|job\s+openings|job\s+board)"
_CAREERS_TAIL = r"(?:\s+(?:site|section|marketplace|portal|page|center|centre|hub))?"
_CAREERS_PREFIX_RE = re.compile(rf"^{_CAREERS_WORDS}\s+(?:at|with)\s+", re.IGNORECASE)
# Only after a space or a separator — "www.rei.jobs" ends in a domain, not a word.
_CAREERS_SUFFIX_RE = re.compile(
    rf"(?:\s+|\s*[-–—:]\s*)(?:external\s+|all\s+)?{_CAREERS_WORDS}{_CAREERS_TAIL}[\s\-–—:]*$", re.IGNORECASE
)
_CAREERS_ONLY_RE = re.compile(rf"^(?:corporate\s+|external\s+)?{_CAREERS_WORDS}{_CAREERS_TAIL}$", re.IGNORECASE)
_HAS_CAREERS_RE = re.compile(rf"(?:^|\s){_CAREERS_WORDS}(?:\s|$)", re.IGNORECASE)
# A hostname standing in for a name (see app.services.crawl_sources._company_name):
# on an ATS's shared domain, a careers./jobs. subdomain, or a .jobs/.careers domain —
# "starbucks.eightfold.ai", "careers.qualcomm.com", "www.rei.jobs". A brand that
# merely contains a dot ("Super.com", "Harness.io") is none of these, and stays.
_ATS_HOST_RE = re.compile(
    r"^(?:[\w-]+\.)+(?:eightfold\.ai|myworkdayjobs\.com|myworkdaysite\.com|oraclecloud\.com|taleo\.net|"
    r"icims\.com|clinchtalent\.com|ultipro\.com|successfactors\.(?:com|eu)|avature\.net|"
    r"dayforcehcm\.com|jobs|careers)$",
    re.IGNORECASE,
)
_CAREERS_HOST_RE = re.compile(r"^(?:careers?|jobs|apply)\.[\w-]+(?:\.[\w-]+)+$", re.IGNORECASE)


def _is_careers_label(segment: str) -> bool:
    """A short "|"-part that is careers wording ("Careers", "Careers at
    Principal", "Datadog Careers") — not a tagline that happens to mention
    careers."""
    return len(segment.split()) <= 6 and bool(_HAS_CAREERS_RE.search(segment))


def _strip_careers_wording(segment: str) -> str:
    return _CAREERS_SUFFIX_RE.sub("", _CAREERS_PREFIX_RE.sub("", segment)).strip(" -–—:|")


def clean_company_name(name: str | None) -> str | None:
    """The display company name to store, from what a page or crawl source
    offered: careers wording stripped ("Careers at Marriott" -> "Marriott",
    "Corporate Careers | Trueblue" -> "Trueblue"), and None for a hostname
    standing in for a name ("starbucks.eightfold.ai"), so the caller falls
    back to its next source. Conservative on purpose — real names that look
    odd ("BambooHR", "Super.com", "Scale AI") pass through unchanged, and a
    name that would clean down to nothing is kept as it was.

    With "|"-separated parts: when one of them is careers wording, the
    company is the last part ("Studio Associate | Careers | Lucid Motors",
    "Join our team | Careers at Principal"); otherwise it's the first, the
    rest being a tagline or a division code ("EQ Bank | Canada's Challenger
    Bank", "CCB iHeartMedia + Entertainment, Inc. | MPG")."""
    if name is None:
        return None
    original = " ".join(name.split())
    if not original:
        return None
    if _ATS_HOST_RE.match(original) or _CAREERS_HOST_RE.match(original):
        return None
    segments = [seg.strip() for seg in original.split("|") if seg.strip()]
    if len(segments) > 1 and any(_is_careers_label(seg) for seg in segments):
        segments = segments[::-1]
    for segment in segments:
        if _CAREERS_ONLY_RE.match(segment):
            continue
        cleaned = _strip_careers_wording(segment)
        if cleaned:
            return cleaned
    return original


# How similar two (already company-matched) titles must be to count as the
# same role. High enough that "Software Engineer" and "Senior Software
# Engineer" at the same company don't collapse into one.
_TITLE_SIMILARITY_THRESHOLD = 0.85


def _metro_level_codes(metros: list[str] | None) -> set[str]:
    """Just the metro/micro-area codes out of a posting's `metros` list — a
    5-digit Census code, unlike the 2-letter state codes app.services.geo
    also files it under (see resolve_area_codes). A same-company posting
    that only shares a *state* isn't the same job: verified live on this
    product's own data — Amazon's identical "Infra Delivery Install
    Technician" title recurs at dozens of distinct PA facilities (Berwick,
    metro 14100; Fairless Hills, metro 37980; ...), each a real, separate
    opening. Matching on the bare state code merged two of them into one
    before this filter existed. Search still wants the state-level code (so
    "Utah" finds everything in it); dedup is precision-first, so it doesn't."""
    return {code for code in (metros or []) if code.isdigit()}


def normalize_company_name(name: str | None) -> str | None:
    """Case-folded, punctuation- and legal-suffix-stripped, so "Acme Inc.",
    "acme, llc" and "Acme" all normalize to "acme"."""
    if not name:
        return None
    value = re.sub(r"[.,]", "", name.strip().lower())
    value = _COMPANY_SUFFIX_RE.sub("", value).strip()
    value = _WHITESPACE_RE.sub(" ", value)
    return value or None


def normalize_title(title: str | None) -> str | None:
    """Case-folded, punctuation-stripped — just enough to compare two
    scraped titles fairly; the similarity threshold in find_duplicate_primary
    does the actual fuzzy matching."""
    if not title:
        return None
    value = _PUNCTUATION_RE.sub(" ", title.strip().lower())
    value = _WHITESPACE_RE.sub(" ", value).strip()
    return value or None


def find_duplicate_primary(db: Session, posting: JobPosting) -> uuid.UUID | None:
    """The id of an existing canonical JobPosting this one duplicates, or
    None if it's not a match for anything already known (i.e. it should be
    its own canonical). Only matches against already-canonical rows
    (primary_posting_id IS NULL) so groups never chain into a duplicate of a
    duplicate.

    "Same job" = same normalized company, overlapping metro *area* (not just
    state — see _metro_level_codes), and a fuzzy title match. Company is the
    cheap pre-filter (this product's company_name cardinality is small, so
    each bucket is cheap to fuzzy-compare in Python); metro overlap and title
    similarity narrow it down to the same specific opening, not just the same
    employer posting the same title at many different locations. A posting
    with no metro-level location at all never matches via this path — no
    positive location evidence, so it's left as its own canonical rather than
    risked against a same-titled-but-different opening elsewhere.
    """
    if posting.company_key is None or posting.title_key is None:
        return None

    # Only the 3 columns the comparison below actually needs — not full
    # JobPosting objects. A company with thousands of postings (this
    # product's largest crawl source is ~2,400) would otherwise mean
    # fetching and ORM-hydrating that many rows' full description/
    # raw_source/extracted_fields on every single scan, most of it never
    # even looked at.
    candidates = db.execute(
        select(JobPosting.id, JobPosting.title_key, JobPosting.metros).where(
            JobPosting.company_key == posting.company_key,
            JobPosting.primary_posting_id.is_(None),
            JobPosting.id != posting.id,
        )
    ).all()

    posting_metro_codes = _metro_level_codes(posting.metros)
    if not posting_metro_codes:
        return None

    for candidate_id, candidate_title_key, candidate_metros in candidates:
        if candidate_title_key is None:
            continue
        if not posting_metro_codes & _metro_level_codes(candidate_metros):
            continue
        similarity = SequenceMatcher(None, candidate_title_key, posting.title_key).ratio()
        if similarity >= _TITLE_SIMILARITY_THRESHOLD:
            return candidate_id
    return None
