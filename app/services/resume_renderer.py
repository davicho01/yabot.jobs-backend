import io
import re
from xml.sax.saxutils import escape as _xml_escape

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

# Word's own built-in template defaults to blue "Office 2007" heading colors
# and 1"/1.25" margins, neither chosen with a resume in mind — this is our
# own deliberate look, still plain/single-column so ATS parsing is unaffected.
_BODY_FONT = "Calibri"
_INK_COLOR = RGBColor(0x1A, 0x1A, 0x1A)
_MUTED_COLOR = RGBColor(0x59, 0x59, 0x59)
_ACCENT_COLOR = RGBColor(0x1F, 0x3A, 0x5F)
_RULE_COLOR_HEX = "1F3A5F"
_HEADING_SIZES_PT = {1: 20, 2: 12, 3: 11}

# Matches a short leading label before a bullet's real content, e.g.
# "Backend Development: Built REST APIs..." or "Languages: Python, Go" — the
# label gets bolded on render. Capped at 58 chars so an ordinary sentence
# that happens to contain a colon further in isn't mistaken for one.
_BULLET_LABEL_RE = re.compile(r"^([A-Za-z0-9][^:\n]{0,58}):\s+(.+)$", re.DOTALL)


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
        # Section headings (level 2 — "Experience"/"Skills"/etc.) get the
        # accent color and more breathing room so they read as distinct
        # blocks; the name (level 1) stays ink-colored and tight above it.
        style.font.color.rgb = _ACCENT_COLOR if level == 2 else _INK_COLOR
        style.paragraph_format.space_before = Pt(16 if level == 2 else 0)
        style.paragraph_format.space_after = Pt(6)

    for style_name in ("List Bullet", "List Number"):
        style = document.styles[style_name]
        style.font.name = _BODY_FONT
        style.font.size = Pt(10.5)
        style.font.color.rgb = _INK_COLOR
        style.paragraph_format.space_after = Pt(2)

    section = document.sections[0]
    section.left_margin = Inches(0.5)
    section.right_margin = Inches(0.5)
    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)


def _add_bottom_border(paragraph, color: str = _RULE_COLOR_HEX, size: int = 6) -> None:
    """Draw a thin rule under a paragraph. python-docx has no high-level API
    for paragraph borders, so this is built directly on the underlying XML —
    it's purely a border property on the paragraph, so ATS text extraction
    is unaffected. Used to separate the contact header from the body and to
    underline each resume section heading.
    """
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    p_pr.append(borders)


def _add_contact_header(document: docx.Document, contact: dict[str, str]) -> None:
    """Write the candidate's name (as the document's one Heading 1, centered)
    and a centered, muted line of whatever contact details are available, in
    a fixed order, with a rule underneath separating it from the body.
    Silently omits whatever is blank rather than leaving gaps.
    """
    name = contact.get("name", "")
    line = " | ".join(
        contact[key] for key in ("email", "phone", "location", "linkedin") if contact.get(key)
    )
    if not name and not line:
        return

    divider_paragraph = None
    if name:
        heading = document.add_heading(name, level=1)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        divider_paragraph = heading

    if line:
        contact_paragraph = document.add_paragraph(line)
        contact_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in contact_paragraph.runs:
            run.font.size = Pt(9.5)
            run.font.color.rgb = _MUTED_COLOR
        divider_paragraph = contact_paragraph

    divider_paragraph.paragraph_format.space_after = Pt(12)
    _add_bottom_border(divider_paragraph)


def _add_bullet_paragraph(document: docx.Document, text: str) -> None:
    """Add one List Bullet paragraph. If the bullet leads with a short
    "Label: rest" prefix (a skill category, sub-topic, etc.), bold the label
    so it stands out from the rest of the bullet.
    """
    paragraph = document.add_paragraph(style="List Bullet")
    match = _BULLET_LABEL_RE.match(text)
    if match:
        label, rest = match.groups()
        paragraph.add_run(f"{label}: ").bold = True
        paragraph.add_run(rest)
    else:
        paragraph.add_run(text)


