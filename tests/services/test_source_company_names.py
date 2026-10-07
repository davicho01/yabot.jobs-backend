"""The official company site owns the job: app.services.company_names.

A confirmed crawl source's name is what every one of its jobs shows (or a
configured sub-brand the page names); a placeholder source takes its name from
the first usable page; jobs with no source keep the cleaned page name.
"""

from datetime import datetime, timezone

import pytest

from app.models import CrawlSource, JobPosting
from app.models.enums import ScanStatus
from app.services import jobs
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

    def test_a_placeholder_is_promoted_once_and_only_later_jobs_use_it(self, scan_db, make_source, make_url):
        source = make_source()
        source.name, source.name_source = "Jj", PLACEHOLDER
        scan_db.commit()
        earlier = self._scan(scan_db, make_url(source), "6090-Johnson & Johnson Services Inc. Legal Entity")
        assert scan_db.get(CrawlSource, source.id).name_source == PLACEHOLDER  # a legal entity isn't promotable

        self._scan(scan_db, make_url(source), "Johnson & Johnson")
        self._scan(scan_db, make_url(source), "Janssen Biotech")  # doesn't overwrite once promoted

        stored = scan_db.get(CrawlSource, source.id)
        assert (stored.name, stored.name_source) == ("Johnson & Johnson", AUTO)
        later = self._scan(scan_db, make_url(source), "6084-Janssen Research & Development, LLC Legal Entity")
        assert later.company_name == "Johnson & Johnson"
        scan_db.expire_all()
        # A name change applies to jobs scanned from now on; the earlier job keeps its name until rescanned.
        assert scan_db.get(JobPosting, earlier.id).company_name != "Johnson & Johnson"

    def test_a_manual_name_is_never_changed_by_a_scan(self, scan_db, make_source, make_url):
        source = make_source()
        source.name, source.name_source = "J&J", MANUAL
        scan_db.commit()
        posting = self._scan(scan_db, make_url(source), "Johnson & Johnson")
        assert posting.company_name == "J&J"
        assert scan_db.get(CrawlSource, source.id).name == "J&J"


class TestOfficialVsElsewhere:
    """Phase B: only a crawled company site is official; jobs found anywhere
    else take an official name they match exactly, and lose dedup to the
    official copy."""

    def _scan(self, scan_db, url_row, page_name, title="Engineer", location="Austin, TX"):
        posting = jobs._upsert_posting(
            scan_db, url_row, ScanResult(success=True, title=title, company_name=page_name, location=location), NOW
        )
        scan_db.commit()
        return posting

    def _board(self, scan_db, make_source, *, name="linkedin.com", status="pending", is_official=False):
        board = make_source()
        board.name, board.name_source, board.status, board.is_official = name, PLACEHOLDER, status, is_official
        scan_db.commit()
        return board

    def test_only_an_active_non_job_board_source_is_official(self):
        from app.services.company_names import is_official_source

        assert is_official_source(CrawlSource(name="Acme", is_official=True, status="active"))
        assert not is_official_source(CrawlSource(name="Acme", is_official=True, status="pending"))
        assert not is_official_source(CrawlSource(name="linkedin.com", is_official=False, status="active"))
        assert not is_official_source(None)

    def test_a_job_board_or_uncrawlable_placeholder_is_never_renamed_after_a_job(self, scan_db, make_source, make_url):
        linkedin = self._board(scan_db, make_source)
        uncrawlable = self._board(scan_db, make_source, name="acme-careers.com", is_official=True)

        self._scan(scan_db, make_url(linkedin), "Acme Corp")
        self._scan(scan_db, make_url(uncrawlable), "Acme Corp")

        for source in (linkedin, uncrawlable):
            assert scan_db.get(CrawlSource, source.id).name_source == PLACEHOLDER

    def test_a_job_from_elsewhere_takes_an_official_name_it_matches_exactly(self, scan_db, make_source, make_url):
        lowes, walmart = make_source(), make_source()
        lowes.name = "Lowe's"
        walmart.name, walmart.sub_brands = "Walmart", ["Sam's Club"]
        scan_db.commit()
        linkedin = self._board(scan_db, make_source)

        assert self._scan(scan_db, make_url(linkedin), "Lowe's, Inc.").company_name == "Lowe's"
        assert self._scan(scan_db, make_url(linkedin), "Sam's Club").company_name == "Sam's Club"
        # A sub-brand merely contained in another company's name isn't a match.
        assert self._scan(scan_db, make_url(linkedin), "Sam's Club Pharmacy Partners").company_name == "Sam's Club Pharmacy Partners"
        assert self._scan(scan_db, make_url(None), "Careers at Initech").company_name == "Initech"

    def test_the_official_copy_takes_over_a_job_found_first_elsewhere(self, scan_db, make_source, make_url):
        acme = make_source()
        acme.name = "Acme"
        scan_db.commit()
        linkedin = self._board(scan_db, make_source)

        first = self._scan(scan_db, make_url(linkedin), "Acme")
        echo = self._scan(scan_db, make_url(None), "Acme")  # another copy, grouped under the first
        assert first.primary_posting_id is None and echo.primary_posting_id == first.id

        official = self._scan(scan_db, make_url(acme), "Acme Inc")
        scan_db.expire_all()

        assert scan_db.get(JobPosting, official.id).primary_posting_id is None
        assert scan_db.get(JobPosting, first.id).primary_posting_id == official.id
        assert scan_db.get(JobPosting, echo.id).primary_posting_id == official.id

        # Rescanning the job board's copy keeps it a duplicate of the official one.
        rescanned = self._scan(scan_db, scan_db.get(JobPosting, first.id).url, "Acme")
        assert rescanned.primary_posting_id == official.id

    def test_an_official_copy_found_first_stays_primary(self, scan_db, make_source, make_url):
        acme = make_source()
        acme.name = "Acme"
        scan_db.commit()
        linkedin = self._board(scan_db, make_source)

        official = self._scan(scan_db, make_url(acme), "Acme")
        board_copy = self._scan(scan_db, make_url(linkedin), "Acme")

        assert official.primary_posting_id is None
        assert board_copy.primary_posting_id == official.id

    def test_registering_a_job_board_url_makes_a_non_official_source(self, scan_db):
        from app.services.crawl_sources import register_discovered_board

        board = register_discovered_board(scan_db, "https://www.linkedin.com/jobs/view/123")
        company = register_discovered_board(scan_db, "https://boards.greenhouse.io/acme/jobs/1")

        assert board is not None and board.is_official is False
        assert company is not None and company.is_official is True


