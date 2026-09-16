from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

TIMEOUT = 30.0


@dataclass(frozen=True)
class AtsAdapter:
    """Everything needed to support one ATS platform, in one place.

    board_key is an internal identifier private to a given adapter — for
    most platforms it's just the board slug/subdomain, but some need more
    than one value (e.g. Workday's "company/instance/site", ADP's
    "cid/ccId") and just join them with "/"; nothing outside this module
    ever needs to know or care about that shape, since match/board_key/
    fetch_jobs/to_board_url are always the ones both producing and
    consuming it.

    match: pure string URL matching, no I/O — None for platforms with no
        static URL shape (Eightfold, Clinch), which only match via
        embedded_match.
    board_key: re-derives board_key from a *stored* board_url for
        listing, when that's cheaper/different than match (Eightfold,
        Clinch only — a trivial host extraction, since their real
        board_key needs guess-and-verify network calls that fetch_jobs
        already does anyway). Defaults to match when unset.
    to_board_url: board_key -> the canonical URL to persist as
        CrawlSource.board_url. None means "verbatim": store whatever URL
        was actually submitted, unchanged (see board_url_for_key in
        app.services.ats_adapters).
    embedded_match: url -> board_key, a slower I/O-based fallback tier for
        platforms white-labeled onto a company's own domain, where the
        board_key isn't visible in the URL string alone.
    """

    ats_type: str
    fetch_jobs: Callable[[str], list[str]]
    match: Callable[[str], str | None] | None = None
    board_key: Callable[[str], str | None] | None = None
    to_board_url: Callable[[str], str] | None = None
    embedded_match: Callable[[str], str | None] | None = None


# Shared by every embedded-widget detector that has to guess a board slug
# from a company's own domain (Greenhouse, Ashby) — e.g. "www.anrok.com" ->
# ["anrok"]. Also strips a trailing corporate suffix as a second guess
# (verified against a real board: "coalitioninc.com" -> "coalition") —
# wrong guesses just fail the caller's verification step harmlessly, same
# as any other candidate here.
_COMPANY_SUFFIXES = ("incorporated", "corp", "inc", "llc", "ltd", "group", "co")


def candidate_slugs_from_domain(domain: str) -> list[str]:
    label = domain.lower().removeprefix("www.").split(".")[0]
    candidates = [label]
    for suffix in _COMPANY_SUFFIXES:
        if label.endswith(suffix) and len(label) > len(suffix):
            candidates.append(label[: -len(suffix)])
    return list(dict.fromkeys(candidates))  # dedupe, keep order


def parse_month_day_year(raw: str | None, *, month_style: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(" ".join(raw.split()), month_style).date()
    except ValueError:
        return None
