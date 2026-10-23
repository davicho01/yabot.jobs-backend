"""Adaptive per-source crawl interval (app.services.crawl_schedule)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import CrawlSource
from app.services import crawl_schedule
from app.services.crawl_schedule import next_interval, schedule_next_crawl


@pytest.fixture(autouse=True)
def _range(monkeypatch):
    monkeypatch.setattr(crawl_schedule.settings, "crawl_min_interval_minutes", 120)
    monkeypatch.setattr(crawl_schedule.settings, "crawl_max_interval_minutes", 4320)


def test_new_jobs_halve_the_interval():
    assert next_interval(720, 5, listing_failed=False) == 360


def test_no_new_jobs_grow_the_interval():
    assert next_interval(720, 0, listing_failed=False) == 1080


def test_interval_never_drops_below_the_minimum():
    assert next_interval(180, 50, listing_failed=False) == 120


def test_interval_never_exceeds_the_maximum():
    assert next_interval(4000, 0, listing_failed=False) == 4320


def test_failed_listing_keeps_the_interval():
    assert next_interval(720, 0, listing_failed=True) == 720


def test_failed_listing_still_clamps_into_a_changed_range():
    # Raising the minimum takes effect on the source's next crawl, even a failed one.
    assert next_interval(30, 0, listing_failed=True) == 120


def test_schedule_sets_next_crawl_within_jitter():
    source = CrawlSource(name="s", board_url="https://example.com", crawl_interval_minutes=720)
    now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)

    schedule_next_crawl(source, now, 3)

    assert source.crawl_interval_minutes == 360
    assert now + timedelta(minutes=324) <= source.next_crawl_at <= now + timedelta(minutes=396)
