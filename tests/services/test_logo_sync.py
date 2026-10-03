"""Tests for logo.dev-backed logos (app.services.logo_dev +
company_logos.sync_company_logos) and admin-pinned logos. logo.dev is
mocked with httpx.MockTransport; its temporary download URLs via
logo_dev.download."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app.models as m
from app.services import company_logos as cl
from app.services import logo_dev
from app.services.page_store import LocalDirPageStore
from tests.services.test_logo_images import png

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


class FakeLogoDev:
    """brands: domain -> (status, etag); search: query -> results list."""

    def __init__(self, brands=None, search=None, image=None):
        self.brands = brands or {}
        self.search = search or {}
        self.image = image or png()
        self.calls: list[str] = []
        self.downloads: list[str] = []

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(str(request.url))
            assert request.headers["Authorization"] == "Bearer sk_test"
            if request.url.path == "/v2/brands/logo":
                domain = request.url.params["domain"]
                status, etag = self.brands.get(domain, (404, None))
                body = {"data": {"url": f"https://img.example/tmp/{domain}?sig=1", "etag": etag}}
                return httpx.Response(status, json=body if status == 200 else {})
            if request.url.path == "/search":
                return httpx.Response(200, json=self.search.get(request.url.params["q"], []))
            return httpx.Response(404)

        return httpx.Client(transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer sk_test"})

    def download(self, url):
        self.downloads.append(url)
        assert "Authorization" not in url
        return httpx.Response(200, content=self.image)


@pytest.fixture
def store(tmp_path):
    return LocalDirPageStore(tmp_path)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeLogoDev()
    monkeypatch.setattr(logo_dev, "download", fake.download)
    return fake


def company(db, key="acme", domain="acme.com", source="site_host", **logo):
    row = m.Company(company_key=key, display_name=key.title(), domain=domain, domain_source=source, **logo)
    db.add(row)
    db.commit()
    return row


def sync(db, fake, store, now=NOW):
    return cl.sync_company_logos(db, now=now, store=store, client=fake.client())


class TestSync:
    def test_stores_our_own_copy(self, scan_db, fake, store):
        fake.brands["acme.com"] = (200, "e1")
        row = company(scan_db)
        assert sync(scan_db, fake, store) == {"ok": 1}
        assert row.logo_key.startswith("logos/acme-") and store.get(row.logo_key)
        assert (row.logo_origin, row.logo_etag, row.logo_status, row.logo_domain) == ("logo_dev", "e1", "ok", "acme.com")
        assert fake.downloads == ["https://img.example/tmp/acme.com?sig=1&format=png&size=256"]

    def test_unchanged_etag_skips_the_download(self, scan_db, fake, store):
        fake.brands["acme.com"] = (200, "e1")
        row = company(scan_db)
        sync(scan_db, fake, store)
        key = row.logo_key
        assert sync(scan_db, fake, store, now=NOW + timedelta(days=91)) == {"unchanged": 1}
        assert (row.logo_key, len(fake.downloads)) == (key, 1)

    def test_not_due_means_no_lookup(self, scan_db, fake, store):
        company(scan_db, logo_status="ok", logo_checked_at=NOW, logo_key="logos/a.png", logo_origin="logo_dev")
        sync(scan_db, fake, store, now=NOW + timedelta(days=10))
        assert fake.calls == []

    def test_404_is_none_and_retried_in_30_days(self, scan_db, fake, store):
        row = company(scan_db)
        assert sync(scan_db, fake, store) == {"none": 1}
        assert row.logo_key is None
        assert sync(scan_db, fake, store, now=NOW + timedelta(days=29)) == {}
        assert sync(scan_db, fake, store, now=NOW + timedelta(days=31)) == {"none": 1}

    def test_202_still_indexing_is_retried_next_run(self, scan_db, fake, store):
        fake.brands["acme.com"] = (202, None)
        row = company(scan_db)
        assert sync(scan_db, fake, store) == {"pending": 1}
        assert row.logo_status is None

    def test_an_error_keeps_the_existing_logo(self, scan_db, fake, store):
        fake.brands["acme.com"] = (500, None)
        row = company(scan_db, logo_status="ok", logo_checked_at=NOW, logo_key="logos/a.png", logo_origin="logo_dev")
        sync(scan_db, fake, store, now=NOW + timedelta(days=91))
        assert (row.logo_key, row.logo_status) == ("logos/a.png", "error")

    def test_pinned_logos_are_never_touched(self, scan_db, fake, store):
        fake.brands["acme.com"] = (200, "e1")
        company(scan_db, logo_key="logos/mine.png", logo_origin="upload")
        assert sync(scan_db, fake, store) == {}
        assert fake.calls == []

    def test_no_key_configured_is_a_no_op(self, scan_db, store, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "logo_dev_secret_key", None)
        company(scan_db)
        assert cl.sync_company_logos(scan_db, now=NOW, store=store) == {}

    def test_out_of_budget_waits_for_the_next_run(self, scan_db, fake, store):
        row = company(scan_db)
        counts = cl.sync_company_logos(scan_db, now=NOW, store=store, client=fake.client(), budget_seconds=-1)
        assert (counts, row.logo_status) == ({"deferred": 1}, None)


class TestNameSearch:
    def test_a_single_exact_match_gives_the_domain_and_logo(self, scan_db, fake, store):
        fake.search["Stripe"] = [{"name": "Stripe, Inc.", "domain": "stripe.com"}, {"name": "Stripes", "domain": "stripes.io"}]
        fake.brands["stripe.com"] = (200, "e1")
        row = company(scan_db, "stripe", None, None)
        assert sync(scan_db, fake, store) == {"ok": 1}
        assert (row.domain, row.domain_source, row.logo_domain) == ("stripe.com", "logo_dev", "stripe.com")

    def test_the_top_ranked_exact_match_wins_over_lower_namesakes(self, scan_db, fake, store):
        fake.search["Ramp"] = [
            {"name": "Rampant", "domain": "rampant.com"},
            {"name": "Ramp", "domain": "ramp.com"},
            {"name": "Ramp", "domain": "rampnetwork.com"},
        ]
        fake.brands["ramp.com"] = (200, "e1")
        row = company(scan_db, "ramp", None, None)
        sync(scan_db, fake, store)
        assert row.domain == "ramp.com"

    def test_a_leading_tax_id_or_company_code_is_ignored(self, scan_db, fake, store):
        fake.search["NVIDIA USA"] = [{"name": "NVIDIA USA", "domain": "nvidia.com"}]
        fake.brands["nvidia.com"] = (200, "e1")
        row = company(scan_db, "2100 nvidia usa", None, None)
        row.display_name = "2100 NVIDIA USA"
        scan_db.commit()
        sync(scan_db, fake, store)
        assert row.domain == "nvidia.com"

    @pytest.mark.parametrize(
        "results",
        [
            [],
            [{"name": "Acme Labs", "domain": "acmelabs.com"}],  # not exact
            [{"name": "Acme", "domain": "greenhouse.io"}],  # a platform, not the company
        ],
    )
    def test_anything_less_is_left_alone(self, scan_db, fake, store, results):
        fake.search["Acme"] = results
        row = company(scan_db, "acme", None, None)
        assert sync(scan_db, fake, store) == {"none": 1}
        assert (row.domain, row.logo_key) == (None, None)


class TestNameEdgeCases:
    def test_a_name_that_is_a_domain_is_used_directly(self, scan_db, fake, store):
        fake.brands["11x.ai"] = (200, "e1")
        row = company(scan_db, "11xai", None, None)
        row.display_name = "11x.ai"
        scan_db.commit()
        sync(scan_db, fake, store)
        assert (row.domain, row.domain_source) == ("11x.ai", "logo_dev")
        assert not any("/search" in call for call in fake.calls)

    def test_legal_endings_are_dropped_on_a_retry(self, scan_db, fake, store):
        fake.search["scribd"] = [{"name": "Scribd", "domain": "scribd.com"}]
        fake.brands["scribd.com"] = (200, "e1")
        row = company(scan_db, "scribd", None, None)
        row.display_name = "Scribd, Inc."
        scan_db.commit()
        sync(scan_db, fake, store)
        assert row.domain == "scribd.com"

    def test_a_platform_is_allowed_its_own_domain(self, scan_db, fake, store):
        fake.search["Bamboohr"] = [{"name": "BambooHR", "domain": "bamboohr.com"}]
        fake.brands["bamboohr.com"] = (200, "e1")
        row = company(scan_db, "bamboohr", None, None)
        sync(scan_db, fake, store)
        assert row.domain == "bamboohr.com"

    @pytest.mark.parametrize(("name", "expected"), [
        ("11x.ai", "11x.ai"), ("Super.com", "super.com"), ("Harness.io Inc", None),
        ("J.P. Morgan", None), ("Stripe", None), ("boards.greenhouse.io", None),
    ])
    def test_domain_named(self, name, expected):
        assert cl.domain_named(name) == expected


class TestSourceNameFallback:
    def _posting_from_source(self, db, make_url, source_name, company_name):
        source = m.CrawlSource(name=source_name, ats_type="workday", board_url=f"https://x/{source_name}")
        db.add(source)
        db.flush()
        url = make_url(source)
        db.add(m.JobPosting(url_id=url.id, company_name=company_name, company_key=company_name.lower()))
        db.commit()

    def test_a_legal_entity_name_falls_back_to_its_sources_brand(self, scan_db, make_url, fake, store):
        self._posting_from_source(scan_db, make_url, "Freedom Mortgage", "FMC Freedom Mortgage Corporation")
        fake.search["Freedom Mortgage"] = [{"name": "Freedom Mortgage", "domain": "freedommortgage.com"}]
        fake.brands["freedommortgage.com"] = (200, "e1")
        row = company(scan_db, "fmc freedom mortgage corporation", None, None)
        assert sync(scan_db, fake, store) == {"ok": 1}
        assert (row.domain, row.logo_origin) == ("freedommortgage.com", "logo_dev")

    def test_slug_named_sources_search_by_their_last_part(self, scan_db, make_url, fake, store):
        self._posting_from_source(scan_db, make_url, "lever/aledade", "Aledade PBC")
        fake.search["aledade"] = [{"name": "Aledade", "domain": "aledade.com"}]
        fake.brands["aledade.com"] = (200, "e1")
        row = company(scan_db, "aledade pbc", None, None)
        sync(scan_db, fake, store)
        assert row.domain == "aledade.com"


class TestDomainChanges:
    def test_a_new_domain_clears_an_automatic_logo(self, scan_db):
        row = company(scan_db, logo_key="logos/a.png", logo_origin="logo_dev", logo_status="ok", logo_domain="acme.com")
        cl.set_manual_domain(scan_db, row, "acme.io")
        assert (row.logo_key, row.logo_status) == (None, None)

    def test_but_never_a_pinned_one(self, scan_db):
        row = company(scan_db, logo_key="logos/mine.png", logo_origin="url")
        cl.set_manual_domain(scan_db, row, "acme.io")
        assert row.logo_key == "logos/mine.png"


class TestManual:
    def test_pin_and_unpin(self, scan_db, store, monkeypatch):
        monkeypatch.setattr("app.services.company_logos.get_page_store", lambda: store)
        row = cl.get_or_create_company(scan_db, "newco", "NewCo")
        cl.set_manual_logo(scan_db, row, png(), origin=cl.ORIGIN_UPLOAD, source_url="newco.png")
        assert row.logo_key.startswith("logos/newco-") and store.get(row.logo_key)
        assert row.logo_origin == "upload"
        cl.clear_manual_logo(scan_db, row)
        assert (row.logo_key, row.logo_origin, row.logo_status) == (None, None, None)


class TestBrandNames:
    def test_entity_codes(self):
        from app.services.job_dedup import has_entity_code

        assert has_entity_code("2100 NVIDIA USA") and has_entity_code("94-1687665 Bank of America, N.A.")
        assert not any(has_entity_code(n) for n in ("84 Lumber", "3M", "7-Eleven", "Freedom Mortgage", None))

    def test_scan_shows_the_source_brand_for_a_coded_name(self, scan_db, make_url):
        from app.services.adapters.base import ScanResult
        from app.services.jobs import _upsert_posting

        source = m.CrawlSource(name="Nvidia", ats_type="workday", board_url="https://nvidia.wd5.myworkdayjobs.com/x")
        scan_db.add(source)
        scan_db.flush()
        url = make_url(source)
        posting = _upsert_posting(
            scan_db, url, ScanResult(success=True, title="Engineer", company_name="2100 NVIDIA USA"), NOW
        )
        assert (posting.company_name, posting.company_key) == ("Nvidia", "nvidia")

    def test_a_slug_named_source_keeps_the_scraped_name(self, scan_db, make_url):
        from app.services.adapters.base import ScanResult
        from app.services.jobs import _upsert_posting

        source = m.CrawlSource(name="workday/acme", ats_type="workday", board_url="https://acme.wd5.myworkdayjobs.com/x")
        scan_db.add(source)
        scan_db.flush()
        posting = _upsert_posting(
            scan_db, make_url(source), ScanResult(success=True, title="Engineer", company_name="2100 Acme USA"), NOW
        )
        assert posting.company_name == "2100 Acme USA"
