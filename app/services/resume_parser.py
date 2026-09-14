import io

import docx
from pypdf import PdfReader

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

SUPPORTED_CONTENT_TYPES = {"application/pdf", _DOCX_CONTENT_TYPE}


def extract_text(file_bytes: bytes, content_type: str) -> str:
    if content_type == "application/pdf":
        return _extract_pdf_text(file_bytes)
    if content_type == _DOCX_CONTENT_TYPE:
        return _extract_docx_text(file_bytes)
    raise ValueError(f"Unsupported resume file type: {content_type!r}")


def _extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def _extract_docx_text(file_bytes: bytes) -> str:
    document = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
