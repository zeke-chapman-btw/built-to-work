"""Email signatures: each person's own (pasted in Settings) and the company one for noreply@."""
from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser

from django.core.mail import EmailMultiAlternatives
from django.utils.html import linebreaks

ALLOWED_TAGS = {"a", "b", "strong", "i", "em", "u", "br", "p", "div", "span", "img", "table", "tbody", "thead", "tr",
                "td", "th", "font", "hr", "small", "ul", "ol", "li", "h1", "h2", "h3", "h4"}
VOID = {"br", "img", "hr"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "head", "title", "noscript", "template", "svg", "form"}
ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "width", "height"}, "td": {"colspan", "rowspan", "width", "valign", "align"},
         "th": {"colspan", "rowspan", "width", "valign", "align"}, "table": {"cellpadding", "cellspacing", "border", "width"},
         "font": {"color", "size", "face"}}
STYLE_PROPS = {"color", "background-color", "font-size", "font-weight", "font-style", "font-family", "text-decoration",
               "line-height", "margin", "margin-top", "margin-bottom", "margin-left", "margin-right", "padding",
               "padding-top", "padding-bottom", "padding-left", "padding-right", "width", "height", "max-width",
               "vertical-align", "text-align", "border", "border-left", "border-right", "border-top", "border-bottom",
               "display", "white-space", "letter-spacing"}
MAX_LENGTH = 20000


def _safe_url(value, *, image=False):
    value = (value or "").strip()
    if re.match(r"^https?://", value, re.I):
        return value
    if not image and re.match(r"^(mailto:|tel:)", value, re.I):
        return value
    return ""


def _safe_style(value):
    kept = []
    for part in (value or "").split(";"):
        if ":" not in part:
            continue
        name, val = (piece.strip() for piece in part.split(":", 1))
        if name.lower() in STYLE_PROPS and not re.search(r"url\(|expression|javascript:|[<>]", val, re.I):
            kept.append(f"{name.lower()}:{val}")
    return ";".join(kept)


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.open = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED_TAGS:
            return
        kept = []
        for name, value in attrs:
            name = name.lower()
            if name == "style":
                style = _safe_style(value)
                if style:
                    kept.append(f'style="{escape(style)}"')
            elif name in ATTRS.get(tag, ()):
                if name in ("href", "src"):
                    value = _safe_url(value, image=name == "src")
                    if not value:
                        continue
                kept.append(f'{name}="{escape(value or "")}"')
        if tag == "img" and not any(item.startswith("src=") for item in kept):
            return  # a picture with nowhere to load from
        if tag == "a":
            kept.append('target="_blank"')
        self.out.append(f"<{tag}{(' ' + ' '.join(kept)) if kept else ''}>")
        if tag not in VOID:
            self.open.append(tag)

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED_TAGS or tag in VOID or tag not in self.open:
            return
        while self.open:
            last = self.open.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data))

    def result(self):
        return "".join(self.out) + "".join(f"</{tag}>" for tag in reversed(self.open))


def clean_html(value):
    """Only simple formatting, links and pictures survive; scripts and tricks are removed."""
    cleaner = _Cleaner()
    cleaner.feed((value or "")[:MAX_LENGTH * 2])
    cleaner.close()
    html = cleaner.result().strip()
    html = re.sub(r"^(?:\s|<br>|<div>\s*<br>\s*</div>)+|(?:\s|<br>|<div>\s*<br>\s*</div>)+$", "", html)
    return html[:MAX_LENGTH]


def plain_text(html):
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", html or "")
    text = re.sub(r"(?i)<img[^>]*alt=\"([^\"]*)\"[^>]*>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def personal(user):
    from .models import EmailSignature
    row = EmailSignature.objects.filter(user=user).first() if user and user.pk else None
    return row.html if row else ""


def company():
    from .models import ConnectionSetting
    try:
        row = ConnectionSetting.objects.filter(key="signature").first()
    except Exception:  # a signature is optional and never stops an email going out
        return ""
    return (row.values or {}).get("html", "") if row else ""


PRIVATE_IMAGE = re.compile(r'<img[^>]*\bsrc="https?://mail\.google\.com/[^"]*"[^>]*>', re.I)


def logo_url():
    """The Built to Work logo as a public picture anyone's email can load."""
    from django.conf import settings
    from django.templatetags.static import static
    base = (getattr(settings, "PARTICIPANT_PORTAL_BASE_URL", "") or "").rstrip("/")
    path = static("email/btw-logo-stacked.png")
    return path if path.startswith("http") or not base else base + path


def public_images(html):
    """Pictures copied from a Gmail signature point inside that person's Gmail, so nobody else can see them.
    Show the Built to Work logo there instead."""
    return PRIVATE_IMAGE.sub(lambda m: f'<img src="{logo_url()}" alt="Built to Work" width="150" height="94" style="width:150px;height:auto">', html or "")


def build(subject, body, sender, to, *, signature_html="", **kwargs):
    """A plain-text email, plus a formatted copy with the signature when there is one."""
    signature_html = public_images(signature_html)
    text = body
    if signature_html:
        text = f"{body.rstrip()}\n\n-- \n{plain_text(signature_html)}"
    message = EmailMultiAlternatives(subject, text, sender, to, **kwargs)
    if signature_html:
        message.attach_alternative(
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222">'
            f'{linebreaks(escape(body.rstrip()))}<br><div>{signature_html}</div></div>', "text/html")
    return message
