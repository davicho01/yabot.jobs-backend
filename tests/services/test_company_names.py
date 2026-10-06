"""Company names that were careers wording or a hostname rather than a name:
job_dedup.clean_company_name, the Eightfold adapter reading the name from the
page title, _upsert_posting applying both, and the one-off backfill. The
examples are the live ones measured on 2026-10-03."""

from datetime import datetime, timezone

import pytest

from app.models import JobPosting
from app.models.enums import ScanStatus
from app.services import jobs
from app.services.adapters.base import ScanResult
from app.services.adapters.eightfold import _title_company
from app.services.job_dedup import clean_company_name

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Careers at Marriott", "Marriott"),
        ("Quest Diagnostics Careers", "Quest Diagnostics"),
        ("Careers at Atlantic Health System", "Atlantic Health System"),
        ("SBH Careers", "SBH"),
        ("WTW External Careers Site", "WTW"),
        ("Corporate Careers | Trueblue", "Trueblue"),
        ("AF Group Careers Section", "AF Group"),
        ("Accenture Federal Services Careers Marketplace", "Accenture Federal Services"),
        ("Verisk Careers | Verisk", "Verisk"),
        ("Texas Children's Careers", "Texas Children's"),
        ("  Hub   Group Careers ", "Hub Group"),
        ("WellPower - All Jobs", "WellPower"),
        ("Studio Associate | Careers | Lucid Motors", "Lucid Motors"),
        ("Join our team | Careers at Principal", "Principal"),
        ("Datadog Careers | Datadog", "Datadog"),
        ("EQ Bank | Canada's Challenger Bank", "EQ Bank"),
        ("CCB iHeartMedia + Entertainment, Inc. | MPG", "CCB iHeartMedia + Entertainment, Inc."),
        ("Achievement First | Achievement First Public Charter Schools prepare every student for college and career",
         "Achievement First"),
        ("Bank of England Job Board -", "Bank of England"),
        ("Realtor.com Careers", "Realtor.com"),
        ("Northwell Career Site", "Northwell"),
        ("Roku Jobs", "Roku"),
    ],
)
def test_careers_wording_is_stripped(raw, expected):
    assert clean_company_name(raw) == expected


@pytest.mark.parametrize(
    "host", ["starbucks.eightfold.ai", "lockheedmartin.eightfold.ai", "careers.qualcomm.com", "careers.elcompanies.com",
             "acme.wd5.myworkdayjobs.com", "jobs.example.org", "www.rei.jobs", "aecom.jobs", "instacart.careers",
             "jobs.dayforcehcm.com"],
)
def test_hostnames_are_dropped_so_the_next_fallback_is_used(host):
    assert clean_company_name(host) is None


@pytest.mark.parametrize(
    "name",
    ["BambooHR", "Ashby", "Super.com", "Harness.io", "11x.ai", "Scale AI", "Netflix", "Starbucks Coffee Company",
     "Estée Lauder Companies"],
)
def test_real_names_pass_through_unchanged(name):
    # Odd-looking but real (BambooHR hires on its own Greenhouse board; Super.com is a brand).
    assert clean_company_name(name) == name


@pytest.mark.parametrize("name", ["Careers", "Jobs", "Corporate Careers", "Candidate Experience site"])
def test_nothing_but_careers_wording_cleans_to_none_so_the_next_source_wins(name):
    assert clean_company_name(name) is None


def test_empty_and_none():
    assert clean_company_name(None) is None
    assert clean_company_name("   ") is None


class TestEightfoldTitle:
    def test_company_is_the_last_title_segment(self):
        assert _title_company("<title>store manager, Tulsa | Starbucks Coffee Company</title>") == "Starbucks Coffee Company"

    def test_og_title_and_multi_segment_titles(self):
        html = '<meta property="og:title" content="Sr. Client Partner | New York,New York | Netflix"><title>x</title>'
        assert _title_company(html) == "Netflix"

    def test_a_title_without_a_company_segment_gives_nothing(self):
        assert _title_company("<title>Just a job title</title>") is None
        assert _title_company("") is None


