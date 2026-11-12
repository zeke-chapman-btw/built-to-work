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
    from apps.participants.models import PortalPreviewFixture
    synthetic_preview = PortalPreviewFixture.objects.filter(event_id=ticket.registration.event_id).exists()
    if not synthetic_preview and (not number or len(number) != 10 or not number.isdigit()):
        raise ValueError('A ten-digit ticket number is required for a downloadable ticket.')
    root = Path(settings.BASE_DIR)
    canvas = Image.new('RGB', (1100, 700), 'white')
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((8, 8, 1092, 692), radius=28, outline='#d7d7d7', width=3)
    # The BTW logo has white lettering, so it always sits on a dark band.
    draw.rounded_rectangle((10, 10, 1090, 174), radius=24, fill='#111111')
    draw.rectangle((10, 120, 1090, 174), fill='#111111')
    logo_path = root / 'static' / 'internal' / 'images' / 'built-to-work-linear.svg'
    logo = Image.open(BytesIO(cairosvg.svg2png(url=str(logo_path), output_width=480))).convert('RGBA')
    canvas.paste(logo, (65, 92 - logo.height // 2), logo)
    draw.rectangle((10, 168, 1090, 174), fill='#cf202f')
    regular = str(root / 'static' / 'kiosk' / 'fonts' / 'roboto-regular.ttf')
    bold = str(root / 'static' / 'kiosk' / 'fonts' / 'roboto-bold.ttf')
    heading = ImageFont.truetype(bold, 43)
    medium = ImageFont.truetype(bold, 31)
    body = ImageFont.truetype(regular, 28)
    small = ImageFont.truetype(regular, 23)
    draw.text((65, 192), 'SYNTHETIC PREVIEW' if synthetic_preview else 'EVENT TICKET', font=medium, fill='#cf202f')
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=12)
    # Preview downloads from every route carry a non-admission QR payload.
    qr.add_data('btw-admin-preview:' + str(ticket.pk) if synthetic_preview else f'tel:{number}')
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    qr_image = qr_image.resize((405, 405), Image.Resampling.NEAREST)
    canvas.paste(qr_image, (55, 245))
    participant = ticket.registration.participant
    event = ticket.registration.event
    name = f'{participant.preferred_name or participant.first_name} {participant.last_name}'.strip()
    from .ticket_text import event_dates, event_place
    event_date = event_dates(event)
    place = event_place(event)
    def fit(value, font, width=555):
        value = str(value)
        while value and draw.textbbox((0, 0), value, font=font)[2] > width:
            value = value[:-2].rstrip() + '…'
        return value
    draw.text((500, 255), fit(name, heading), font=heading, fill='#111111')
    draw.text((500, 325), fit(event.name, medium), font=medium, fill='#111111')
    draw.text((500, 372), fit(event_date, body), font=body, fill='#333333')
    if place:
        draw.text((500, 410), fit(place, small), font=small, fill='#333333')
    if event.group_mode != 'disabled' and ticket.registration.group_id:
        draw.text((500, 440), fit(f'{event.group_label}: {ticket.registration.group.name}', small), font=small, fill='#333333')
    draw.text((500, 465), 'TICKET NUMBER', font=small, fill='#555555')
    draw.text((500, 505), 'PREVIEW ONLY' if synthetic_preview else number, font=ImageFont.truetype(bold, 51), fill='#111111')
    draw.text((500, 595), 'Not valid for event admission.' if synthetic_preview else 'Show this ticket at check-in.', font=small, fill='#333333')
    output = BytesIO()
    canvas.save(output, format='PNG', optimize=True)
    return output.getvalue()


def ticket_phone_png_bytes(ticket) -> bytes:
    """Tall ticket sized for a phone screen; same QR payload as the printable ticket."""
    from io import BytesIO
    from pathlib import Path
    from zoneinfo import ZoneInfo

    import cairosvg
    from django.conf import settings
    from django.utils import timezone
    from PIL import Image, ImageDraw, ImageFont

    number = ticket.ticket_number
    from apps.participants.models import PortalPreviewFixture
    synthetic_preview = PortalPreviewFixture.objects.filter(event_id=ticket.registration.event_id).exists()
    if not synthetic_preview and (not number or len(number) != 10 or not number.isdigit()):
        raise ValueError('A ten-digit ticket number is required for a downloadable ticket.')
    root = Path(settings.BASE_DIR)
    W, H = 750, 1334
    canvas = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, W, 190), fill='#111111')
    draw.rectangle((0, 184, W, 192), fill='#cf202f')
    logo_path = root / 'static' / 'internal' / 'images' / 'built-to-work-linear.svg'
    logo = Image.open(BytesIO(cairosvg.svg2png(url=str(logo_path), output_width=470))).convert('RGBA')
    canvas.paste(logo, ((W - logo.width) // 2, 92 - logo.height // 2), logo)
    regular = str(root / 'static' / 'kiosk' / 'fonts' / 'roboto-regular.ttf')
    bold = str(root / 'static' / 'kiosk' / 'fonts' / 'roboto-bold.ttf')
    label = ImageFont.truetype(bold, 28)
    heading = ImageFont.truetype(bold, 52)
    medium = ImageFont.truetype(bold, 36)
    body = ImageFont.truetype(regular, 30)
    small = ImageFont.truetype(regular, 26)
    big = ImageFont.truetype(bold, 64)
    participant = ticket.registration.participant
    event = ticket.registration.event
    name = f'{participant.preferred_name or participant.first_name} {participant.last_name}'.strip()
    from .ticket_text import event_dates, event_place
    event_date = event_dates(event)
    place = event_place(event)

    def fit(value, font, width=W - 100):
        value = str(value)
        while value and draw.textbbox((0, 0), value, font=font)[2] > width:
            value = value[:-2].rstrip() + '…'
        return value

    def centered(y, text, font, fill):
        text = fit(text, font)
        width = draw.textbbox((0, 0), text, font=font)[2]
        draw.text(((W - width) // 2, y), text, font=font, fill=fill)

    centered(232, 'SYNTHETIC PREVIEW' if synthetic_preview else 'EVENT TICKET', label, '#cf202f')
    centered(282, name, heading, '#111111')
    centered(356, event.name, medium, '#111111')
    centered(408, event_date, body, '#333333')
    y = 452
    if place:
        centered(y, place, small, '#333333')
        y += 40
    if event.group_mode != 'disabled' and ticket.registration.group_id:
        centered(y, f'{event.group_label}: {ticket.registration.group.name}', small, '#333333')
        y += 40
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=12)
    qr.add_data('btw-admin-preview:' + str(ticket.pk) if synthetic_preview else f'tel:{number}')
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white').convert('RGB').resize((540, 540), Image.Resampling.NEAREST)
    canvas.paste(qr_image, ((W - 540) // 2, y + 20))
    y += 580
    centered(y, 'TICKET NUMBER', small, '#555555')
    centered(y + 40, 'PREVIEW ONLY' if synthetic_preview else number, big, '#111111')
    centered(H - 90, 'Not valid for event admission.' if synthetic_preview else 'Show this at check-in.', body, '#333333')
    output = BytesIO()
    canvas.save(output, format='PNG', optimize=True)
    return output.getvalue()


def bulk_ticket_pdf_bytes(tickets):
    """Print-ready A4 sheets, six complete existing ticket images per page."""
    from io import BytesIO
    from PIL import Image

    tickets = list(tickets)
    if not tickets:
        raise ValueError("Select at least one ticket.")
    pages = []
    for offset in range(0, len(tickets), 6):
        page = Image.new("RGB", (2550, 3300), "white")
        for slot, ticket in enumerate(tickets[offset:offset + 6]):
            image = Image.open(BytesIO(ticket_png_bytes(ticket))).convert("RGB")
            x = 130 + (slot % 2) * 1160
            y = 480 + (slot // 2) * 850
            page.paste(image, (x, y))
        pages.append(page)
    output = BytesIO()
    pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:], resolution=300.0)
    return output.getvalue()
