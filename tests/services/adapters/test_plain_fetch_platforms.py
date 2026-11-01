from datetime import date

import pytest

from app.services.adapters import base, careerplug, freshteam, hireology, jobscore, jobsoid, kula, pageup
from tests.conftest import FakeResponse


def _serve(monkeypatch, module, responder):
    """responder(url, params) -> FakeResponse; records every request."""
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append((url, params))
        return responder(url, params)

    monkeypatch.setattr(module, "get_with_retry", fake_get)
    return calls


# --- matching: tenant boards match, the vendors' own sites don't --------------


@pytest.mark.parametrize(
    "module,url,key",
    [
        (hireology, "https://careers.hireology.com/s3hotelgroup/2911346/description?ref=google", "s3hotelgroup"),
        (jobscore, "https://careers.jobscore.com/careers/ierus/jobs/purchaser-abc123", "ierus"),
        (kula, "https://careers.kula.ai/invoca/54960-ai-operations-lead", "invoca"),
        (pageup, "https://careers.pageuppeople.com/858/studentjobs/en-us/job/503241/tutor", "858/studentjobs"),
        (careerplug, "https://Boost.careerplug.com/jobs/3298622/apps/new", "boost.careerplug.com"),
        (freshteam, "https://Spark.freshteam.com/jobs/Q_o-KW4iNtzX/nurse", "spark.freshteam.com"),
        (jobsoid, "https://vibrus.jobsoid.com/j/44202/travel-nurse-rn", "vibrus.jobsoid.com"),
    ],
)
def test_a_tenant_url_gives_its_board_key(module, url, key):
    assert module.ADAPTER.match(url) == key


@pytest.mark.parametrize(
    "module,url",
    [
        (hireology, "https://careers.hireology.com/sitemap_index.xml"),
        (hireology, "https://hireology.com/careers"),
        (kula, "https://careers.kula.ai/_next/static/chunks/main.js"),
        (kula, "https://careers-cdn.kula.ai/_next/static/css/a.css"),
        (careerplug, "https://app.careerplug.com/login"),
        (careerplug, "https://www.careerplug.com/pricing"),
        (freshteam, "https://www.freshteam.com/pricing"),
        (jobsoid, "https://www.jobsoid.com/"),
        (pageup, "https://careers.pageuppeople.com/robots.txt"),
    ],
)
def test_the_vendors_own_pages_are_not_boards(module, url):
    assert module.ADAPTER.match(url) is None


# --- Hireology ------------------------------------------------------------------


def _hireology_job(job_id):
    return {"id": job_id, "career_site_url": f"https://careers.hireology.com/acme/{job_id}/description"}


def test_hireology_pages_through_the_api_until_the_count_is_reached(monkeypatch):
    pages = {1: [_hireology_job(1), _hireology_job(2)], 2: [_hireology_job(3)]}
    calls = _serve(
        monkeypatch,
        hireology,
        lambda url, params: FakeResponse(json_data={"data": pages.get(params["page"], []), "count": 3}),
    )

    urls = hireology._fetch_jobs("acme")

    assert urls == [f"https://careers.hireology.com/acme/{i}/description" for i in (1, 2, 3)]
    assert [params["page"] for _, params in calls] == [1, 2]


def test_hireology_with_no_jobs_is_empty_and_a_bad_answer_is_an_error(monkeypatch):
    _serve(monkeypatch, hireology, lambda url, params: FakeResponse(json_data={"data": [], "count": 0}))
    assert hireology._fetch_jobs("acme") == []

    _serve(monkeypatch, hireology, lambda url, params: FakeResponse(json_data={"message": "no"}))
    with pytest.raises(ValueError):
        hireology._fetch_jobs("acme")

    _serve(monkeypatch, hireology, lambda url, params: FakeResponse(status_code=404))
    with pytest.raises(Exception):
        hireology._fetch_jobs("nope")


