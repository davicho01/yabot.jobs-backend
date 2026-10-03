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
    ],
)
def test_careers_wording_is_stripped(raw, expected):
    assert clean_company_name(raw) == expected


@pytest.mark.parametrize(
    "host", ["starbucks.eightfold.ai", "lockheedmartin.eightfold.ai", "careers.qualcomm.com", "careers.elcompanies.com",
             "acme.wd5.myworkdayjobs.com", "jobs.example.org"],
)
def test_hostnames_are_dropped_so_the_next_fallback_is_used(host):
    assert clean_company_name(host) is None


@pytest.mark.parametrize(
    "name",
    ["BambooHR", "Ashby", "Super.com", "Harness.io", "11x.ai", "Scale AI", "Netflix", "Starbucks Coffee Company",
     "Estée Lauder Companies", "Careers", "Jobs", "Corporate Careers"],
)
def test_real_names_pass_through_unchanged(name):
    # Odd-looking but real (BambooHR hires on its own Greenhouse board; Super.com
    # is a brand), or careers wording with nothing else to keep.
    assert clean_company_name(name) == name


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

    def test_careers_wording_from_the_page_is_cleaned_and_keyed(self, scan_db, make_source, make_url):
        posting = self._scan(scan_db, make_url(make_source()), "Careers at Marriott")
        assert (posting.company_name, posting.company_key) == ("Marriott", "marriott")

    def test_a_hostname_named_source_never_becomes_the_company(self, scan_db, make_source, make_url):
        source = make_source()
        source.name = "starbucks.eightfold.ai"
        scan_db.commit()
        posting = self._scan(scan_db, make_url(source), None)
        assert posting.company_name is None

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
        assert unresolved == ["hp.eightfold.ai (1 of 1 posting(s))"]

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
