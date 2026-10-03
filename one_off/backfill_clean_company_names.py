"""One-off backfill: clean up JobPosting.company_name values that are careers
wording or a hostname instead of a company name, and recompute company_key
to match — app.services.job_dedup.clean_company_name, which
app.services.jobs._upsert_posting now applies on every scan, only fixes
postings going forward. Measured live on 2026-10-03: ~3,800 open jobs across
~30 companies, e.g. "Careers at Marriott" (796), "starbucks.eightfold.ai"
(736), "Quest Diagnostics Careers" (469), "lockheedmartin.eightfold.ai" (464).

  - Careers wording ("Careers at Marriott" -> "Marriott"): the new name only
    depends on the old one, so each distinct name is one bulk UPDATE.
  - A hostname ("starbucks.eightfold.ai"): the real name comes from the
    posting's own stored page title, "<job> | Starbucks Coffee Company" —
    the same rule the Eightfold adapter now uses on new scans
    (adapters.eightfold._title_company), so backfilled and newly scanned jobs
    end up with the identical name (and company_key). A posting whose stored
    excerpt doesn't reach the <title> gets the name found for most of the
    other postings from its crawl source; with none found it's left as is
    and reported.

primary_posting_id is left alone: every posting of a company is renamed
together, so links between duplicates of the same company stay valid.
Idempotent — a cleaned name cleans to itself.

Usage:
    python -m one_off.backfill_clean_company_names --dry-run   # every old -> new mapping, with counts
    python -m one_off.backfill_clean_company_names             # write

On prod, run it from the deployed image as a one-off Cloud Run job execution
(same image/secrets/Cloud SQL as generate-job-pages-backfill), --dry-run first.
"""

import argparse
import logging
from collections import Counter

from sqlalchemy import bindparam, func, select, update

from app.db.session import SessionLocal
from app.models.enums import ScanStatus
from app.models.job_posting import JobPosting
from app.services.adapters.eightfold import _title_company
from app.services.job_dedup import clean_company_name, normalize_company_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.backfill_clean_company_names")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report every old -> new mapping without writing.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Per-posting updates per round trip.")
    return parser.parse_args()


# Page-title parts that aren't a company ("Welcome" on some iCIMS portals).
_GENERIC_TITLE_PARTS = {"welcome", "home", "careers", "career", "jobs", "search jobs", "job search", "job details", "search"}
# Share of a hostname's postings whose titles must agree on its name.
MIN_MAJORITY = 0.6


def recovered_name(excerpt: str | None) -> str | None:
    """The company named in a stored page's title — the same "<job> |
    <Company>" rule the Eightfold adapter uses — with "<team> at <Company>"
    reduced to the company ("Optum Washington at UnitedHealth Group" ->
    "UnitedHealth Group") and generic words rejected."""
    name = _title_company(excerpt or "")
    if not name:
        return None
    if " at " in name:
        name = name.rsplit(" at ", 1)[1].strip()
    if not name or name.lower() in _GENERIC_TITLE_PARTS:
        return None
    return name


def plan_renames(db) -> tuple[dict[str, str], dict[str, list[tuple]], list[str]]:
    """({old: new} for careers wording, {hostname: [(posting_id, new), ...]},
    [hostnames left unresolved])."""
    names = db.execute(
        select(JobPosting.company_name, func.count())
        .where(JobPosting.extraction_status == ScanStatus.SUCCESS, JobPosting.company_name.is_not(None))
        .group_by(JobPosting.company_name)
    ).all()
    by_name: dict[str, str] = {}
    hostnames: list[str] = []
    for name, _count in names:
        cleaned = clean_company_name(name)
        if cleaned is None:
            hostnames.append(name)
        elif cleaned != name:
            by_name[name] = cleaned

    by_posting: dict[str, list[tuple]] = {}
    unresolved: list[str] = []
    for host in hostnames:
        rows = db.execute(
            select(JobPosting.id, JobPosting.raw_source["html_excerpt"].as_string())
            .where(JobPosting.company_name == host, JobPosting.extraction_status == ScanStatus.SUCCESS)
        ).all()
        votes = Counter(name for name in (recovered_name(excerpt) for _, excerpt in rows) if name)
        name, n = votes.most_common(1)[0] if votes else (None, 0)
        # One name for every posting of a hostname, and only a clear winner:
        # per-posting titles go wrong on odd pages (a few of jobs.sap.com's
        # are "<job> | <country>"), the majority doesn't.
        if name and n / sum(votes.values()) >= MIN_MAJORITY:
            by_posting[host] = [(posting_id, name) for posting_id, _ in rows]
        else:
            unresolved.append(f"{host} ({len(rows)} posting(s); title votes: {dict(votes.most_common(3))})")
    return by_name, by_posting, unresolved


def main() -> None:
    args = _parse_args()
    db = SessionLocal()
    try:
        by_name, by_posting, unresolved = plan_renames(db)
        counts = dict(
            db.execute(
                select(JobPosting.company_name, func.count())
                .where(JobPosting.company_name.in_(list(by_name)), JobPosting.extraction_status == ScanStatus.SUCCESS)
                .group_by(JobPosting.company_name)
            ).all()
        ) if by_name else {}
        for old, new in sorted(by_name.items(), key=lambda kv: -counts.get(kv[0], 0)):
            logger.info("%6d  %r -> %r", counts.get(old, 0), old, new)
        for host, renames in sorted(by_posting.items(), key=lambda kv: -len(kv[1])):
            for new, n in Counter(name for _, name in renames).most_common():
                logger.info("%6d  %r -> %r", n, host, new)
        for item in unresolved:
            logger.warning("Left as is (no clear company name in its titles): %s", item)
        total = sum(counts.values()) + sum(len(r) for r in by_posting.values())
        logger.info(
            "%s %d posting(s): %d careers-wording name(s), %d hostname name(s).",
            "Would rename" if args.dry_run else "Renaming", total, len(by_name), len(by_posting),
        )
        if args.dry_run:
            return

        for old, new in by_name.items():
            db.execute(
                update(JobPosting)
                .where(JobPosting.company_name == old)
                .values(company_name=new, company_key=normalize_company_name(new))
                .execution_options(synchronize_session=False)
            )
        db.commit()

        table = JobPosting.__table__
        write = (
            update(table)
            .where(table.c.id == bindparam("posting_id"))
            .values(company_name=bindparam("new_name"), company_key=bindparam("new_key"))
        )
        params = [
            {"posting_id": pid, "new_name": name, "new_key": normalize_company_name(name)}
            for renames in by_posting.values()
            for pid, name in renames
        ]
        for start in range(0, len(params), args.batch_size):
            db.execute(write, params[start : start + args.batch_size])
            db.commit()
        logger.info("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
