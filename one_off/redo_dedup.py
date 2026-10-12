"""One-off backfill: re-group existing postings under the current dedup rule
(app.services.job_dedup: same company, same title, same place — see
find_duplicate_primary and same_place).

_upsert_posting applies the rule on each scan/rescan, but a rule change only
reaches a row when it's rescanned. Prod on 2026-10-12 had ~55k postings
hidden from search as "duplicates" of a different store or a different title
(the old fuzzy-title + shared-metro rule), and ~19k same-store repeats left
unmerged (no metro-level location, so never compared). This brings every row
in line at once.

Per (company_key, title_key), successfully scanned rows are ordered by who
should be the primary — the official site's copy, then an open listing, then
the current primary (so groups that are still right don't change hands, and
their pages don't churn), then the oldest — and each row joins the first
primary it matches, or becomes one. Rows that aren't successfully scanned
(hidden from search anyway) keep pointing at their group, re-pointed to its
new primary so groups never chain. Writes only primary_posting_id, and only
where it changes; never scanned_at/first_scanned_at, so jobs coming back into
search don't trigger saved-search alerts (see app.services.saved_search_alerts).
Idempotent.

Use --dry-run first: it writes nothing and reports how many rows would come
back into search (un-hidden) and leave it (newly hidden), the top companies
for each, samples, and how many of those are static-page-eligible (new pages
the next generate_static_job_pages run will render, live pages it will drop).

Usage:
    python -m one_off.redo_dedup --dry-run
    python -m one_off.redo_dedup --dry-run --company-key autozone
    python -m one_off.redo_dedup --company-key autozone
    python -m one_off.redo_dedup

On prod, run it from the deployed image as a one-off Cloud Run job execution
(the `redo-dedup` job: same image/secrets/env as `migrate`, command
`python -m one_off.redo_dedup`; point it at the new image first).
"""

import argparse
import collections
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import bindparam, func, select, update

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import CrawlSourceStatus, ScanStatus
from app.models.job_posting import JobPosting
from app.models.job_url import JobPostingUrl
from app.services.job_dedup import location_key
from app.services.static_job_pages import COUNTRY_SLUG, MAX_AGE_DAYS
from app.services.static_pages import COUNTRY_ISO2_BY_SLUG

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.redo_dedup")

SAMPLES = 20


@dataclass(slots=True)
class Row:
    id: uuid.UUID
    company_key: str
    title_key: str | None
    success: bool
    official: bool
    closed: bool
    created_at: datetime
    primary_posting_id: uuid.UUID | None
    domain: str
    locations: list[str] | None
    places: list[list[float]] | None
    # Only for the report.
    company_name: str | None = None
    title: str | None = None
    location: str | None = None
    url: str | None = None
    page_eligible: bool = False


def regroup(rows: list[Row]) -> dict[uuid.UUID, uuid.UUID | None]:
    """{posting id: new primary_posting_id} for one company's rows (None =
    its own primary). The same matching as find_duplicate_primary/same_place —
    a primary is matched by exact location_key, or, from another domain, by a
    shared city — indexed so a bucket like CVS's ~3,000 Pharmacy Technicians
    isn't compared pairwise."""
    new: dict[uuid.UUID, uuid.UUID | None] = {}
    buckets: dict[str, list[Row]] = collections.defaultdict(list)
    for row in rows:
        if row.success and row.title_key is not None:
            buckets[row.title_key].append(row)
        else:
            new[row.id] = None  # placeholder; resolved below

    for bucket in buckets.values():
        bucket.sort(key=lambda r: (not r.official, r.closed, r.primary_posting_id is not None, r.created_at, str(r.id)))
        by_location: dict[tuple[str, ...], Row] = {}
        by_place: dict[tuple[float, float], list[Row]] = collections.defaultdict(list)
        for row in bucket:
            key = location_key(row.locations)
            places = {(round(lat, 4), round(lon, 4)) for lat, lon in (row.places or [])}
            primary = None
            if key:
                primary = by_location.get(key)
                if primary is None:
                    primary = next(
                        (p for place in places for p in by_place[place] if p.domain != row.domain), None
                    )
            if primary is not None:
                new[row.id] = primary.id
                continue
            new[row.id] = None
            if key:
                by_location.setdefault(key, row)
                for place in places:
                    by_place[place].append(row)

    # Rows not in the matching (not successfully scanned): follow their old
    # group to its new primary, so nothing points at a row that's now a duplicate.
    for row in rows:
        if row.success and row.title_key is not None:
            continue
        old = row.primary_posting_id
        new[row.id] = None if old is None else (new.get(old) or old)
    return new


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")
    parser.add_argument("--company-key", default=None, help="Only this company_key (e.g. 'autozone').")
    parser.add_argument("--limit", type=int, default=None, help="Look at at most this many companies.")
    parser.add_argument("--batch-size", type=int, default=200, help="Companies read/written per round trip.")
    return parser.parse_args()


