"""Turn plain SOP text into simple, safe HTML (headings, bullets, numbered steps, paragraphs)."""
import re
from django.utils.html import escape
from django.utils.safestring import mark_safe

_NUM = re.compile(r"^\d+[.)]\s+")


def render_body(text):
    out, para, items, kind = [], [], [], None

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(escape(line) for line in para) + "</p>")
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{escape(i)}</li>" for i in items) + f"</{kind}>")
            items.clear()
        kind = None

    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush_para(); flush_list(); continue
        if line.startswith("#"):
            flush_para(); flush_list()
            level = "h3" if line.startswith("##") else "h2"
            out.append(f"<{level}>{escape(line.lstrip('#').strip())}</{level}>")
        elif line.startswith(("- ", "* ", "• ")):
            flush_para()
            if kind != "ul": flush_list(); kind = "ul"
            items.append(line[2:].strip())
        elif _NUM.match(line):
            flush_para()
            if kind != "ol": flush_list(); kind = "ol"
            items.append(_NUM.sub("", line, count=1))
        else:
            flush_list(); para.append(line)
    flush_para(); flush_list()
    return mark_safe("".join(out))
