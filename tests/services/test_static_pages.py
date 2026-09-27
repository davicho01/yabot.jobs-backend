"""Tests for app.services.static_pages — the query/render/publish logic
behind generate_static_job_pages.py. Runs against an in-memory SQLite engine,
same pattern as tests/services/conftest.py's scan_db (JSONB compiled to JSON
so JobPosting's table can exist under SQLite). Never touches real S3/
CloudFront: generate_for_date's dry_run path skips those calls outright, and
the one non-dry-run test monkeypatches the upload/write functions instead of
hitting the network.
"""

import itertools
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import JobSector, ScanStatus
from app.services import static_pages as sp


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def make_posting(db):
    counter = itertools.count()

    def _make(
        *,
        created_at: datetime,
        sector: str = JobSector.ENGINEERING_TECH,
        country: str | None = "US",
        extraction_status: str = ScanStatus.SUCCESS,
        title: str | None = "Software Engineer",
        primary_posting_id: uuid.UUID | None = None,
        flagged_at: datetime | None = None,
        company_name: str = "Acme Corp",
        location: str = "Remote",
        posted_at: date | None = None,
        salary_min: int | None = None,
        salary_max: int | None = None,
        salary_currency: str | None = None,
    ) -> m.JobPostingUrl:
        n = next(counter)
        url_row = m.JobPostingUrl(
            url=f"https://example.com/jobs/{n}",
            normalized_url=f"https://example.com/jobs/{n}",
            url_hash=f"hash-{n}",
            domain="example.com",
            created_at=created_at,
            flagged_at=flagged_at,
        )
        db.add(url_row)
        db.flush()
        posting = m.JobPosting(
            url_id=url_row.id,
            title=title,
            company_name=company_name,
            location=location,
            sector=sector,
            country=country,
            extraction_status=extraction_status,
            primary_posting_id=primary_posting_id,
            posted_at=posted_at,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
        )
        db.add(posting)
        db.commit()
        return url_row

    return _make


# A day with no DST ambiguity: 9am Pacific on 2026-09-26 is safely inside
# that Pacific calendar day regardless of PST/PDT.
PT_NOON = datetime(2026, 9, 26, 17, 0, tzinfo=timezone.utc)  # 10:00 PT (PDT, UTC-7)


class TestDayBoundsUtc:
    def test_pdt_offset_in_september(self):
        start, end = sp.day_bounds_utc(date(2026, 9, 26))
        assert start == datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc)  # midnight PDT = 07:00 UTC
        assert end == datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)

    def test_pst_offset_in_january(self):
        start, end = sp.day_bounds_utc(date(2026, 1, 15))
        assert start == datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)  # midnight PST = 08:00 UTC
        assert end == datetime(2026, 1, 16, 8, 0, tzinfo=timezone.utc)


