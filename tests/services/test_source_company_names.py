"""The official company site owns the job: app.services.company_names.

A confirmed crawl source's name is what every one of its jobs shows (or a
configured sub-brand the page names); a placeholder source takes its name from
the first usable page; jobs with no source keep the cleaned page name.
"""

from datetime import datetime, timezone

import pytest

from app.models import CrawlSource, JobPosting
from app.models.enums import ScanStatus
from app.services import company_names, jobs
from app.services.adapters.base import ScanResult
from app.services.company_names import AUTO, MANUAL, PLACEHOLDER, company_for, matching_sub_brand, promotable_name

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def _source(name, name_source=AUTO, sub_brands=(), is_official=True):
    return CrawlSource(name=name, name_source=name_source, sub_brands=list(sub_brands), is_official=is_official,
                       board_url="https://x", ats_type="workday")


class TestCompanyFor:
    @pytest.mark.parametrize(
        "page",
        ["B10 Wells Fargo Bank, N. A.", "I16 Wells Fargo International Solutions Private LTD", "Candidate Experience site",
         "sales and marketing", None],
    )
    def test_a_confirmed_source_wins_over_entities_portals_and_departments(self, page):
        assert company_for(_source("Wells Fargo"), page) == "Wells Fargo"

    def test_sub_brands_match_whole_words_only(self):
        tjx = _source("TJX", sub_brands=["HomeGoods", "Marshalls"])
        assert company_for(tjx, "Marshalls of MA") == "Marshalls"
        assert company_for(tjx, "Homegoods LLC") == "HomeGoods"
        assert company_for(tjx, "Marmaxx Operating Corp") == "TJX"
        assert company_for(_source("Dick's", sub_brands=["Golf Galaxy"]), "Galaxy Foods") == "Dick's"
        assert matching_sub_brand(["Sam's Club"], "Sam's Club") == "Sam's Club"

    def test_an_admin_note_on_the_source_name_never_shows(self):
        assert company_for(_source("CenterWell (Humana primary care / home health)"), "804 CenterWell Corp.") == "CenterWell"

    def test_a_slug_named_source_falls_back_to_the_page_until_curated(self):
        assert company_for(_source("greenhouse/clearstreet"), "Clear Street") == "Clear Street"

    def test_a_placeholder_source_uses_the_cleaned_page_name(self):
        assert company_for(_source("Jj", name_source=PLACEHOLDER), "Careers at Johnson & Johnson") == "Johnson & Johnson"

    def test_no_source_or_a_job_board_uses_the_cleaned_page_name(self):
        assert company_for(None, "Careers at Marriott") == "Marriott"
        assert company_for(_source("LinkedIn", is_official=False), "Acme Careers") == "Acme"


class TestPromotable:
    @pytest.mark.parametrize("page", ["Starbucks Coffee Company", "Careers at Paramount"])
    def test_usable_names(self, page):
        assert promotable_name(page)

    @pytest.mark.parametrize(
        "page", [None, "", "starbucks.eightfold.ai", "B10 Wells Fargo Bank, N. A.", "2100 NVIDIA USA", "Candidate Experience site"]
    )
    def test_unusable_names(self, page):
        assert promotable_name(page) is None


class TestUpsertAndPromotion:
    def _scan(self, scan_db, url_row, page_name):
        jobs._upsert_posting(scan_db, url_row, ScanResult(success=True, title="Engineer", company_name=page_name), NOW)
        scan_db.commit()
        return scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()

    def test_a_placeholder_is_promoted_once_from_a_usable_page_and_its_earlier_jobs_follow(self, scan_db, make_source, make_url):
        source = make_source()
        source.name, source.name_source = "Jj", PLACEHOLDER
        scan_db.commit()
        earlier = self._scan(scan_db, make_url(source), "6090-Johnson & Johnson Services Inc. Legal Entity")
        assert scan_db.get(CrawlSource, source.id).name_source == PLACEHOLDER  # a legal entity isn't promotable

        self._scan(scan_db, make_url(source), "Johnson & Johnson")
        self._scan(scan_db, make_url(source), "Janssen Biotech")  # doesn't overwrite once promoted

        stored = scan_db.get(CrawlSource, source.id)
        assert (stored.name, stored.name_source) == ("Johnson & Johnson", AUTO)
        scan_db.expire_all()
        assert scan_db.get(JobPosting, earlier.id).company_name == "Johnson & Johnson"

    def test_a_manual_name_is_never_changed_by_a_scan(self, scan_db, make_source, make_url):
        source = make_source()
        source.name, source.name_source = "J&J", MANUAL
        scan_db.commit()
        posting = self._scan(scan_db, make_url(source), "Johnson & Johnson")
        assert posting.company_name == "J&J"
        assert scan_db.get(CrawlSource, source.id).name == "J&J"


class TestApplySourceCompany:
    def test_a_rename_and_new_sub_brands_recompute_every_job_from_the_raw_page_name(self, scan_db, make_source, make_url, monkeypatch):
        monkeypatch.setattr("app.services.company_logos.resolve_company", lambda *a, **k: None)
        source = make_source()
        source.name = "TJX Companies"
        rows = []
        for page in ("Marshalls of MA", "Homegoods LLC", "Marmaxx Operating Corp"):
            url_row = make_url(source)
            posting = JobPosting(url_id=url_row.id, title="Associate", page_company_name=page, company_name="TJX Companies",
                                 company_key="tjx", extraction_status=ScanStatus.SUCCESS)
            scan_db.add(posting)
            rows.append(posting)
        scan_db.commit()

        source.name, source.sub_brands = "TJX", ["Marshalls", "HomeGoods"]
        changed = company_names.apply_source_company(scan_db, source)
        scan_db.commit()
        scan_db.expire_all()

        assert changed == 3
        assert [(scan_db.get(JobPosting, p.id).company_name, scan_db.get(JobPosting, p.id).company_key) for p in rows] == [
            ("Marshalls", "marshalls"), ("HomeGoods", "homegoods"), ("TJX", "tjx")
        ]
        assert company_names.apply_source_company(scan_db, source) == 0  # idempotent