def _add_entry(document: docx.Document, entry: dict, *, is_first: bool) -> None:
    """Render one job/degree/project block: a bold title line, an optional
    italic muted subtitle line (e.g. "Company · Dates") directly under it,
    then its own bullets — so consecutive entries under one section heading
    (e.g. different jobs under "Experience") read as visually separate
    blocks instead of one undifferentiated bullet list.
    """
    title_paragraph = document.add_paragraph()
    title_paragraph.paragraph_format.space_before = Pt(2 if is_first else 10)
    title_paragraph.paragraph_format.space_after = Pt(0)
    title_paragraph.add_run(entry.get("title", "")).bold = True

    subtitle = entry.get("subtitle")
    if subtitle:
        subtitle_paragraph = document.add_paragraph()
        subtitle_paragraph.paragraph_format.space_after = Pt(4)
        subtitle_run = subtitle_paragraph.add_run(subtitle)
        subtitle_run.italic = True
        subtitle_run.font.size = Pt(9.5)
        subtitle_run.font.color.rgb = _MUTED_COLOR

    for bullet in entry.get("bullets", []):
        _add_bullet_paragraph(document, bullet)


def render_tailored_resume_docx(
    summary: str,
    sections: list[tuple[str, list[str], list[dict]]],
    contact: dict[str, str] | None = None,
) -> bytes:
    """Render a tailored resume's structured content (see
    app.schemas.resume.TailoredResumeUpload — the same shape produced by
    this app's own LLM generation and accepted from an uploaded one, e.g.
    from an MCP client's own LLM) into a plain, single-column, ATS-safe
    .docx: a contact header, the summary as an intro paragraph, then each
    section as a heading plus either job/degree/project entries or a flat
    bullet list.
    """
    document = docx.Document()
    _style_document(document)

    _add_contact_header(document, contact or {})

    if summary:
        summary_paragraph = document.add_paragraph(summary)
        summary_paragraph.paragraph_format.space_after = Pt(14)

    for heading, bullets, entries in sections:
        heading_paragraph = document.add_heading(heading.upper(), level=2)
        _add_bottom_border(heading_paragraph)
        for index, entry in enumerate(entries):
            _add_entry(document, entry, is_first=index == 0)
        for bullet in bullets:
            _add_bullet_paragraph(document, bullet)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- PDF rendering (reportlab) ----------------------------------------------
#
# A pure-Python PDF renderer with no system-level dependencies (unlike
# WeasyPrint/LibreOffice), so it drops straight into the existing slim
# Dockerfile. Mirrors the docx renderer's structure and color palette above,
# though the font is Helvetica (reportlab's built-in nearest match to
# Calibri) rather than an embedded TTF, so it isn't pixel-identical.

_PDF_INK = HexColor("#1A1A1A")
_PDF_MUTED = HexColor("#595959")
_PDF_ACCENT = HexColor("#1F3A5F")
_PDF_MARGIN = 0.5 * inch


def _pdf_escape(text: str) -> str:
    """reportlab's Paragraph interprets a small XML-like markup language, so
    any free text embedded in one (as opposed to markup we build ourselves,
    e.g. our own <b> tags) must be escaped first.
    """
    return _xml_escape(text or "")