class TestJobsForSectorDay:
    def test_includes_a_valid_job_on_the_target_day(self, db, make_posting):
        make_posting(created_at=PT_NOON)
        jobs = sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26))
        assert len(jobs) == 1
        assert jobs[0].title == "Software Engineer"

    def test_excludes_wrong_sector(self, db, make_posting):
        make_posting(created_at=PT_NOON, sector=JobSector.SALES)
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_excludes_wrong_country(self, db, make_posting):
        make_posting(created_at=PT_NOON, country="GB")
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_excludes_unresolved_country(self, db, make_posting):
        make_posting(created_at=PT_NOON, country=None)
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_excludes_unsuccessful_scans(self, db, make_posting):
        make_posting(created_at=PT_NOON, extraction_status=ScanStatus.NEEDS_REVIEW)
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_excludes_non_canonical_duplicates(self, db, make_posting):
        make_posting(created_at=PT_NOON, primary_posting_id=uuid.uuid4())
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_excludes_flagged_listings(self, db, make_posting):
        make_posting(created_at=PT_NOON, flagged_at=PT_NOON)
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_evening_pacific_post_does_not_spill_into_next_utc_day(self, db, make_posting):
        # 11pm Pacific (PDT, UTC-7) on Sept 26 is 06:00 UTC on Sept 27 — a
        # naive UTC-day bucket would wrongly file this under Sept 27.
        late_pt = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)
        make_posting(created_at=late_pt)
        assert len(sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26))) == 1
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 27)) == []

    def test_carries_salary_fields_through(self, db, make_posting):
        make_posting(created_at=PT_NOON, salary_min=100_000, salary_max=130_000, salary_currency="USD")
        jobs = sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26))
        assert jobs[0].salary_min == 100_000
        assert jobs[0].salary_max == 130_000
        assert jobs[0].salary_display == "$100,000 – $130,000"

    def test_excludes_adjacent_days(self, db, make_posting):
        make_posting(created_at=PT_NOON - timedelta(days=1))
        make_posting(created_at=PT_NOON + timedelta(days=1))
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_buckets_by_posted_at_not_scan_date_when_known(self, db, make_posting):
        # Scanned today, but the employer says it was posted a week ago —
        # belongs on that day's page, not today's.
        make_posting(created_at=PT_NOON, posted_at=date(2026, 9, 19))
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []
        assert len(sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 19))) == 1

    def test_falls_back_to_scan_date_when_posted_at_unknown(self, db, make_posting):
        make_posting(created_at=PT_NOON, posted_at=None)
        assert len(sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26))) == 1

    def test_known_posted_at_elsewhere_is_not_rescued_by_todays_scan(self, db, make_posting):
        # Scanned today (created_at in today's window) but posted_at names a
        # *different* day — the known date always wins, never overridden by
        # when we happened to find it.
        make_posting(created_at=PT_NOON, posted_at=date(2026, 9, 25))
        assert sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26)) == []

    def test_ordered_by_posted_at_then_title_not_by_when_we_found_it(self, db, make_posting):
        # Found in reverse of alphabetical order, on purpose — the tiebreak
        # must come from title, not created_at. All share the same posted_at
        # (2026-09-26) so posted_at itself doesn't distinguish them; the
        # null-posted_at one falls back to created_at for inclusion and
        # sorts last (nulls_last).
        make_posting(created_at=PT_NOON, title="Zebra Role", posted_at=date(2026, 9, 26))
        make_posting(created_at=PT_NOON + timedelta(minutes=1), title="Beta Role", posted_at=date(2026, 9, 26))
        make_posting(created_at=PT_NOON + timedelta(minutes=2), title="Alpha Role", posted_at=date(2026, 9, 26))
        make_posting(created_at=PT_NOON + timedelta(minutes=3), title="No Date Role", posted_at=None)

        jobs = sp.jobs_for_sector_day(db, "US", JobSector.ENGINEERING_TECH, date(2026, 9, 26))

        assert [j.title for j in jobs] == ["Alpha Role", "Beta Role", "Zebra Role", "No Date Role"]


class TestManifestShape:
    def test_current_nested_shape_is_recognized(self):
        assert sp._is_current_manifest_shape({"us": {"engineering-tech": {"2026-09-26": 5}}}) is True
        assert sp._is_current_manifest_shape({}) is True

    def test_old_flat_shape_is_rejected(self):
        # Pre-country-segment manifests: {sector_slug: {date: count}} — one
        # level shallower, second-level values are ints, not dicts.
        assert sp._is_current_manifest_shape({"engineering-tech": {"2026-09-26": 5}}) is False

    def test_read_manifest_discards_an_old_shape_manifest(self, monkeypatch):
        class _FakeS3:
            class exceptions:
                class NoSuchKey(Exception):
                    pass

            def get_object(self, *, Bucket, Key):
                body = json.dumps({"engineering-tech": {"2026-09-26": 5}}).encode("utf-8")
                return {"Body": type("B", (), {"read": lambda self: body})()}

        monkeypatch.setattr(sp, "_get_s3_client", lambda: _FakeS3())
        assert sp.read_manifest() == {}


