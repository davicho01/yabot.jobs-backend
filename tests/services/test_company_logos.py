"""Tests for app.services.company_logos — resolving a company's own domain
from scan signals, and the logo URLs built from it."""

import pytest

import app.models as m
from app.services import company_logos as cl


def _resolve(db, key="acme", *, company_url=None, site_urls=()):
    company = cl.resolve_company(
        db, company_key=key, company_name=key.title(), company_url=company_url, site_urls=list(site_urls)
    )
    db.flush()
    return company


class TestRegistrableDomain:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("https://careers.rcwilley.com/jobs/1", "rcwilley.com"),
            ("jobs.bbc.co.uk", "bbc.co.uk"),
            ("HTTPS://WWW.Acme.COM/", "acme.com"),
            ("acme.com", "acme.com"),
            ("http://localhost:8000/x", None),
            ("http://10.0.0.1/", None),
            ("", None),
            (None, None),
        ],
    )
    def test_reduces_to_the_registrable_domain(self, value, expected):
        assert cl.registrable_domain(value) == expected


class TestLogoUrl:
    def test_none_without_a_key(self):
        assert cl.logo_url_for(None) is None

    def test_served_from_our_own_domain(self):
        assert cl.logo_url_for("logos/acme.com-abcd1234.png") == "https://yabot.jobs/logos/acme.com-abcd1234.png"


class TestHiringOrgUrl:
    def test_prefers_url(self):
        ld = {"hiringOrganization": {"url": "https://acme.com", "sameAs": "https://acme.io"}}
        assert cl.hiring_org_url(ld) == "https://acme.com"

    def test_skips_platform_and_social_links(self):
        ld = {
            "hiringOrganization": {
                "url": "https://boards.greenhouse.io/acme",
                "sameAs": ["https://www.linkedin.com/company/acme", "https://acme.com"],
            }
        }
        assert cl.hiring_org_url(ld) == "https://acme.com"

    @pytest.mark.parametrize("ld", [None, {}, {"hiringOrganization": "Acme"}, {"hiringOrganization": {"name": "Acme"}}])
    def test_none_when_absent(self, ld):
        assert cl.hiring_org_url(ld) is None


class TestResolveCompany:
    def test_creates_the_row_without_a_domain_for_an_ats_host(self, scan_db):
        company = _resolve(scan_db, site_urls=["https://boards.greenhouse.io/acme/jobs/1"])
        assert (company.company_key, company.domain) == ("acme", None)

    def test_own_careers_site_host(self, scan_db):
        company = _resolve(scan_db, site_urls=["https://careers.acme.com/jobs/1"])
        assert (company.domain, company.domain_source) == ("acme.com", cl.SOURCE_SITE_HOST)

    def test_falls_back_to_the_crawl_source_board_url(self, scan_db):
        company = _resolve(scan_db, site_urls=["https://acme.wd5.myworkdayjobs.com/x/job/1", "https://jobs.acme.com/"])
        assert company.domain == "acme.com"

    def test_jsonld_beats_site_host(self, scan_db):
        _resolve(scan_db, site_urls=["https://acmecareers.com/jobs/1"])
        company = _resolve(scan_db, company_url="https://acme.com", site_urls=["https://acmecareers.com/jobs/1"])
        assert (company.domain, company.domain_source) == ("acme.com", cl.SOURCE_JSONLD)

    def test_site_host_never_replaces_jsonld(self, scan_db):
        _resolve(scan_db, company_url="https://acme.com")
        company = _resolve(scan_db, site_urls=["https://acmecareers.com/jobs/1"])
        assert company.domain == "acme.com"

    def test_same_rank_does_not_flip_flop(self, scan_db):
        _resolve(scan_db, site_urls=["https://careers.acme.com/1"])
        company = _resolve(scan_db, site_urls=["https://jobs.acme-group.com/2"])
        assert company.domain == "acme.com"

    def test_manual_is_never_overwritten(self, scan_db):
        company = _resolve(scan_db)
        cl.set_manual_domain(scan_db, company, "https://www.acme.org/")
        company = _resolve(scan_db, company_url="https://acme.com", site_urls=["https://careers.acme.com/1"])
        assert (company.domain, company.domain_source) == ("acme.org", cl.SOURCE_MANUAL)

    def test_clearing_manual_reopens_automatic_resolution(self, scan_db):
        company = _resolve(scan_db)
        cl.set_manual_domain(scan_db, company, "acme.org")
        cl.set_manual_domain(scan_db, company, None)
        assert (company.domain, company.domain_source) == (None, None)
        assert _resolve(scan_db, company_url="https://acme.com").domain == "acme.com"

    def test_manual_rejects_junk(self, scan_db):
        with pytest.raises(ValueError):
            cl.set_manual_domain(scan_db, _resolve(scan_db), "not a domain")

    def test_a_host_shared_by_other_companies_is_rejected_and_evicted(self, scan_db):
        for key in ("one", "two"):
            assert _resolve(scan_db, key, site_urls=["https://jobs.sharedats.com/x"]).domain == "sharedats.com"
        assert _resolve(scan_db, "three", site_urls=["https://jobs.sharedats.com/y"]).domain is None
        assert {cl.usable_domain(c) for c in scan_db.query(m.Company)} == {None}
        # ...and stays refused, rather than the next tenant getting it back.
        assert _resolve(scan_db, "four", site_urls=["https://jobs.sharedats.com/z"]).domain is None

    def test_companies_sharing_a_domain_legitimately_do_not_make_it_a_shared_host(self, scan_db):
        # A parent's legal entities on one domain (set by an admin, JSON-LD,
        # or logo.dev) aren't an ATS host — only job-page host claims count.
        cl.set_manual_domain(scan_db, _resolve(scan_db, "parent"), "shared.com")
        _resolve(scan_db, "sub", company_url="https://shared.com")
        _resolve(scan_db, "third", site_urls=["https://jobs.shared.com/1"])
        domains = {c.company_key: c.domain for c in scan_db.query(m.Company)}
        assert domains == {"parent": "shared.com", "sub": "shared.com", "third": "shared.com"}

    def test_no_key_or_name_is_a_no_op(self, scan_db):
        assert cl.resolve_company(scan_db, company_key=None, company_name="X", company_url=None, site_urls=[]) is None
        assert scan_db.query(m.Company).count() == 0

    def test_posting_reads_its_logo_through_company_key(self, scan_db, make_url):
        url_row = make_url(None)
        posting = m.JobPosting(url_id=url_row.id, company_name="Acme", company_key="acme")
        scan_db.add(posting)
        company = _resolve(scan_db, company_url="https://acme.com")
        company.logo_key, company.logo_origin, company.logo_status = "logos/acme.com-1.png", "logo_dev", "ok"
        scan_db.commit()
        scan_db.expire_all()
        posting = scan_db.get(m.JobPosting, posting.id)
        assert posting.company_domain == "acme.com"
        assert posting.company_logo_url == "https://yabot.jobs/logos/acme.com-1.png"


