"""Admin company-logo routes: the Edit source dialog's company list, and
setting a logo from a URL / upload / back to automatic. Route functions are
called directly (no HTTP layer), like the rest of tests/api."""

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes import admin
from app.api.routes.crawl_sources import list_crawl_source_companies
from app.db.base import Base
from app.schemas.admin import AdminCompanyLogoUrl
from app.services import logo_images
from app.services.page_store import LocalDirPageStore
from tests.services.test_logo_images import png


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[m.CrawlSource.__table__, m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__]
    )
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    local = LocalDirPageStore(tmp_path)
    monkeypatch.setattr("app.services.company_logos.get_page_store", lambda: local)
    return local


def _source(db, name="Acme Corp") -> m.CrawlSource:
    source = m.CrawlSource(name=name, ats_type="greenhouse", board_url=f"https://boards.greenhouse.io/{name}")
    db.add(source)
    db.commit()
    return source


def _posting(db, source, company_name, n):
    url = m.JobPostingUrl(
        url=f"https://x/{n}", normalized_url=f"https://x/{n}", url_hash=f"h{n}", domain="x", crawl_source_id=source.id
    )
    db.add(url)
    db.flush()
    db.add(m.JobPosting(url_id=url.id, company_name=company_name, company_key=company_name.lower()))
    db.commit()


class TestSourceCompanies:
    def test_falls_back_to_the_source_name_before_any_postings(self, db):
        rows = list_crawl_source_companies(_source(db).id, db)
        assert [(r.company_key, r.display_name, r.logo_url) for r in rows] == [("acme", "Acme Corp", None)]

    def test_lists_its_postings_companies_most_first(self, db):
        source = _source(db, "Walmart")
        for n, name in enumerate(["Walmart", "Walmart", "Sams Club"]):
            _posting(db, source, name, n)
        rows = list_crawl_source_companies(source.id, db)
        assert [(r.company_key, r.posting_count) for r in rows] == [("walmart", 2), ("sams club", 1)]


class TestSetLogo:
    def test_from_url_pins_our_own_copy(self, db, store, monkeypatch):
        monkeypatch.setattr(
            logo_images, "logo_from_url",
            lambda url: logo_images.LogoResult(logo_images.STATUS_OK, png=logo_images.normalize(png()), source_url=url),
        )
        read = admin.set_company_logo_from_url(
            AdminCompanyLogoUrl(company_key="acme", display_name="Acme Corp", url="https://acme.com/logo.png"), db
        )
        assert read.logo_origin == "url" and read.logo_url.endswith(".png")
        assert store.get(db.query(m.Company).one().logo_key)

    def test_from_url_rejection_is_a_readable_422(self, db, monkeypatch):
        def reject(url):
            raise logo_images.LogoRejected("Nothing at that address (HTTP 404)")

        monkeypatch.setattr(logo_images, "logo_from_url", reject)
        with pytest.raises(HTTPException) as exc:
            admin.set_company_logo_from_url(AdminCompanyLogoUrl(company_key="acme", url="https://acme.com/x"), db)
        assert (exc.value.status_code, exc.value.detail) == (422, "Nothing at that address (HTTP 404)")

    def _upload(self, db, data: bytes):
        file = UploadFile(file=io.BytesIO(data), filename="logo.png")
        return asyncio.run(admin.upload_company_logo(company_key="acme", display_name="Acme", file=file, db=db))

    def test_upload_then_back_to_automatic(self, db):
        read = self._upload(db, png())
        assert (read.logo_origin, read.display_name) == ("upload", "Acme")
        read = admin.clear_company_logo(company_key="acme", db=db)
        assert (read.logo_origin, read.logo_url) == (None, None)

    def test_upload_rejects_a_non_image(self, db):
        with pytest.raises(HTTPException) as exc:
            self._upload(db, b"not an image")
        assert exc.value.status_code == 422 and "isn't an image" in exc.value.detail