def test_hireology_scan_ignores_the_json_ld_valid_through_date(monkeypatch):
    # The page's validThrough is just created + 30 days; the API still lists the job.
    page = (
        '<script type="application/ld+json">{"@type": "JobPosting", "title": "Line Cook", '
        '"description": "<p>Cook on the line in a busy hotel kitchen, grill and saute stations.</p>", '
        '"validThrough": "2025-10-23T19:54", "hiringOrganization": {"name": "S3 Hotel Group"}}</script>'
    )
    monkeypatch.setattr(hireology, "fetch_html", lambda url: base.FetchedPage(text=page, url=url))

    result = hireology.scan_job_url("https://careers.hireology.com/acme/2530305/description")

    assert result.success and not result.expired
    assert (result.title, result.company_name) == ("Line Cook", "S3 Hotel Group")


def test_hireology_scan_leaves_other_urls_and_pages_without_a_posting_alone(monkeypatch):
    assert hireology.scan_job_url("https://careers.hireology.com/acme") is None
    monkeypatch.setattr(hireology, "fetch_html", lambda url: base.FetchedPage(text="<html></html>", url=url))
    assert hireology.scan_job_url("https://careers.hireology.com/acme/1/description") is None


# --- JobScore -------------------------------------------------------------------


def test_jobscore_lists_the_feed_without_tracking_params_and_reads_opened_dates(monkeypatch):
    feed = {
        "jobs": [
            {
                "detail_url": "https://careers.jobscore.com/careers/acme/jobs/purchaser-abc?ref=rss&sid=68",
                "opened_date": "2026-09-15T21:03:31.847Z",
            },
            {"detail_url": ""},
        ]
    }
    calls = _serve(monkeypatch, jobscore, lambda url, params: FakeResponse(json_data=feed))

    listing = jobscore._fetch_jobs("acme")

    assert calls[0][0] == "https://careers.jobscore.com/careers/acme/feed.json"
    assert listing.urls == ["https://careers.jobscore.com/careers/acme/jobs/purchaser-abc"]
    assert listing.posted[listing.urls[0]] == date(2026, 9, 15)


def test_jobscore_without_a_feed_is_an_error(monkeypatch):
    _serve(monkeypatch, jobscore, lambda url, params: FakeResponse(json_data={"error": "x"}))
    with pytest.raises(ValueError):
        jobscore._fetch_jobs("acme")


# --- Jobsoid --------------------------------------------------------------------


def test_jobsoid_lists_hosted_urls_with_posted_dates(monkeypatch):
    jobs = [{"hostedUrl": "https://acme.jobsoid.com/j/1/nurse", "postedDate": "2026-10-07T01:00:11.52"}, {"id": 2}]
    _serve(monkeypatch, jobsoid, lambda url, params: FakeResponse(json_data=jobs))

    listing = jobsoid._fetch_jobs("acme.jobsoid.com")

    assert listing.urls == ["https://acme.jobsoid.com/j/1/nurse"]
    assert listing.posted[listing.urls[0]] == date(2026, 10, 7)


def test_jobsoid_empty_list_is_empty_and_anything_else_is_an_error(monkeypatch):
    _serve(monkeypatch, jobsoid, lambda url, params: FakeResponse(json_data=[]))
    assert jobsoid._fetch_jobs("acme.jobsoid.com").urls == []

    _serve(monkeypatch, jobsoid, lambda url, params: FakeResponse(json_data={"message": "nope"}))
    with pytest.raises(ValueError):
        jobsoid._fetch_jobs("acme.jobsoid.com")


# --- HTML boards: empty only when the page is recognizably the listing ---------

KULA_PAGE = (
    '<html><a href="/acme/101-engineer">x</a><a href="/acme/101-engineer">dup</a>'
    '<a href="/acme/102-join-our-engineering-talent-community">y</a><a href="/other/9-nope">z</a></html>'
)
FRESHTEAM_PAGE = '<a href="/jobs/Q_o-KW4iNtzX/nurse-remote">a</a><a href="/jobs/Q_o-KW4iNtzX/nurse-remote">b</a><a href="/jobs">all</a>'
PAGEUP_PAGE = (
    '<a href="/858/site/en-us/job/1/tutor">a</a><a href="/858/site/en-us/job/2/aide">b</a>'
    '<a href="/858/site/en-us/listing/">list</a>'
)


