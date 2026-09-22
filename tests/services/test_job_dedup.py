from difflib import SequenceMatcher

from app.services.job_dedup import _TITLE_SIMILARITY_THRESHOLD, _metro_level_codes, normalize_company_name, normalize_title


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


def test_title_similarity_threshold_separates_distinct_seniorities():
    # The exact scenario find_duplicate_primary must not collapse: two real,
    # different roles at the same company should score below the threshold.
    a = normalize_title("Software Engineer")
    b = normalize_title("Senior Software Engineer")
    similarity = SequenceMatcher(None, a, b).ratio()
    assert similarity < _TITLE_SIMILARITY_THRESHOLD


def test_title_similarity_threshold_matches_the_same_role_reworded():
    a = normalize_title("Senior Product Manager, Growth")
    b = normalize_title("Senior Product Manager - Growth")
    similarity = SequenceMatcher(None, a, b).ratio()
    assert similarity >= _TITLE_SIMILARITY_THRESHOLD


def test_metro_level_codes_excludes_bare_state_codes():
    # Regression: verified live against this product's own data — Amazon
    # posts an identical "Infra Delivery Install Technician" title at dozens
    # of distinct PA facilities. Berwick (metro 14100) and Fairless Hills
    # (metro 37980) are genuinely different openings, but both file under the
    # bare state code "PA" too (see app.services.geo.resolve_area_codes) —
    # before this filter existed, that shared state code alone was enough to
    # wrongly merge them. Only the 5-digit metro/micro code should count.
    assert _metro_level_codes(["14100", "PA"]) == {"14100"}
    assert _metro_level_codes(["37980", "PA"]) == {"37980"}
    assert _metro_level_codes(["14100", "PA"]) & _metro_level_codes(["37980", "PA"]) == set()


def test_metro_level_codes_state_only_or_empty_yields_no_codes():
    # A posting with no specific city (just a state, or nothing extracted at
    # all) has no positive location evidence to match on — find_duplicate_primary
    # treats that as "don't merge" rather than guessing.
    assert _metro_level_codes(["PA"]) == set()
    assert _metro_level_codes([]) == set()
    assert _metro_level_codes(None) == set()


def test_metro_level_codes_matches_when_the_same_metro_recurs():
    assert _metro_level_codes(["14100", "PA"]) & _metro_level_codes(["14100", "PA"]) == {"14100"}
