"""PDF text extraction using PyMuPDF (fitz)."""
import fitz

_MAX_CHARS = 200_000
_TRUNCATION_MARKER = "[truncated]"


def extract_text(pdf_bytes: bytes) -> str:
    """Extract and concatenate text from all pages of a PDF.

    Raises ValueError if the bytes cannot be parsed as a PDF.
    Truncates output to 200,000 characters with a [truncated] marker.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = [page.get_text() for page in doc]
        doc.close()
    except Exception as exc:
        raise ValueError(f"Could not parse PDF: {exc}") from exc

    text = "\n\n".join(pages).rstrip()

    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + _TRUNCATION_MARKER

    return text
