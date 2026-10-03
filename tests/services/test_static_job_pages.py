"""Tests for app.services.static_job_pages — the per-job /job/{url_id} pages.
Same in-memory SQLite setup as test_static_pages.py; publishing goes to a
LocalDirPageStore in tmp_path rather than S3."""

import itertools
import json
import html as html_module
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
        sector: str = JobSector.ENGINEERING_TECH,
        company_name: str = "Acme Corp",
        company_key: str | None = "acme",
        title_key: str | None = None,
        metros: list[str] | None = None,
        extracted_fields: dict | None = None,
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
                company_name=company_name,
                company_key=company_key,
                title_key=title_key if title_key is not None else (title or "").lower(),
                metros=metros if metros is not None else (["12420", "TX"] if location == "Austin, TX" else []),
                extracted_fields=extracted_fields,
                location=location,
                locations=[location] if location else [],
                sector=sector,
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


def _ld_items(page_html: str) -> list[dict]:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page_html, re.S)
    payload = json.loads(match.group(1))
    return payload if isinstance(payload, list) else [payload]


def _ld(page_html: str, kind: str = "JobPosting") -> dict:
    return next(item for item in _ld_items(page_html) if item["@type"] == kind)


def _path(store, url_id) -> str:
    """Where a published job's page lives, per the manifest."""
    pages = json.loads(store.get(sjp.MANIFEST_KEY))["pages"]
    return sjp.parse_entry(str(url_id), pages[str(url_id)]).path


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
        path = _path(store, row.id)
        assert path.startswith("jobs/us/") and path.endswith(str(row.id))
        assert b"Software Engineer" in store.get(path)
        manifest = json.loads(store.get(sjp.MANIFEST_KEY))
        assert manifest["pages"][str(row.id)].startswith(f"{sjp.PAGE_VERSION}|")
        assert f"/{path}</loc>".encode() in store.get("sitemap-job-pages-1.xml")
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

        path = _path(store, row.id)
        assert result.updated == 1
        assert b"Staff Engineer" in store.get(path)
        assert f"/{path}" in result.touched_paths

    def test_a_closed_job_gets_the_gone_page_and_leaves_the_sitemap(self, db, make_job, store):
        row = make_job()
        sjp.generate_job_pages(db, now=NOW)
        path = _path(store, row.id)
        row.closed_at = NOW
        db.commit()

        result = sjp.generate_job_pages(db, now=NOW)

        assert result.removed == 1
        assert b"no longer available" in store.get(path)
        assert str(row.id).encode() not in store.get("sitemap-job-pages-1.xml")
        assert f"/{path}" in result.touched_paths

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
        assert b"Apply on Yabot Jobs" in store.get(_path(store, row.id))

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
        assert all(store.get(_path(store, r.id)) for r in rows)

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
        monkeypatch.setattr(script, "generate_for_date", lambda db, d, **kw: day)
        monkeypatch.setattr(script, "read_job_paths", lambda: {})
        monkeypatch.setattr(script, "generate_job_pages", lambda db, dry_run, full: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(script, "invalidate_paths", lambda paths: invalidated.append(paths))
        monkeypatch.setattr("sys.argv", ["generate_static_job_pages.py"])

        with pytest.raises(RuntimeError, match="boom"):
            script.main()

        assert invalidated == [["/jobs/*", "/sitemap-jobs.xml"]]


# --- SEO overhaul: URLs, manifest entries, related links, hubs, content ----


def _link_job(**kw) -> sjp.LinkJob:
    base = dict(
        url_id="11111111-2222-4333-8444-555555555555", title="Sr. Network Development Engineer",
        company_name="Bandwidth", domain="bandwidth.com", company_key="bandwidth", title_key="sr network development engineer",
        sector=JobSector.ENGINEERING_TECH, metros=("39580", "NC"), location="Raleigh, NC", workplace_type=WorkplaceType.ONSITE,
        posted_at=date(2026, 10, 2), found_at=NOW, scanned="2026-10-02T00:00:00+00:00",
    )
    base.update(kw)
    return sjp.LinkJob(**base)


class TestPaths:
    def test_new_path_nests_under_the_sector_day_page_with_title_and_city(self):
        job = _link_job()
        assert sjp.new_job_path(job) == (
            "jobs/us/engineering-tech/2026-10-02/sr-network-development-engineer-raleigh-nc-" + job.url_id
        )

    def test_day_falls_back_to_the_pacific_day_we_found_it(self):
        # 02:00 UTC on Oct 3 is still Oct 2 in Pacific time.
        job = _link_job(posted_at=None, found_at=datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc))
        assert "/2026-10-02/" in sjp.new_job_path(job)

    @pytest.mark.parametrize(
        "location, workplace, expected",
        [
            ("Cary, NC", WorkplaceType.ONSITE, "cary-nc"),  # the city, not its metro (Raleigh)
            ("North Carolina", WorkplaceType.ONSITE, "north-carolina"),
            ("Remote - US", WorkplaceType.REMOTE, "remote"),
            (None, WorkplaceType.REMOTE, "remote"),
            (None, WorkplaceType.ONSITE, ""),
        ],
    )
    def test_place_segment(self, location, workplace, expected):
        assert sjp.place_slug(location, workplace) == expected

    def test_unknown_sector_goes_under_other_and_unresolved_place_is_omitted(self):
        job = _link_job(sector="unknown", location=None)
        assert sjp.new_job_path(job) == f"jobs/us/other/2026-10-02/sr-network-development-engineer-{job.url_id}"

    def test_title_slug_is_capped_at_a_word_boundary(self):
        slug = sjp.slugify("Principal " * 20 + "Engineer", 60)
        assert len(slug) <= 60 and not slug.endswith("-") and slug.startswith("principal-principal")

    def test_slugify_folds_accents_and_punctuation(self):
        assert sjp.slugify("Señor Café & Bar, Inc.") == "senor-cafe-bar-inc"


