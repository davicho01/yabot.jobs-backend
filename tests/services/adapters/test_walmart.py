from app.services.adapters import base, walmart


def test_job_not_found_redirect_is_expired(monkeypatch):
    monkeypatch.setattr(
        base,
        "fetch_html",
        lambda url: base.FetchedPage(text="<html></html>", url="https://careers.walmart.com/us/en/job-not-found"),
    )

    result = walmart.scan_job_url("https://careers.walmart.com/us/en/jobs/R-2589069")

    assert (result.success, result.expired) == (False, True)
