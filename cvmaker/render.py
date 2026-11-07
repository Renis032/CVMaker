import base64
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from . import editor, settings, web_fetch
from .models import CoverLetter, CVContent, Job, Profile

env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)
env.filters["md_bold"] = lambda s: Markup(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(escape(s))))


def _photo_data_uri() -> str:
    if not settings.PHOTO_PATH.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(settings.PHOTO_PATH.read_bytes()).decode()


def font_choices() -> tuple[float, ...]:
    """Text sizes offered for a tailored CV, around the size set in the CV editor."""
    base = editor.font_size()
    return tuple(round(base + step, 1) for step in (-1.2, -0.9, -0.6, -0.3, 0, 0.4, 0.8, 1.2, 1.6))


def fitting_sizes() -> tuple[float, ...]:
    """Text sizes tried to keep a CV on one page: the editor's size, then a little smaller each time."""
    base = editor.font_size()
    return tuple(round(base - step, 1) for step in (0, 0.3, 0.6, 0.9, 1.2))


def cv_html(cv: CVContent, font_pt: float | None = None, cv_layout: dict | None = None) -> str:
    """The CV as print-ready HTML, drawn by the CV editor's page (its style, and the tailored CV's own version:
    order, hidden parts, wording). Without cv_layout it is the profile, laid out like your first version."""
    if cv_layout is None:
        return editor.print_html(cv, None, font_pt)
    return editor.print_html(cv, cv_layout.get("editor_variant"), font_pt, cv_layout.get("editor_ids", {}))


def page_count(pdf_path: Path) -> int:
    try:
        info = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=30).stdout
    except FileNotFoundError:
        return 1
    match = re.search(r"^Pages:\s+(\d+)", info, re.M)
    return int(match.group(1)) if match else 1


def overflow(pdf_path: Path) -> float:
    """How much of the CV is past page one, as a share of page one's text (0.25 = a quarter page too much)."""
    def chars(first: int, last: int) -> int:
        try:
            out = subprocess.run(
                ["pdftotext", "-f", str(first), "-l", str(last), str(pdf_path), "-"],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except FileNotFoundError:
            return 0
        return len(out.split())

    first_page = chars(1, 1)
    return chars(2, 99) / first_page if first_page else 0.3


def cv_pdf(cv: CVContent, out_path: Path, font_pt: float | None = None, cv_layout: dict | None = None) -> tuple[Path, int, float]:
    """Render to PDF; returns (path, pages, font size used).

    With no font size given, the text shrinks a little if that keeps the CV on one page."""
    if font_pt:
        _print_pdf(cv_html(cv, font_pt, cv_layout), out_path)
        return out_path, page_count(out_path), font_pt
    sizes = fitting_sizes()
    for size in sizes:
        _print_pdf(cv_html(cv, size, cv_layout), out_path)
        if page_count(out_path) == 1:
            return out_path, 1, size
    _print_pdf(cv_html(cv, sizes[0], cv_layout), out_path)
    return out_path, page_count(out_path), sizes[0]


def letter_body(letter: CoverLetter, name: str) -> str:
    """The editable plain text of a cover letter: greeting, paragraphs, sign-off and name."""
    return "\n\n".join([letter.greeting, *letter.paragraphs, f"{letter.closing}\n{name}"])


def letter_html(body: str, profile: Profile, job: Job) -> str:
    c = profile.contact
    return env.get_template("letter.html").render(
        name=profile.name,
        headline=profile.headline,
        contact=[c.email, c.phone, c.linkedin, c.location],
        date=date.today().strftime("%d %B %Y"),
        company=job.company,
        role=job.title,
        paragraphs=[p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()],
    )


def letter_pdf(body: str, profile: Profile, job: Job, out_path: Path) -> Path:
    return _print_pdf(letter_html(body, profile, job), out_path)


# What a page printed to PDF may load: inline styles and scripts, images inlined as data: URLs, Google Fonts.
# Nothing else - so content in a CV can't pull in local files (file://) or reach the network.
PRINT_CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src data: https://fonts.gstatic.com; script-src 'unsafe-inline'"
)


def _with_csp(html: str) -> str:
    meta = f'<meta http-equiv="Content-Security-Policy" content="{PRINT_CSP}">'
    lower = html[:2000].lower()
    at = lower.find("<head>")
    if at >= 0:
        return html[: at + 6] + meta + html[at + 6 :]
    return meta + html


def _print_pdf(html: str, out_path: Path) -> Path:
    html_path = out_path.with_suffix(".html")
    html_path.write_text(_with_csp(html), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="cvmaker-chrome-") as profile:
        result = subprocess.run(
            web_fetch.chrome_args(
                f"--user-data-dir={profile}",
                "--no-pdf-header-footer",
                f"--print-to-pdf={out_path}",
                html_path.as_uri(),
            ),
            capture_output=True,
            text=True,
            timeout=90,
        )
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"PDF rendering failed: {result.stderr[-300:]}")
    return out_path
