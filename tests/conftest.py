import httpx


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
