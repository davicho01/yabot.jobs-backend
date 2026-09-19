import io

import docx

from app.services.resume_renderer import render_cover_letter_docx, render_tailored_resume_docx


def _paragraphs(docx_bytes: bytes) -> list[str]:
    document = docx.Document(io.BytesIO(docx_bytes))
    return [paragraph.text for paragraph in document.paragraphs]


def _full_contact() -> dict[str, str]:
    return {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "555-1234",
        "location": "Austin, TX",
        "linkedin": "linkedin.com/in/janedoe",
    }


def test_tailored_resume_renders_contact_header():
    docx_bytes = render_tailored_resume_docx(
        "Experienced engineer.", [("Experience", ["Did things."])], _full_contact()
    )
    paragraphs = _paragraphs(docx_bytes)
    assert paragraphs[0] == "Jane Doe"
    assert paragraphs[1] == "jane@example.com | 555-1234 | Austin, TX | linkedin.com/in/janedoe"
    assert "Experienced engineer." in paragraphs


def test_tailored_resume_omits_header_when_contact_blank():
    docx_bytes = render_tailored_resume_docx("Experienced engineer.", [("Experience", ["Did things."])], {})
    paragraphs = _paragraphs(docx_bytes)
    assert paragraphs[0] == "Experienced engineer."


def test_tailored_resume_header_defaults_when_contact_omitted():
    docx_bytes = render_tailored_resume_docx("Experienced engineer.", [("Experience", ["Did things."])])
    paragraphs = _paragraphs(docx_bytes)
    assert paragraphs[0] == "Experienced engineer."


def test_cover_letter_renders_contact_header():
    docx_bytes = render_cover_letter_docx(
        "Dear Hiring Manager,", ["I would be a great fit."], "Sincerely, Jane Doe", _full_contact()
    )
    paragraphs = _paragraphs(docx_bytes)
    assert paragraphs[0] == "Jane Doe"
    assert paragraphs[1] == "jane@example.com | 555-1234 | Austin, TX | linkedin.com/in/janedoe"
    assert "Dear Hiring Manager," in paragraphs
    assert "I would be a great fit." in paragraphs
    assert "Sincerely, Jane Doe" in paragraphs


def test_cover_letter_omits_header_when_contact_blank():
    docx_bytes = render_cover_letter_docx(
        "Dear Hiring Manager,", ["I would be a great fit."], "Sincerely,", {}
    )
    paragraphs = _paragraphs(docx_bytes)
    assert paragraphs[0] == "Dear Hiring Manager,"


def test_contact_header_uses_only_available_fields():
    docx_bytes = render_tailored_resume_docx(
        "Summary.", [], {"name": "Jane Doe", "email": "jane@example.com", "phone": "", "location": "", "linkedin": ""}
    )
    paragraphs = _paragraphs(docx_bytes)
    assert paragraphs[0] == "Jane Doe"
    assert paragraphs[1] == "jane@example.com"
