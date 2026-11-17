"""Consent display helpers: where a consent is shown, and showing its text with structure.

The stored text stays plain (it is what people agree to and what is fingerprinted).
A few simple marks add structure:
  ## Heading        ### Smaller heading
  **bold text**     __underlined text__
  • item  (or "- item")      ◦ item  (a point under the one above)
"""
import re

from django import template
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def consent_usage(document):
    from apps.events.models import Event, OfflineEventPreparation
    from apps.participants.models import ConsentDocumentVersion
    current_general = ConsentDocumentVersion.objects.filter(key="btw-intake", is_approved=True).order_by("-effective_at", "-version").first()
    events = list(Event.objects.filter(public_consent_document=document, status="upcoming",
                                       end_at__gte=timezone.now()).order_by("start_at")[:20])
    trailers = list(OfflineEventPreparation.objects.filter(consent_document=document).exclude(status="closed")
                    .select_related("event", "trailer")[:20])
    return {"general": current_general is not None and current_general.pk == document.pk,
            "events": events, "trailers": trailers,
            "unused": not (current_general and current_general.pk == document.pk) and not events and not trailers}



def _inline(text):
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
    return text.replace("\n", "<br>")


@register.filter
def consent_text(value):
    blocks = re.split(r"\n\s*\n", (value or "").replace("\r\n", "\n").strip())
    html, items = [], []

    def flush():
        if not items:
            return
        out, nested = ["<ul>"], False
        for level, text in items:
            if level and not nested:
                out[-1] = out[-1][:-5] if out[-1].endswith("</li>") else out[-1]
                out.append("<ul>")
                nested = True
            elif not level and nested:
                out.append("</ul></li>")
                nested = False
            out.append(f"<li>{_inline(text)}</li>")
        if nested:
            out.append("</ul></li>")
        out.append("</ul>")
        html.append("".join(out))
        items.clear()

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if all(re.match(r"^\s*([•\-*◦▪]|o)\s+", line) for line in lines):
            for line in lines:
                match = re.match(r"^(\s*)([•\-*◦▪]|o)\s+(.*)$", line)
                level = 1 if match.group(2) in "◦▪o" or len(match.group(1)) >= 2 else 0
                items.append((level, match.group(3)))
            continue
        flush()
        if block.startswith("### "):
            html.append(f"<h4>{_inline(block[4:])}</h4>")
        elif block.startswith("## "):
            html.append(f"<h3>{_inline(block[3:])}</h3>")
        elif block.startswith("# "):
            html.append(f"<h3>{_inline(block[2:])}</h3>")
        else:
            html.append(f"<p>{_inline(block)}</p>")
    flush()
    return mark_safe("".join(html))
