import itertools
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.models as m
from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.services.job_scanner import normalize_url
from app.services.job_scanner import url_hash as compute_url_hash
from app.services.jobs import _backoff_seconds, _enforce_submission_rate_limit, get_or_create_job_posting

_url_counter = itertools.count()


def _submit(scan_db, user_id: uuid.UUID | None, *, minutes_ago: float) -> m.JobPostingUrl:
    """A JobPostingUrl dated relative to the real wall clock, not the frozen
    `NOW` tests/services/conftest.py's make_url fixture uses for the
    per-source scan-claim tests it was built for. _enforce_submission_rate_limit
    windows against datetime.now(timezone.utc) (it has to — it's throttling
    real, currently-happening submissions), so make_url's age_minutes (always
    relative to that frozen constant, several days in the past by now) would
    place every row outside any real rate-limit window regardless of what's
    passed — silently proving nothing rather than failing loudly.
    """
    n = next(_url_counter)
    row = m.JobPostingUrl(
        url=f"https://example.com/jobs/rate-limit-{n}",
        normalized_url=f"https://example.com/jobs/rate-limit-{n}",
        url_hash=f"rate-limit-hash-{n}",
        domain="example.com",
        submitted_by_user_id=user_id,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    scan_db.add(row)
    scan_db.commit()
    return row


def test_backoff_seconds_grows_by_the_configured_multiplier():
    # base=1h, multiplier=4 by default: 1h, then 4h, then 16h.
    assert _backoff_seconds(1) == settings.scan_retry_base_seconds
    assert _backoff_seconds(2) == settings.scan_retry_base_seconds * settings.scan_retry_backoff_multiplier
    assert _backoff_seconds(3) == settings.scan_retry_base_seconds * settings.scan_retry_backoff_multiplier**2


def test_backoff_seconds_caps_at_the_configured_maximum():
    # attempt 4 would be 64h uncapped (base=1h * 4**3) — must clamp to the 24h cap,
    # not just keep growing, so a stuck source doesn't back off for days.
    assert _backoff_seconds(4) == settings.scan_retry_max_seconds
    assert _backoff_seconds(10) == settings.scan_retry_max_seconds


def test_backoff_seconds_never_exceeds_the_cap_up_to_max_attempts():
    # scan_retry_max_attempts is when a row gives up (NEEDS_REVIEW) rather than
    # retrying again — every attempt up to and including that one must still
    # produce a sane, capped wait.
    for attempt in range(1, settings.scan_retry_max_attempts + 1):
        assert 0 < _backoff_seconds(attempt) <= settings.scan_retry_max_seconds


def test_enforce_submission_rate_limit_blocks_once_a_user_hits_the_cap(scan_db, make_url, now):
    user_id = uuid.uuid4()
    for _ in range(settings.job_submission_rate_limit_max_new_urls):
        make_url(None, submitted_by_user_id=user_id, age_minutes=1)

    with pytest.raises(RateLimitExceeded):
        _enforce_submission_rate_limit(scan_db, user_id, now=now)


def test_enforce_submission_rate_limit_ignores_other_users_and_stale_submissions(scan_db, make_url, now):
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    cap = settings.job_submission_rate_limit_max_new_urls
    window_minutes = settings.job_submission_rate_limit_window_minutes

    # Another user's submissions never count against this one.
    for _ in range(cap):
        make_url(None, submitted_by_user_id=other_user_id, age_minutes=1)
    # This user's own submissions, but from outside the rate-limit window.
    for _ in range(cap):
        make_url(None, submitted_by_user_id=user_id, age_minutes=window_minutes + 5)

    _enforce_submission_rate_limit(scan_db, user_id, now=now)  # must not raise


def test_get_or_create_job_posting_blocks_a_brand_new_url_once_a_user_is_over_the_cap(scan_db, make_url, now):
    user_id = uuid.uuid4()
    for _ in range(settings.job_submission_rate_limit_max_new_urls):
        make_url(None, submitted_by_user_id=user_id, age_minutes=1)

    with pytest.raises(RateLimitExceeded):
        get_or_create_job_posting(scan_db, "https://example.com/brand-new-job", user_id, now=now)


def test_get_or_create_job_posting_still_allows_resubmitting_a_known_url_past_the_cap(scan_db, make_url, now):
    # Over the cap on *new* submissions...
    user_id = uuid.uuid4()
    for _ in range(settings.job_submission_rate_limit_max_new_urls):
        make_url(None, submitted_by_user_id=user_id, age_minutes=1)

    # ...but re-submitting a URL that's already known is a dedup lookup, not
    # a new scan, so it's never throttled.
    raw_url = "https://example.com/already-known-job"
    normalized = normalize_url(raw_url)
    scan_db.add(
        m.JobPostingUrl(
            url=raw_url,
            normalized_url=normalized,
            url_hash=compute_url_hash(normalized),
            domain="example.com",
        )
    )
    scan_db.commit()

    posting, url_row = get_or_create_job_posting(scan_db, raw_url, user_id, now=now)
    assert url_row.url == raw_url
    assert posting.url_id == url_row.id
