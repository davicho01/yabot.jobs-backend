"""Tests for app.services.static_job_pages — the per-job /job/{url_id} pages.
Same in-memory SQLite setup as test_static_pages.py; publishing goes to a
LocalDirPageStore in tmp_path rather than S3."""

import itertools
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import app.models as m
from app.db.base import Base
from app.models.enums import EmploymentType, JobSector, ScanStatus, WorkplaceType
from app.services import page_store
from app.services import static_job_pages as sjp
from app.services.page_store import LocalDirPageStore


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


NOW = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture
def store(tmp_path, monkeypatch):
    local = LocalDirPageStore(tmp_path)
    monkeypatch.setattr(page_store, "_store", local)
    return local


@pytest.fixture
def make_job(db):
    counter = itertools.count()

    def _make(
        *,
        created_at: datetime = NOW - timedelta(days=1),
        scanned_at: datetime = NOW - timedelta(days=1),
        country: str | None = "US",
        extraction_status: str = ScanStatus.SUCCESS,
        title: str | None = "Software Engineer",
        primary_posting_id: uuid.UUID | None = None,
        flagged_at: datetime | None = None,
        closed_at: datetime | None = None,
        posted_at: date | None = None,
        description: str | None = "Build **things**.",
        workplace_type: str = WorkplaceType.ONSITE,
        location: str | None = "Austin, TX",
        salary_min: int | None = None,
        salary_max: int | None = None,
    ) -> m.JobPostingUrl:
        n = next(counter)
        url_row = m.JobPostingUrl(
            url=f"https://example.com/jobs/{n}",
            normalized_url=f"https://example.com/jobs/{n}",
            url_hash=f"hash-{n}",
            domain="example.com",
            created_at=created_at,
            flagged_at=flagged_at,
            closed_at=closed_at,
        )
        db.add(url_row)
        db.flush()
        db.add(
            m.JobPosting(
                url_id=url_row.id,
                title=title,
                company_name="Acme Corp",
                location=location,
                locations=[location] if location else [],
                sector=JobSector.ENGINEERING_TECH,
                country=country,
                extraction_status=extraction_status,
                primary_posting_id=primary_posting_id,
                posted_at=posted_at,
                scanned_at=scanned_at,
                description=description,
                workplace_type=workplace_type,
                employment_type=EmploymentType.FULL_TIME,
                salary_min=salary_min,
                salary_max=salary_max,
            )
        )
        db.commit()
        return url_row

    return _make


def _ld(page_html: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page_html, re.S)
    return json.loads(match.group(1))


class TestEligibility:
    def test_includes_a_live_us_job(self, db, make_job):
        row = make_job()
        assert set(sjp.eligible_job_ids(db, NOW)) == {str(row.id)}

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"country": "GB"},
            {"country": None},
            {"extraction_status": ScanStatus.FAILED},
            {"title": None},
            {"primary_posting_id": uuid.uuid4()},
            {"flagged_at": NOW},
            {"closed_at": NOW},
            {"created_at": NOW - timedelta(days=sjp.MAX_AGE_DAYS + 1)},
            {"posted_at": (NOW - timedelta(days=sjp.MAX_AGE_DAYS + 1)).date()},
        ],
    )
    def test_excludes(self, db, make_job, kwargs):
        make_job(**kwargs)
        assert sjp.eligible_job_ids(db, NOW) == {}

    def test_a_recent_posted_at_keeps_an_old_scan_eligible(self, db, make_job):
        make_job(created_at=NOW - timedelta(days=90), posted_at=(NOW - timedelta(days=5)).date())
        assert len(sjp.eligible_job_ids(db, NOW)) == 1

    def test_version_is_the_scan_time(self, db, make_job):
        row = make_job(scanned_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))
        assert sjp.eligible_job_ids(db, NOW)[str(row.id)].startswith("2026-10-01T12:00:00")