class TestAdminUpdate:
    def test_renaming_a_source_confirms_the_name_and_renames_its_jobs(self, scan_db, make_source, make_url, monkeypatch):
        from app.api.routes.crawl_sources import update_crawl_source
        from app.schemas.crawl_source import CrawlSourceRead, CrawlSourceUpdate

        monkeypatch.setattr("app.services.company_logos.resolve_company", lambda *a, **k: None)
        source = make_source()
        source.name, source.name_source = "Jj", PLACEHOLDER
        url_row = make_url(source)
        posting = JobPosting(url_id=url_row.id, title="Engineer", page_company_name="6090-Janssen Biotech, Inc. Legal Entity",
                             company_name="Jj", company_key="jj", extraction_status=ScanStatus.SUCCESS)
        scan_db.add(posting)
        scan_db.commit()

        updated = update_crawl_source(
            source.id, CrawlSourceUpdate(name="Johnson & Johnson", sub_brands=[" Janssen ", "janssen", ""]), scan_db
        )
        scan_db.commit()
        scan_db.expire_all()

        read = CrawlSourceRead.model_validate(updated)
        assert (read.name, read.name_source, read.sub_brands) == ("Johnson & Johnson", MANUAL, ["Janssen"])
        assert scan_db.get(JobPosting, posting.id).company_name == "Janssen"

    def test_changing_only_the_crawl_settings_does_not_touch_the_name(self, scan_db, make_source):
        from app.api.routes.crawl_sources import update_crawl_source
        from app.schemas.crawl_source import CrawlSourceUpdate

        source = make_source()
        source.name_source = AUTO
        scan_db.commit()
        update_crawl_source(source.id, CrawlSourceUpdate(max_concurrent_scans=2), scan_db)
        assert scan_db.get(CrawlSource, source.id).name_source == AUTO

class TestOneOffs:
    def _jobs(self, scan_db, make_url, source, page_name, n):
        for _ in range(n):
            url_row = make_url(source)
            scan_db.add(JobPosting(url_id=url_row.id, title="Engineer", page_company_name=page_name, company_name=page_name,
                                   company_key=(page_name or "").lower(), extraction_status=ScanStatus.SUCCESS))
        scan_db.commit()

    def test_curation_fixes_slugs_and_notes_flags_mismatches_and_lists_sub_brands(self, scan_db, make_source, make_url):
        from one_off.curate_source_names import curate

        slug, noted, mismatched, tjx, manual = (make_source() for _ in range(5))
        slug.name, noted.name, mismatched.name, tjx.name = "greenhouse/clearstreet", "CenterWell (Humana home health)", "Gem", "TJX"
        manual.name, manual.name_source = "lever/keepme", MANUAL
        scan_db.commit()
        self._jobs(scan_db, make_url, slug, "Clear Street", 3)
        self._jobs(scan_db, make_url, mismatched, "11x.ai", 12)
        self._jobs(scan_db, make_url, tjx, "Marshalls of MA", 25)
        self._jobs(scan_db, make_url, tjx, "TJX Companies", 30)
        self._jobs(scan_db, make_url, tjx, "B10 TJX Legal Co LLC", 40)  # an entity code: not a sub-brand

        auto_fixes, review, candidates = curate(scan_db)

        assert {s.name: new for s, new in auto_fixes.items()} == {
            "greenhouse/clearstreet": "Clear Street", "CenterWell (Humana home health)": "CenterWell"
        }
        assert [(s.name, dominant) for s, dominant, _ in review] == [("Gem", "11x.ai")]
        # Gem's 11x.ai jobs (12) are under the 20-job floor; TJX's entity-coded name never qualifies.
        assert [(s.name, name, n) for s, name, n in candidates] == [("TJX", "Marshalls of MA", 25)]

    def test_apply_renames_every_job_to_its_source_and_cleans_no_source_jobs(self, scan_db, make_source, make_url, monkeypatch):
        from one_off import apply_source_company_names as apply

        monkeypatch.setattr("app.services.company_logos.resolve_company", lambda *a, **k: None)
        wells = make_source()
        wells.name = "Wells Fargo"
        scan_db.commit()
        self._jobs(scan_db, make_url, wells, "B10 Wells Fargo Bank, N. A.", 2)
        self._jobs(scan_db, make_url, None, "Careers at Marriott", 1)

        changes = apply.planned_changes(scan_db)
        assert changes["Wells Fargo"][("B10 Wells Fargo Bank, N. A.", "Wells Fargo")] == 2
        assert changes["(no source)"][("Careers at Marriott", "Marriott")] == 1

        monkeypatch.setattr(apply, "SessionLocal", lambda: scan_db)
        monkeypatch.setattr(scan_db, "close", lambda: None)
        monkeypatch.setattr("sys.argv", ["apply_source_company_names"])
        apply.main()
        scan_db.expire_all()
        assert sorted(p.company_name for p in scan_db.query(JobPosting)) == ["Marriott", "Wells Fargo", "Wells Fargo"]
        assert apply.planned_changes(scan_db) == {}  # idempotent

    def test_discovered_boards_start_with_a_placeholder_name(self, scan_db):
        from app.services.crawl_sources import register_discovered_board

        source = register_discovered_board(scan_db, "https://boards.greenhouse.io/clearstreet/jobs/123")
        assert source is not None and source.name_source == PLACEHOLDER
