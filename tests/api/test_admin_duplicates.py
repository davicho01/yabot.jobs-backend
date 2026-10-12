import itertools
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.admin import get_duplicates
from app.db.base import Base
from app.models.enums import ScanStatus

_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[m.JobPostingUrl.__table__, m.JobPosting.__table__, m.Company.__table__, m.CrawlSource.__table__]
    )
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _posting(
    db,
    domain="example.com",
    company="Acme",
    title="Software Engineer",
    location="Austin, TX",
    primary=None,
    status=ScanStatus.SUCCESS,
    closed=False,
) -> m.JobPosting:
    n = next(_counter)
    url = m.JobPostingUrl(
        url=f"https://{domain}/jobs/{n}",
        normalized_url=f"https://{domain}/jobs/{n}",
        url_hash=f"duplicates-hash-{n}",
        domain=domain,
        closed_at=datetime.now(timezone.utc) if closed else None,
    )
    db.add(url)
    db.flush()
    posting = m.JobPosting(
        url_id=url.id,
        company_name=company,
        title=title,
        company_key=company.lower(),
        title_key=title.lower(),
        location=location,
        extraction_status=status,
        primary_posting_id=primary.id if primary else None,
    )
    db.add(posting)
    db.commit()
    return posting


def _duplicates(db, kind, page=1, page_size=20):
    return get_duplicates(kind=kind, page=page, page_size=page_size, db=db)


def test_linked_lists_canonical_with_its_duplicates(db):
    primary = _posting(db, domain="careers.acme.com")
    dup = _posting(db, domain="boards.greenhouse.io", primary=primary)
    _posting(db, title="Designer")  # canonical with no duplicates: not a group

    result = _duplicates(db, "linked")

    assert result.total == 1
    [group] = result.items
    assert (group.company_name, group.title) == ("Acme", "Software Engineer")
    assert [(mem.posting_id, mem.is_primary) for mem in group.members] == [(primary.id, True), (dup.id, False)]


def test_possible_lists_unlinked_same_company_and_title(db):
    a = _posting(db, domain="careers.acme.com", location="Austin, TX")
    b = _posting(db, domain="jobs.lever.co", location="Remote")
    _posting(db, domain="old.acme.com", closed=True)  # closed: left out
    _posting(db, domain="broken.acme.com", status=ScanStatus.FAILED)  # not a successful scan
    _posting(db, domain="boards.greenhouse.io", primary=a)  # already linked
    _posting(db, title="Designer")  # only one of its kind

    result = _duplicates(db, "possible")

    assert result.total == 1
    [group] = result.items
    assert {mem.posting_id for mem in group.members} == {a.id, b.id}
    assert {mem.location for mem in group.members} == {"Austin, TX", "Remote"}
    assert all(mem.is_primary for mem in group.members)


def test_possible_pages_biggest_groups_first(db):
    for _ in range(3):
        _posting(db, title="Nurse")
    for _ in range(2):
        _posting(db, title="Cashier")

    first = _duplicates(db, "possible", page=1, page_size=1)
    second = _duplicates(db, "possible", page=2, page_size=1)

    assert first.total == second.total == 2
    assert [g.title for g in first.items] == ["Nurse"]
    assert len(first.items[0].members) == 3
    assert [g.title for g in second.items] == ["Cashier"]