class TestAdminUpdate:
    def test_renaming_a_source_confirms_the_name_for_future_jobs_only(self, scan_db, make_source, make_url, monkeypatch):
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
        assert scan_db.get(JobPosting, posting.id).company_name == "Jj"  # existing jobs keep their name

        later = jobs._upsert_posting(
            scan_db, make_url(source), ScanResult(success=True, title="Scientist", company_name="Janssen Biotech, Inc."), NOW
        )
        assert later.company_name == "Janssen"  # the next scan uses the new name and sub-brands

    def test_changing_only_the_crawl_settings_does_not_touch_the_name(self, scan_db, make_source):
        from app.api.routes.crawl_sources import update_crawl_source
        from app.schemas.crawl_source import CrawlSourceUpdate

        source = make_source()
        source.name_source = AUTO
        scan_db.commit()
        update_crawl_source(source.id, CrawlSourceUpdate(max_concurrent_scans=2), scan_db)
        assert scan_db.get(CrawlSource, source.id).name_source == AUTO

    def test_rejecting_a_source_records_why(self, scan_db, make_source):
        from app.api.routes.crawl_sources import update_crawl_source
        from app.schemas.crawl_source import CrawlSourceRead, CrawlSourceUpdate

        source = make_source()
        source.name_source = AUTO
        scan_db.commit()
        updated = update_crawl_source(
            source.id, CrawlSourceUpdate(status="rejected", notes="robots.txt: User-agent: * / Disallow: /"), scan_db
        )
        read = CrawlSourceRead.model_validate(updated)
        assert (read.status, read.notes) == ("rejected", "robots.txt: User-agent: * / Disallow: /")
        assert read.name_source == AUTO  # a note isn't a rename

    def test_creating_a_white_label_board_uses_embedded_detection(self, scan_db, monkeypatch):
        from app.api.routes import crawl_sources
        from app.schemas.crawl_source import CrawlSourceCreate

        monkeypatch.setattr(crawl_sources, "detect_embedded_ats_source", lambda url: ("paradox", "careers.chuys.com"))
        source = crawl_sources.create_crawl_source(
            CrawlSourceCreate(name="Chuy's", board_url="https://careers.chuys.com"), scan_db
        )
        assert (source.ats_type, source.board_url, source.status) == ("paradox", "https://careers.chuys.com", "active")

