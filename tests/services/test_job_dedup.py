from datetime import datetime, timezone

from app.models import JobPosting
from app.services import jobs
from app.services.adapters.base import ScanResult
from app.services.job_dedup import location_key, normalize_company_name, normalize_title, same_place
from app.services.scan_completeness import incomplete_reasons


def test_normalize_company_name_strips_legal_suffixes_and_punctuation():
    # "Acme Inc.", "Acme, LLC" and "Acme" must all match the same job.
    assert normalize_company_name("Acme Inc.") == "acme"
    assert normalize_company_name("Acme, LLC") == "acme"
    assert normalize_company_name("ACME Corp") == "acme"
    assert normalize_company_name("Acme") == "acme"


def test_normalize_company_name_collapses_whitespace():
    assert normalize_company_name("  Acme   Robotics  ") == "acme robotics"


def test_normalize_company_name_none_and_blank():
    assert normalize_company_name(None) is None
    assert normalize_company_name("") is None
    assert normalize_company_name("   ") is None


def test_normalize_company_name_does_not_strip_a_suffix_mid_name():
    # "Company" as part of the actual name, not a trailing legal suffix, e.g.
    # a company literally named "The Trading Company" shouldn't become "the trading".
    assert normalize_company_name("Delta Air Lines") == "delta air lines"


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("Senior Software Engineer (Backend)") == "senior software engineer backend"
    assert normalize_title("  Data Scientist!  ") == "data scientist"


def test_normalize_title_none_and_blank():
    assert normalize_title(None) is None
    assert normalize_title("") is None


def test_reworded_punctuation_normalizes_to_the_same_title_key():
    # Titles now match exactly, so rewordings that only differ in punctuation
    # must still produce one key.
    assert normalize_title("Senior Product Manager, Growth") == normalize_title("Senior Product Manager - Growth")


def test_location_key_ignores_order_case_and_repeats():
    assert location_key(["Newark, DE", "Jersey City, NJ"]) == location_key(["jersey city,  nj", "Newark, DE", "Newark, DE"])
    assert location_key([]) == ()
    assert location_key(None) == ()


AUSTIN = [[30.2672, -97.7431]]
NYC = [[40.7128, -74.006]]


def test_same_site_same_location_is_the_same_place():
    # CVS-style repeats: two requisitions for one store.
    store = ["05044 - South Carolina CVS Pharmacy, L.L.C., United States of America"]
    assert same_place(store, [], "cvs.wd1.myworkdayjobs.com", list(store), [], "cvs.wd1.myworkdayjobs.com")


def test_same_site_different_store_in_one_metro_is_not():
    # Regression from prod (2026-10-12): Target's Bridgehampton and Staten
    # Island stores were merged for sharing the NYC metro.
    assert not same_place(
        ["2102 Montauk Hwy, Bridgehampton,NY"], NYC, "target.wd5.myworkdayjobs.com",
        ["1520 Forest Ave, Staten Island,NY"], NYC, "target.wd5.myworkdayjobs.com",
    )


def test_across_sites_a_shared_city_is_the_same_place():
    assert same_place(["Austin, TX"], AUSTIN, "boards.greenhouse.io", ["Austin, Texas, United States"], AUSTIN, "acme.com")


def test_across_sites_different_cities_are_not():
    assert not same_place(["Austin, TX"], AUSTIN, "boards.greenhouse.io", ["New York, NY"], NYC, "acme.com")


def test_no_location_is_never_the_same_place():
    assert not same_place([], [], "a.com", [], [], "a.com")
    assert not same_place(None, None, "a.com", ["Austin, TX"], AUSTIN, "b.com")


# ------------------------------------------------- through a real scan write

FULL = "We are hiring. " * 30


def _scan(scan_db, url_row, title="Engineer", location="Austin, TX") -> JobPosting:
    result = ScanResult(success=True, title=title, company_name="Acme", location=location, description=FULL)
    result.incomplete_reasons = incomplete_reasons(result)
    jobs._apply_scan_result(scan_db, url_row, result)
    scan_db.commit()
    return scan_db.query(JobPosting).filter_by(url_id=url_row.id).one()


def test_scan_links_a_repeat_at_the_same_location(scan_db, make_url):
    first = _scan(scan_db, make_url(None))
    second = _scan(scan_db, make_url(None))
    assert second.primary_posting_id == first.id


def test_scan_keeps_part_time_and_full_time_apart(scan_db, make_url):
    # Regression from prod (2026-10-12): the 0.85 fuzzy match merged these.
    _scan(scan_db, make_url(None), title="Part Sales Manager – Part Time")
    full_time = _scan(scan_db, make_url(None), title="Part Sales Manager – Full Time")
    assert full_time.primary_posting_id is None


def test_scan_keeps_different_stores_apart(scan_db, make_url):
    _scan(scan_db, make_url(None), location="Bridgehampton, NY")
    other_store = _scan(scan_db, make_url(None), location="Staten Island, NY")
    assert other_store.primary_posting_id is None


def test_scan_never_links_to_a_closed_listing(scan_db, make_url):
    closed_row = make_url(None)
    _scan(scan_db, closed_row)
    closed_row.closed_at = datetime.now(timezone.utc)
    scan_db.commit()

    reposted = _scan(scan_db, make_url(None))
    assert reposted.primary_posting_id is None
