"""Turn a plain-text job posting into readable HTML: headings, bullet lists and paragraphs.

Postings arrive as text with inconsistent line breaks, so this only guesses at structure:
- a line ending with ':' or a short line on its own (no sentence punctuation) is a heading,
- lines starting with -, •, * or "1." are bullets, and so are two or more short plain lines in a row under a heading,
- anything else is a paragraph.
"""

import re

from markupsafe import Markup, escape

BULLET = re.compile(r"^\s*(?:[-–•*·▪◦●►✓✔]|\d{1,2}[.)])\s+")
END_PUNCT = (".", ",", ";", "!", "?")
HEADING_MAX_WORDS = 6
LIST_LINE_MAX = 140


def _lines(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("�", "")
    out: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        # A sentence wrapped mid-way ("solve\nreal business problems"): glue it back on.
        if (line and out and out[-1] and line[0].islower() and not BULLET.match(line)
                and not out[-1].endswith(END_PUNCT + (":",))):
            out[-1] += " " + line
        else:
            out.append(line)
    return out


def _is_heading(line: str, prev_kind: str | None, prev_line: str) -> bool:
    if BULLET.match(line) or len(line) > 80:
        return False
    if line.endswith(":"):
        return True
    words = line.split()
    return (len(words) <= HEADING_MAX_WORDS and not line.endswith(END_PUNCT)
            and (prev_kind in (None, "blank", "li") or (prev_kind == "p" and prev_line.endswith(END_PUNCT))))


def _classify(lines: list[str]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    prev, prev_line = None, ""
    for line in lines:
        if not line:
            kind = "blank"
        elif BULLET.match(line):
            kind, line = "li", BULLET.sub("", line)
        elif _is_heading(line, prev, prev_line):
            kind, line = "h", line.rstrip(":").strip()
        else:
            kind = "p"
        items.append((kind, line))
        prev, prev_line = kind, line

    # Under a heading, two or more plain lines in a row read as a list.
    for i, (kind, _) in enumerate(items):
        if kind != "h":
            continue
        j = i + 1
        while j < len(items) and items[j][0] == "blank":
            j += 1
        run = j
        while run < len(items) and items[run][0] == "p":
            run += 1
        lines = [items[k][1] for k in range(j, run)]
        if len(lines) >= 2 and (all(len(x) <= LIST_LINE_MAX for x in lines) or not any(x.endswith(".") for x in lines)):
            for k in range(j, run):
                items[k] = ("li", items[k][1])
    return items


def posting_html(text: str | None) -> Markup:
    parts: list[str] = []
    open_list = False
    for kind, line in _classify(_lines(text or "")):
        if kind == "blank":
            continue
        if kind != "li" and open_list:
            parts.append("</ul>")
            open_list = False
        if kind == "h":
            parts.append(f"<h4>{escape(line)}</h4>")
        elif kind == "li":
            if not open_list:
                parts.append("<ul>")
                open_list = True
            parts.append(f"<li>{escape(line)}</li>")
        else:
            parts.append(f"<p>{escape(line)}</p>")
    if open_list:
        parts.append("</ul>")
    return Markup("\n".join(parts))
