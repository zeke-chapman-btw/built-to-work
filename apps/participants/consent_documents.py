"""Validation and text extraction for private consent PDFs."""
import hashlib
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
        text = '\n\n'.join((page.extract_text() or '').strip() for page in reader.pages).strip()
    except Exception as exc:
        raise forms.ValidationError('The PDF could not be read. Upload a text-based, unencrypted PDF.') from exc
    if not text or len(text) > 1_000_000:
        raise forms.ValidationError('No usable text was extracted. Upload a text-based PDF.')
    return text, hashlib.sha256(data).hexdigest()
