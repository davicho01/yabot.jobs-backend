"""crawl_worker no longer publishes one scan message per discovered URL."""

from datetime import datetime, timezone

import crawl_worker
from app.models import JobPostingUrl
from app.models.enums import ScanStatus


def _run_crawl(monkeypatch, scan_db, source, urls):
    wakeups: list[tuple] = []

    def fake_bulk_register(db, raw_urls, crawl_source_id):
        # What the real function leaves behind for brand-new URLs: PENDING rows.
        for url in raw_urls:
            db.add(
                JobPostingUrl(
                    url=url,
                    normalized_url=url,
                    url_hash=url,
                    domain="example.com",
                    scan_status=ScanStatus.PENDING,
                    crawl_source_id=crawl_source_id,
                )
            )
        db.flush()
        return 0

    monkeypatch.setattr(crawl_worker, "list_job_urls", lambda ats_type, board_url: urls)
    monkeypatch.setattr(crawl_worker, "bulk_register_discovered_urls", fake_bulk_register)
    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", lambda source_id, lanes=1: wakeups.append((source_id, lanes)))

    crawl_worker._crawl_source(scan_db, source.id)
    return wakeups


def test_crawl_wakes_the_sources_lanes_once_instead_of_queueing_every_url(monkeypatch, scan_db, make_source):
    source = make_source(max_concurrent_scans=4)
    urls = [f"https://example.com/jobs/{i}" for i in range(50)]

    wakeups = _run_crawl(monkeypatch, scan_db, source, urls)

    assert wakeups == [(source.id, 4)]  # one wake-up per allowed lane, not 50 messages


def test_crawl_with_nothing_to_scan_wakes_no_lanes(monkeypatch, scan_db, make_source):
    source = make_source()

    wakeups = _run_crawl(monkeypatch, scan_db, source, [])

    assert wakeups == []


def test_crawl_still_records_its_stats_when_waking_lanes_fails(monkeypatch, scan_db, make_source):
    source = make_source()
    monkeypatch.setattr(crawl_worker, "list_job_urls", lambda ats_type, board_url: ["https://example.com/jobs/1"])

    def fake_bulk_register(db, raw_urls, crawl_source_id):
        for url in raw_urls:
            db.add(
                JobPostingUrl(
                    url=url, normalized_url=url, url_hash=url, domain="example.com", crawl_source_id=crawl_source_id
                )
            )
        db.flush()
        return 0

    monkeypatch.setattr(crawl_worker, "bulk_register_discovered_urls", fake_bulk_register)

    def broken(source_id, lanes=1):
        raise RuntimeError("pubsub down")

    monkeypatch.setattr(crawl_worker, "enqueue_source_scan", broken)

    crawl_worker._crawl_source(scan_db, source.id)  # must not raise

    scan_db.expire_all()
    assert scan_db.get(type(source), source.id).last_crawled_at is not None


def test_crawl_records_discovered_url_count_for_coverage_monitoring(monkeypatch, scan_db, make_source):
    source = make_source()
    urls = [f"https://example.com/jobs/{i}" for i in range(7)]

    _run_crawl(monkeypatch, scan_db, source, urls)

    scan_db.expire_all()
    refreshed = scan_db.get(type(source), source.id)
    assert refreshed.coverage_last_count == 7
    assert refreshed.coverage_baseline == 7  # first-ever sample, running mean == the value itself
    assert refreshed.coverage_sample_count == 1


def test_crawl_clears_the_dispatch_claim_on_success(monkeypatch, scan_db, make_source):
    # So crawl_dispatcher.py can dispatch this source again next cycle
    # instead of skipping it forever — see CrawlSource.crawl_claimed_at.
    source = make_source()
    source.crawl_claimed_at = datetime.now(timezone.utc)
    scan_db.commit()

    _run_crawl(monkeypatch, scan_db, source, ["https://example.com/jobs/1"])

    scan_db.expire_all()
    assert scan_db.get(type(source), source.id).crawl_claimed_at is None


def test_crawl_clears_the_dispatch_claim_even_when_listing_jobs_fails(monkeypatch, scan_db, make_source):
    source = make_source()
    source.crawl_claimed_at = datetime.now(timezone.utc)
    scan_db.commit()

    def broken(ats_type, board_url):
        raise RuntimeError("ATS API down")

    monkeypatch.setattr(crawl_worker, "list_job_urls", broken)

    crawl_worker._crawl_source(scan_db, source.id)  # must not raise
    scan_db.commit()

    scan_db.expire_all()
    assert scan_db.get(type(source), source.id).crawl_claimed_at is None
