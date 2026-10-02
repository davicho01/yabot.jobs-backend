"""Closure detection: record_board_presence after a crawl, the crawl worker's
healthy-crawl gate around it, a scan that finds the posting gone, and closed
listings dropping out of search."""

from datetime import timedelta

import crawl_worker
from app.models import CrawlSource, JobPosting, JobPostingUrl
from app.models.enums import ScanStatus
from app.services import jobs as jobs_service
from app.services.adapters.base import ScanResult
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


def test_healthy_crawl_closes_urls_unseen_past_the_grace_period(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    later = now + timedelta(hours=40)
    closed = record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=later)
    scan_db.commit()

    rows = _rows(scan_db, source)
    assert closed == 1
    assert rows[URLS[2]].closed_at is not None
    assert rows[URLS[0]].closed_at is None and rows[URLS[1]].closed_at is None


def test_url_missing_for_less_than_the_grace_period_stays_open(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    closed = record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=10))

    assert closed == 0


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
    record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=40))
    scan_db.commit()

    record_board_presence(scan_db, source.id, URLS, healthy=True, now=now + timedelta(hours=50))
    scan_db.commit()

    assert _rows(scan_db, source)[URLS[2]].closed_at is None


def test_presence_is_scoped_to_its_own_source(scan_db, make_source, now):
    a, b = make_source(), make_source()
    _register(scan_db, a, URLS[:1], now)
    _register(scan_db, b, URLS[1:2], now)

    record_board_presence(scan_db, a.id, URLS[:1], healthy=True, now=now + timedelta(hours=40))
    scan_db.commit()

    assert _rows(scan_db, b)[URLS[1]].closed_at is None


def _crawl(monkeypatch, scan_db, source, urls, when):
    monkeypatch.setattr(crawl_worker, "list_job_urls", lambda ats_type, board_url: urls)
    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", lambda source_id, lanes=1: None)
    real = jobs_service.record_board_presence
    monkeypatch.setattr(
        crawl_worker,
        "record_board_presence",
        lambda db, source_id, raw_urls, *, healthy: real(db, source_id, raw_urls, healthy=healthy, now=when),
    )
    crawl_worker._crawl_source(scan_db, source.id)


def test_crawl_worker_closes_on_a_healthy_crawl(monkeypatch, scan_db, make_source, now):
    source = make_source()
    _crawl(monkeypatch, scan_db, source, URLS, now)
    _crawl(monkeypatch, scan_db, source, URLS[:2], now + timedelta(hours=40))

    assert _rows(scan_db, source)[URLS[2]].closed_at is not None


def test_crawl_worker_skips_closing_when_coverage_is_low(monkeypatch, scan_db, make_source, now):
    source = make_source()
    _crawl(monkeypatch, scan_db, source, URLS, now)
    scan_db.get(CrawlSource, source.id).coverage_low_streak = 2
    monkeypatch.setattr(crawl_worker, "update_coverage", lambda source, count: None)
    scan_db.commit()

    _crawl(monkeypatch, scan_db, source, URLS[:1], now + timedelta(hours=40))

    assert all(r.closed_at is None for r in _rows(scan_db, source).values())


def test_crawl_worker_skips_closing_on_an_empty_listing(monkeypatch, scan_db, make_source, now):
    source = make_source()
    _crawl(monkeypatch, scan_db, source, URLS, now)
    _crawl(monkeypatch, scan_db, source, [], now + timedelta(hours=40))

    assert all(r.closed_at is None for r in _rows(scan_db, source).values())


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


def test_mass_closure_is_held_back(scan_db, make_source, now):
    source = make_source()
    urls = _register_many(scan_db, source, 50, now)

    # A "healthy" crawl that only lists 10 of 50: 40 closures is 80% of the board.
    closed = record_board_presence(scan_db, source.id, urls[:10], healthy=True, now=now + timedelta(hours=40))
    scan_db.commit()

    assert closed == 0
    assert all(r.closed_at is None for r in _rows(scan_db, source).values())


def test_normal_churn_under_the_guard_still_closes(scan_db, make_source, now):
    source = make_source()
    urls = _register_many(scan_db, source, 100, now)

    closed = record_board_presence(scan_db, source.id, urls[:85], healthy=True, now=now + timedelta(hours=40))

    assert closed == 15


def test_a_small_board_can_close_a_few_even_above_the_fraction(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)  # 3 open; closing 1 is 33% but under MASS_CLOSE_MIN

    assert record_board_presence(scan_db, source.id, URLS[:2], healthy=True, now=now + timedelta(hours=40)) == 1


def test_recently_seen_rows_are_not_rewritten(scan_db, make_source, now):
    source = make_source()
    _register(scan_db, source, URLS, now)

    record_board_presence(scan_db, source.id, URLS, healthy=True, now=now + timedelta(hours=1))
    scan_db.commit()

    assert {r.last_seen_at.replace(tzinfo=None) for r in _rows(scan_db, source).values()} == {now.replace(tzinfo=None)}


def test_a_presence_failure_does_not_fail_the_crawl(monkeypatch, scan_db, make_source, now):
    source = make_source()
    wakeups = []
    monkeypatch.setattr(crawl_worker, "list_job_urls", lambda ats_type, board_url: URLS)
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

