"""Avature's own schema.org JobPosting JSON-LD only covers the page's first
content section and emits an empty jobLocation (verified live on
delta.avature.net) - the real, complete description and location only exist
in the rendered page's own template. These tests pin down that the adapter
reads the template directly rather than trusting Avature's incomplete
structured data.
"""

import pytest

from app.services.adapters import avature

URL = "https://delta.avature.net/en_US/careers/JobDetail/Senior-Partnership-Lead/33879"

_HEADER_ARTICLE = """
<article class="article--details">
  <div class="details--data">
    <p class="paragraph"><i class="fa fa-globe" aria-hidden="true"></i> <strong>United States, Georgia, Atlanta</strong></p>
    <p class="paragraph"><i class="fa fa-archive" aria-hidden="true"></i> <strong>Marketing &amp; Prod Dev</strong></p>
    <p class="paragraph"><i class="fa fa-suitcase" aria-hidden="true"></i> <strong>Ref #: 33879</strong></p>
  </div>
</article>
"""


def _section_article(heading: str, body_html: str) -> str:
    return f"""
<article class="article--details">
  <div class="article__header">
    <div class="article__header__text">
      <h3 class="article__header__text__title article__header__text__title--1 uppercase">{heading}</h3>
    </div>
  </div>
  <div class="article__content article__content--rich-text" itemprop="description">
    {body_html}
  </div>
</article>
"""


def _page(json_ld_description: str = "Intro text.<ul><li>Resp one</li></ul>") -> str:
    overview = _section_article(
        "How you'll help us Keep Climbing (overview &amp; key responsibilities)",
        "<p>Intro text.</p><ul><li>Resp one</li></ul>",
    )
    benefits = _section_article("Benefits and Perks to Help You Keep Climbing", "<p>401k and great pay.</p>")
    quals = _section_article("What you need to succeed (minimum qualifications)", "<ul><li>5+ years experience</li></ul>")
    return f"""
    <html><head><meta name="avature.portal.id" content="123"></head>
    <body>
    <script type="application/ld+json">
    {{
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Senior Partnership Lead",
        "datePosted": "2026-09-14",
        "description": "{json_ld_description}",
        "hiringOrganization": {{"@type": "Organization", "name": "Delta Air Lines, Inc."}},
        "jobLocation": {{"@type": "Place", "address": {{"@type": "PostalAddress", "addressLocality": "", "addressRegion": null, "addressCountry": null}}}}
    }}
    </script>
    <div class="grid__item description-ajax">
    {_HEADER_ARTICLE}
    {overview}
    {benefits}
    {quals}
    </div>
    </body></html>
    """


def test_extract_pulls_location_from_the_header_pair_not_the_empty_json_ld_one():
    fields = avature.extract(_page())
    assert fields.location == "United States, Georgia, Atlanta"


def test_extract_concatenates_every_section_not_just_the_first():
    fields = avature.extract(_page())
    assert "Overview" in fields.description or "Keep Climbing" in fields.description
    assert "Resp one" in fields.description
    assert "Benefits and Perks" in fields.description
    assert "401k and great pay" in fields.description
    assert "minimum qualifications" in fields.description
    assert "5+ years experience" in fields.description


def test_extract_skips_the_headerless_details_article():
    fields = avature.extract(_page())
    # The location/department/ref# header article has no h2-4 heading, so it
    # must not show up as a spurious empty "## " section.
    assert fields.description.count("## ") == 3


def test_scan_job_url_rejects_non_job_detail_urls():
    assert avature.scan_job_url("https://delta.avature.net/careers/SearchJobs/") is None


def test_scan_job_url_returns_none_when_signature_missing(monkeypatch):
    monkeypatch.setattr(avature, "_fetch_page_html", lambda _url: "<html>not avature</html>")
    assert avature.scan_job_url(URL) is None


def test_scan_job_url_reports_failure_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(avature, "_fetch_page_html", lambda _url: None)
    result = avature.scan_job_url(URL)
    assert result.success is False


def test_scan_job_url_uses_json_ld_for_title_but_dom_for_description_and_location(monkeypatch):
    monkeypatch.setattr(avature, "_fetch_page_html", lambda _url: _page())

    result = avature.scan_job_url(URL)

    assert result.success is True
    assert result.title == "Senior Partnership Lead"
    assert result.company_name == "Delta Air Lines, Inc."
    assert result.location == "United States, Georgia, Atlanta"
    assert "Benefits and Perks" in result.description
    assert "401k and great pay" in result.description
    assert str(result.posted_at) == "2026-09-14"
