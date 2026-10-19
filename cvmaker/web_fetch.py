import subprocess
from html import unescape
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from . import settings


def http() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.USER_AGENT}, timeout=30, follow_redirects=True
    )


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(unescape(html), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def fetch_html(url: str, render: bool = False) -> str:
    """Fetch a page; with render=True, run headless Chrome so JavaScript-built listings appear."""
    if not render:
        with http() as c:
            r = c.get(url)
            r.raise_for_status()
            return r.text
    result = subprocess.run(
        [
            settings.CHROME_BIN,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-agent={settings.USER_AGENT}",
            "--virtual-time-budget=15000",
            "--dump-dom",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"Chrome failed to render {url}: {result.stderr[-300:]}")
    return result.stdout


def page_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        text = " ".join(a.get_text(" ").split())
        if not text or href in seen or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        seen.add(href)
        links.append((text, href))
    return links
