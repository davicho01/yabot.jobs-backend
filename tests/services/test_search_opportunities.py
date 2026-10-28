"""Tests for app.services.search_opportunities: reading what a Google search
is after, grouping Search Console's (query, page) rows by it, and discovery
taking the role pages people searched for first."""

from sqlalchemy import select

import app.models as m
from app.api.routes import admin_collections as routes
from app.models.enums import CollectionStatus as S
from app.models.enums import WorkplaceType
from app.services import collection_discovery as cdisc
from app.services import search_opportunities as so
from tests.services.test_static_collection_pages import NOW, collection, db, job  # noqa: F401  (fixtures)

COMPANIES = {"zendesk": ("zendesk", "Zendesk"), "uniswap labs": ("uniswap", "Uniswap Labs")}


def _parse(query):
    i = so.parse(query, COMPANIES)
    return i.kind, i.key, i.label


def test_parse_roles():
    assert _parse("software engineer jobs") == ("role", "role=software-engineer", "Software Engineer jobs")
    assert _parse("Senior Software Engineers") == ("role", "role=senior-software-engineer", "Senior Software Engineer jobs")
    assert _parse("remote data engineer jobs")[:2] == ("role", "role=data-engineer;location=remote")
    assert _parse("data engineer work from home")[1] == "role=data-engineer;location=remote"
    assert _parse("software engineer jobs in houston") == (
        "role", "role=software-engineer;location=houston-tx", "Software Engineer jobs in Houston, TX"
    )
    assert _parse("software engineer google")[1] == "role=software-engineer"  # not a place's own name
    assert _parse("software engineer new york")[1] == "role=software-engineer;location=new-york-ny"
    assert _parse("software engineer texas")[1] == "role=software-engineer;location=texas"
    assert _parse("software engineer bay area")[1] == "role=software-engineer;location=san-francisco-ca"


def test_parse_places_companies_and_the_rest():
    assert _parse("legal jobs houston")[:2] == ("other", "query:legal jobs houston")  # "legal" isn't a tech title
    assert _parse("jobs in houston") == ("location", "location:houston-tx", "Jobs in Houston, TX")
    assert _parse("empleos caguas")[1] == "location:san-juan-pr"
    assert _parse("remote jobs") == ("location", "location:remote", "Remote jobs")
    assert _parse("Zendesk Careers") == ("company", "company:zendesk", "Zendesk jobs")
    assert _parse("uniswap labs jobs")[1] == "company:uniswap"
    assert _parse("remote rn jobs")[:2] == ("other", "query:remote rn jobs")
    assert _parse("nike careers")[0] == "other"  # no open jobs at Nike


def _row(query, url, clicks, impressions, position):
    return {"keys": [query, url], "clicks": clicks, "impressions": impressions, "position": position}


def test_build_groups_searches_and_counts_open_jobs():
    live = [job(company_name="Acme", company_key="acme") for _ in range(2)] + [job() for _ in range(3)]
    live += [job(workplace_type=WorkplaceType.REMOTE) for _ in range(3)]
    live += [job(closed="2026-10-20T00:00:00+00:00")]
    rows = [
        _row("software engineer jobs", "https://yabot.jobs/jobs/us/software-engineer", 2, 100, 5.0),
        _row("software engineers", "https://yabot.jobs/jobs/us/software-engineer/page/2", 1, 50, 8.0),
        _row("software engineer jobs", "https://yabot.jobs/jobs/us/engineering-tech", 0, 10, 20.0),
        _row("remote software engineer", "https://yabot.jobs/jobs/us/software-engineer", 0, 30, 9.0),
        _row("software engineer salt lake city", "https://yabot.jobs/jobs/us/x", 0, 5, 3.0),
        _row("acme careers", "https://yabot.jobs/companies/acme", 0, 7, 4.0),
        _row("ai44506", "https://yabot.jobs/jobs/us/y", 0, 200, 6.0),
        _row("ignored", "https://example.com/z", 9, 999, 1.0),
    ]
    out = {o.key: o for o in so.build(rows, live, NOW)}
    assert list(out) == [
        "query:ai44506", "role=software-engineer", "role=software-engineer;location=remote", "company:acme",
        "role=software-engineer;location=salt-lake-city-ut",
    ]
    se = out["role=software-engineer"]
    assert (se.kind, se.clicks, se.impressions, se.open_jobs) == ("role", 3, 160, 8)
    assert se.position == round((500 + 400 + 200) / 160, 2)
    assert se.collection_key == "role=software-engineer"
    assert se.queries == [
        {"query": "software engineer jobs", "clicks": 2, "impressions": 110},
        {"query": "software engineers", "clicks": 1, "impressions": 50},
    ]
    assert se.pages[0] == {"page": "/jobs/us/software-engineer", "impressions": 100}
    assert out["role=software-engineer;location=remote"].open_jobs == 3
    assert out["role=software-engineer;location=salt-lake-city-ut"].open_jobs == 8
    assert (out["company:acme"].label, out["company:acme"].open_jobs) == ("Acme jobs", 2)
    assert out["query:ai44506"].open_jobs is None and out["query:ai44506"].collection_key is None
    assert so.demand(list(out.values())) == {
        "role=software-engineer": 160, "role=software-engineer;location=remote": 30,
        "role=software-engineer;location=salt-lake-city-ut": 5,
    }


def test_store_replaces_everything(db):
    rows = [_row("software engineer jobs", "https://yabot.jobs/jobs/us/software-engineer", 1, 10, 5.0)]
    so.store(db, so.build([_row("jobs in houston", "https://yabot.jobs/", 0, 3, 9.0)], [], NOW))
    so.store(db, so.build(rows, [job()], NOW))
    assert [o.key for o in db.scalars(select(m.SearchOpportunity))] == ["role=software-engineer"]


def test_discovery_takes_searched_for_pages_first(db):
    live = [job(standard_titles=("Data Engineer",)) for _ in range(60)]
    live += [job(standard_titles=("QA Engineer",), company_name="Solo", company_key="solo") for _ in range(25)]
    by_score = [p.key for p in cdisc.discover(db, live, NOW, apply=False)]
    searched = "role=qa-engineer"
    assert searched in by_score and by_score[0] != searched
    found = cdisc.discover(db, live, NOW, apply=True, limit=1, demand={searched: 42})
    assert [p.key for p in found] == [searched]
    [c] = db.scalars(select(m.JobCollection)).all()
    assert c.events[0].reason == "found by discovery; shown 42 times in Google searches"


def test_admin_lists_searches_with_their_collection(db):
    c = collection(db, S.QUALIFIED, role="software-engineer")
    rows = [
        _row("software engineer jobs", "https://yabot.jobs/jobs/us/software-engineer", 1, 10, 5.0),
        _row("data engineer jobs", "https://yabot.jobs/jobs/us/x", 0, 20, 7.0),
        _row("jobs in houston", "https://yabot.jobs/", 0, 3, 9.0),
    ]
    so.store(db, so.build(rows, [job()], NOW))
    result = routes.list_searches(kind=None, page=1, page_size=50, db=db)
    assert [i.key for i in result.items] == ["role=data-engineer", "role=software-engineer", "location:houston-tx"]
    assert result.counts == {"role": 2, "location": 1} and result.total == 3
    assert str(result.updated_on) == "2026-10-26"
    se = result.items[1]
    assert (se.collection_id, se.collection_status) == (c.id, S.QUALIFIED)
    assert result.items[0].collection_id is None  # no such collection yet
    only = routes.list_searches(kind="location", page=1, page_size=50, db=db)
    assert [i.key for i in only.items] == ["location:houston-tx"] and only.total == 1