class TestRendering:
    def _page(self, db, make_job, **kwargs) -> str:
        row = make_job(**kwargs)
        return sjp.render_job_page(sjp.load_job_pages(db, [str(row.id)])[str(row.id)])

    def test_page_has_title_canonical_cta_and_description(self, db, make_job):
        row = make_job()
        page = sjp.render_job_page(sjp.load_job_pages(db, [str(row.id)])[str(row.id)])
        assert "<h1>Software Engineer</h1>" in page
        assert f'<link rel="canonical" href="https://yabot.jobs/job/{row.id}" />' in page
        assert f'href="https://yabot.jobs/jobs/{row.id}">Apply on Yabot Jobs</a>' in page
        assert "<strong>things</strong>" in page
        assert "https://example.com/jobs/" not in page  # the apply URL stays behind login

    def test_json_ld_is_a_valid_job_posting(self, db, make_job):
        ld = _ld(self._page(db, make_job, salary_min=120000, salary_max=150000, posted_at=date(2026, 9, 30)))
        assert ld["@type"] == "JobPosting"
        assert ld["datePosted"] == "2026-09-30"
        assert ld["hiringOrganization"]["name"] == "Acme Corp"
        assert ld["jobLocation"]["address"] == {
            "@type": "PostalAddress", "addressCountry": "US", "addressLocality": "Austin", "addressRegion": "TX",
        }
        assert ld["employmentType"] == "FULL_TIME"
        assert ld["baseSalary"]["value"]["unitText"] == "YEAR"
        assert "<strong>things</strong>" in ld["description"]

    def test_remote_job_is_telecommute(self, db, make_job):
        ld = _ld(self._page(db, make_job, workplace_type=WorkplaceType.REMOTE, location=None))
        assert ld["jobLocationType"] == "TELECOMMUTE"
        assert "jobLocation" not in ld

    def test_raw_html_in_a_description_is_escaped(self, db, make_job):
        page = self._page(db, make_job, description='Hi <script>alert(1)</script> [x](javascript:alert(1))')
        assert "<script>alert(1)</script>" not in page
        assert 'href="javascript:' not in page

    def test_script_close_in_a_title_cannot_break_out_of_the_json_ld(self, db, make_job):
        page = self._page(db, make_job, title="Eng </script><b>x")
        assert _ld(page)["title"] == "Eng </script><b>x"

    def test_links_in_descriptions_are_nofollow(self, db, make_job):
        page = self._page(db, make_job, description="[apply](https://evil.example)")
        assert 'rel="nofollow ugc"' in page

    def test_gone_page_is_noindex(self, db, make_job):
        row = make_job()
        page = sjp.render_job_gone(sjp.load_job_pages(db, [str(row.id)])[str(row.id)])
        assert 'content="noindex, follow"' in page
        assert "Software Engineer at Acme Corp" in page
        assert "noindex" in sjp.render_job_gone(None)


class TestSitemaps:
    def test_shards_and_index(self, monkeypatch):
        monkeypatch.setattr(sjp, "SITEMAP_SHARD_SIZE", 2)
        out = sjp.build_job_sitemaps({"a": "2026-10-01T00:00:00+00:00", "b": "2026-10-02T00:00:00+00:00", "c": "2026-10-02T00:00:00+00:00"})
        assert set(out) == {"sitemap-job-pages.xml", "sitemap-job-pages-1.xml", "sitemap-job-pages-2.xml"}
        assert "<loc>https://yabot.jobs/sitemap-job-pages-2.xml</loc>" in out["sitemap-job-pages.xml"]
        assert "<loc>https://yabot.jobs/job/a</loc><lastmod>2026-10-01</lastmod>" in out["sitemap-job-pages-1.xml"]

    def test_lastmod_comes_from_a_versioned_manifest_entry(self):
        out = sjp.build_job_sitemaps({"a": "3|2026-10-01T12:00:00+00:00"})
        assert "<lastmod>2026-10-01</lastmod>" in out["sitemap-job-pages-1.xml"]

    def test_empty_still_writes_a_valid_index(self):
        out = sjp.build_job_sitemaps({})
        assert "sitemap-job-pages-1.xml" in out["sitemap-job-pages.xml"]


