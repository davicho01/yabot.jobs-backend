from app.models.enums import JobSector
from app.services.job_sector import classify_sector


class TestClassifySector:
    def test_title_single_word_match(self):
        assert classify_sector("Senior Software Engineer") == JobSector.ENGINEERING_TECH

    def test_title_multi_word_beats_broader_single_word(self):
        assert classify_sector("Sales Engineer") == JobSector.SALES

    def test_description_multi_word_fallback_still_matches(self):
        assert classify_sector("Team Lead", "You'll own our site reliability practice.") == JobSector.ENGINEERING_TECH

    def test_description_bare_single_word_no_longer_false_positives(self):
        # Regression: a generic "degree in ... Engineering ... or related
        # field" qualifications line used to land this in engineering_tech
        # purely from the bare word "engineering" — verified live against a
        # real Walmart "Manager, Seller Engagement" posting.
        title = "Manager, Operations - Seller Engagement - Performance"
        description = (
            "Bachelor's degree in Business Administration, Engineering, Operations, or related field "
            "and 2 years experience in operations, project management, or related area."
        )
        assert classify_sector(title, description) == JobSector.UNKNOWN

    def test_no_title_or_description_is_unknown(self):
        assert classify_sector(None, None) == JobSector.UNKNOWN

    def test_keyword_embedded_inside_an_unrelated_word_does_not_match(self):
        # Regression: naive substring matching let "dba" (database
        # administrator) match inside "Handbags" — verified live against a
        # real Macy's "Retail Sales Ambassador - Designer Handbags" posting.
        assert classify_sector("Retail Sales Ambassador - Designer Handbags", "") == JobSector.UNKNOWN

    def test_keyword_still_matches_as_a_real_standalone_word(self):
        assert classify_sector("Oracle DBA", "") == JobSector.ENGINEERING_TECH

    def test_manufacturing_engineer_is_traditional_not_tech(self):
        # Regression: with no more-specific multi-word match, this fell
        # through to engineering_tech's bare "engineer" — verified live
        # against a real Hubbell "Manufacturing Engineer" posting.
        assert classify_sector("Manufacturing Engineer - Lincoln, NH", "") == JobSector.ENGINEERING_TRADITIONAL

    def test_quality_engineer_stays_tech_not_a_blanket_traditional_match(self):
        # "Quality Engineer" is genuinely ambiguous (software QA vs.
        # manufacturing/hardware QA) — verified live, a real L3 "Quality
        # Engineer" posting was actually Software Quality Engineering, so
        # this deliberately isn't in engineering_traditional's keyword list
        # and falls through to the bare "engineer" match instead.
        assert classify_sector("Sr Associate, Quality Engineer", "") == JobSector.ENGINEERING_TECH

    def test_information_technology_boilerplate_does_not_land_in_tech(self):
        # Regression: "information technology" used to be a description-
        # fallback keyword, but it also matches when a career site's own
        # department/category tag leaks into an unrelated description —
        # verified live against a real Walgreens "Pharmacy Intern Grad"
        # posting whose description contained a stray "information
        # technology" line with no surrounding sentence (a scraped page
        # artifact, not job content).
        title = "Pharmacy Intern Grad"
        description = (
            "Ensures the use of all elements of the Good Faith Dispensing policy.\n"
            "information technology\n"
            "Ensures the accurate processing of insurance claims to resolve customer issues."
        )
        assert classify_sector(title, description) != JobSector.ENGINEERING_TECH