class TestManifestAndSitemap:
    def test_add_to_manifest_records_count_without_mutating_input(self):
        original = {}
        updated = sp.add_to_manifest(original, "us", JobSector.ENGINEERING_TECH, date(2026, 9, 26), 5)
        assert original == {}
        assert updated == {"us": {"engineering-tech": {"2026-09-26": 5}}}

    def test_add_to_manifest_updates_same_day_count_idempotently(self):
        manifest = sp.add_to_manifest({}, "us", JobSector.ENGINEERING_TECH, date(2026, 9, 26), 5)
        manifest = sp.add_to_manifest(manifest, "us", JobSector.ENGINEERING_TECH, date(2026, 9, 26), 9)
        assert manifest == {"us": {"engineering-tech": {"2026-09-26": 9}}}

    def test_add_to_manifest_keeps_countries_and_sectors_independent(self):
        manifest = sp.add_to_manifest({}, "us", JobSector.ENGINEERING_TECH, date(2026, 9, 26), 5)
        manifest = sp.add_to_manifest(manifest, "us", JobSector.SALES, date(2026, 9, 26), 2)
        assert manifest == {"us": {"engineering-tech": {"2026-09-26": 5}, "sales": {"2026-09-26": 2}}}

    def test_build_sitemap_xml_lists_index_and_day_urls_nested_by_country(self):
        manifest = {"us": {"engineering-tech": {"2026-09-26": 3, "2026-09-25": 1}}}
        xml = sp.build_sitemap_xml(manifest)
        assert "<loc>https://yabot.jobs/jobs/us/engineering-tech</loc>" in xml
        assert "<loc>https://yabot.jobs/jobs/us/engineering-tech/2026-09-26</loc>" in xml
        assert "<loc>https://yabot.jobs/jobs/us/engineering-tech/2026-09-25</loc>" in xml


class TestRendering:
    def test_day_page_contains_job_link_and_valid_json_ld(self):
        jobs = [
            sp.JobRow(
                url_id="11111111-1111-1111-1111-111111111111",
                title="Senior Backend Engineer",
                company_name="Acme & Co",
                location="Austin, TX",
                scanned_at_local=PT_NOON,
            )
        ]
        manifest = sp.add_to_manifest({}, "us", JobSector.ENGINEERING_TECH, date(2026, 9, 26), 1)
        html = sp.render_day_page(
            country_slug="us",
            sector=JobSector.ENGINEERING_TECH,
            local_day=date(2026, 9, 26),
            jobs=jobs,
            generated_at=datetime.now(timezone.utc),
            manifest=manifest,
        )
        assert "/jobs/11111111-1111-1111-1111-111111111111" in html
        assert "/jobs/us/engineering-tech/2026-09-26" in html
        assert "Senior Backend Engineer" in html
        assert "United States" in html

        import json
        import re

        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        payload = json.loads(match.group(1))
        assert payload["itemListElement"][0]["item"]["title"] == "Senior Backend Engineer"

    def test_day_page_shows_salary_range_when_present(self):
        jobs = [
            sp.JobRow(
                url_id="11111111-1111-1111-1111-111111111111",
                title="Senior Backend Engineer",
                company_name="Acme Corp",
                location="Austin, TX",
                scanned_at_local=PT_NOON,
                salary_min=120_000,
                salary_max=150_000,
            )
        ]
        manifest = sp.add_to_manifest({}, "us", JobSector.ENGINEERING_TECH, date(2026, 9, 26), 1)
        html = sp.render_day_page(
            country_slug="us",
            sector=JobSector.ENGINEERING_TECH,
            local_day=date(2026, 9, 26),
            jobs=jobs,
            generated_at=datetime.now(timezone.utc),
            manifest=manifest,
        )
        assert "$120,000" in html and "$150,000" in html

        import json
        import re

        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        payload = json.loads(match.group(1))
        base_salary = payload["itemListElement"][0]["item"]["baseSalary"]
        assert base_salary == {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {"@type": "QuantitativeValue", "minValue": 120_000, "maxValue": 150_000},
        }

    def test_job_row_salary_display_variants(self):
        no_salary = sp.JobRow(url_id="x", title="t", company_name=None, location=None, scanned_at_local=PT_NOON)
        assert no_salary.salary_display is None

        range_usd = sp.JobRow(
            url_id="x", title="t", company_name=None, location=None, scanned_at_local=PT_NOON,
            salary_min=90_000, salary_max=110_000,
        )
        assert range_usd.salary_display == "$90,000 – $110,000"

        single_value = sp.JobRow(
            url_id="x", title="t", company_name=None, location=None, scanned_at_local=PT_NOON, salary_max=75_000
        )
        assert single_value.salary_display == "$75,000"

        other_currency = sp.JobRow(
            url_id="x", title="t", company_name=None, location=None, scanned_at_local=PT_NOON,
            salary_min=50_000, salary_max=60_000, salary_currency="EUR",
        )
        assert other_currency.salary_display == "50,000 EUR – 60,000 EUR"

    def test_sector_index_lists_dates_grouped_by_month(self):
        html = sp.render_sector_index(
            country_slug="us",
            sector=JobSector.ENGINEERING_TECH,
            dates_with_counts=[("2026-09-26", 5), ("2026-08-30", 2)],
        )
        assert "2026-09-26" in html
        assert "September 2026" in html
        assert "August 2026" in html
        assert "/jobs/us/engineering-tech" in html


