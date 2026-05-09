"""Tests for pdf_service.extract_text — no live Firebase required."""
import fitz
import pytest

from app.services.pdf_service import extract_text, _MAX_CHARS, _TRUNCATION_MARKER


def _make_pdf(text: str) -> bytes:
    """Build a minimal one-page in-memory PDF containing `text`."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_extract_text_basic():
    pdf = _make_pdf("Hello, DeMaestro!")
    result = extract_text(pdf)
    assert "Hello, DeMaestro!" in result


def test_extract_text_strips_trailing_whitespace():
    pdf = _make_pdf("  trailing  ")
    result = extract_text(pdf)
    assert not result.endswith(" ")


def test_extract_text_truncates_long_content():
    # insert_text places one line (~76 chars) per call; fill ~54 lines × 55 pages
    # = ~225 000 extractable chars, safely over _MAX_CHARS (200 000).
    doc = fitz.open()
    for _ in range(55):
        p = doc.new_page()
        for y in range(50, 800, 14):
            p.insert_text((50, y), "A" * 76)
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract_text(pdf_bytes)
    assert len(result) <= _MAX_CHARS + len(_TRUNCATION_MARKER)
    assert result.endswith(_TRUNCATION_MARKER)


def test_extract_text_invalid_bytes():
    with pytest.raises(ValueError, match="Could not parse PDF"):
        extract_text(b"this is not a pdf")


def test_extract_text_multipage():
    doc = fitz.open()
    for word in ("page one", "page two", "page three"):
        p = doc.new_page()
        p.insert_text((50, 72), word)
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract_text(pdf_bytes)
    assert "page one" in result
    assert "page two" in result
    assert "page three" in result
