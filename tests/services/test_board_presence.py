"""Closure detection: record_board_presence after a crawl, the crawl worker's
healthy-crawl gate around it, a scan that finds the posting gone, and closed
listings dropping out of search."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import crawl_worker
from app.models import CrawlSource, JobPosting, JobPostingUrl
from app.models.enums import ScanStatus
from app.services import jobs as jobs_service
from app.services.adapters.base import BoardListing, ScanResult
from app.services.crawl_schedule import full_read_due, full_read_slot
from app.services.jobs import _apply_scan_result, build_job_search_statement, bulk_register_discovered_urls, record_board_presence

URLS = [f"https://example.com/jobs/{i}" for i in range(3)]


def _register(scan_db, source, urls, now):
    bulk_register_discovered_urls(scan_db, urls, source.id)
    record_board_presence(scan_db, source.id, urls, healthy=True, now=now)
    scan_db.commit()


def _rows(scan_db, source):
    scan_db.expire_all()
    return {row.url: row for row in scan_db.query(JobPostingUrl).filter_by(crawl_source_id=source.id)}


def test_new_urls_are_stamped_seen_and_open(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    rows = _rows(scan_db, source)
    assert {r.last_seen_at.replace(tzinfo=None) for r in rows.values()} == {now.replace(tzinfo=None)}
    assert all(r.closed_at is None for r in rows.values())


def _miss_twice(scan_db, source, urls, now):
    """Two healthy crawls 2h apart that list only `urls`; returns how many
    the second one closed."""
    record_board_presence(scan_db, source.id, urls, healthy=True, now=now + timedelta(hours=2))
    scan_db.commit()
    closed = record_board_presence(scan_db, source.id, urls, healthy=True, now=now + timedelta(hours=4))
    scan_db.commit()
    return closed


def test_a_url_missing_from_two_healthy_crawls_in_a_row_closes(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    closed = _miss_twice(scan_db, source, URLS[:2], now)

    rows = _rows(scan_db, source)
    assert closed == 1
    assert rows[URLS[2]].closed_at is not None
    assert rows[URLS[0]].closed_at is None and rows[URLS[1]].closed_at is None


def test_one_miss_only_marks_the_url_missing(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    closed = record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=2))
    scan_db.commit()

    rows = _rows(scan_db, source)
    assert closed == 0
    assert rows[URLS[2]].closed_at is None
    assert rows[URLS[2]].missing_since.replace(tzinfo=None) == (now + timedelta(hours=2)).replace(tzinfo=None)
    assert rows[URLS[0]].missing_since is None


def test_a_url_listed_again_between_misses_starts_over(scan_db, make_source, now):
    # A one-off flicker: missed, listed, missed. That's one miss, not two.
    source = make_source()
    _register(scan_db, source, URLS, now)

    record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=2))
    record_board_presence(scan_db, source.id, URLS, healthy=True, now=now + timedelta(hours=4))
    closed = record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=6))
    scan_db.commit()

    row = _rows(scan_db, source)[URLS[2]]
    assert closed == 0
    assert row.closed_at is None
    assert row.missing_since.replace(tzinfo=None) == (now + timedelta(hours=6)).replace(tzinfo=None)


def test_a_crawl_rerun_straight_away_is_not_a_second_miss(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=2))
    closed = record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=2, minutes=5))
    scan_db.commit()

    assert closed == 0
    assert _rows(scan_db, source)[URLS[2]].closed_at is None


def test_an_unhealthy_crawl_between_misses_neither_counts_nor_resets(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=2))
    record_board_presence(scan_db, source.id, URLS[:1], healthy=False, now=now + timedelta(hours=3))
    closed = record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=4))
    scan_db.commit()

    rows = _rows(scan_db, source)
    assert closed == 1
    assert rows[URLS[2]].closed_at is not None
    assert rows[URLS[1]].missing_since is None  # the unhealthy crawl's miss didn't count


def test_unhealthy_crawl_never_closes_anything(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    closed = record_board_presence(scan_db, source.id, [], healthy=False, now=now + timedelta(days=30))
    scan_db.commit()

    assert closed == 0
    assert all(r.closed_at is None for r in _rows(scan_db, source).values())


def test_closed_url_reopens_when_its_board_lists_it_again(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)
    _miss_twice(scan_db, source, URLS[:2], now)

    record_board_presence(scan_db, source.id, URLS, healthy=True, now=now + timedelta(hours=50))
    scan_db.commit()

    row = _rows(scan_db, source)[URLS[2]]
    assert row.closed_at is None and row.missing_since is None


def test_presence_is_scoped_to_its_own_source(scan_db, make_source, now):
    a, b = make_source(), make_source()
    _register(scan_db, a, URLS[:1], now)
    _register(scan_db, b, URLS[1:2], now)

    _miss_twice(scan_db, a, URLS[:1], now)

    row = _rows(scan_db, b)[URLS[1]]
    assert row.closed_at is None and row.missing_since is None


def _crawl(monkeypatch, scan_db, source, urls, when, complete=True):
    monkeypatch.setattr(crawl_worker, "list_board", lambda ats_type, board_url, **_: BoardListing(urls, complete=complete))
    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", lambda source_id, lanes=1: None)
    real = jobs_service.record_board_presence
    monkeypatch.setattr(
        crawl_worker,
        "record_board_presence",
        lambda db, source_id, raw_urls, *, healthy: real(db, source_id, raw_urls, healthy=healthy, now=when),
    )
    crawl_worker._crawl_source(scan_db, source.id)


# 20:30 EDT, after every board's full-read slot (8:00-20:00 ET).
EVENING = datetime(2026, 9, 22, 0, 30, tzinfo=timezone.utc)


def test_an_uncapped_source_reads_in_full_once_a_day_and_sets_its_baseline_once(monkeypatch, scan_db, make_source):
    source = make_source()
    source.uncapped = True
    scan_db.commit()
    calls = []

    def fake_list_board(ats_type, board_url, *, uncapped=False):
        calls.append(uncapped)
        return BoardListing(URLS)

    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", lambda source_id, lanes=1: None)
    monkeypatch.setattr(crawl_worker, "list_board", fake_list_board)

    crawl_worker._crawl_source(scan_db, source.id, now=EVENING)
    scan_db.expire_all()
    stored = scan_db.get(CrawlSource, source.id)
    baseline, read_at = stored.uncapped_baseline_at, stored.uncapped_crawled_at
    crawl_worker._crawl_source(scan_db, source.id, now=EVENING + timedelta(hours=2))  # same day: capped
    crawl_worker._crawl_source(scan_db, source.id, now=EVENING + timedelta(days=1))  # next day: full again
    scan_db.expire_all()

    assert calls == [True, False, True]
    assert baseline is not None and read_at is not None
    assert scan_db.get(CrawlSource, source.id).uncapped_baseline_at == baseline  # set by the first full read only
    # The first full read only records the board: nothing undated is scanned.
    assert {r.scan_status for r in _rows(scan_db, source).values()} == {ScanStatus.SKIPPED}


def test_a_full_read_cut_off_by_the_time_budget_still_waits_a_day(monkeypatch, scan_db, make_source):
    source = make_source()
    source.uncapped = True
    scan_db.commit()
    calls = []

    def fake_list_board(ats_type, board_url, *, uncapped=False):
        calls.append(uncapped)
        return BoardListing(URLS, complete=False)  # a board too big to finish

    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", lambda source_id, lanes=1: None)
    monkeypatch.setattr(crawl_worker, "list_board", fake_list_board)

    crawl_worker._crawl_source(scan_db, source.id, now=EVENING)
    crawl_worker._crawl_source(scan_db, source.id, now=EVENING + timedelta(hours=2))

    assert calls == [True, False]


def test_full_read_due_waits_for_the_boards_slot_and_a_day_between_reads(make_source):
    source = make_source()
    slot = full_read_slot(source)
    assert time(8, 0) <= slot < time(20, 0)
    zone = ZoneInfo("America/New_York")
    day = datetime(2026, 9, 21, tzinfo=zone)
    at = lambda t: datetime.combine(day.date(), t, tzinfo=zone)  # noqa: E731

    assert not full_read_due(source, at(time(7, 59)))
    assert full_read_due(source, at(slot))
    source.uncapped_crawled_at = at(slot).astimezone(timezone.utc)
    assert not full_read_due(source, at(slot) + timedelta(hours=19))
    assert full_read_due(source, at(slot) + timedelta(days=1))


def test_full_read_slots_are_scattered_through_the_day(make_source):
    hours = {full_read_slot(make_source()).hour for _ in range(60)}

    assert len(hours) >= 8  # not all in the first morning run


def test_a_browser_rendered_board_stays_capped_even_when_set_uncapped(monkeypatch, scan_db, make_source, now):
    source = make_source()
    source.ats_type, source.uncapped = "bestbuy", True
    scan_db.commit()
    calls = []

    def fake_list_board(ats_type, board_url, *, uncapped=False):
        calls.append(uncapped)
        return BoardListing(URLS)

    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", lambda source_id, lanes=1: None)
    monkeypatch.setattr(crawl_worker, "list_board", fake_list_board)

    crawl_worker._crawl_source(scan_db, source.id)
    scan_db.expire_all()

    assert calls == [False]
    assert scan_db.get(CrawlSource, source.id).uncapped_baseline_at is None
    assert {r.scan_status for r in _rows(scan_db, source).values()} == {ScanStatus.PENDING}


def test_crawl_worker_closes_on_a_healthy_crawl(monkeypatch, scan_db, make_source, now):
    source = make_source()
    _crawl(monkeypatch, scan_db, source, URLS, now)
    _crawl(monkeypatch, scan_db, source, URLS[:2], now + timedelta(hours=2))
    _crawl(monkeypatch, scan_db, source, URLS[:2], now + timedelta(hours=4))

    assert _rows(scan_db, source)[URLS[2]].closed_at is not None


def test_crawl_worker_skips_closing_when_coverage_is_low(monkeypatch, scan_db, make_source, now):
    source = make_source()
    _crawl(monkeypatch, scan_db, source, URLS, now)
    scan_db.get(CrawlSource, source.id).coverage_low_streak = 2
    monkeypatch.setattr(crawl_worker, "update_coverage", lambda source, count: None)
    scan_db.commit()

    _crawl(monkeypatch, scan_db, source, URLS[:1], now + timedelta(hours=2))
    _crawl(monkeypatch, scan_db, source, URLS[:1], now + timedelta(hours=4))

    assert all(r.closed_at is None and r.missing_since is None for r in _rows(scan_db, source).values())


def test_crawl_worker_skips_closing_on_a_partial_listing(monkeypatch, scan_db, make_source, now):
    # A recent-only (or capped) listing drops a job once it ages out, while
    # the job is still open on the board: seen ones are stamped, none closed.
    source = make_source()
    _crawl(monkeypatch, scan_db, source, URLS, now, complete=False)
    _crawl(monkeypatch, scan_db, source, URLS[:1], now + timedelta(hours=2), complete=False)
    later = now + timedelta(hours=8)
    _crawl(monkeypatch, scan_db, source, URLS[:1], later, complete=False)

    rows = _rows(scan_db, source)
    assert all(r.closed_at is None and r.missing_since is None for r in rows.values())
    assert rows[URLS[0]].last_seen_at.replace(tzinfo=None) == later.replace(tzinfo=None)


def test_crawl_worker_skips_closing_on_an_empty_listing(monkeypatch, scan_db, make_source, now):
    source = make_source()
    _crawl(monkeypatch, scan_db, source, URLS, now)
    _crawl(monkeypatch, scan_db, source, [], now + timedelta(hours=2))
    _crawl(monkeypatch, scan_db, source, [], now + timedelta(hours=4))

    assert all(r.closed_at is None and r.missing_since is None for r in _rows(scan_db, source).values())


def test_scan_finding_the_posting_gone_closes_it(scan_db, make_url):
    url_row = make_url(None)
    _apply_scan_result(scan_db, url_row, ScanResult(success=False, error="gone", expired=True))

    assert url_row.closed_at is not None


def test_transient_scan_failure_does_not_close(scan_db, make_url):
    url_row = make_url(None)
    _apply_scan_result(scan_db, url_row, ScanResult(success=False, error="timeout"))

    assert url_row.closed_at is None


def test_successful_scan_reopens_a_user_submitted_url_but_not_a_crawled_one(scan_db, make_url, make_source, monkeypatch, now):
    monkeypatch.setattr(jobs_service, "_upsert_posting", lambda db, url_row, result, now: None)
    submitted = make_url(None)
    crawled = make_url(make_source())
    for row in (submitted, crawled):
        row.closed_at = now
        _apply_scan_result(scan_db, row, ScanResult(success=True, title="t"))

    assert submitted.closed_at is None
    assert crawled.closed_at == now


def test_closed_listings_drop_out_of_search(scan_db, make_url, now):
    open_row, closed_row = make_url(None), make_url(None)
    closed_row.closed_at = now
    for row in (open_row, closed_row):
        scan_db.add(JobPosting(url_id=row.id, extraction_status=ScanStatus.SUCCESS, title="Engineer"))
    scan_db.commit()

    stmt, _, _ = build_job_search_statement()
    ids = {row.id for row in scan_db.scalars(stmt)}

    assert ids == {open_row.id}


def _register_many(scan_db, source, n, now):
    urls = [f"https://example.com/many/{i}" for i in range(n)]
    _register(scan_db, source, urls, now)
    return urls


def test_a_mass_closure_is_spread_over_days_at_the_daily_limit(scan_db, make_source, now):
    source = make_source()
    urls = _register_many(scan_db, source, 100, now)

    # Crawls that list only 50 of 100: half the board gone at once (a board
    # read in full for the first time, or a broken adapter).
    first = _miss_twice(scan_db, source, urls[:50], now)
    same_day = record_board_presence(scan_db, source.id, urls[:50], healthy=True, now=now + timedelta(hours=6))
    next_day = record_board_presence(scan_db, source.id, urls[:50], healthy=True, now=now + timedelta(hours=29))
    scan_db.commit()

    assert first == 20  # 20% of the 100 open
    assert same_day == 0  # today's share is spent
    assert next_day == 16  # 20% of the 80 still open
    rows = _rows(scan_db, source)
    # Every missing URL carries its miss, closed or waiting its turn.
    assert all(rows[u].missing_since is not None for u in urls[50:])
    assert all(rows[u].closed_at is None for u in urls[:50])


def test_the_daily_limit_closes_the_longest_missing_first(scan_db, make_source, now):
    source = make_source()
    urls = _register_many(scan_db, source, 100, now)
    # 30 go missing first, 30 more half an hour later (too soon to close any).
    record_board_presence(scan_db, source.id, urls[:70], healthy=True, now=now + timedelta(hours=2))
    record_board_presence(scan_db, source.id, urls[:40], healthy=True, now=now + timedelta(hours=2, minutes=30))
    closed = record_board_presence(scan_db, source.id, urls[:40], healthy=True, now=now + timedelta(hours=6))
    scan_db.commit()

    rows = _rows(scan_db, source)
    assert closed == 20
    assert sum(rows[u].closed_at is not None for u in urls[70:]) == 20  # all from the earlier 30
    assert all(rows[u].closed_at is None for u in urls[40:70])


def test_normal_churn_under_the_limit_still_closes(scan_db, make_source, now):
    source = make_source()
    urls = _register_many(scan_db, source, 100, now)

    closed = _miss_twice(scan_db, source, urls[:85], now)

    assert closed == 15


def test_a_small_board_can_close_a_few_even_above_the_fraction(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)  # 3 open; closing 1 is 33% but under MAX_DAILY_CLOSE_MIN

    assert _miss_twice(scan_db, source, URLS[:2], now) == 1


def test_recently_seen_rows_are_not_rewritten(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    record_board_presence(scan_db, source.id, URLS, healthy=True, now=now + timedelta(hours=1))
    scan_db.commit()

    assert {r.last_seen_at.replace(tzinfo=None) for r in _rows(scan_db, source).values()} == {now.replace(tzinfo=None)}


def test_a_presence_failure_does_not_fail_the_crawl(monkeypatch, scan_db, make_source, now):
    source = make_source()
    wakeups = []
    monkeypatch.setattr(crawl_worker, "list_board", lambda ats_type, board_url, **_: BoardListing(URLS))
    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", lambda source_id, lanes=1: wakeups.append(source_id))

    def broken(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(crawl_worker, "record_board_presence", broken)

    crawl_worker._crawl_source(scan_db, source.id)  # must not raise

    scan_db.expire_all()
    stored = scan_db.get(CrawlSource, source.id)
    assert stored.last_crawled_at is not None and stored.crawl_claimed_at is None
    assert len(_rows(scan_db, source)) == 3  # URLs registered
    assert wakeups == [source.id]  # lanes still woken