class TestOneOffs:
    def _jobs(self, scan_db, make_url, source, page_name, n, page_title=None):
        raw = {"html_excerpt": f"<html><head><title>{page_title}</title></head></html>"} if page_title else None
        for _ in range(n):
            url_row = make_url(source)
            scan_db.add(JobPosting(url_id=url_row.id, title="Engineer", page_company_name=page_name, company_name=page_name,
                                   company_key=(page_name or "").lower(), extraction_status=ScanStatus.SUCCESS,
                                   raw_source=raw))
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

    def test_curation_only_auto_applies_page_names_the_board_address_confirms(self, scan_db, make_source, make_url):
        from one_off.curate_source_names import curate

        honda, l3harris, jpmc, spaced = (make_source() for _ in range(4))
        honda.name, honda.board_url = "careers.honda.com", "https://careers.honda.com"
        l3harris.name, l3harris.board_url = "jobs.l3harris.com", "https://jobs.l3harris.com"
        jpmc.name, jpmc.board_url = "jpmc.fa.oraclecloud.com", "https://jpmc.fa.oraclecloud.com/hcmUI"
        spaced.name = "Summit Health / CityMD"  # a real name with a spaced slash, not a board slug
        scan_db.commit()
        self._jobs(scan_db, make_url, honda, "American Honda Motor Company", 5)
        self._jobs(scan_db, make_url, l3harris, "L3HHCM20", 5)
        self._jobs(scan_db, make_url, jpmc, "Business Systems, Data & AI (Wealth Management)", 5)
        self._jobs(scan_db, make_url, spaced, "Summit Health Management, LLC", 5)

        auto_fixes, review, _ = curate(scan_db)

        assert {s.name: new for s, new in auto_fixes.items()} == {"careers.honda.com": "American Honda Motor Company"}
        assert sorted((s.name, proposal) for s, proposal, _ in review) == [
            ("jobs.l3harris.com", "L3HHCM20"),
            ("jpmc.fa.oraclecloud.com", "Business Systems, Data & AI (Wealth Management)"),
        ]

    def test_curation_falls_back_to_a_page_title_name_the_board_address_confirms(self, scan_db, make_source, make_url):
        from one_off.curate_source_names import curate

        l3harris = make_source()
        l3harris.name, l3harris.board_url = "jobs.l3harris.com", "https://jobs.l3harris.com"
        scan_db.commit()
        self._jobs(scan_db, make_url, l3harris, "L3HHCM20", 5, page_title="Systems Engineer | L3Harris Technologies")

        auto_fixes, review, _ = curate(scan_db)

        assert {s.name: new for s, new in auto_fixes.items()} == {"jobs.l3harris.com": "L3Harris Technologies"}
        assert review == []

    def test_curation_never_proposes_a_slug_keeps_doubt_notes_and_matches_short_names(
        self, scan_db, make_source, make_url
    ):
        from one_off.curate_source_names import curate

        slug, doubted, short = (make_source() for _ in range(3))
        slug.name, slug.board_url = "greenhouse/coalition", "https://boards.greenhouse.io/coalition"
        doubted.name = "Sphere (company unconfirmed)"
        short.name = "HP"
        scan_db.commit()
        self._jobs(scan_db, make_url, slug, "greenhouse/coalition", 5)  # old jobs named after the slug
        self._jobs(scan_db, make_url, short, "HP", 12)

        auto_fixes, review, _ = curate(scan_db)

        assert auto_fixes == {}
        assert sorted((s.name, proposal) for s, proposal, _ in review) == [
            ("Sphere (company unconfirmed)", "Sphere"),
            ("greenhouse/coalition", "greenhouse/coalition"),
        ]

    def test_curation_proposes_spacing_out_a_squashed_slug_name_for_review(self, scan_db, make_source, make_url):
        from one_off.curate_source_names import curate

        panw, epicor, single, manual, camel, caps = (make_source() for _ in range(6))
        panw.name, epicor.name, single.name = "Paloaltonetworks", "Epicorsoftware", "Thoughtworks"
        manual.name, manual.name_source = "Grafanalabs", MANUAL
        camel.name, caps.name = "WeWork", "FIS"  # brands spelled as one word on purpose
        scan_db.commit()
        self._jobs(scan_db, make_url, panw, "Palo Alto Networks, Inc.", 5)
        self._jobs(scan_db, make_url, epicor, "EPIC Epicor Software", 5)
        self._jobs(scan_db, make_url, single, "Thoughtworks", 5)  # already one word on its pages too
        self._jobs(scan_db, make_url, manual, "Grafana Labs", 5)
        self._jobs(scan_db, make_url, camel, "We Work Management LLC", 5)
        self._jobs(scan_db, make_url, caps, "F I S Global", 5)

        auto_fixes, review, _ = curate(scan_db)

        assert auto_fixes == {}  # never applied on its own: "Goodyear" looks just like a slug
        assert sorted((s.name, proposal) for s, proposal, _ in review) == [
            ("Epicorsoftware", "Epicor Software"),
            ("Paloaltonetworks", "Palo Alto Networks"),
        ]

    def test_curation_write_applies_auto_fixes_reviewed_names_and_sub_brands(self, scan_db, make_source):
        from one_off.curate_source_names import write_changes

        noted, l3harris, tjx, twin_a, twin_b = (make_source() for _ in range(5))
        noted.name, l3harris.name, tjx.name = "CenterWell (Humana home health)", "jobs.l3harris.com", "TJX"
        twin_a.name = twin_b.name = "Brigham Young University"
        scan_db.commit()

        write_changes(
            scan_db,
            {noted: "CenterWell"},
            names={"jobs.l3harris.com": " L3Harris  Technologies ", "Brigham Young University": "BYU", "nope": "X"},
            sub_brands={"TJX": ["HomeGoods", " Marshalls ", "HomeGoods", ""]},
        )

        assert (noted.name, noted.name_source) == ("CenterWell", AUTO)
        assert (l3harris.name, l3harris.name_source) == ("L3Harris Technologies", MANUAL)
        assert {twin_a.name, twin_b.name} == {"BYU"}
        assert tjx.sub_brands == ["HomeGoods", "Marshalls"]

    def test_curation_files_can_be_inline_json(self):
        from one_off.curate_source_names import _load

        assert _load('{"jobs.l3harris.com": "L3Harris Technologies"}') == {"jobs.l3harris.com": "L3Harris Technologies"}
        assert _load(None) is None

    def test_recent_backfill_renames_only_the_last_days_jobs_and_keeps_their_page_name(self, scan_db, make_source, make_url, monkeypatch):
        from datetime import timedelta

        from one_off import backfill_recent_company_names as backfill

        wells = make_source()
        wells.name = "Wells Fargo"
        scan_db.commit()
        now = datetime.now(timezone.utc)
        rows = {}
        for label, found, page in (("recent", now - timedelta(hours=6), "B10 Wells Fargo Bank, N. A."),
                                   ("old", now - timedelta(days=10), "I16 Wells Fargo International")):
            url_row = make_url(wells)
            url_row.created_at = found
            # Existing rows have no page_company_name yet: the migration doesn't backfill it.
            rows[label] = JobPosting(url_id=url_row.id, title="Analyst", company_name=page, company_key=page.lower(),
                                     extraction_status=ScanStatus.SUCCESS)
            scan_db.add(rows[label])
        scan_db.commit()

        changes = backfill.planned_changes(scan_db, now - timedelta(days=2))
        assert [(old, new) for _, old, new, _ in changes] == [("B10 Wells Fargo Bank, N. A.", "Wells Fargo")]

        monkeypatch.setattr(backfill, "SessionLocal", lambda: scan_db)
        monkeypatch.setattr(scan_db, "close", lambda: None)
        monkeypatch.setattr("sys.argv", ["backfill_recent_company_names", "--days", "2"])
        backfill.main()
        scan_db.expire_all()

        recent, old = scan_db.get(JobPosting, rows["recent"].id), scan_db.get(JobPosting, rows["old"].id)
        assert (recent.company_name, recent.company_key, recent.page_company_name) == (
            "Wells Fargo", "wells fargo", "B10 Wells Fargo Bank, N. A."
        )
        assert old.company_name == "I16 Wells Fargo International"  # older than the window: untouched
        assert backfill.planned_changes(scan_db, now - timedelta(days=2)) == []  # idempotent

    def test_discovered_boards_start_with_a_placeholder_name(self, scan_db):
        from app.services.crawl_sources import register_discovered_board

        source = register_discovered_board(scan_db, "https://boards.greenhouse.io/clearstreet/jobs/123")
        assert source is not None and source.name_source == PLACEHOLDER
