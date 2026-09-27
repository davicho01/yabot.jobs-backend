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
