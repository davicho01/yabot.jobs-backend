"""Whether a successful scan actually got the job's content.

A scan counts as successful whenever the page answers with a title, even if
all it got was a teaser: the page's meta/og description, the location
repeated as the description, empty section headings, an "Apply to X at Y"
line, or nothing. A prod audit on 2026-10-12 (5% sample of 704,816 open
jobs) found ~4.4% of them like that, across Workable, Greenhouse, Gupy,
Best Buy, JazzHR, Eightfold, PeopleAdmin, Ashby, Taleo and others.

The one signal that separated them was description length: descriptions
under 300 characters were their own cluster (1,542 of the sample), with only
71 between 300 and 500 and the 5th percentile at 834. Missing location/date
and "..." endings didn't separate them (many broken ones have a location;
many complete Oracle descriptions end in "..."), and placeholder titles
("Jobs", "Página da Vaga") only ever came with a short description anyway.

app.services.jobs._apply_scan_result keeps an incomplete job visible but
retries its URL like a failed scan, so one that never completes ends up in
needs_review with scan_error starting with INCOMPLETE_SCAN_ERROR_PREFIX.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.adapters.base import ScanResult

INCOMPLETE_SCAN_ERROR_PREFIX = "Incomplete scan: "


def is_complete_description(description: str | None) -> bool:
    return len((description or "").strip()) >= settings.scan_min_description_chars


def description_reasons(description: str | None) -> list[str]:
    """Why a stored or scanned description looks incomplete; empty when it's fine."""
    description = (description or "").strip()
    if not description:
        return ["no description"]
    if not is_complete_description(description):
        return [f"description is {len(description)} characters (under {settings.scan_min_description_chars})"]
    return []


def incomplete_reasons(result: ScanResult) -> list[str]:
    """Why a successful scan's content looks incomplete; empty when it's fine."""
    return description_reasons(result.description)
