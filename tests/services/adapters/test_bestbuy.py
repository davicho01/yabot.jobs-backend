from app.services.adapters import base, bestbuy

URL = "https://jobs.bestbuy.com/bby?id=job_details&req_id=1050950BR"
SHELL = (
    '<meta property="og:title" content="Retail Warehouse Associate">'
    '<meta property="og:description" content="As a Retail Warehouse Associate, you\'ll help us.">'
)
DESCRIPTION = "As a Retail Warehouse Associate, you'll keep our sales floor stocked. " * 10
RENDERED = f"""
<div class="panel panel-default bby-career-job-details ng-scope">
  <h2>Retail Warehouse Associate</h2>
  <p>Overview</p><p>Part time</p>
  <div class="m-t job-html iframe-container"><span class="m-b copy-title">Job Description</span><br>
    <div class="m-b m-t">{DESCRIPTION}</div></div>
</div><div class="sp-widget"></div>
"""
EXPIRED = """
<section class="panel panel-default wrapper ng-scope"><div class="cs-alert-message">
The job posting you are looking for has expired or the position has already been filled.
Please visit our Job Search page to explore other opportunities.</div></section>
"""


def _scan(monkeypatch, rendered):
    waits = []

    def fake_render(url, wait_for_selector=None, **kwargs):
        waits.append(wait_for_selector)
        return rendered

    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=SHELL, url=u))
    monkeypatch.setattr(bestbuy, "fetch_rendered_html", fake_render)
    return bestbuy.scan_job_url(URL), waits


def test_render_waits_for_the_description_or_the_expired_notice(monkeypatch):
    result, waits = _scan(monkeypatch, RENDERED)

    assert waits == [".bby-career-job-details .iframe-container, .cs-alert-message"]
    assert result.success and "keep our sales floor stocked" in result.description


def test_expired_notice_is_expired(monkeypatch):
    result, _ = _scan(monkeypatch, EXPIRED)
    assert (result.success, result.expired) == (False, True)