class TestSameSession:
    def test_shared_host_is_caught_without_an_explicit_flush(self, scan_db):
        for key in ("one", "two", "three"):
            cl.resolve_company(
                scan_db, company_key=key, company_name=key, company_url=None, site_urls=["https://x.sharedats.com/1"]
            )
        assert {cl.usable_domain(c) for c in scan_db.query(m.Company)} == {None}


class TestBackfill:
    def test_resolves_own_hosts_and_not_shared_ones(self, scan_db, make_url):
        from one_off.backfill_companies import backfill

        rows = [
            ("acme", "https://careers.acme.com/1"),
            ("acme", "https://careers.acme.com/2"),
            ("ghco", "https://boards.greenhouse.io/ghco/jobs/3"),
        ] + [(f"tenant{i}", f"https://tenant{i}.unknownats.com/job/{i}") for i in range(4)]
        for key, url in rows:
            url_row = make_url(None)
            url_row.url = url
            scan_db.add(m.JobPosting(url_id=url_row.id, company_name=key, company_key=key, extraction_status="success"))
        scan_db.commit()

        stats = backfill(scan_db, commit=True)

        domains = {c.company_key: cl.usable_domain(c) for c in scan_db.query(m.Company)}
        assert domains["acme"] == "acme.com"
        assert domains["ghco"] is None
        # The first two claimants got unknownats.com before it was knowable
        # that it's shared; the third claim evicts it from them.
        assert all(domains[f"tenant{i}"] is None for i in range(4))
        assert stats["companies"] == 6


class TestDigestEmail:
    def _job(self, url):
        from types import SimpleNamespace

        return SimpleNamespace(posting=SimpleNamespace(company_logo_url=url, company_name="Acme & Co"))

    def test_logo_cell(self):
        from app.services.email import _logo_cell

        cell = _logo_cell(self._job("https://yabot.jobs/logos/acme.com-1.png"))
        assert 'src="https://yabot.jobs/logos/acme.com-1.png"' in cell
        assert 'alt="Acme &amp; Co"' in cell

    def test_no_cell_without_a_logo(self):
        from app.services.email import _logo_cell

        assert _logo_cell(self._job(None)) == ""
