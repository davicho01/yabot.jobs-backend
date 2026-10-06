"""One-off: make every crawl source's name a usable official company name
(see app.services.company_names — the source's name is what all its jobs
show), and report what needs a human.

Reports, per source:
  - AUTO-FIX: a name that's a board slug, a hostname or carries an admin note
    ("greenhouse/clearstreet", "jpmc.fa.oraclecloud.com", "CenterWell (Humana
    primary care / home health)") -> the note stripped, else the name most of
    its jobs' pages agree on (company_names.promotable_name), else the
    company in most of its stored page titles. Written with --write, unless
    an admin already set the name (name_source "manual").
  - REVIEW: a usable-looking name that shares no word with the name most of
    its jobs' pages give ("Gem" vs "11x.ai") — maybe the wrong company.
    Never written; fix in admin.
  - SUB-BRAND?: page names on 20+ of a source's jobs that aren't a legal
    entity code or portal wording and don't share a word with the source's
    name (TJX -> "Homegoods LLC", "Marshalls of MA"). Never written as is:
    list the ones you want, with the display name, in a JSON file
    ({"<source name>": ["HomeGoods", "Marshalls"]}) and pass it with
    --sub-brands-file together with --write.

Only crawl_sources rows change: the names apply to jobs scanned from now on.
(one_off/backfill_recent_company_names.py re-applies them to the last few
days' jobs, to validate.)

Usage:
    python -m one_off.curate_source_names                     # report only
    python -m one_off.curate_source_names --write             # apply AUTO-FIX names
    python -m one_off.curate_source_names --write --sub-brands-file brands.json
"""

import argparse
import json
import logging
from collections import Counter, defaultdict

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.company_names import AUTO, MANUAL, _words, promotable_name
from app.services.job_dedup import brand_from_source_name, has_entity_code, prefers_source_brand
from one_off.backfill_clean_company_names import recovered_name

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("app.curate_source_names")

MIN_MAJORITY = 0.5
SUB_BRAND_MIN_JOBS = 20
REVIEW_MIN_JOBS = 10
_STOPWORDS = {"inc", "llc", "the", "and", "corp", "co", "company", "group", "services", "usa", "us", "of", "ltd"}


def _tokens(text: str | None) -> set[str]:
    return {t for t in _words(text).split() if len(t) > 2 and t not in _STOPWORDS}


def _page_names(db) -> dict:
    """{source_id: Counter(page company name -> jobs)}."""
    rows = db.execute(
        select(JobPostingUrl.crawl_source_id, JobPosting.page_company_name, JobPosting.company_name)
        .join(JobPosting, JobPosting.url_id == JobPostingUrl.id)
        .where(JobPostingUrl.crawl_source_id.is_not(None), JobPosting.extraction_status == ScanStatus.SUCCESS)
    ).all()
    names: dict = defaultdict(Counter)
    for source_id, page, current in rows:
        name = page if page is not None else current
        if name:
            names[source_id][name] += 1
    return names


def _title_name(db, source_id) -> str | None:
    """The company most of a source's stored page titles name, if a clear one."""
    excerpts = db.execute(
        select(JobPosting.raw_source["html_excerpt"].as_string())
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .where(JobPostingUrl.crawl_source_id == source_id)
        .limit(200)
    ).scalars()
    votes = Counter(name for name in (recovered_name(e) for e in excerpts) if name)
    if not votes:
        return None
    name, n = votes.most_common(1)[0]
    return name if n / sum(votes.values()) >= MIN_MAJORITY else None


def curate(db) -> tuple[dict, list, list]:
    """(auto_fixes {source: new name}, review [(source, dominant, share)],
    sub_brand_candidates [(source, page name, jobs)])."""
    page_names = _page_names(db)
    auto_fixes: dict = {}
    review: list = []
    candidates: list = []
    for source in db.scalars(select(CrawlSource).order_by(CrawlSource.name)):
        names = page_names.get(source.id, Counter())
        promotable = Counter()
        for name, n in names.items():
            usable = promotable_name(name)
            if usable:
                promotable[usable] += n
        dominant, share = None, 0.0
        if promotable:
            dominant, n = promotable.most_common(1)[0]
            share = n / sum(promotable.values())

        brand = brand_from_source_name(source.name)
        if brand != source.name and source.name_source != MANUAL:
            proposal = brand or (dominant if share >= MIN_MAJORITY else None) or _title_name(db, source.id)
            if proposal and proposal != source.name:
                auto_fixes[source] = proposal
        elif (
            brand
            and dominant
            and share >= MIN_MAJORITY
            and sum(names.values()) >= REVIEW_MIN_JOBS
            and not (_tokens(brand) & _tokens(dominant))
        ):
            review.append((source, dominant, share))

        own = _tokens(auto_fixes.get(source, source.name))
        for name, n in names.most_common():
            if n < SUB_BRAND_MIN_JOBS:
                break
            if has_entity_code(name) or prefers_source_brand(name) or (_tokens(name) & own):
                continue
            candidates.append((source, name, n))
    return auto_fixes, review, candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="Apply the AUTO-FIX names (and --sub-brands-file).")
    parser.add_argument("--sub-brands-file", help='JSON {"<source name>": ["Sub Brand", ...]} to set, with --write.')
    args = parser.parse_args()
    db = SessionLocal()
    try:
        auto_fixes, review, candidates = curate(db)
        for source, name in sorted(auto_fixes.items(), key=lambda kv: kv[0].name):
            logger.info("AUTO-FIX     %r -> %r  (%s)", source.name, name, source.board_url)
        for source, dominant, share in review:
            logger.info("REVIEW       %r: its pages say %r (%.0f%%)  (%s)", source.name, dominant, share * 100, source.board_url)
        for source, name, n in candidates:
            logger.info("SUB-BRAND?   %r: %r on %d job(s)", source.name, name, n)
        logger.info(
            "%d auto-fix(es), %d to review, %d sub-brand candidate(s).", len(auto_fixes), len(review), len(candidates)
        )
        if not args.write:
            return

        for source, name in auto_fixes.items():
            source.name, source.name_source = name[:255], AUTO
        if args.sub_brands_file:
            with open(args.sub_brands_file) as fh:
                wanted = json.load(fh)
            by_name = {s.name: s for s in db.scalars(select(CrawlSource))}
            for source_name, brands in wanted.items():
                source = by_name.get(source_name)
                if source is None:
                    logger.warning("No crawl source named %r; skipped.", source_name)
                    continue
                source.sub_brands = [" ".join(b.split()) for b in brands if b and b.strip()]
                logger.info("Sub-brands for %r: %s", source.name, source.sub_brands)
        db.commit()
        logger.info("Written. To validate on recent jobs: python -m one_off.backfill_recent_company_names --dry-run")
    finally:
        db.close()


if __name__ == "__main__":
    main()
