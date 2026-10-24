"""Adaptive per-source crawl schedule.

Crawling every board on the same fixed cadence wastes crawls on boards that
rarely post, and is too slow for boards that post hundreds of jobs a day
(those can overflow an adapter's DEFAULT_MAX_JOBS_PER_CRAWL listing between
crawls and lose jobs). Instead each CrawlSource keeps its own
crawl_interval_minutes, adjusted after every crawl:

- the crawl found new URLs -> halve the interval (busy boards converge on
  settings.crawl_min_interval_minutes quickly)
- it found nothing new -> grow it 1.5x (quiet boards drift towards
  settings.crawl_max_interval_minutes)
- the listing failed -> leave it alone (a broken board is never retried
  faster than it was being crawled)

At equilibrium a source is crawled at roughly the pace where ~37% of its
crawls find something new. crawl_dispatcher.py picks the sources whose
next_crawl_at has passed.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.crawl_source import CrawlSource

_SHRINK = 0.5
_GROW = 1.5
# Spreads sources that share an interval so they don't all come due in the
# same dispatch run.
_JITTER = 0.1


def clamp_interval(minutes: float) -> int:
    return int(max(settings.crawl_min_interval_minutes, min(settings.crawl_max_interval_minutes, round(minutes))))


def next_interval(current: int, new_count: int, *, listing_failed: bool) -> int:
    if listing_failed:
        return clamp_interval(current)
    return clamp_interval(current * (_SHRINK if new_count > 0 else _GROW))


# A board set uncapped (CrawlSource.uncapped) is read in full once a day —
# enough to close jobs that came down — and crawled the usual capped way the
# rest of the time. Crawling stops overnight, so "20h since the last full
# read" alone would put every board's full read in the first morning run;
# instead each board has its own slot in the 8:00-20:00 ET crawl day, from a
# stable hash of its id, which scatters full reads evenly through the day.
FULL_READ_EVERY = timedelta(hours=20)
_FULL_READ_ZONE = ZoneInfo("America/New_York")
_FULL_READ_DAY_START = time(8, 0)
_FULL_READ_DAY_MINUTES = 12 * 60


def full_read_slot(source: CrawlSource) -> time:
    """The ET time of day after which this board's daily full read is due."""
    minutes = _FULL_READ_DAY_START.hour * 60 + int(source.id.hex, 16) % _FULL_READ_DAY_MINUTES
    return time(minutes // 60, minutes % 60)


def full_read_due(source: CrawlSource, now: datetime) -> bool:
    """Whether this crawl of an uncapped board should read the whole board:
    at least FULL_READ_EVERY since the last one, and past its slot today."""
    last = source.uncapped_crawled_at
    if last is not None and last.tzinfo is None:  # SQLite (tests) hands back naive UTC
        last = last.replace(tzinfo=timezone.utc)
    if last is not None and now - last < FULL_READ_EVERY:
        return False
    return now.astimezone(_FULL_READ_ZONE).time() >= full_read_slot(source)


def schedule_next_crawl(source: CrawlSource, now: datetime, new_count: int, *, listing_failed: bool = False) -> None:
    source.crawl_interval_minutes = next_interval(source.crawl_interval_minutes, new_count, listing_failed=listing_failed)
    jittered = source.crawl_interval_minutes * random.uniform(1 - _JITTER, 1 + _JITTER)
    source.next_crawl_at = now + timedelta(minutes=jittered)