class TestManifestEntries:
    def test_old_two_part_entries_live_at_job_slash_id(self):
        entry = sjp.parse_entry("abc", "1|2026-10-01T00:00:00+00:00")
        assert (entry.version_key, entry.path, entry.rendered_at) == ("1|2026-10-01T00:00:00+00:00", "job/abc", "")

    def test_round_trip(self):
        entry = sjp.ManifestEntry("2|2026-10-01T00:00:00+00:00", "jobs/us/sales/2026-10-01/x-abc", "2026-10-03T00:00:00")
        assert sjp.parse_entry("abc", entry.dump()) == entry


class TestUrlLifecycle:
    def test_existing_pages_keep_their_job_slash_id_url_and_are_re_rendered_in_place(self, db, make_job, store):
        row = make_job()
        store.put(sjp.MANIFEST_KEY, json.dumps({"pages": {str(row.id): "1|old"}}), "application/json")

        result = sjp.generate_job_pages(db, now=NOW)

        assert result.updated == 1  # the PAGE_VERSION bump re-renders it...
        assert _path(store, row.id) == f"job/{row.id}"  # ...at the URL it already had
        page = store.get(f"job/{row.id}").decode()
        assert f'rel="canonical" href="https://yabot.jobs/job/{row.id}"' in page
        assert "Job at a glance" in page

    def test_a_published_url_never_moves_when_title_sector_or_date_change(self, db, make_job, store):
        row = make_job()
        sjp.generate_job_pages(db, now=NOW)
        first = _path(store, row.id)
        posting = db.query(m.JobPosting).filter_by(url_id=row.id).one()
        posting.title, posting.sector, posting.posted_at, posting.scanned_at = "Staff Engineer", JobSector.SALES, date(2026, 9, 1), NOW
        db.commit()

        sjp.generate_job_pages(db, now=NOW)

        assert _path(store, row.id) == first
        assert b"Staff Engineer" in store.get(first)

    def test_stale_pages_are_refreshed_with_leftover_capacity_only_after_a_week(self, db, make_job, store):
        make_job()
        sjp.generate_job_pages(db, now=NOW)
        assert sjp.generate_job_pages(db, now=NOW + timedelta(days=1)).refreshed == 0
        assert sjp.generate_job_pages(db, now=NOW + timedelta(days=8)).refreshed == 1


