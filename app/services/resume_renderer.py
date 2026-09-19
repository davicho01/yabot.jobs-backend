import io

import docx
from docx.shared import Inches, Pt, RGBColor

# Word's own built-in template defaults to blue "Office 2007" heading colors
# and 1"/1.25" margins, neither chosen with a resume in mind — this is our
# own deliberate look, still plain/single-column so ATS parsing is unaffected.
_BODY_FONT = "Calibri"
_INK_COLOR = RGBColor(0x1A, 0x1A, 0x1A)
_HEADING_SIZES_PT = {1: 18, 2: 12, 3: 11}


def _style_document(document: docx.Document) -> None:
    normal = document.styles["Normal"]
    normal.font.name = _BODY_FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = _INK_COLOR
    normal.paragraph_format.space_after = Pt(6)

    for level, size_pt in _HEADING_SIZES_PT.items():
        style = document.styles[f"Heading {level}"]
        style.font.name = _BODY_FONT
        style.font.size = Pt(size_pt)
        style.font.bold = True
        style.font.italic = False
        style.font.color.rgb = _INK_COLOR
        style.paragraph_format.space_before = Pt(10 if level > 1 else 0)
        style.paragraph_format.space_after = Pt(4)

    for style_name in ("List Bullet", "List Number"):
        style = document.styles[style_name]
        style.font.name = _BODY_FONT
        style.font.size = Pt(10.5)
        style.font.color.rgb = _INK_COLOR
        style.paragraph_format.space_after = Pt(2)

    section = document.sections[0]
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)
    section.top_margin = Inches(0.6)
    section.bottom_margin = Inches(0.6)


def _add_contact_header(document: docx.Document, contact: dict[str, str]) -> None:
    """Write the candidate's name (as the document's one Heading 1) and a
    single line of whatever contact details are available, in a fixed
    order. Silently omits whatever is blank rather than leaving gaps.
    """
    name = contact.get("name", "")
    if name:
        document.add_heading(name, level=1)

    line = " | ".join(
        contact[key] for key in ("email", "phone", "location", "linkedin") if contact.get(key)
    )
    if line:
        document.add_paragraph(line)


def render_tailored_resume_docx(
    summary: str, sections: list[tuple[str, list[str]]], contact: dict[str, str] | None = None
) -> bytes:
    """Render a tailored resume's structured content (see
    app.schemas.resume.TailoredResumeUpload — the same shape produced by
    this app's own LLM generation and accepted from an uploaded one, e.g.
    from an MCP client's own LLM) into a plain, single-column, ATS-safe
    .docx: a contact header, the summary as an intro paragraph, then each
    section as a heading plus a flat bullet list.
    """
    document = docx.Document()
    _style_document(document)

    _add_contact_header(document, contact or {})

    if summary:
        document.add_paragraph(summary)

    for heading, bullets in sections:
        document.add_heading(heading, level=2)
        for bullet in bullets:
            document.add_paragraph(bullet, style="List Bullet")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def render_cover_letter_docx(
    greeting: str, body_paragraphs: list[str], closing: str, contact: dict[str, str] | None = None
) -> bytes:
    """Render a cover letter's structured content (see
    app.schemas.resume.CoverLetterUpload) into a plain .docx: a contact
    header, greeting paragraph, each body paragraph, then the closing
    paragraph.
    """
    document = docx.Document()
    _style_document(document)

    _add_contact_header(document, contact or {})

    if greeting:
        document.add_paragraph(greeting)
    for paragraph in body_paragraphs:
        document.add_paragraph(paragraph)
    if closing:
        document.add_paragraph(closing)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
