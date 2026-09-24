import re

from app.models.enums import AtsType
from app.services.adapters.base import TIMEOUT, AtsAdapter, get_with_retry, limit_job_urls

_APPLICANTPRO_URL_RE = re.compile(r"([a-zA-Z0-9-]+)\.applicantpro\.com", re.IGNORECASE)
_LISTINGS_URL = "https://{board_key}.applicantpro.com/jobs/view.php?n=jobListings&f=getListings&keywords="
# The listing endpoint resolves the tenant from the request host alone — a
# domain_id query param appears in the page's own JS-driven calls, but is
# verified live to make no difference to the response, so it's left out.
_JOB_HREF_RE = re.compile(r'href="(https://[a-zA-Z0-9-]+\.applicantpro\.com/jobs/\d+\.html)"', re.IGNORECASE)


def _match(url: str) -> str | None:
    match = _APPLICANTPRO_URL_RE.search(url)
    return match.group(1) if match else None


def _fetch_jobs(board_key: str) -> list[str]:
    response = get_with_retry(_LISTINGS_URL.format(board_key=board_key), timeout=TIMEOUT)
    response.raise_for_status()
    return limit_job_urls(dict.fromkeys(_JOB_HREF_RE.findall(response.text)))


ADAPTER = AtsAdapter(
    AtsType.APPLICANTPRO,
    match=_match,
    fetch_jobs=_fetch_jobs,
    to_board_url=lambda key: f"https://{key}.applicantpro.com/jobs/",
)