class TestGenerate:
    def test_dry_run_writes_nothing(self, db, make_job, monkeypatch):
        make_job()
        monkeypatch.setattr(page_store, "_store", None)
        monkeypatch.setattr(page_store.settings, "seo_pages_output_dir", None)
        monkeypatch.setattr(page_store.settings, "seo_pages_bucket", None)
        result = sjp.generate_job_pages(db, dry_run=True, now=NOW)  # would raise if it touched the store
        assert (result.published, result.touched_paths) == (1, [])

    def test_first_run_publishes_pages_manifest_and_sitemaps(self, db, make_job, store):
        row = make_job()
        result = sjp.generate_job_pages(db, now=NOW)

        assert result.published == 1
        assert b"Software Engineer" in store.get(f"job/{row.id}")
        manifest = json.loads(store.get(sjp.MANIFEST_KEY))
        assert manifest["pages"][str(row.id)].startswith(f"{sjp.PAGE_VERSION}|")
        assert f"/job/{row.id}".encode() in store.get("sitemap-job-pages-1.xml")
        # A brand-new key was never cached, so only the sitemaps need invalidating.
        assert sorted(result.touched_paths) == ["/sitemap-job-pages-1.xml", "/sitemap-job-pages.xml"]

    def test_unchanged_jobs_are_skipped(self, db, make_job, store):
        make_job()
        sjp.generate_job_pages(db, now=NOW)
        result = sjp.generate_job_pages(db, now=NOW)
        assert (result.published, result.updated, result.unchanged) == (0, 0, 1)

    def test_a_rescan_re_renders_and_invalidates(self, db, make_job, store):
        row = make_job()
        sjp.generate_job_pages(db, now=NOW)
        posting = db.query(m.JobPosting).filter_by(url_id=row.id).one()
        posting.title, posting.scanned_at = "Staff Engineer", NOW
        db.commit()

        result = sjp.generate_job_pages(db, now=NOW)

        assert result.updated == 1
        assert b"Staff Engineer" in store.get(f"job/{row.id}")
        assert f"/job/{row.id}" in result.touched_paths

    def test_a_closed_job_gets_the_gone_page_and_leaves_the_sitemap(self, db, make_job, store):
        row = make_job()
        sjp.generate_job_pages(db, now=NOW)
        row.closed_at = NOW
        db.commit()

        result = sjp.generate_job_pages(db, now=NOW)

        assert result.removed == 1
        assert b"no longer available" in store.get(f"job/{row.id}")
        assert str(row.id).encode() not in store.get("sitemap-job-pages-1.xml")
        assert f"/job/{row.id}" in result.touched_paths

    def test_a_reopened_job_comes_back(self, db, make_job, store):
        row = make_job()
        sjp.generate_job_pages(db, now=NOW)
        row.closed_at = NOW
        db.commit()
        sjp.generate_job_pages(db, now=NOW)
        row.closed_at = None
        db.commit()

        result = sjp.generate_job_pages(db, now=NOW)

        assert result.published == 1
        assert b"Apply on Yabot Jobs" in store.get(f"job/{row.id}")

    def test_full_or_a_page_version_bump_re_renders_everything(self, db, make_job, store, monkeypatch):
        make_job()
        sjp.generate_job_pages(db, now=NOW)
        assert sjp.generate_job_pages(db, now=NOW, full=True).updated == 1
        monkeypatch.setattr(sjp, "PAGE_VERSION", sjp.PAGE_VERSION + 1)
        assert sjp.generate_job_pages(db, now=NOW).updated == 1
        assert sjp.generate_job_pages(db, now=NOW).updated == 0

    def test_render_cap_defers_the_rest_to_later_runs(self, db, make_job, store):
        rows = [make_job() for _ in range(5)]

        first = sjp.generate_job_pages(db, now=NOW, max_renders=2)
        manifest = json.loads(store.get(sjp.MANIFEST_KEY))["pages"]
        assert (first.published, first.deferred) == (2, 3)
        assert len(manifest) == 2  # deferred jobs aren't live yet, so not in the manifest or sitemap
        assert store.get("sitemap-job-pages-1.xml").count(b"<url>") == 2

        sjp.generate_job_pages(db, now=NOW, max_renders=2)
        third = sjp.generate_job_pages(db, now=NOW, max_renders=2)
        assert (third.published, third.deferred, third.unchanged) == (1, 0, 4)
        assert all(store.get(f"job/{r.id}") for r in rows)

    def test_full_ignores_the_cap(self, db, make_job, store):
        for _ in range(3):
            make_job()
        assert sjp.generate_job_pages(db, now=NOW, full=True, max_renders=1).published == 3

    def test_a_version_bump_under_the_cap_makes_progress_every_run(self, db, make_job, store, monkeypatch):
        for _ in range(3):
            make_job()
        sjp.generate_job_pages(db, now=NOW)
        monkeypatch.setattr(sjp, "PAGE_VERSION", sjp.PAGE_VERSION + 1)

        runs = [sjp.generate_job_pages(db, now=NOW, max_renders=2).updated for _ in range(3)]

        assert runs == [2, 1, 0]



    def test_a_run_that_dies_partway_keeps_its_checkpointed_progress(self, db, make_job, store, monkeypatch):
        for _ in range(5):
            make_job()
        monkeypatch.setattr(sjp, "RENDER_BATCH_SIZE", 1)
        monkeypatch.setattr(sjp, "CHECKPOINT_EVERY_BATCHES", 2)
        real_load, calls = sjp.load_job_pages, []

        def dies_on_the_fourth_batch(db, ids):
            calls.append(ids)
            if len(calls) == 4:
                raise RuntimeError("connection lost")
            return real_load(db, ids)

        monkeypatch.setattr(sjp, "load_job_pages", dies_on_the_fourth_batch)
        with pytest.raises(RuntimeError):
            sjp.generate_job_pages(db, now=NOW, full=True)

        # Batches 1-3 uploaded; the checkpoint after batch 2 saved two of them.
        assert len(json.loads(store.get(sjp.MANIFEST_KEY))["pages"]) == 2
        monkeypatch.setattr(sjp, "load_job_pages", real_load)
        resumed = sjp.generate_job_pages(db, now=NOW)
        assert (resumed.published, resumed.unchanged) == (3, 2)

    def test_a_dropped_connection_is_retried_once(self, db, make_job, monkeypatch):
        from sqlalchemy.exc import OperationalError

        row_id = str(make_job().id)  # read before patching: refreshing an expired row goes through execute too
        real_execute, attempts = db.execute, []

        def flaky(stmt, *a, **k):
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationalError("SELECT", {}, Exception("server closed the connection unexpectedly"))
            return real_execute(stmt, *a, **k)

        monkeypatch.setattr(db, "execute", flaky)
        assert row_id in sjp.load_job_pages(db, [row_id])
        assert len(attempts) == 2

class TestGeneratorScript:
    def test_a_job_page_failure_still_invalidates_the_day_pages_then_fails_the_run(self, monkeypatch):
        import generate_static_job_pages as script
        from app.services.static_pages import GenerationResult

        day = GenerationResult(
            generated_at=NOW, target_date=NOW.date(), job_counts={"us/sales": 1}, manifest={},
            touched_paths=["/jobs/us/sales/2026-10-02", "/sitemap-jobs.xml"],
        )
        invalidated: list[list[str]] = []
        monkeypatch.setattr(script, "SessionLocal", lambda: type("S", (), {"rollback": lambda s: None, "close": lambda s: None})())
        monkeypatch.setattr(script, "generate_for_date", lambda db, d, dry_run, invalidate: day)
        monkeypatch.setattr(script, "generate_job_pages", lambda db, dry_run, full: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(script, "invalidate_paths", lambda paths: invalidated.append(paths))
        monkeypatch.setattr("sys.argv", ["generate_static_job_pages.py"])

        with pytest.raises(RuntimeError, match="boom"):
            script.main()

        assert invalidated == [["/jobs/*", "/sitemap-jobs.xml"]]