class TestUpsert:
    def _scan(self, scan_db, url_row, company_name):
        jobs._upsert_posting(
            scan_db, url_row, ScanResult(success=True, title="Engineer", company_name=company_name, location="Remote"), NOW
        )
        scan_db.commit()
        return scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()

    def test_careers_wording_from_the_page_is_cleaned_and_keyed_when_there_is_no_source(self, scan_db, make_url):
        posting = self._scan(scan_db, make_url(None), "Careers at Marriott")
        assert (posting.company_name, posting.company_key) == ("Marriott", "marriott")

    def test_a_confirmed_source_name_wins_over_the_page(self, scan_db, make_source, make_url):
        source = make_source()
        source.name = "Marriott"
        scan_db.commit()
        posting = self._scan(scan_db, make_url(source), "Careers at Marriott International")
        assert (posting.company_name, posting.page_company_name) == ("Marriott", "Careers at Marriott International")

    def test_a_real_name_from_the_page_beats_a_hostname_named_source(self, scan_db, make_source, make_url):
        source = make_source()
        source.name = "starbucks.eightfold.ai"
        scan_db.commit()
        posting = self._scan(scan_db, make_url(source), "Starbucks Coffee Company")
        assert posting.company_name == "Starbucks Coffee Company"

    def test_with_nothing_better_the_hostname_is_kept_not_lost(self, scan_db, make_source, make_url):
        # e.g. careers.underarmour.com: no name on the page. Keeping the hostname keeps the
        # company's jobs grouped (company_key, hub, related links); None would drop all that.
        source = make_source()
        source.name = "careers.underarmour.com"
        scan_db.commit()
        posting = self._scan(scan_db, make_url(source), None)
        assert (posting.company_name, posting.company_key) == ("careers.underarmour.com", "careersunderarmourcom")

    def test_a_cleaned_source_name_is_still_a_fallback(self, scan_db, make_source, make_url):
        source = make_source()
        source.name = "FirstEnergy Careers"
        scan_db.commit()
        assert self._scan(scan_db, make_url(source), None).company_name == "FirstEnergy"


class TestBackfill:
    def _posting(self, scan_db, make_url, source, name, excerpt=None):
        url_row = make_url(source)
        posting = JobPosting(
            url_id=url_row.id, title="Engineer", company_name=name, company_key=(name or "").lower(),
            extraction_status=ScanStatus.SUCCESS, raw_source={"html_excerpt": excerpt} if excerpt is not None else None,
        )
        scan_db.add(posting)
        scan_db.commit()
        return posting

    def test_plans_and_writes_both_kinds_and_leaves_real_names_alone(self, scan_db, make_source, make_url, monkeypatch):
        from one_off import backfill_clean_company_names as backfill

        starbucks = make_source()
        marriott = self._posting(scan_db, make_url, None, "Careers at Marriott")
        titled = self._posting(scan_db, make_url, starbucks, "starbucks.eightfold.ai",
                               "<html><title>barista | Starbucks Coffee Company</title>")
        untitled = self._posting(scan_db, make_url, starbucks, "starbucks.eightfold.ai", "<html>" + "x" * 50)
        orphan = self._posting(scan_db, make_url, make_source(), "hp.eightfold.ai", "<html>no title")
        real = self._posting(scan_db, make_url, None, "BambooHR")

        by_name, by_posting, unresolved = backfill.plan_renames(scan_db)
        assert by_name == {"Careers at Marriott": "Marriott"}
        # The untitled Starbucks posting takes the name found for its source's other postings.
        assert sorted(name for _, name in by_posting["starbucks.eightfold.ai"]) == ["Starbucks Coffee Company"] * 2
        assert len(unresolved) == 1 and unresolved[0].startswith("hp.eightfold.ai (1 posting(s)")

        monkeypatch.setattr(backfill, "SessionLocal", lambda: scan_db)
        monkeypatch.setattr(scan_db, "close", lambda: None)
        monkeypatch.setattr("sys.argv", ["backfill_clean_company_names"])
        backfill.main()
        scan_db.expire_all()

        assert (scan_db.get(JobPosting, marriott.id).company_name, scan_db.get(JobPosting, marriott.id).company_key) == ("Marriott", "marriott")
        for p in (titled, untitled):
            row = scan_db.get(JobPosting, p.id)
            assert (row.company_name, row.company_key) == ("Starbucks Coffee Company", "starbucks coffee")
        assert scan_db.get(JobPosting, orphan.id).company_name == "hp.eightfold.ai"  # reported, not guessed
        assert scan_db.get(JobPosting, real.id).company_name == "BambooHR"

        # Idempotent: a second run finds nothing to do.
        by_name, by_posting, _ = backfill.plan_renames(scan_db)
        assert by_name == {} and "starbucks.eightfold.ai" not in by_posting

    def test_hostname_recovery_takes_the_majority_and_reduces_or_rejects_odd_titles(self, scan_db, make_source, make_url):
        from one_off import backfill_clean_company_names as backfill

        source = make_source()
        for _ in range(4):
            self._posting(scan_db, make_url, source, "jobs.sap.com", "<title>Developer | SAP</title>")
        self._posting(scan_db, make_url, source, "jobs.sap.com", "<title>Partner Manager | Saudi Arabia</title>")
        for _ in range(2):
            self._posting(scan_db, make_url, source, "careers.unitedhealthgroup.com",
                          "<title>Nurse | Optum Washington at UnitedHealth Group</title>")
        self._posting(scan_db, make_url, source, "careers-x.icims.com", "<title>Jobs | Welcome</title>")

        _, by_posting, unresolved = backfill.plan_renames(scan_db)

        assert {name for _, name in by_posting["jobs.sap.com"]} == {"SAP"}  # the odd one out follows the majority
        assert {name for _, name in by_posting["careers.unitedhealthgroup.com"]} == {"UnitedHealth Group"}
        assert "careers-x.icims.com" not in by_posting and unresolved[0].startswith("careers-x.icims.com")

