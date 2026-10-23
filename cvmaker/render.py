import base64
import re
import subprocess
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from . import settings
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


FONT_SIZES_PT = (9.6, 9.3, 9.0, 8.7)


def cv_html(cv: CVContent, font_pt: float = FONT_SIZES_PT[0]) -> str:
    return env.get_template("cv.html").render(cv=cv, photo=_photo_data_uri(), font_pt=font_pt)


def _page_count(pdf_path: Path) -> int:
    try:
        info = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=30).stdout
    except FileNotFoundError:
        return 1
    match = re.search(r"^Pages:\s+(\d+)", info, re.M)
    return int(match.group(1)) if match else 1


def cv_pdf(cv: CVContent, out_path: Path) -> Path:
    """Render to PDF, shrinking the text a little if that keeps the CV on one page."""
    for font_pt in FONT_SIZES_PT:
        _print_pdf(cv_html(cv, font_pt), out_path)
        if _page_count(out_path) == 1:
            return out_path
    return _print_pdf(cv_html(cv), out_path)


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


def _print_pdf(html: str, out_path: Path) -> Path:
    html_path = out_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    result = subprocess.run(
        [
            settings.CHROME_BIN,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out_path}",
            html_path.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"PDF rendering failed: {result.stderr[-300:]}")
    return out_path