class TestRelated:
    def test_same_company_and_similar_title_newest_first_never_self(self):
        me = _link_job(url_id="me")
        colleague_old = _link_job(url_id="c1", title_key="other", found_at=NOW - timedelta(days=2))
        colleague_new = _link_job(url_id="c2", title_key="other", found_at=NOW)
        twin = _link_job(url_id="t1", company_key="elsewhere")
        related = sjp.RelatedIndex([me, colleague_old, colleague_new, twin])

        same = related.same_company(me)
        assert [j.url_id for j in same] == ["c2", "c1"]
        assert [j.url_id for j in related.similar(me, exclude=same)] == ["t1"]

    def test_similar_tops_up_from_same_sector_and_metro_but_not_same_company(self):
        me = _link_job(url_id="me", title_key="unique")
        neighbour = _link_job(url_id="n1", title_key="x", company_key="neighbour")
        colleague = _link_job(url_id="c1", title_key="y")  # same company: belongs in "More jobs at", not here
        assert [j.url_id for j in sjp.RelatedIndex([me, neighbour, colleague]).similar(me)] == ["n1"]

    def test_job_page_links_related_jobs_at_their_own_paths(self, db, make_job, store):
        a = make_job(title="Backend Engineer")
        b = make_job(title="Frontend Engineer")
        sjp.generate_job_pages(db, now=NOW)
        page = store.get(_path(store, a.id)).decode()
        assert "More jobs at Acme Corp" in page
        assert f'href="https://yabot.jobs/{_path(store, b.id)}"' in page


class TestContent:
    def _page(self, db, make_job, **kwargs):
        row = make_job(**kwargs)
        return sjp.render_job_page(sjp.load_job_pages(db, [str(row.id)])[str(row.id)])

    def test_title_fits_in_60_and_meta_description_in_160(self, db, make_job):
        page = self._page(db, make_job, title="Senior Staff Distributed Systems Reliability Engineer, Platform Infrastructure")
        title = html_module.unescape(re.search(r"<title>(.*?)</title>", page).group(1))
        description = html_module.unescape(re.search(r'name="description" content="(.*?)"', page).group(1))
        assert len(title) <= 60 and title.endswith("| Yabot Jobs")
        assert len(description) <= 160 and description.startswith("Senior Staff")

    def test_label_paragraphs_become_h3(self):
        out = sjp.render_job_description_html("**About the Role**\n\nWho We Are:\n\nWe build **great** things.")
        assert "<h3>About the Role</h3>" in out and "<h3>Who We Are:</h3>" in out
        assert "<p>We build <strong>great</strong> things.</p>" in out

    def test_a_long_bold_sentence_stays_a_paragraph(self):
        out = sjp.render_job_description_html("**You will own the whole platform roadmap end to end every day**")
        assert "<h3>" not in out

    def test_valid_through_prefers_the_employer_then_falls_back_to_retirement(self, db, make_job):
        employer = _ld(self._page(db, make_job, posted_at=date(2026, 10, 1), extracted_fields={"validThrough": "2026-11-15T23:59:59Z"}))
        fallback = _ld(self._page(db, make_job, posted_at=date(2026, 10, 1)))
        assert employer["validThrough"] == "2026-11-15"
        assert fallback["validThrough"] == (date(2026, 10, 1) + timedelta(days=sjp.MAX_AGE_DAYS)).isoformat()

    def test_breadcrumb_list_runs_home_to_job_through_sector_and_day(self, db, make_job):
        crumbs = _ld(self._page(db, make_job, posted_at=date(2026, 10, 1)), "BreadcrumbList")["itemListElement"]
        assert [c["name"] for c in crumbs] == ["Home", "Jobs", "United States", "Engineering & Technology", "Oct 1, 2026", "Software Engineer"]
        assert crumbs[4]["item"] == "https://yabot.jobs/jobs/us/engineering-tech/2026-10-01"

    def test_page_has_glance_and_section_headings(self, db, make_job):
        page = self._page(db, make_job, salary_min=100000, salary_max=120000)
        assert '<h2 id="glance-h">Job at a glance</h2>' in page and '<h2 id="about-h">About the job</h2>' in page
        assert "<dt>Pay</dt><dd>$100,000 – $120,000</dd>" in page


