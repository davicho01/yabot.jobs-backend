"""Phase C: find the official careers site of a company seen only elsewhere."""

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.models import CrawlSource, JobPosting
from app.models.company import Company
from app.models.enums import ScanStatus
from app.services import official_sites
from app.services.company_names import AUTO, PLACEHOLDER

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)

HOMEPAGE = """<html><body>
<a href="https://www.linkedin.com/company/acme">LinkedIn</a>
<a href="https://facebook.com/acme">Facebook</a>
<a href="/about">About</a>
<a href="https://boards.greenhouse.io/acme">Careers</a>
</body></html>"""


def _client(pages: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404, text="")

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _job(scan_db, make_url, source, company):
    url_row = make_url(source)
    url_row.created_at = NOW - timedelta(days=1)
    scan_db.add(JobPosting(url_id=url_row.id, title="Engineer", company_name=company,
                           company_key=company.lower(), extraction_status=ScanStatus.SUCCESS))
    scan_db.commit()


def _linkedin(scan_db, make_source):
    board = make_source()
    board.name, board.name_source, board.status, board.is_official = "linkedin.com", PLACEHOLDER, "pending", False
    scan_db.commit()
    return board


def test_a_page_link_to_a_supported_board_is_found_and_social_links_are_not():
    assert official_sites.board_in_page("https://acme.com/", HOMEPAGE) == "https://boards.greenhouse.io/acme"
    assert official_sites.board_in_page("https://acme.com/", '<a href="https://linkedin.com/jobs">x</a>') is None


def test_only_companies_seen_elsewhere_without_an_official_source_are_candidates(scan_db, make_source, make_url):
    linkedin = _linkedin(scan_db, make_source)
    official = make_source()
    official.name = "Globex"
    scan_db.commit()
    _job(scan_db, make_url, linkedin, "Acme")
    _job(scan_db, make_url, linkedin, "Acme")
    _job(scan_db, make_url, None, "Initech")
    _job(scan_db, make_url, linkedin, "Globex")  # its official site is already a source
    _job(scan_db, make_url, official, "Hooli")  # found on an official site: not a candidate
    scan_db.add(Company(company_key="initech", display_name="Initech", official_site_checked_at=NOW - timedelta(days=3)))
    scan_db.commit()

    candidates = official_sites.companies_needing_official_site(scan_db, now=NOW, limit=10)

    assert candidates == [("acme", "Acme")]  # Initech was looked at 3 days ago


def test_a_dry_run_reports_the_board_and_writes_nothing(scan_db, make_source, make_url):
    _job(scan_db, make_url, _linkedin(scan_db, make_source), "Acme")
    scan_db.add(Company(company_key="acme", display_name="Acme", domain="acme.com", domain_source="jsonld"))
    scan_db.commit()
    sources_before = scan_db.query(CrawlSource).count()

    outcomes = official_sites.run(scan_db, dry_run=True, now=NOW, client=_client({"https://acme.com": HOMEPAGE}))

    assert [(o.company_key, o.board_url, o.result) for o in outcomes] == [("acme", "https://boards.greenhouse.io/acme", "found")]
    scan_db.expire_all()
    assert scan_db.query(CrawlSource).count() == sources_before
    assert scan_db.scalar(select(Company).where(Company.company_key == "acme")).official_site_checked_at is None


def test_a_found_board_becomes_a_pending_source_named_after_the_company_and_isnt_looked_at_again(
    scan_db, make_source, make_url
):
    _job(scan_db, make_url, _linkedin(scan_db, make_source), "Acme")
    scan_db.add(Company(company_key="acme", display_name="Acme", domain="acme.com", domain_source="jsonld"))
    scan_db.commit()

    official_sites.run(scan_db, dry_run=False, now=NOW, client=_client({"https://acme.com/careers": HOMEPAGE}))

    source = scan_db.scalar(select(CrawlSource).where(CrawlSource.board_url.contains("greenhouse.io/acme")))
    assert source is not None
    # Pending until an agent verifies the board is really Acme's; its platform is already known.
    assert (source.name, source.name_source, source.status, source.ats_type) == ("Acme", AUTO, "pending", "greenhouse")
    company = scan_db.scalar(select(Company).where(Company.company_key == "acme"))
    assert (company.official_site_result, company.official_site_checked_at is not None) == ("found", True)
    assert official_sites.companies_needing_official_site(scan_db, now=NOW, limit=10) == []


def test_no_domain_or_no_board_is_recorded_as_none(scan_db, make_source, make_url):
    _job(scan_db, make_url, _linkedin(scan_db, make_source), "Acme")
    _job(scan_db, make_url, None, "Initech")
    scan_db.add(Company(company_key="acme", display_name="Acme", domain="acme.com", domain_source="jsonld"))
    scan_db.commit()

    outcomes = official_sites.run(
        scan_db, dry_run=False, now=NOW, client=_client({"https://acme.com": "<a href='/about'>About</a>"})
    )

    assert sorted((o.company_key, o.domain, o.result) for o in outcomes) == [
        ("acme", "acme.com", "none"),
        ("initech", None, "none"),
    ]
    company = scan_db.scalar(select(Company).where(Company.company_key == "initech"))
    assert company is not None and company.official_site_result == "none"
