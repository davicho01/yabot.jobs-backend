"""Tests for app.services.seo_validator — the deterministic checks on
collection pages (spec §56) — and how the generator acts on them
(static_collection_pages.validate_collection_pages)."""

from app.models.enums import CollectionStatus as S
from app.models.enums import WorkplaceType
from app.services import seo_validator as sv
from app.services import static_collection_pages as scp
from tests.services.test_static_collection_pages import NOW, collection, db, enabled, job  # noqa: F401  (fixtures)

URL = "https://yabot.jobs/jobs/us/software-engineer"
PATH = "jobs/us/software-engineer"


def _codes(issues):
    return {i.code for i in issues}


def _page(db, n_jobs=60, status=S.INDEXABLE, role="software-engineer"):
    collection(db, status, role=role)
    return scp.plan_collection_pages(db, [job() for _ in range(n_jobs)], NOW)


def test_rendered_pages_pass(db, enabled):
    [page] = _page(db)
    for n, path in enumerate(page.paths(), start=1):
        html = scp.render_collection_page(page, n)
        assert sv.check_page(path, html, url=f"https://yabot.jobs/{path}", indexable=True) == []


def test_noindex_pages_pass_with_noindex(db, enabled):
    [page] = _page(db, status=S.NOINDEX)
    assert sv.check_page(PATH, scp.render_collection_page(page, 1), url=URL, indexable=False) == []


def test_each_broken_part_is_an_error(db, enabled):
    [page] = _page(db)
    html = scp.render_collection_page(page, 1)
    broken = {
        "title": html.replace("<title>", "<x>").replace("</title>", "</x>"),
        "description": html.replace('name="description"', 'name="x"'),
        "h1": html.replace("<h1>", "<h1>a</h1><h1>"),
        "canonical": html.replace(f'rel="canonical" href="{URL}"', f'rel="canonical" href="{URL}/page/2"'),
        "robots": html.replace("index, follow", "noindex, follow"),
        "no_jobs": html.replace("job-rows__title", "x"),
        "structured_data": html.replace('<script type="application/ld+json">{', '<script type="application/ld+json">{{'),
        "job_posting_markup": html.replace('"@type": "BreadcrumbList"', '"@type": "JobPosting"'),
    }
    for code, bad in broken.items():
        assert bad != html, code
        issues = sv.check_page(PATH, bad, url=URL, indexable=True)
        assert code in _codes(issues), code
        assert all(i.severity is sv.Severity.ERROR for i in issues if i.code == code)


def test_long_titles_and_odd_descriptions_are_warnings(db, enabled):
    [page] = _page(db)
    html = scp.render_collection_page(page, 1).replace(page.description, "Too short.").replace(
        f"<title>{page.title}", f"<title>{page.title}{'x' * 80}"
    )
    issues = sv.check_page(PATH, html, url=URL, indexable=True)
    assert _codes(issues) == {"title_length", "description_length"}
    assert all(i.severity is sv.Severity.WARNING for i in issues)


def test_duplicate_intent_between_collections(db, enabled):
    collection(db, S.INDEXABLE, role="software-engineer")
    collection(db, S.INDEXABLE, role="backend-engineer")
    live = [job(standard_titles=("Software Engineer", "Backend Engineer")) for _ in range(30)]
    pages = scp.plan_collection_pages(db, live, NOW)
    issues = sv.check_collections(pages)
    assert [(i.code, i.path) for i in issues if i.code == "duplicate_intent"] == [
        ("duplicate_intent", "jobs/us/software-engineer")
    ]


def test_a_remote_page_inside_its_role_page_is_not_a_duplicate(db, enabled):
    collection(db, S.INDEXABLE, role="software-engineer")
    collection(db, S.INDEXABLE, role="software-engineer", location="remote")
    live = [job() for _ in range(40)] + [job(workplace_type=WorkplaceType.REMOTE) for _ in range(20)]
    pages = scp.plan_collection_pages(db, live, NOW)
    assert len(pages) == 2
    assert "duplicate_intent" not in _codes(sv.check_collections(pages))


def test_thin_indexed_collections_are_flagged(db, enabled):
    pages = _page(db, n_jobs=5)
    assert _codes(sv.check_collections(pages)) == {"thin"}


def test_a_failing_collection_is_kept_out_of_the_index(db, enabled, monkeypatch):
    [page] = _page(db)
    render = scp.render_collection_page
    monkeypatch.setattr(scp, "render_collection_page", lambda p, n: render(p, n).replace("<h1>", "<p>"))
    issues = scp.validate_collection_pages([page])
    assert "h1" in _codes(issues)
    assert page.indexable is False
    assert page.collection.status == S.INDEXABLE  # status untouched: back once fixed
    assert scp.CollectionLinks([page]).top_roles() == []


def test_clean_pages_stay_indexed(db, enabled):
    [page] = _page(db)
    assert scp.validate_collection_pages([page]) == []
    assert page.indexable is True


def test_report_counts_and_orders_errors_first():
    issues = [sv.Issue("b", "thin", sv.Severity.WARNING), sv.Issue("c", "h1", sv.Severity.ERROR)]
    out = sv.report(issues, 3, "2026-10-27T00:00:00+00:00")
    assert (out["pages"], out["errors"], out["warnings"]) == (3, 1, 1)
    assert [i["code"] for i in out["issues"]] == ["h1", "thin"]
    assert out["issues"][0]["severity"] == "error"
