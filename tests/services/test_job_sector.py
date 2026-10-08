import pytest

from app.models.enums import JobSector
from app.services import job_sector
from app.services.job_sector import classify_sector


class TestClassifySector:
    @pytest.mark.parametrize(
        ("title", "description", "expected"),
        [
            ("Registered Nurse - ICU", "Provide direct patient care in the intensive care unit.", JobSector.HEALTHCARE),
            ("Senior Software Engineer", "Design and build backend services in Go and Python.", JobSector.ENGINEERING_TECH),
            ("Line Cook", "Prepare food to recipe standards on the line.", JobSector.SERVICE_TRADES),
            ("Staff Accountant", "Prepare journal entries and reconcile general ledger accounts.", JobSector.FINANCE_ACCOUNTING),
            ("Recruiter", "Source, screen and interview candidates for open roles.", JobSector.HR),
            # Sector rules from the labeling definitions:
            ("Bank Teller", "Process deposits and withdrawals for branch customers.", JobSector.CUSTOMER_SUPPORT),
            ("Universal Banker", "Open accounts and recommend banking products to customers.", JobSector.SALES),
            ("Maintenance Technician - Plant", "Repair production line equipment in our manufacturing plant.", JobSector.OPERATIONS_MANUFACTURING),
        ],
    )
    def test_clear_cut_postings(self, title, description, expected):
        assert classify_sector(title, description) == expected

    def test_no_title_or_description_is_unknown(self):
        assert classify_sector(None, None) == JobSector.UNKNOWN
        assert classify_sector("", "") == JobSector.UNKNOWN

    def test_missing_description_still_classifies_from_title(self):
        assert classify_sector("Cashier", None) == JobSector.SERVICE_TRADES

    def test_html_in_description_is_ignored(self):
        assert classify_sector("Registered Nurse", "<p>Provide <b>patient care</b> on the unit.</p>") == JobSector.HEALTHCARE

    def test_every_model_sector_is_a_valid_job_sector(self):
        for name in job_sector._model().classes:
            JobSector(name)

    def test_missing_model_file_gives_unknown_instead_of_failing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(job_sector, "MODEL_PATH", tmp_path / "missing.npz")
        job_sector._model.cache_clear()
        try:
            assert classify_sector("Registered Nurse", "Patient care.") == JobSector.UNKNOWN
        finally:
            job_sector._model.cache_clear()
