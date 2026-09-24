import json
from datetime import date

from app.models.enums import EmploymentType
from app.services.adapters import rippling


def _next_data_html(job_post: dict, job_board: dict | None = None) -> str:
    payload = {
        "props": {
            "pageProps": {
                "apiData": {
                    "jobPost": job_post,
                    "jobBoard": job_board or {"companyName": "PDQ"},
                }
            }
        }
    }
    return f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'


def test_extract_combines_company_and_role_description():
    # Rippling splits a posting's body into "company" (generic "About
    # {Company}" boilerplate) and "role" (the actual job-specific content:
    # location, responsibilities, requirements) — verified live against a
    # PDQ posting whose stored description previously ended at "Our Core
    # Values" because only "company" was read, silently dropping "role"
    # entirely.
    job_post = {
        "name": "Systems Engineer",
        "description": {
            "company": "<p>About PDQ. Our values: Honesty.</p>",
            "role": "<p>Location: Remote, US</p><p>Responsibilities: build things.</p>",
        },
        "workLocations": ["Remote (United States)"],
        "employmentType": {"label": "SALARIED_FT"},
        "createdOn": "2026-09-02T00:00:00",
    }
    html = _next_data_html(job_post)

    result = rippling.extract(html)

    assert result.title == "Systems Engineer"
    assert "About PDQ" in result.description
    assert "Responsibilities: build things" in result.description
    assert result.company_name == "PDQ"
    assert result.location == "Remote (United States)"
    assert result.employment_type == EmploymentType.FULL_TIME
    assert result.posted_at == date(2026, 9, 2)


def test_extract_handles_missing_role_or_company():
    job_post = {
        "name": "Engineer",
        "description": {"role": "<p>Just the role.</p>"},
        "workLocations": [],
        "employmentType": {},
        "createdOn": None,
    }
    html = _next_data_html(job_post)

    result = rippling.extract(html)

    assert result.description == "Just the role."


def test_extract_none_when_description_entirely_missing():
    job_post = {"name": "Engineer", "workLocations": [], "employmentType": {}, "createdOn": None}
    html = _next_data_html(job_post)

    result = rippling.extract(html)

    assert result.description is None
