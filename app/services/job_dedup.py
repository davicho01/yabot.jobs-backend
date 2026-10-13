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
find_duplicate_primary compares only same-company, same-title candidates by
place (see same_place). one_off/redo_dedup.py re-groups existing rows with
the same rule after it changes.
"""

import re
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl

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
_CAREERS_HOST_RE = re.compile(r"^(?:careers?|jobs|apply|hiring)\.[\w-]+(?:\.[\w-]+)+$", re.IGNORECASE)
# Any other host with a subdomain ("signicat.teamtailor.com",
# "careerunitedstates.autoliv.com", "www.jobs-ups.com"): a brand written as a
# domain has two labels ("Ancestry.com", "incident.io"), not three. Lowercase
# only, as hosts are written, so initials like "J.P.Morgan" stay a name.
_MULTI_LABEL_HOST_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+){2,}$")
# Careers-portal wording — Oracle "Candidate Experience" site names and the like
# ("JPMC Candidate Experience page", "Chubb External", "Molina Talent
# Acquisition", "Employment Opportunities at BuzzFeed, Inc.", "6090-Johnson &
# Johnson Services Inc. Legal Entity").
_PORTAL_SUFFIX_RE = re.compile(
    r"(?:^|\s*[-–—:]?\s+|\s*[-–—:]\s*)(?:the\s+)?"
    r"(?:candidate\s+experience(?:\s+external)?\s+(?:site|page)|candidate\s+site|talent\s+acquisition|legal\s+entity|external)"
    r"(?:\s*[-–—]\s*\w+)?\s*$",
    re.IGNORECASE,
)
_PORTAL_PREFIX_RE = re.compile(r"^(?:employment|career)\s+opportunities\s+(?:at|with)\s+", re.IGNORECASE)


def _is_careers_label(segment: str) -> bool:
    """A short "|"-part that is careers wording ("Careers", "Careers at
    Principal", "Datadog Careers") — not a tagline that happens to mention
    careers."""
    return len(segment.split()) <= 6 and bool(_HAS_CAREERS_RE.search(segment))


def _strip_careers_wording(segment: str) -> str:
    segment = _PORTAL_PREFIX_RE.sub("", _CAREERS_PREFIX_RE.sub("", segment))
    segment = _PORTAL_SUFFIX_RE.sub("", segment)
    return _CAREERS_SUFFIX_RE.sub("", segment).strip(" -–—:|")


def clean_company_name(name: str | None) -> str | None:
    """The display company name to store, from what a page or crawl source
    offered: careers wording stripped ("Careers at Marriott" -> "Marriott",
    "Corporate Careers | Trueblue" -> "Trueblue"), and None for a hostname
    standing in for a name ("starbucks.eightfold.ai"), so the caller falls
    back to its next source. Conservative on purpose — real names that look
    odd ("BambooHR", "Super.com", "Scale AI") pass through unchanged. A name
    that is nothing but careers/portal wording ("Candidate Experience site",
    "Careers") cleans to None too, so the caller's next source (the crawl
    source's brand) wins; the caller keeps the raw name as a last resort.

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
    if _ATS_HOST_RE.match(original) or _CAREERS_HOST_RE.match(original) or _MULTI_LABEL_HOST_RE.match(original):
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
    return None


def location_key(locations: list[str] | None) -> tuple[str, ...]:
    """A posting's locations, case- and whitespace-folded, de-duplicated and
    sorted — so "Jersey City; Newark" and "Newark; Jersey City" compare
    equal. Empty when the posting names no location."""
    return tuple(sorted({_WHITESPACE_RE.sub(" ", loc.strip().lower()) for loc in (locations or []) if loc.strip()}))


def _place_set(places: list[list[float]] | None) -> set[tuple[float, float]]:
    return {(round(lat, 4), round(lon, 4)) for lat, lon in (places or [])}


def same_place(
    a_locations: list[str] | None,
    a_places: list[list[float]] | None,
    a_domain: str,
    b_locations: list[str] | None,
    b_places: list[list[float]] | None,
    b_domain: str,
) -> bool:
    """Whether two same-company, same-title postings are at the same place,
    i.e. the same opening. On one site the locations must match exactly: a
    board writes its locations one consistent way, so a different string is a
    different site — prod (2026-10-12) had Target's Bridgehampton and Staten
    Island stores merged because both fall in the NYC metro, and dozens of
    Amazon PA facilities share a state. Across two sites the same place is
    often written differently ("Austin, TX" vs "Austin, Texas, United
    States"), so a shared city (app.services.geo.resolve_places) counts too.
    No location at all is never a match: no evidence it's the same opening."""
    a_key, b_key = location_key(a_locations), location_key(b_locations)
    if not a_key or not b_key:
        return False
    if a_key == b_key:
        return True
    return a_domain != b_domain and bool(_place_set(a_places) & _place_set(b_places))


# Workday tenants often report the hiring legal entity with its tax ID or
# internal company code in front ("94-1687665 Bank of America, National
# Association", "2100 NVIDIA USA"). Deliberately narrow — a 9-digit EIN or a
# 3+ digit code — so real brands that start with a number ("84 Lumber",
# "3M") never match.
_ENTITY_CODE_RE = re.compile(r"^(?:\d{2}-\d{7}|\d{3,})\s+")


def has_entity_code(name: str | None) -> bool:
    return bool(name and _ENTITY_CODE_RE.match(name.strip()))


# prefers_source_brand: the codes Workday/Oracle tenants put in front of the
# hiring legal entity, beyond has_entity_code's narrow set — alphanumeric
# ("B10 Wells Fargo Bank", "US0001 Sysco Jackson", "UW1861 University of
# Washington", "469g Siemens…", "IN10 (FCRS = IN010) Novartis…"), hyphenated
# ("6090-Johnson & Johnson…", "1072-28 Target India") and parenthesized
# ("(0122) Sanofi…"). Three or more characters with a digit, so "3M" isn't one.
_ALNUM_CODE_RE = re.compile(r"^\(?(?=[a-z]*\d)[a-z0-9]{3,8}\)?(?:\s*\([^)]*\))?\s*(?:[-–]\s*)?\s(?=\S)", re.IGNORECASE)
_HYPHEN_CODE_RE = re.compile(r"^\d{3,}(?:-\d+)?(?:-|\s+)(?=[^\d\s])")
_PAREN_CODE_RE = re.compile(r"^\(\s*\w+\s*\)\s+")
# A bare two-digit code ("02 CACI, INC.-FEDERAL", "20 Saia Motor Freight Line
# LLC") only with a legal-form suffix in the name, so brands that start with a
# number ("84 Lumber", "99 Ranch Market", "24 Hour Fitness") never qualify.
_TWO_DIGIT_CODE_RE = re.compile(r"^\d{2}\s*(?:-\s*)?\s(?=\S)")
_LEGAL_FORM_RE = re.compile(
    r"\b(?:llc|l\.l\.c|inc|incorporated|corp|corporation|company|co|l\.?p|ltd|limited|plc|pllc|p\.?c|p\.?a|n\.\s?a)\b\.?",
    re.IGNORECASE,
)
_PORTAL_ANYWHERE_RE = re.compile(
    r"candidate\s+experience|candidate\s+site|talent\s+acquisition|legal\s+entity|\sexternal\s*$|"
    r"^(?:employment|career)\s+opportunities\s+(?:at|with)\s",
    re.IGNORECASE,
)


def prefers_source_brand(name: str | None) -> bool:
    """True when a scanned company name describes a legal entity (with an
    internal code) or a careers portal rather than the brand — so
    _upsert_posting shows the brand the crawl source is named after instead
    ("B10 Wells Fargo Bank, N. A." -> "Wells Fargo", "Candidate Experience
    site" -> "IU Health"), which also groups a company's subsidiaries under
    one name. Broader than has_entity_code because getting it wrong only
    swaps in that brand; has_entity_code/strip_entity_code stay narrow for
    the logo name search (app.services.logo_dev)."""
    if not name:
        return False
    text = " ".join(name.split())
    if has_entity_code(text) or _PORTAL_ANYWHERE_RE.search(text):
        return True
    if _ALNUM_CODE_RE.match(text) or _HYPHEN_CODE_RE.match(text) or _PAREN_CODE_RE.match(text):
        return True
    return bool(_TWO_DIGIT_CODE_RE.match(text) and _LEGAL_FORM_RE.search(text))


# A trailing "(…)" note on a crawl source's admin label ("CenterWell (Humana
# primary care / home health)").
_SOURCE_NOTE_RE = re.compile(r"\s*\([^)]*\)\s*$")
# A board slug standing in for a name: "lever/aledade",
# "oracle_fusion/eeho.fa.us2.oraclecloud.com/jobsearch" — no spaces. A name
# with a spaced slash ("Summit Health / CityMD") is a real name.
_SOURCE_SLUG_RE = re.compile(r"^\S+/\S+$")


def brand_from_source_name(name: str | None) -> str | None:
    """The brand a crawl source's admin label names, for showing as a company:
    None for a board slug ("lever/aledade") or a hostname
    ("jpmc.fa.oraclecloud.com"), and without a trailing "(…)" note."""
    if not name:
        return None
    bare = _SOURCE_NOTE_RE.sub("", name).strip()
    if not bare or _SOURCE_SLUG_RE.match(bare):
        return None
    return clean_company_name(bare)


def strip_entity_code(name: str) -> str:
    return _ENTITY_CODE_RE.sub("", name.strip()).strip()


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


def find_duplicate_primary(db: Session, posting: JobPosting, domain: str) -> uuid.UUID | None:
    """The id of an existing canonical JobPosting this one duplicates, or
    None if it's not a match for anything already known (i.e. it should be
    its own canonical). Only matches against already-canonical rows
    (primary_posting_id IS NULL) so groups never chain into a duplicate of a
    duplicate. `domain` is this posting's JobPostingUrl.domain.

    "Same job" = same normalized company, the same normalized title, and the
    same place (see same_place). Titles match exactly, not fuzzily: on prod a
    0.85 similarity merged "Part Sales Manager – Part Time" with "– Full
    Time", "Assistant Bakery Manager" with "Assistant Grocery Manager", and
    Lockheed's "Sr - E3" with "Stf - E4" — the one word that differs is the
    whole point, and a long shared title outweighs it.
    """
    if posting.company_key is None or posting.title_key is None:
        return None
    if not location_key(posting.locations):
        return None

    # Only the columns same_place needs — not full JobPosting objects, whose
    # description/raw_source/extracted_fields would be loaded for nothing.
    candidates = db.execute(
        select(JobPosting.id, JobPosting.locations, JobPosting.places, JobPostingUrl.domain)
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .where(
            JobPosting.company_key == posting.company_key,
            JobPosting.title_key == posting.title_key,
            JobPosting.primary_posting_id.is_(None),
            JobPosting.id != posting.id,
            # Only a visible canonical: a posting hidden as an incomplete scan
            # (see app.services.scan_completeness) or closed on its board would
            # otherwise take an open, complete duplicate out of search with it.
            JobPosting.extraction_status == ScanStatus.SUCCESS,
            JobPostingUrl.closed_at.is_(None),
        )
        .order_by(JobPostingUrl.created_at)
    ).all()

    for candidate_id, candidate_locations, candidate_places, candidate_domain in candidates:
        if same_place(
            posting.locations, posting.places, domain, candidate_locations, candidate_places, candidate_domain
        ):
            return candidate_id
    return None


def claim_primary(db: Session, posting: JobPosting, primary_id: uuid.UUID) -> None:
    """Make `posting` the canonical row of the group `primary_id` heads: the
    company's official site is the authority on its jobs, so its copy wins
    over one found first somewhere else (a job board). The old primary and
    every duplicate pointing at it re-point to `posting` — one level, as
    groups never chain."""
    db.execute(
        update(JobPosting)
        .where(
            or_(JobPosting.id == primary_id, JobPosting.primary_posting_id == primary_id),
            JobPosting.id != posting.id,
        )
        .values(primary_posting_id=posting.id)
        .execution_options(synchronize_session=False)
    )
    posting.primary_posting_id = None
