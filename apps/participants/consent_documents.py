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
        indent = len(line) - len(line.lstrip(" \t\xa0"))
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if re.fullmatch(r"\d{1,3}", line):
            continue  # page number
        yield indent, line


def readable_text(reader):
    """PDF text as paragraphs: wrapped lines joined, bullets kept, page numbers dropped.

    Some PDFs (Word exports especially) come out one word per line with the default
    extractor; the layout extractor plus this clean-up gives text a person can check.
    """
    paragraphs, current, previous, bullet_indent = [], "", "", None
    for page in reader.pages:
        for indent, line in _page_lines(page):
            if not line:
                continue  # blank lines (and page breaks) don't end a sentence that wraps onto the next line
            bullet = line[0] in _BULLETS and (len(line) == 1 or line[1] == " ")
            if bullet:
                if bullet_indent is None or not previous.startswith(("• ", "◦ ")) and not current.startswith(("• ", "◦ ")):
                    bullet_indent = indent
                sub = indent >= bullet_indent + 3
                line = ("◦ " if sub else "• ") + line[1:].strip()
            elif not current.startswith(("• ", "◦ ")):
                bullet_indent = None
            continues = (current and not bullet and not re.search(r"[.:;!?]$", previous)
                         and (line[0].islower() or len(previous) > 70))
            if continues:
                current += " " + line
            else:
                if current:
                    paragraphs.append(current)
                current = line
            previous = line
    if current:
        paragraphs.append(current)
    paragraphs = _mark_bold(paragraphs, _styled_runs(reader))
    text = "\n\n".join(paragraphs).strip()
    if not text:
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    return text


def _squash(text):
    return re.sub(r"\s+", "", text or "")


def _styled_runs(reader):
    """Text the PDF sets in bold and in italic, squashed (no spaces), as strings with | between pieces."""
    found = {"bold": [], "italic": []}
    current = {"bold": [], "italic": []}

    def style(font):
        name = str((font or {}).get("/BaseFont", "")).lower()
        return {"bold": any(w in name for w in ("bold", "black", "heavy")),
                "italic": any(w in name for w in ("italic", "oblique"))}

    def visit(text, _cm, _tm, font, _size):
        if not text.strip():
            return
        flags = style(font)
        for key in found:
            if flags[key] and not (key == "italic" and flags["bold"]):
                current[key].append(text)
            elif current[key]:
                found[key].append(_squash("".join(current[key])))
                current[key].clear()

    try:
        for page in reader.pages:
            page.extract_text(visitor_text=visit)
    except Exception:
        return {"bold": "", "italic": ""}
    for key in found:
        if current[key]:
            found[key].append(_squash("".join(current[key])))
    return {key: "|" + "|".join(pieces) + "|" for key, pieces in found.items()}


def _prefix_for(body, squashed_length):
    """The part of ``body`` covering its first ``squashed_length`` non-space characters."""
    count = 0
    for index, char in enumerate(body):
        if not char.isspace():
            count += 1
            if count == squashed_length:
                return body[:index + 1]
    return body


def _mark_bold(paragraphs, styles):
    """Bold paragraphs become headings (short) or **bold** text; italic short lines become subheadings;
    bold lead-ins ("Cookies.") and bold terms in quotes stay bold."""
    bold, italic = styles.get("bold", ""), styles.get("italic", "")
    if not bold.strip("|") and not italic.strip("|"):
        return paragraphs
    marked = []
    for paragraph in paragraphs:
        bullet = paragraph.startswith(("• ", "◦ "))
        mark = paragraph[:2] if bullet else ""
        body = paragraph[2:] if bullet else paragraph
        squashed = _squash(body)
        short = len(body) <= 80 and not re.search(r"[.;,]$", body)
        if len(squashed) >= 2 and squashed in bold:
            marked.append("## " + body if not bullet and short else mark + "**" + body + "**")
            continue
        if len(squashed) >= 4 and squashed in italic and not bullet and short:
            marked.append("### " + body)
            continue
        lead = next((piece for piece in bold.split("|") if len(piece) >= 4 and squashed.startswith(piece)), "")
        if lead and len(lead) < len(squashed):
            prefix = _prefix_for(body, len(lead))
            body = "**" + prefix + "**" + body[len(prefix):]

        def term(match):
            inner = _squash(match.group(2))
            keep = len(inner) > 1 and ("|" + inner + "|" in bold or (len(inner) > 3 and inner in bold))
            return f"**{match.group(1)}{match.group(2)}{match.group(3)}**" if keep else match.group(0)
        body = re.sub(r"([“\"])([^”\"]{1,40})([”\"])", term, body)
        marked.append(mark + body)
    return marked
