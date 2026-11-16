"""Validation and text extraction for private consent PDFs."""
import hashlib
import re
from io import BytesIO

from django import forms
from pypdf import PdfReader

MAX_CONSENT_PDF_BYTES = 10 * 1024 * 1024


def extract_consent_pdf(upload):
    if not upload.name.lower().endswith('.pdf') or upload.size > MAX_CONSENT_PDF_BYTES:
        raise forms.ValidationError('Upload a PDF no larger than 10 MB.')
    data = upload.read()
    upload.seek(0)
    if not data.startswith(b'%PDF-') or len(data) != upload.size:
        raise forms.ValidationError('The uploaded file is not a valid PDF.')
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 200:
            raise ValueError('Encrypted, empty, or oversized PDF')
        text = readable_text(reader)
    except Exception as exc:
        raise forms.ValidationError('The PDF could not be read. Upload a text-based, unencrypted PDF.') from exc
    if not text or len(text) > 1_000_000:
        raise forms.ValidationError('No usable text was extracted. Upload a text-based PDF.')
    return text, hashlib.sha256(data).hexdigest()


_BULLETS = "●•▪◦‣∙·-–*"


def _page_lines(page):
    try:
        raw = page.extract_text(extraction_mode="layout") or ""
    except Exception:
        raw = page.extract_text() or ""
    for line in raw.replace("\u200b", "").splitlines():
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if re.fullmatch(r"\d{1,3}", line):
            continue  # page number
        yield line


def readable_text(reader):
    """PDF text as paragraphs: wrapped lines joined, bullets kept, page numbers dropped.

    Some PDFs (Word exports especially) come out one word per line with the default
    extractor; the layout extractor plus this clean-up gives text a person can check.
    """
    paragraphs, current, previous = [], "", ""
    for page in reader.pages:
        for line in _page_lines(page):
            if not line:
                continue  # blank lines (and page breaks) don't end a sentence that wraps onto the next line
            bullet = line[0] in _BULLETS and (len(line) == 1 or line[1] == " ")
            if bullet:
                line = "• " + line[1:].strip()
            continues = (current and not bullet and not re.search(r"[.:;!?]$", previous)
                         and (line[0].islower() or len(previous) > 70 or previous.startswith("• ") and line[0].islower()))
            if continues:
                current += " " + line
            else:
                if current:
                    paragraphs.append(current)
                current = line
            previous = line
    if current:
        paragraphs.append(current)
    text = "\n\n".join(paragraphs).strip()
    if not text:
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    return text