def _pdf_styles() -> dict[str, ParagraphStyle]:
    title_style = ParagraphStyle(
        "EntryTitle", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=_PDF_INK, spaceBefore=10
    )
    return {
        "name": ParagraphStyle(
            "Name",
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=_PDF_INK,
            alignment=TA_CENTER,
            spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "Contact",
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=_PDF_MUTED,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "summary": ParagraphStyle(
            "Summary", fontName="Helvetica", fontSize=10.5, leading=14, textColor=_PDF_INK, spaceAfter=14
        ),
        "heading": ParagraphStyle(
            "SectionHeading",
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=_PDF_ACCENT,
            spaceBefore=16,
            spaceAfter=4,
        ),
        "title": title_style,
        "title_first": ParagraphStyle("EntryTitleFirst", parent=title_style, spaceBefore=2),
        "subtitle": ParagraphStyle(
            "EntrySubtitle",
            fontName="Helvetica-Oblique",
            fontSize=9.5,
            leading=12,
            textColor=_PDF_MUTED,
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            fontName="Helvetica",
            fontSize=10.5,
            leading=13,
            textColor=_PDF_INK,
            leftIndent=14,
            bulletIndent=0,
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=10.5, leading=14, textColor=_PDF_INK, spaceAfter=10
        ),
    }


def _pdf_bullet_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    match = _BULLET_LABEL_RE.match(text)
    if match:
        label, rest = match.groups()
        body = f"<b>{_pdf_escape(label)}:</b> {_pdf_escape(rest)}"
    else:
        body = _pdf_escape(text)
    return Paragraph(body, style, bulletText="•")


def _pdf_contact_flowables(contact: dict[str, str], styles: dict[str, ParagraphStyle]) -> list:
    name = contact.get("name", "")
    line = " | ".join(contact[key] for key in ("email", "phone", "location", "linkedin") if contact.get(key))
    if not name and not line:
        return []

    flowables = []
    if name:
        flowables.append(Paragraph(_pdf_escape(name), styles["name"]))
    if line:
        flowables.append(Paragraph(_pdf_escape(line), styles["contact"]))
    flowables.append(HRFlowable(width="100%", thickness=0.75, color=_PDF_ACCENT, spaceAfter=12))
    return flowables


def _pdf_entry_flowables(entry: dict, styles: dict[str, ParagraphStyle], *, is_first: bool) -> list:
    flowables = [Paragraph(_pdf_escape(entry.get("title", "")), styles["title_first"] if is_first else styles["title"])]
    subtitle = entry.get("subtitle")
    if subtitle:
        flowables.append(Paragraph(_pdf_escape(subtitle), styles["subtitle"]))
    flowables.extend(_pdf_bullet_paragraph(bullet, styles["bullet"]) for bullet in entry.get("bullets", []))
    return flowables


def _build_pdf(story: list) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=_PDF_MARGIN,
        rightMargin=_PDF_MARGIN,
        topMargin=_PDF_MARGIN,
        bottomMargin=_PDF_MARGIN,
    )
    document.build(story or [Spacer(1, 1)])
    return buffer.getvalue()


def render_tailored_resume_pdf(
    summary: str,
    sections: list[tuple[str, list[str], list[dict]]],
    contact: dict[str, str] | None = None,
) -> bytes:
    """PDF counterpart to render_tailored_resume_docx — same structured
    content, same section/entry/bullet layout, rendered with reportlab
    instead of python-docx. See that function's docstring for the content
    shape.
    """
    styles = _pdf_styles()
    story = _pdf_contact_flowables(contact or {}, styles)

    if summary:
        story.append(Paragraph(_pdf_escape(summary), styles["summary"]))

    for heading, bullets, entries in sections:
        story.append(Paragraph(_pdf_escape(heading.upper()), styles["heading"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=_PDF_ACCENT, spaceAfter=4))
        for index, entry in enumerate(entries):
            story.extend(_pdf_entry_flowables(entry, styles, is_first=index == 0))
        story.extend(_pdf_bullet_paragraph(bullet, styles["bullet"]) for bullet in bullets)

    return _build_pdf(story)


def render_cover_letter_pdf(
    greeting: str, body_paragraphs: list[str], closing: str, contact: dict[str, str] | None = None
) -> bytes:
    """PDF counterpart to render_cover_letter_docx — see that function's
    docstring for the content shape.
    """
    styles = _pdf_styles()
    story = _pdf_contact_flowables(contact or {}, styles)

    if greeting:
        story.append(Paragraph(_pdf_escape(greeting), styles["body"]))
    story.extend(Paragraph(_pdf_escape(paragraph), styles["body"]) for paragraph in body_paragraphs)
    if closing:
        story.append(Paragraph(_pdf_escape(closing), styles["body"]))

    return _build_pdf(story)


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
