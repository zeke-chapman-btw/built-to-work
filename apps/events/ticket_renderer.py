from __future__ import annotations
import struct
import zlib
import qrcode

def qr_png_bytes(payload: str, *, scale: int = 8, border: int = 4) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=border)
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    width = len(matrix) * scale
    rows = []
    for row in matrix:
        pixels = b"".join((b"\x00\x00\x00" if cell else b"\xff\xff\xff") * scale for cell in row)
        rows.extend([b"\x00" + pixels] * scale)
    raw = b"".join(rows)
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ticket_png_bytes(ticket) -> bytes:
    """Render the complete locally branded ticket; QR contains only tel: + ten digits."""
    from io import BytesIO
    from pathlib import Path
    from zoneinfo import ZoneInfo

    import cairosvg
    from django.conf import settings
    from django.utils import timezone
    from PIL import Image, ImageDraw, ImageFont

    number = ticket.ticket_number
    if not number or len(number) != 10 or not number.isdigit():
        raise ValueError('A ten-digit ticket number is required for a downloadable ticket.')
    root = Path(settings.BASE_DIR)
    canvas = Image.new('RGB', (1100, 700), 'white')
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((8, 8, 1092, 692), radius=28, outline='#d7d7d7', width=3)
    logo_path = root / 'static' / 'internal' / 'images' / 'built-to-work-linear.svg'
    logo = Image.open(BytesIO(cairosvg.svg2png(url=str(logo_path), output_width=580))).convert('RGBA')
    canvas.paste(logo, (65, 40), logo)
    draw.rectangle((65, 165, 1035, 172), fill='#cf202f')
    regular = str(root / 'static' / 'kiosk' / 'fonts' / 'roboto-regular.ttf')
    bold = str(root / 'static' / 'kiosk' / 'fonts' / 'roboto-bold.ttf')
    heading = ImageFont.truetype(bold, 43)
    medium = ImageFont.truetype(bold, 31)
    body = ImageFont.truetype(regular, 28)
    small = ImageFont.truetype(regular, 23)
    draw.text((65, 192), 'EVENT TICKET', font=medium, fill='#cf202f')
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=12)
    qr.add_data(f'tel:{number}')
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    qr_image = qr_image.resize((405, 405), Image.Resampling.NEAREST)
    canvas.paste(qr_image, (55, 245))
    participant = ticket.registration.participant
    event = ticket.registration.event
    name = f'{participant.preferred_name or participant.first_name} {participant.last_name}'.strip()
    local_date = timezone.localtime(event.start_at, ZoneInfo(event.timezone_name))
    event_date = f'{local_date:%B} {local_date.day}, {local_date.year}'
    def fit(value, font, width=555):
        value = str(value)
        while value and draw.textbbox((0, 0), value, font=font)[2] > width:
            value = value[:-2].rstrip() + '…'
        return value
    draw.text((500, 255), fit(name, heading), font=heading, fill='#111111')
    draw.text((500, 325), fit(event.name, medium), font=medium, fill='#111111')
    draw.text((500, 378), event_date, font=body, fill='#333333')
    draw.text((500, 465), 'TICKET NUMBER', font=small, fill='#555555')
    draw.text((500, 505), number, font=ImageFont.truetype(bold, 51), fill='#111111')
    draw.text((500, 595), 'Present this ticket at the BTW kiosk.', font=small, fill='#333333')
    output = BytesIO()
    canvas.save(output, format='PNG', optimize=True)
    return output.getvalue()
