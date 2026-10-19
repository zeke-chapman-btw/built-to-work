"""Reuse private storage conventions; never fall back to Render's ephemeral disk."""
from io import BytesIO
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from apps.participants.participant_document_storage import PrivateParticipantDocumentStorage
from apps.participants.document_forms import validate_document_file
class OperationalDocumentStorage(PrivateParticipantDocumentStorage):
    def __init__(self):
        root=getattr(settings,"OPERATIONAL_DOCUMENT_ROOT",None)
        if not root: raise ValidationError("Private operational document storage is not configured.")
        super().__init__(location=Path(root))
def uploads_available():
    return bool(getattr(settings,"OPERATIONAL_DOCUMENT_STORAGE_DURABLE",False) and (getattr(settings,"OPERATIONAL_DOCUMENT_STORAGE_ALIAS",None) or getattr(settings,"OPERATIONAL_DOCUMENT_ROOT",None)))
def private_storage():
    if not uploads_available(): raise ValidationError("Real uploads are unavailable until private durable operational storage is configured. No temporary Render uploads are accepted.")
    alias=getattr(settings,"OPERATIONAL_DOCUMENT_STORAGE_ALIAS",None)
    return storages[alias] if alias else OperationalDocumentStorage()
def validate_upload(upload):
    # Use the existing PDF/PNG/JPEG allowlist and signature validation, then fully parse.
    raw_name=upload.name.replace(chr(92),"/")
    if "/" in raw_name or any(ord(c)<32 for c in raw_name): raise ValidationError("Use a plain filename without paths or control characters.")
    limit=int(getattr(settings,"OPERATIONAL_DOCUMENT_MAX_UPLOAD_BYTES",10*1024*1024))
    if upload.size<=0 or upload.size>limit: raise ValidationError("Operational files must be nonempty and no larger than 10 MB.")
    filename,content_type=validate_document_file(upload)
    payload=upload.read(limit+1);upload.seek(0)
    if len(payload)!=upload.size or len(payload)>limit: raise ValidationError("The upload size is invalid.")
    try:
        if content_type=="application/pdf":
            from pypdf import PdfReader
            reader=PdfReader(BytesIO(payload),strict=True)
            if reader.is_encrypted or not len(reader.pages): raise ValueError()
        else:
            from PIL import Image
            with Image.open(BytesIO(payload)) as img:
                if img.format not in ("PNG","JPEG"): raise ValueError()
                img.verify()
    except Exception as exc:
        raise ValidationError("Choose a readable, unencrypted PDF, PNG or JPEG file.") from exc
    return filename,content_type,payload
def demo_pdf():
    # Fixed, visibly synthetic content; no user data or live operational instructions.
    stream=b"BT /F1 18 Tf 50 740 Td (SYNTHETIC BTW BUILD 9D TEST DOCUMENT) Tj 0 -32 Td /F1 12 Tf (Preview demonstration only. Not real event instructions.) Tj ET"
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",b"<< /Length "+str(len(stream)).encode()+b" >>\nstream\n"+stream+b"\nendstream"]
    result=b"%PDF-1.4\n";offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(result));result+=str(i).encode()+b" 0 obj\n"+obj+b"\nendobj\n"
    start=len(result);result+=b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:result+=f"{offset:010} 00000 n \n".encode()
    return result+b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"+str(start).encode()+b"\n%%EOF\n"
