"""One-off: "no fake jobs" verification audit.

Samples random still-open jobs, stratified by ATS platform, and re-fetches
each one's original apply URL with the real scanner (scan_job_url — a pure
function, no DB writes) to see whether the posting is still live. The result
backs a marketing claim like "X% of our jobs verified open", so it never
mutates anything: it reads the sample, calls the scanner, writes a CSV.

Classification per job:
    open         scanner read the page and it is a live posting
    closed       page is gone (404/410) or marked expired (validThrough, ...)
    unreachable  fetch failed for another reason (WAF, timeout, 5xx, parse
                 error). Reported separately; NOT counted as closed or open.

The headline rate is open / (open + closed); unreachable is shown beside it
so a high unreachable share can't hide inside the number.

Caveat: some ATSes keep serving a delisted page, so "open" is an upper
bound until a hand check of a subset (see the plan) confirms it.

Usage:
    python -m one_off.verify_open_audit --limit 20 --dry-run   # sample only, no fetches
    python -m one_off.verify_open_audit --limit 20
    python -m one_off.verify_open_audit --limit 400 --out /path/audit.csv
"""

import argparse
import csv
import logging
import math
import random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.crawl_source import CrawlSource
from app.models.enums import ScanStatus
from app.models.job_url import JobPostingUrl
from app.services.job_scanner import scan_job_url

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("app.verify_open_audit")
logger.setLevel(logging.INFO)

USER_SUBMITTED = "(user-submitted)"


POOL_QUERY = (
    "SELECT u.id, u.url, u.last_scanned_at, s.ats_type, s.board_url "
    "FROM job_posting_urls u LEFT JOIN crawl_sources s ON s.id = u.crawl_source_id "
    "WHERE u.closed_at IS NULL AND u.scan_status = 'success'"
)


def pool_from_db(db) -> list[tuple]:
    return db.execute(
        select(JobPostingUrl.id, JobPostingUrl.url, JobPostingUrl.last_scanned_at, CrawlSource.ats_type, CrawlSource.board_url)
        .outerjoin(CrawlSource, CrawlSource.id == JobPostingUrl.crawl_source_id)
        .where(JobPostingUrl.closed_at.is_(None), JobPostingUrl.scan_status == ScanStatus.SUCCESS)
    ).all()


def pool_from_csv(path: str) -> list[tuple]:
    """A pool exported from prod with POOL_QUERY (psql \\copy ... CSV HEADER),
    so the audit never needs a prod connection of its own."""
    with open(path, newline="") as fh:
        return [
            (r["id"], r["url"], r["last_scanned_at"], r["ats_type"] or None, r["board_url"] or None)
            for r in csv.DictReader(fh)
        ]


def sample_jobs(rows: list[tuple], limit: int, per_platform_cap: int, seed: int | None) -> list[dict]:
    """Random open jobs, at most per_platform_cap per platform, topped up
    (still randomly) from the rest if the caps leave the sample short."""
    rng = random.Random(seed)
    by_platform: dict[str, list] = defaultdict(list)
    for url_id, url, last_scanned_at, ats_type, board_url in rows:
        by_platform[ats_type or USER_SUBMITTED].append(
            {"url_id": str(url_id), "url": url, "platform": ats_type or USER_SUBMITTED,
             "board_url": board_url, "last_scanned_at": last_scanned_at}
        )
    for jobs in by_platform.values():
        rng.shuffle(jobs)

    # Proportional quota per platform, capped, so big ATSes don't dominate.
    total = sum(len(j) for j in by_platform.values())
    chosen: list[dict] = []
    leftovers: list[dict] = []
    for jobs in by_platform.values():
        quota = min(per_platform_cap, max(1, math.ceil(limit * len(jobs) / total)))
        chosen.extend(jobs[:quota])
        leftovers.extend(jobs[quota:])
    rng.shuffle(chosen)
    if len(chosen) > limit:
        chosen = chosen[:limit]
    elif len(chosen) < limit:
        rng.shuffle(leftovers)
        chosen.extend(leftovers[: limit - len(chosen)])
    logger.info("Open jobs in pool: %d across %d platform(s).", total, len(by_platform))
    return chosen


def check(job: dict) -> dict:
    try:
        result = scan_job_url(job["url"], board_url=job["board_url"])
    except Exception as exc:  # the scanner should not raise; if it does, that's unreachable
        return {**job, "verdict": "unreachable", "detail": f"{type(exc).__name__}: {exc}"[:200]}
    if result.expired:
        return {**job, "verdict": "closed", "detail": (result.error or "expired")[:200]}
    if result.success:
        return {**job, "verdict": "open", "detail": ""}
    return {**job, "verdict": "unreachable", "detail": (result.error or "unknown")[:200]}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=400, help="Jobs to sample (default 400).")
    parser.add_argument("--per-platform-cap", type=int, default=40)
    parser.add_argument("--workers", type=int, default=6, help="Concurrent fetches (default 6; keep polite).")
    parser.add_argument("--seed", type=int, help="Make the sample reproducible.")
    parser.add_argument("--pool-csv", help="Sample from a CSV exported from prod (see POOL_QUERY) instead of the DB.")
    parser.add_argument("--out", help="CSV path (default: audit_<timestamp>.csv in the current directory).")
    parser.add_argument("--dry-run", action="store_true", help="Show the sample's platform mix; fetch nothing.")
    args = parser.parse_args()

    if args.pool_csv:
        rows = pool_from_csv(args.pool_csv)
    else:
        db = SessionLocal()
        try:
            rows = pool_from_db(db)
        finally:
            db.close()
    jobs = sample_jobs(rows, args.limit, args.per_platform_cap, args.seed)

    logger.info("Sampled %d job(s):", len(jobs))
    for platform, n in Counter(j["platform"] for j in jobs).most_common():
        logger.info("%6d  %s", n, platform)
    if args.dry_run:
        return

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(check, j) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if i % 25 == 0:
                logger.info("  checked %d/%d", i, len(jobs))

    out = args.out or f"audit_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["url_id", "platform", "verdict", "detail", "last_scanned_at", "url"], extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(sorted(results, key=lambda r: (r["platform"], r["verdict"])))

    verdicts = Counter(r["verdict"] for r in results)
    n_open, n_closed, n_unreach = verdicts["open"], verdicts["closed"], verdicts["unreachable"]
    decided = n_open + n_closed
    logger.info("\nChecked %d: open %d, closed %d, unreachable %d", len(results), n_open, n_closed, n_unreach)
    if decided:
        lo, hi = wilson(n_open, decided)
        logger.info("Verified open: %.1f%% of %d decided (95%% CI %.1f%%-%.1f%%)", 100 * n_open / decided, decided, 100 * lo, 100 * hi)
    logger.info("Unreachable share: %.1f%% of all checked", 100 * n_unreach / len(results))
    per_platform: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        per_platform[r["platform"]][r["verdict"]] += 1
    logger.info("\n%-22s %5s %5s %6s %11s", "platform", "n", "open", "closed", "unreachable")
    for platform, c in sorted(per_platform.items(), key=lambda kv: -sum(kv[1].values())):
        logger.info("%-22s %5d %5d %6d %11d", platform, sum(c.values()), c["open"], c["closed"], c["unreachable"])
    logger.info("\nCSV: %s", out)


if __name__ == "__main__":
    main()
