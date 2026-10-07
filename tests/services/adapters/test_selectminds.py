import pytest

from app.services.adapters import selectminds

ENERGY_TRANSFER = """<!DOCTYPE html><html><head>
<title>Engineer - Data Initiatives - Energy Transfer Family of Partnerships Careers</title>
<meta property="og:site_name" content="Energy Transfer Family of Partnerships" />
<meta property="og:title" content="Engineer - Data Initiatives in HOUSTON, Texas, United States" />
</head><body>
<a class="primary_location" href="#"><span class="icon"></span> HOUSTON, Texas, United States </a>
<div class="job_description"><p>Build data pipelines.</p></div>
</body></html>"""

# No og:site_name: the employer comes from the <title> suffix.
ZIONS = """<html><head><title>Senior Analyst - Zions Bancorporation Careers</title></head>
<body><div class="job_description"><p>Analyze.</p></div></body></html>"""


def test_each_tenant_names_its_own_employer_and_keeps_the_full_title():
    fields = selectminds.extract(ENERGY_TRANSFER)

    assert fields.company_name == "Energy Transfer Family of Partnerships"
    assert fields.title == "Engineer - Data Initiatives"
    assert fields.location == "HOUSTON, Texas, United States"


@pytest.mark.parametrize("html,company,title", [
    (ZIONS, "Zions Bancorporation", "Senior Analyst"),
    ("<html><head><title>Engineer</title></head></html>", None, None),
])
def test_company_falls_back_to_the_title_suffix(html, company, title):
    fields = selectminds.extract(html)

    assert fields.company_name == company
    assert fields.title == title
