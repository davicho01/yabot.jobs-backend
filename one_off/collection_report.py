"""Report-only: score today's hub pages, and a bounded set of new
collection candidates, with the qualification rules
(app.services.collection_qualification) — to calibrate the thresholds
before anything acts on them (Q-016). Reads the live job index the static
generator builds; writes nothing to the database, S3 or CloudFront.

Scored:
  company          every company hub (COMPANY_MIN_JOBS+ open jobs)
  location         every metro/micro/state hub, and the remote hub
                   (LOCATION_MIN_JOBS+)
  category_location every metro × sector hub, and remote × sector
                   (category_remote), each against its location as parent
  role             each standard tech title with any open job — new
  role_remote      ... and its remote jobs, against the role — new

Prints a CSV row per collection to stdout and a summary (kind ×
recommendation) to the log.

Usage:
    python -m one_off.collection_report > report.csv
"""

import csv
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.enums import WorkplaceType
from app.services import geo
from app.services.collection_qualification import WEIGHTS, Evaluation, evaluate_jobs
from app.services.static_hub_pages import COMPANY_MIN_JOBS, LOCATION_MIN_JOBS
from app.services.static_job_pages import OTHER_SECTOR_SLUG, LinkJob, load_link_index, sector_slug, slugify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.collection_report")

COLUMNS = ["kind", "key", "name", "open_jobs", "companies_hiring", "total", "recommendation", "issues"] + [
    f"{k}_score" for k in WEIGHTS
]


def candidates(live: list[LinkJob]) -> list[tuple[str, str, str, list[LinkJob], int | None, bool]]:
    """(kind, key, name, jobs, parent size, single employer) for everything
    scored, grouped in one pass over the live jobs."""
    by_company: dict[str, list[LinkJob]] = defaultdict(list)
    by_area: dict[str, list[LinkJob]] = defaultdict(list)
    by_area_sector: dict[tuple[str, str], list[LinkJob]] = defaultdict(list)
    by_role: dict[str, list[LinkJob]] = defaultdict(list)
    for job in live:
        if job.company_key and slugify(job.company_key):
            by_company[job.company_key].append(job)
        remote = job.workplace_type == WorkplaceType.REMOTE
        areas = list(job.metros) + (["remote"] if remote else [])
        sector = sector_slug(job.sector)
        for code in areas:
            by_area[code].append(job)
            if sector != OTHER_SECTOR_SLUG and (code == "remote" or getattr(geo.metro_by_code(code), "kind", "state") != "state"):
                by_area_sector[(code, sector)].append(job)
        for title in job.standard_titles:
            by_role[title].append(job)

    def area_name(code: str) -> str | None:
        if code == "remote":
            return "Remote"
        area = geo.metro_by_code(code)
        return area.name if area else None

    out = []
    for key, jobs in by_company.items():
        if len(jobs) >= COMPANY_MIN_JOBS:
            out.append(("company", key, Counter(j.company_display for j in jobs).most_common(1)[0][0], jobs, None, True))
    for code, jobs in by_area.items():
        if len(jobs) >= LOCATION_MIN_JOBS and area_name(code):
            out.append(("location", code, area_name(code), jobs, None, False))
    for (code, sector), jobs in by_area_sector.items():
        if len(jobs) >= LOCATION_MIN_JOBS and len(by_area[code]) >= LOCATION_MIN_JOBS and area_name(code):
            kind = "category_remote" if code == "remote" else "category_location"
            out.append((kind, f"{code}/{sector}", f"{sector} in {area_name(code)}", jobs, len(by_area[code]), False))
    for title, jobs in by_role.items():
        out.append(("role", slugify(title), title, jobs, None, False))
        remote = [j for j in jobs if j.workplace_type == WorkplaceType.REMOTE]
        if remote:
            out.append(("role_remote", f"{slugify(title)}/remote", f"Remote {title}", remote, len(jobs), False))
    return out


def row(kind: str, key: str, name: str, e: Evaluation) -> dict:
    return {
        "kind": kind, "key": key, "name": name, "open_jobs": e.stats.open_jobs,
        "companies_hiring": e.stats.companies_hiring, "total": e.total, "recommendation": e.recommendation.value,
        "issues": " ".join(e.issues), **{f"{k}_score": getattr(e, f"{k}_score") for k in WEIGHTS},
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        index = load_link_index(db, now)
    live = [job for job in index.values() if not job.closed]
    logger.info("%d live jobs, %d open.", len(index), len(live))

    writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS)
    writer.writeheader()
    summary: Counter = Counter()
    for kind, key, name, jobs, parent, single in candidates(live):
        e = evaluate_jobs(jobs, now, parent_size=parent, single_employer=single)
        writer.writerow(row(kind, key, name, e))
        summary[(kind, e.recommendation.value)] += 1
    for kind in sorted({k for k, _ in summary}):
        logger.info("%s: %s", kind, ", ".join(f"{r} {summary[(kind, r)]}" for r in ("index", "review", "noindex")))


if __name__ == "__main__":
    main()
