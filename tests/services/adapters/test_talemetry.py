import pytest

from app.services.adapters import talemetry

HOST = "careers.example.com"


def _links(start, count):
    return "".join(f'<a href="https://{HOST}/jobs/{n}-role/">' for n in range(start, start + count))


def _serve(monkeypatch, pages):
    monkeypatch.setattr(talemetry, "_fetch_page_html", lambda url: pages.get(int(url.split("page=")[1].split("&")[0])))


def test_an_unreadable_first_page_is_an_error_not_an_empty_board(monkeypatch):
    # A WAF that blocks both the direct fetch and the browser render used to
    # read as a board with no jobs.
    _serve(monkeypatch, {})

    with pytest.raises(ValueError):
        talemetry._fetch_jobs(HOST)


def test_a_readable_first_page_with_no_jobs_is_empty(monkeypatch):
    _serve(monkeypatch, {1: "<html>No results</html>"})

    assert talemetry._fetch_jobs(HOST) == []


def test_a_later_page_failing_keeps_the_jobs_already_found(monkeypatch):
    _serve(monkeypatch, {1: _links(0, 25)})

    assert len(talemetry._fetch_jobs(HOST)) == 25
