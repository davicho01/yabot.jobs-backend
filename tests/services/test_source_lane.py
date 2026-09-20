"""run_source_lane: the per-source drain loop behind the throttled scan queue."""

from datetime import datetime, timezone

import pytest

from app.models import JobPostingUrl
from app.models.enums import ScanStatus
from app.services import jobs
from app.services.adapters.base import ScanResult


@pytest.fixture
def lane_env(monkeypatch, scan_db):
    """Stub everything a lane touches outside the DB and record the calls."""

    class Env:
        scanned: list[str] = []
        sleeps: list[float] = []
        wakeups: list = []
        scan = staticmethod(lambda url: ScanResult(success=True))

    env = Env()
    env.scanned, env.sleeps, env.wakeups = [], [], []

    def fake_scan(url):
        env.scanned.append(url)
        return env.scan(url)

    monkeypatch.setattr(jobs, "scan_job_url", fake_scan)
    monkeypatch.setattr(jobs.time, "sleep", env.sleeps.append)
    monkeypatch.setattr(jobs, "enqueue_source_scan", lambda source_id, lanes=1: env.wakeups.append((source_id, lanes)))
    # JobPosting uses Postgres-only JSONB and has no table under SQLite; the
    # lane's own behaviour (claiming, status, pacing) is what's under test.
    monkeypatch.setattr(jobs, "_upsert_posting", lambda *args, **kwargs: None)
    return env


def _statuses(scan_db) -> list[str]:
    scan_db.expire_all()
    return [row.scan_status for row in scan_db.query(JobPostingUrl).order_by(JobPostingUrl.created_at).all()]


def test_drains_every_pending_url_with_a_pause_after_each(scan_db, make_source, make_url, lane_env):
    source = make_source(max_concurrent_scans=3)
    for _ in range(4):
        make_url(source)

    scanned = jobs.run_source_lane(scan_db, source.id)

    assert scanned == 4
    assert len(lane_env.scanned) == 4
    assert _statuses(scan_db) == [ScanStatus.SUCCESS] * 4
    assert lane_env.sleeps == [jobs.settings.scan_min_interval_seconds] * 4
    assert lane_env.wakeups == []


def test_url_is_claimed_before_it_is_fetched(scan_db, make_source, make_url, lane_env):
    source = make_source()
    row = make_url(source)
    seen = {}

    def scan(url):
        scan_db.expire_all()
        current = scan_db.get(JobPostingUrl, row.id)
        seen["status"], seen["claimed"] = current.scan_status, current.scan_claimed_at
        return ScanResult(success=True)

    lane_env.scan = scan
    jobs.run_source_lane(scan_db, source.id)

    assert seen["status"] == ScanStatus.PENDING
    assert seen["claimed"] is not None
    scan_db.expire_all()
    assert scan_db.get(JobPostingUrl, row.id).scan_claimed_at is None  # released once stored


def test_failed_scan_is_recorded_and_the_lane_moves_on(scan_db, make_source, make_url, lane_env):
    source = make_source()
    bad = make_url(source, age_minutes=20)
    good = make_url(source, age_minutes=10)
    lane_env.scan = lambda url: ScanResult(success=False, error="404") if url == bad.url else ScanResult(success=True)

    jobs.run_source_lane(scan_db, source.id)

    scan_db.expire_all()
    assert scan_db.get(JobPostingUrl, bad.id).scan_status == ScanStatus.FAILED
    assert scan_db.get(JobPostingUrl, bad.id).scan_error == "404"
    assert scan_db.get(JobPostingUrl, good.id).scan_status == ScanStatus.SUCCESS


def test_scan_that_raises_is_marked_failed_instead_of_stalling_the_source(scan_db, make_source, make_url, lane_env):
    source = make_source()
    boom = make_url(source, age_minutes=20)
    ok = make_url(source, age_minutes=10)

    def scan(url):
        if url == boom.url:
            raise RuntimeError("parser exploded")
        return ScanResult(success=True)

    lane_env.scan = scan

    scanned = jobs.run_source_lane(scan_db, source.id)

    assert scanned == 2
    scan_db.expire_all()
    failed = scan_db.get(JobPostingUrl, boom.id)
    assert failed.scan_status == ScanStatus.FAILED
    assert "parser exploded" in failed.scan_error
    assert failed.scan_claimed_at is None
    assert scan_db.get(JobPostingUrl, ok.id).scan_status == ScanStatus.SUCCESS


def test_lane_does_nothing_when_the_source_is_already_at_its_cap(scan_db, make_source, make_url, lane_env):
    source = make_source(max_concurrent_scans=2)
    for _ in range(2):
        row = make_url(source)
        row.scan_claimed_at = datetime.now(timezone.utc)  # two other lanes are mid-fetch
    make_url(source)
    scan_db.commit()

    scanned = jobs.run_source_lane(scan_db, source.id)

    assert scanned == 0
    assert lane_env.scanned == []
    assert lane_env.wakeups == []


def test_out_of_time_lane_leaves_a_wakeup_when_work_remains(monkeypatch, scan_db, make_source, make_url, lane_env):
    monkeypatch.setattr(jobs.settings, "scan_lane_deadline_seconds", 0.0)
    source = make_source()
    for _ in range(3):
        make_url(source)

    scanned = jobs.run_source_lane(scan_db, source.id)

    assert scanned == 1  # finishes the URL it had, then stops taking new ones
    assert lane_env.wakeups == [(source.id, 1)]
    assert lane_env.sleeps == []


def test_out_of_time_lane_with_nothing_left_publishes_no_wakeup(monkeypatch, scan_db, make_source, make_url, lane_env):
    monkeypatch.setattr(jobs.settings, "scan_lane_deadline_seconds", 0.0)
    source = make_source()
    make_url(source)

    jobs.run_source_lane(scan_db, source.id)

    assert lane_env.wakeups == []


def test_a_result_already_stored_by_someone_else_is_not_overwritten(scan_db, make_source, make_url, lane_env):
    source = make_source()
    row = make_url(source)

    def scan(url):
        # Simulates a reclaimed-claim race: another lane finished this URL while
        # this (slow) one was still fetching.
        scan_db.expire_all()
        other = scan_db.get(JobPostingUrl, row.id)
        other.scan_status = ScanStatus.SUCCESS
        other.scan_error = "stored by the other lane"
        scan_db.commit()
        return ScanResult(success=False, error="late result")

    lane_env.scan = scan
    jobs.run_source_lane(scan_db, source.id)

    scan_db.expire_all()
    stored = scan_db.get(JobPostingUrl, row.id)
    assert stored.scan_status == ScanStatus.SUCCESS
    assert stored.scan_error == "stored by the other lane"