def test_kula_lists_its_own_jobs_once_and_skips_the_talent_community(monkeypatch):
    _serve(monkeypatch, kula, lambda url, params: FakeResponse(text=KULA_PAGE))

    assert kula._fetch_jobs("acme") == ["https://careers.kula.ai/acme/101-engineer"]


def test_freshteam_lists_each_job_once(monkeypatch):
    _serve(monkeypatch, freshteam, lambda url, params: FakeResponse(text=FRESHTEAM_PAGE))

    assert freshteam._fetch_jobs("acme.freshteam.com") == ["https://acme.freshteam.com/jobs/Q_o-KW4iNtzX/nurse-remote"]


def test_pageup_lists_the_boards_job_links(monkeypatch):
    _serve(monkeypatch, pageup, lambda url, params: FakeResponse(text=PAGEUP_PAGE))

    assert pageup._fetch_jobs("858/site") == [
        "https://careers.pageuppeople.com/858/site/en-us/job/1/tutor",
        "https://careers.pageuppeople.com/858/site/en-us/job/2/aide",
    ]


def test_careerplug_pages_until_a_page_adds_nothing(monkeypatch):
    pages = {1: '<a href="/jobs/11">a</a><a href="/jobs/12">b</a>', 2: '<a href="/jobs/13">c</a><a href="/jobs/12">b</a>'}
    calls = _serve(
        monkeypatch, careerplug, lambda url, params: FakeResponse(text=pages.get(params["page"], "<html>careerplug</html>"))
    )

    assert careerplug._fetch_jobs("acme.careerplug.com") == [f"https://acme.careerplug.com/jobs/{i}" for i in (11, 12, 13)]
    assert [params["page"] for _, params in calls] == [1, 2, 3]


@pytest.mark.parametrize(
    "module,key,marker",
    [
        (kula, "acme", "kula"),
        (freshteam, "acme.freshteam.com", "freshteam"),
        (pageup, "858/site", "pageup"),
        (careerplug, "acme.careerplug.com", "careerplug"),
    ],
)
def test_a_listing_with_no_jobs_is_empty_but_a_page_that_is_not_the_listing_is_an_error(monkeypatch, module, key, marker):
    _serve(monkeypatch, module, lambda url, params: FakeResponse(text=f"<html><body class='{marker}'>No openings</body></html>"))
    assert module._fetch_jobs(key) == []

    _serve(monkeypatch, module, lambda url, params: FakeResponse(text="<html>Just a moment... Checking your browser</html>"))
    with pytest.raises(ValueError):
        module._fetch_jobs(key)


# --- PageUp scan ----------------------------------------------------------------

PAGEUP_JOB = """
<meta property="og:title" content="Academic Success Tutor, Ongoing" />
<meta property="og:site_name" content="University of Delaware" />
<p><b>Location:</b> <span class="location">Newark, DE</span><br></p>
<div id="job-details"><p><strong>Estimated Pay:</strong> $15.00 per hour</p>
<p>Student tutors help their peers understand class material and build good study habits.</p></div>
"""


def test_pageup_scan_reads_title_employer_location_and_the_posting(monkeypatch):
    monkeypatch.setattr(base, "fetch_html", lambda url: base.FetchedPage(text=PAGEUP_JOB, url=url))

    result = pageup.scan_job_url("https://careers.pageuppeople.com/858/studentjobs/en-us/job/503241/tutor")

    assert result.success
    assert (result.title, result.company_name, result.location) == (
        "Academic Success Tutor, Ongoing",
        "University of Delaware",
        "Newark, DE",
    )
    assert "Student tutors help their peers" in result.description


def test_pageup_scan_ignores_other_urls():
    assert pageup.scan_job_url("https://careers.pageuppeople.com/858/studentjobs/en-us/listing/") is None
