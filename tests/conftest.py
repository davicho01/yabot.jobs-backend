import httpx
import pytest
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.compiler import compiles


@compiles(UUID, "sqlite")
def _uuid_as_text_on_sqlite(_type, _compiler, **_kw):
    # A column declared "UUID" gets NUMERIC affinity in SQLite, so a uuid
    # whose hex reads as a number ("…e901", all digits) is stored as a float
    # and can't be read back — a rare, random test failure. CHAR keeps it text.
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def _no_standard_title_model(monkeypatch):
    """No standard-title model unless a test loads one (tests/services/
    test_standard_titles.py): the same results whether or not a dev has
    fetched the model, and searches stay a plain title match."""
    from app.core.config import settings
    from app.services import standard_titles

    monkeypatch.setattr(settings, "standard_title_model", "/nonexistent/standard_title_model")
    monkeypatch.setattr(standard_titles, "_model", None)
    monkeypatch.setattr(standard_titles, "_load_failed", False)
    standard_titles._search_titles.cache_clear()
    yield
    standard_titles._search_titles.cache_clear()


class FakeResponse:
    """Minimal stand-in for httpx.Response, enough for adapter code that
    only ever calls .raise_for_status(), .json(), .text, and .url on a
    response — real network I/O would make these tests flaky and slow, and
    every adapter fix under test is about how the code reacts to a given
    response body, not about httpx itself.
    """

    def __init__(self, json_data=None, text: str = "", status_code: int = 200, url: str = ""):
        self._json = json_data
        self.text = text
        self.status_code = status_code
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=httpx.Request("GET", "https://example.com"), response=self
            )

    def json(self):
        return self._json
