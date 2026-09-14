import io

import docx
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from docx.text.paragraph import Paragraph

# Only these tags are ever requested from the LLM (see app.services.prompts)
# or accepted from an uploaded tailored-resume/cover-letter HTML fragment —
# deliberately narrow so rendering stays predictable and the output stays a
# plain, single-column, ATS-scannable .docx (no tables/columns/images).
_HEADING_TAGS = {"h1": 0, "h2": 1, "h3": 2}
_LIST_ITEM_PARENTS = {"ul", "ol"}
_INLINE_BOLD_TAGS = {"b", "strong"}
_INLINE_ITALIC_TAGS = {"i", "em"}


def _add_inline_runs(paragraph: Paragraph, node: Tag, *, bold: bool = False, italic: bool = False) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if not text:
                continue
            run = paragraph.add_run(text.replace("\n", " "))
            run.bold = bold
            run.italic = italic
        elif isinstance(child, Tag):
            if child.name == "br":
                paragraph.add_run().add_break()
                continue
            _add_inline_runs(
                paragraph,
                child,
                bold=bold or child.name in _INLINE_BOLD_TAGS,
                italic=italic or child.name in _INLINE_ITALIC_TAGS,
            )


def render_html_docx(html: str) -> bytes:
    """Render an HTML fragment (LLM-generated or client-uploaded — see
    POST /resumes/main/tailored, /resumes/main/tailored/upload, and the
    cover-letter equivalents) into a plain, single-column .docx. Only
    headings/paragraphs/lists/bold/italic are honored; anything else
    (tables, images, inline styles) is silently dropped rather than
    rejected, since ATS parsers don't handle them reliably either.
    """
    soup = BeautifulSoup(html, "html.parser")
    document = docx.Document()

    for node in soup.find_all(["h1", "h2", "h3", "p", "ul", "ol"], recursive=False):
        if node.name in _HEADING_TAGS:
            document.add_heading(node.get_text(strip=True), level=_HEADING_TAGS[node.name] + 1)
        elif node.name == "p":
            _add_inline_runs(document.add_paragraph(), node)
        elif node.name in _LIST_ITEM_PARENTS:
            style = "List Bullet" if node.name == "ul" else "List Number"
            for item in node.find_all("li", recursive=False):
                _add_inline_runs(document.add_paragraph(style=style), item)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