class TestGenerateForDate:
    def test_dry_run_returns_counts_without_touching_s3(self, db, make_posting, monkeypatch):
        make_posting(created_at=PT_NOON)

        def _boom(*a, **k):
            raise AssertionError("dry_run must not call S3/CloudFront")

        monkeypatch.setattr(sp, "upload_html", _boom)
        monkeypatch.setattr(sp, "write_manifest", _boom)
        monkeypatch.setattr(sp, "read_manifest", _boom)
        monkeypatch.setattr(sp, "invalidate_paths", _boom)

        result = sp.generate_for_date(db, date(2026, 9, 26), dry_run=True)
        assert result.job_counts == {"us/engineering-tech": 1}

    def test_skips_country_sector_combos_with_zero_jobs(self, db):
        result = sp.generate_for_date(db, date(2026, 9, 26), dry_run=True)
        assert result.job_counts == {}

    def test_non_us_postings_are_not_published_anywhere(self, db, make_posting):
        make_posting(created_at=PT_NOON, country="GB")
        result = sp.generate_for_date(db, date(2026, 9, 26), dry_run=True)
        assert result.job_counts == {}

    def test_publishes_day_and_index_pages_and_updates_manifest(self, db, make_posting, monkeypatch):
        make_posting(created_at=PT_NOON)
        uploaded: dict[str, str] = {}
        monkeypatch.setattr(sp, "read_manifest", lambda: {})
        monkeypatch.setattr(sp, "upload_html", lambda key, html: uploaded.__setitem__(key, html))
        monkeypatch.setattr(sp, "write_manifest", lambda manifest: uploaded.__setitem__("_manifest", manifest))
        monkeypatch.setattr(sp, "invalidate_paths", lambda paths: uploaded.__setitem__("_invalidated", paths))
        monkeypatch.setattr(sp, "_get_s3_client", lambda: _FakeS3(uploaded))

        result = sp.generate_for_date(db, date(2026, 9, 26), dry_run=False)

        assert result.job_counts == {"us/engineering-tech": 1}
        assert "jobs/us/engineering-tech/2026-09-26" in uploaded
        assert "jobs/us/engineering-tech" in uploaded
        assert uploaded["_manifest"] == {"us": {"engineering-tech": {"2026-09-26": 1}}}
        assert "/sitemap-jobs.xml" in uploaded["_invalidated"]


class _FakeS3:
    """Stands in for the lazy boto3 client generate_for_date reaches for
    directly (to PUT the sitemap object) — everything else goes through the
    monkeypatched module functions above."""

    def __init__(self, sink: dict):
        self._sink = sink

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self._sink[Key] = Body