def _load(db, company_keys: list[str], now: datetime) -> list[Row]:
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    us = COUNTRY_ISO2_BY_SLUG[COUNTRY_SLUG]
    stmt = (
        select(
            JobPosting.id, JobPosting.company_key, JobPosting.title_key, JobPosting.extraction_status,
            JobPosting.primary_posting_id, JobPosting.locations, JobPosting.places,
            JobPosting.company_name, JobPosting.title, JobPosting.location, JobPosting.country, JobPosting.posted_at,
            JobPostingUrl.domain, JobPostingUrl.url, JobPostingUrl.closed_at, JobPostingUrl.flagged_at,
            JobPostingUrl.created_at.label("url_created_at"),
            CrawlSource.is_official, CrawlSource.status.label("source_status"),
        )
        .join(JobPostingUrl, JobPostingUrl.id == JobPosting.url_id)
        .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPosting.company_key.in_(company_keys))
    )
    rows = []
    for r in db.execute(stmt):
        success = r.extraction_status == ScanStatus.SUCCESS
        url_created = r.url_created_at if r.url_created_at.tzinfo else r.url_created_at.replace(tzinfo=timezone.utc)
        recent = r.posted_at >= cutoff.date() if isinstance(r.posted_at, date) else url_created >= cutoff
        rows.append(
            Row(
                id=r.id,
                company_key=r.company_key,
                title_key=r.title_key,
                success=success,
                official=bool(r.is_official) and r.source_status == CrawlSourceStatus.ACTIVE,
                closed=r.closed_at is not None,
                created_at=url_created,
                primary_posting_id=r.primary_posting_id,
                domain=r.domain,
                locations=r.locations,
                places=r.places,
                company_name=r.company_name,
                title=r.title,
                location=r.location,
                url=r.url,
                # static_job_pages._eligible_filter, minus the canonical check.
                page_eligible=success and r.title is not None and r.country == us and r.flagged_at is None
                and r.closed_at is None and recent,
            )
        )
    return rows


def main() -> None:
    args = _parse_args()
    now = datetime.now(timezone.utc)
    table = JobPosting.__table__
    # Core (table-level) UPDATE: an ORM-enabled one with extra WHERE criteria
    # can't be used with executemany.
    write = update(table).where(table.c.id == bindparam("posting_id")).values(primary_posting_id=bindparam("new_primary"))

    rng = random.Random(0)
    db = SessionLocal()
    try:
        keys_stmt = (
            select(JobPosting.company_key)
            .where(JobPosting.company_key.is_not(None))
            .group_by(JobPosting.company_key)
            .having(func.count() > 1)
            .order_by(JobPosting.company_key)
        )
        if args.company_key:
            keys_stmt = keys_stmt.where(JobPosting.company_key == args.company_key)
        company_keys = list(db.scalars(keys_stmt))
        if args.limit:
            company_keys = company_keys[: args.limit]
        logger.info("%d compan(ies) with more than one posting.", len(company_keys))

        seen = unchanged = repointed = 0
        unhidden: collections.Counter[str] = collections.Counter()
        hidden: collections.Counter[str] = collections.Counter()
        unhidden_pages = hidden_pages = 0
        unhidden_samples: list[str] = []
        hidden_samples: list[str] = []

        def sample(bucket: list[str], line: str, count: int) -> None:
            # Reservoir sampling: SAMPLES rows uniformly from everything seen.
            if len(bucket) < SAMPLES:
                bucket.append(line)
            elif (j := rng.randrange(count)) < SAMPLES:
                bucket[j] = line

        for start in range(0, len(company_keys), args.batch_size):
            rows = _load(db, company_keys[start : start + args.batch_size], now)
            by_id = {row.id: row for row in rows}
            by_company: dict[str, list[Row]] = collections.defaultdict(list)
            for row in rows:
                by_company[row.company_key].append(row)

            updates = []
            for company_rows in by_company.values():
                for posting_id, new_primary in regroup(company_rows).items():
                    row = by_id[posting_id]
                    seen += 1
                    if new_primary == row.primary_posting_id:
                        unchanged += 1
                        continue
                    updates.append({"posting_id": posting_id, "new_primary": new_primary})
                    if not row.success:
                        repointed += 1
                    elif new_primary is None:
                        unhidden[row.company_key] += 1
                        unhidden_pages += row.page_eligible
                        old = by_id.get(row.primary_posting_id)
                        sample(
                            unhidden_samples,
                            f"{row.company_name} | {row.title} | {row.location} ({row.url})"
                            f"  was dup of: {old.title if old else '?'} | {old.location if old else '?'}",
                            sum(unhidden.values()),
                        )
                    else:
                        was_primary = row.primary_posting_id is None
                        if was_primary:
                            hidden[row.company_key] += 1
                            hidden_pages += row.page_eligible
                            primary = by_id[new_primary]
                            sample(
                                hidden_samples,
                                f"{row.company_name} | {row.title} | {row.location} ({row.url})"
                                f"  -> dup of: {primary.title} | {primary.location} ({primary.url})",
                                sum(hidden.values()),
                            )
                        else:
                            repointed += 1

            if updates and not args.dry_run:
                db.execute(write, updates)
                db.commit()
            logger.info(
                "%d/%d companies: %d rows seen, %d un-hidden, %d newly hidden, %d re-pointed so far.",
                min(start + args.batch_size, len(company_keys)), len(company_keys), seen,
                sum(unhidden.values()), sum(hidden.values()), repointed,
            )

        prefix = "[dry-run] " if args.dry_run else ""
        logger.info(
            "%sDone. %d rows: %d unchanged, %d back in search (%d static-page-eligible), "
            "%d newly hidden (%d static-page-eligible), %d re-pointed within a group.",
            prefix, seen, unchanged, sum(unhidden.values()), unhidden_pages, sum(hidden.values()), hidden_pages,
            repointed,
        )
        for label, counter in (("back in search", unhidden), ("newly hidden", hidden)):
            logger.info("Top companies, %s:", label)
            for company_key, count in counter.most_common(20):
                logger.info("  %8d  %s", count, company_key)
        for label, lines in (("back in search", unhidden_samples), ("newly hidden", hidden_samples)):
            logger.info("Samples, %s:", label)
            for line in lines:
                logger.info("  %s", line)
    finally:
        db.close()


if __name__ == "__main__":
    main()