class TestHubs:
    def _jobs(self, n, **kw):
        return [_link_job(url_id=f"{kw.get('company_key', 'bandwidth')}-{kw.get('sector', 'x')}-{i}", **kw) for i in range(n)]

    def test_thresholds(self):
        from app.services import static_hub_pages as hubs

        plan = hubs.plan_hubs(self._jobs(4) + self._jobs(1, company_key="solo", location="Austin, TX", metros=("12420", "TX")))
        paths = set(plan.pages)
        assert "jobs/us/companies/bandwidth" in paths  # 4 jobs >= 2
        assert "jobs/us/companies/solo" not in paths  # 1 job
        assert "jobs/us/locations/raleigh-nc" not in paths  # 4 < 5 for locations

    def test_metro_metro_sector_state_and_remote_hubs(self):
        from app.services import static_hub_pages as hubs

        live = self._jobs(5) + self._jobs(5, company_key="remoteco", workplace_type=WorkplaceType.REMOTE, metros=(), location=None)
        plan = hubs.plan_hubs(live)
        assert {
            "jobs/us/locations/raleigh-nc",
            "jobs/us/locations/raleigh-nc/engineering-tech",
            "jobs/us/locations/north-carolina",
            "jobs/us/locations/remote",
            "jobs/us/locations/remote/engineering-tech",
        } <= set(plan.pages)
        links = plan.location_links(live[0])
        assert ("Engineering & Technology jobs in Raleigh, NC", "jobs/us/locations/raleigh-nc/engineering-tech") in links
        assert plan.country_links()["locations"][0][1] in plan.pages

    def test_a_multi_metro_job_counts_toward_each_metro(self):
        from app.services import static_hub_pages as hubs

        live = self._jobs(5, metros=("39580", "NC", "12420", "TX"))
        plan = hubs.plan_hubs(live)
        assert {"jobs/us/locations/raleigh-nc", "jobs/us/locations/austin-tx"} <= set(plan.pages)

    def test_run_publishes_hubs_and_retires_dropped_ones_with_noindex(self, db, make_job, store):
        rows = [make_job(title=f"Engineer {i}") for i in range(5)]
        result = sjp.generate_job_pages(db, now=NOW)
        assert result.hubs and store.get("jobs/us/locations/austin-tx")
        assert b"sitemap-hubs-1.xml" in store.get(sjp.SITEMAP_INDEX_KEY)

        for row in rows[1:]:
            row.closed_at = NOW
        db.commit()
        sjp.generate_job_pages(db, now=NOW)
        assert b"noindex" in store.get("jobs/us/locations/austin-tx")


class TestDayPageLinks:
    def test_day_page_links_each_job_at_its_published_path(self):
        from app.services import static_pages as sp

        jobs = [
            sp.JobRow(url_id="old", title="Old", company_name="A", location=None, scanned_at_local=NOW, path="job/old"),
            sp.JobRow(url_id="new", title="New", company_name="A", location=None, scanned_at_local=NOW,
                      path="jobs/us/sales/2026-10-02/new-new"),
            sp.JobRow(url_id="later", title="Later", company_name="A", location=None, scanned_at_local=NOW,
                      path="jobs/later"),
        ]
        page = sp.render_day_page(country_slug="us", sector=JobSector.SALES, local_day=date(2026, 10, 2), jobs=jobs,
                                  generated_at=NOW, manifest={})
        for path in ("job/old", "jobs/us/sales/2026-10-02/new-new", "jobs/later"):
            assert f'href="https://yabot.jobs/{path}"' in page


def test_hub_city_list_uses_the_geo_matched_city_not_the_first_comma_word():
    # Amazon writes locations country-first; the old first-word rule listed "USA" as a city.
    assert _link_job(location="USA, TX, Austin").city == "Austin"
    assert _link_job(location="Remote - US").city is None
