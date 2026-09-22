import itertools
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.models as m
from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.models.enums import ScanStatus
from app.services.job_scanner import normalize_url
from app.services.job_scanner import url_hash as compute_url_hash
from app.services.jobs import (
    _backoff_seconds,
    _enforce_submission_rate_limit,
    build_job_search_statement,
    ensure_user_applicant,
    find_existing_application,
    get_or_create_job_posting,
    parse_search_query,
)

_url_counter = itertools.count()


def _make_posting(
    scan_db, *, primary_posting_id: uuid.UUID | None = None, title: str = "Engineer"
) -> m.JobPosting:
    n = next(_url_counter)
    url_row = m.JobPostingUrl(
        url=f"https://example.com/jobs/dedup-{n}",
        normalized_url=f"https://example.com/jobs/dedup-{n}",
        url_hash=f"dedup-hash-{n}",
        domain="example.com",
    )
    scan_db.add(url_row)
    scan_db.flush()
    posting = m.JobPosting(
        url_id=url_row.id,
        company_name="Acme",
        title=title,
        extraction_status=ScanStatus.SUCCESS,
        primary_posting_id=primary_posting_id,
    )
    scan_db.add(posting)
    scan_db.commit()
    return posting


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


# ------------------------------------------------------- application dedup


def test_find_existing_application_matches_a_cross_posted_duplicate(scan_db):
    user_id = uuid.uuid4()
    canonical = _make_posting(scan_db)
    duplicate = _make_posting(scan_db, primary_posting_id=canonical.id)
    application = m.UserJobApplication(user_id=user_id, job_posting_id=canonical.id, status="saved")
    scan_db.add(application)
    scan_db.commit()

    # Looked up via the *duplicate*'s id — still finds the application saved
    # against the canonical posting.
    found = find_existing_application(scan_db, user_id, duplicate.id)
    assert found is not None and found.id == application.id


def test_find_existing_application_matches_via_the_canonical_from_another_duplicate(scan_db):
    user_id = uuid.uuid4()
    canonical = _make_posting(scan_db)
    duplicate_a = _make_posting(scan_db, primary_posting_id=canonical.id)
    duplicate_b = _make_posting(scan_db, primary_posting_id=canonical.id)
    application = m.UserJobApplication(user_id=user_id, job_posting_id=duplicate_a.id, status="saved")
    scan_db.add(application)
    scan_db.commit()

    # Two siblings under the same canonical, neither one the canonical itself.
    found = find_existing_application(scan_db, user_id, duplicate_b.id)
    assert found is not None and found.id == application.id


def test_find_existing_application_ignores_unrelated_postings_and_other_users(scan_db):
    user_id = uuid.uuid4()
    posting = _make_posting(scan_db)
    unrelated_posting = _make_posting(scan_db)  # a different job entirely, no primary_posting_id link
    scan_db.add(m.UserJobApplication(user_id=user_id, job_posting_id=posting.id, status="saved"))
    scan_db.commit()

    assert find_existing_application(scan_db, user_id, unrelated_posting.id) is None
    assert find_existing_application(scan_db, uuid.uuid4(), posting.id) is None  # right posting, wrong user


def test_find_existing_application_is_none_for_an_unknown_posting(scan_db):
    assert find_existing_application(scan_db, uuid.uuid4(), uuid.uuid4()) is None


def test_ensure_user_applicant_does_not_duplicate_across_a_cross_posted_duplicate(scan_db):
    user_id = uuid.uuid4()
    canonical = _make_posting(scan_db)
    duplicate = _make_posting(scan_db, primary_posting_id=canonical.id)

    ensure_user_applicant(scan_db, user_id, canonical.id)
    ensure_user_applicant(scan_db, user_id, duplicate.id)  # same job, found via a different URL
    scan_db.commit()

    applications = scan_db.scalars(
        select(m.UserJobApplication).where(m.UserJobApplication.user_id == user_id)
    ).all()
    assert len(applications) == 1
    assert applications[0].job_posting_id == canonical.id  # the first one made stays the tracked row


# ------------------------------------------------------- search query parsing


def test_parse_search_query_with_no_exclusions_is_unchanged():
    assert parse_search_query("Senior Software Engineer") == ("Senior Software Engineer", [])


def test_parse_search_query_pulls_out_dash_prefixed_terms():
    include, exclude_terms = parse_search_query("engineer -senior -lead")
    assert include == "engineer"
    assert exclude_terms == ["senior", "lead"]


def test_parse_search_query_exclusions_can_be_interspersed():
    # Order in the box doesn't matter — every non-excluded token still ends
    # up in `include`, in the order it was typed.
    include, exclude_terms = parse_search_query("-remote senior engineer -contract")
    assert include == "senior engineer"
    assert exclude_terms == ["remote", "contract"]


def test_parse_search_query_all_exclusions_has_no_include_text():
    assert parse_search_query("-senior") == (None, ["senior"])


def test_parse_search_query_a_lone_dash_is_kept_as_a_literal_token():
    # "-" alone has nothing after the dash to exclude, so it's treated as
    # ordinary (if useless) search text rather than a malformed exclusion.
    assert parse_search_query("-") == ("-", [])


# --------------------------------------------- build_job_search_statement search


def test_build_job_search_statement_excludes_postings_matching_a_dash_term(scan_db):
    keep = _make_posting(scan_db, title="Staff Engineer")
    _make_posting(scan_db, title="Senior Engineer")

    stmt, _order, _area = build_job_search_statement(q="engineer -senior")
    results = scan_db.scalars(stmt).all()

    assert [row.id for row in results] == [keep.url_id]


def test_build_job_search_statement_with_only_an_exclusion_still_filters(scan_db):
    keep = _make_posting(scan_db, title="Staff Engineer")
    _make_posting(scan_db, title="Senior Engineer")

    stmt, _order, _area = build_job_search_statement(q="-senior")
    results = scan_db.scalars(stmt).all()

    assert [row.id for row in results] == [keep.url_id]
