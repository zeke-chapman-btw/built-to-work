"""Turns handbook text into safe HTML. Everything is escaped first, so nothing typed can inject HTML."""
import re
from django.utils.html import escape
from django.utils.safestring import mark_safe

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_NUM = re.compile(r"^(\d+)\.\s+(.*)$")


def _inline(text):
    return _BOLD.sub(r"<strong>\1</strong>", escape(text))


def render(body):
    out, stack, para = [], [], []  # stack holds (tag, indent)

    def close_para():
        if para:
            out.append("<p>" + " ".join(para) + "</p>")
            para.clear()

    def close_lists():
        while stack:
            out.append("</li></" + stack.pop()[0] + ">")

    for raw in (body or "").replace("\r\n", "\n").split("\n"):
        if not raw.strip():
            close_para(); close_lists(); continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        tag, text = None, None
        if line.startswith("- "):
            tag, text = "ul", line[2:]
        elif _NUM.match(line):
            tag, text = "ol", _NUM.match(line).group(2)
        if tag:
            close_para()
            while stack and stack[-1][1] > indent:
                out.append("</li></" + stack.pop()[0] + ">")
            if stack and stack[-1][1] == indent and stack[-1][0] == tag:
                out.append("</li><li>" + _inline(text))
            else:
                if stack and stack[-1][1] == indent:
                    out.append("</li></" + stack.pop()[0] + ">")
                start = _NUM.match(line).group(1) if tag == "ol" else "1"
                out.append(("<ol start=\"%s\">" % start if tag == "ol" and start != "1" else "<" + tag + ">") + "<li>" + _inline(text))
                stack.append((tag, indent))
            continue
        close_lists()
        if line.startswith("### "):
            close_para(); out.append("<h4>" + _inline(line[4:]) + "</h4>")
        else:
            para.append(_inline(line))
    close_para(); close_lists()
    return mark_safe("".join(out))
