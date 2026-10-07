from datetime import datetime, timezone

from app.models import JobPostingUrl
from app.models.enums import ScanStatus
from one_off.requeue_needs_review import requeue, stuck_urls


def test_only_open_needs_review_jobs_are_reset_with_a_fresh_retry_budget(scan_db, make_source, make_url):
    source = make_source()
    stuck = make_url(source, status=ScanStatus.NEEDS_REVIEW)
    closed = make_url(source, status=ScanStatus.NEEDS_REVIEW)
    closed.closed_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    done = make_url(source, status=ScanStatus.SUCCESS)
    for row in (stuck, closed):
        row.scan_attempts = 5
    scan_db.commit()

    rows = stuck_urls(scan_db, ats=None, limit=None)
    assert [url_id for url_id, _, _ in rows] == [stuck.id]

    requeue(scan_db, [stuck.id])
    scan_db.expire_all()

    reset = scan_db.get(JobPostingUrl, stuck.id)
    assert (reset.scan_status, reset.scan_attempts, reset.next_retry_at) == (ScanStatus.PENDING, 0, None)
    assert scan_db.get(JobPostingUrl, closed.id).scan_status == ScanStatus.NEEDS_REVIEW
    assert scan_db.get(JobPostingUrl, done.id).scan_status == ScanStatus.SUCCESS


def test_the_platform_filter_narrows_to_one_ats(scan_db, make_source, make_url):
    greenhouse = make_source()  # make_source boards are on greenhouse
    make_url(greenhouse, status=ScanStatus.NEEDS_REVIEW)

    assert len(stuck_urls(scan_db, ats="greenhouse", limit=None)) == 1
    assert stuck_urls(scan_db, ats="avature", limit=None) == []
