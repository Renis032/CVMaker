import ipaddress
import os
import socket
import subprocess
import tempfile
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from . import settings

# Fetching a link a user pasted must not reach this machine or its private network (cloud metadata, admin panels,
# databases). Every request - and every redirect - is checked. Set CVMAKER_ALLOW_PRIVATE_FETCH=1 only for development.
ALLOW_PRIVATE = os.environ.get("CVMAKER_ALLOW_PRIVATE_FETCH", "").lower() in {"1", "true", "yes"}
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}


class BlockedURL(ValueError):
    pass


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_url(url: str) -> str:
    """Raise BlockedURL unless `url` is http(s) on a normal port and its host resolves only to public addresses."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise BlockedURL(f"Only http(s) links can be fetched: {url[:200]}")
    if ALLOW_PRIVATE:
        return url
    try:
        port = parts.port
    except ValueError as e:
        raise BlockedURL(f"Bad port in {url[:200]}") from e
    if port not in ALLOWED_PORTS:
        raise BlockedURL(f"Links on port {port} can't be fetched")
    try:
        infos = socket.getaddrinfo(parts.hostname, port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise BlockedURL(f"Unknown host {parts.hostname}") from e
    if not infos or not all(_public(info[4][0]) for info in infos):
        raise BlockedURL(f"{parts.hostname} is a private or local address - it can't be fetched")
    return url


def guard_request(request: httpx.Request) -> None:
    check_url(str(request.url))


def http() -> httpx.Client:
    """An HTTP client for addresses from the internet (job boards, postings, career pages): private addresses are
    refused, also after redirects."""
    return httpx.Client(
        headers={"User-Agent": settings.USER_AGENT}, timeout=30, follow_redirects=True, max_redirects=8,
        event_hooks={"request": [guard_request]},
    )


def chrome_args(*extra: str) -> list[str]:
    """Headless Chrome with its sandbox on (unless CVMAKER_CHROME_NO_SANDBOX, for containers that are the sandbox),
    a throw-away profile and no background traffic."""
    args = [
        settings.CHROME_BIN, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-sync", "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
        "--mute-audio",
    ]
    if settings.CHROME_NO_SANDBOX:
        args.append("--no-sandbox")
    return [*args, *extra]


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
    check_url(url)
    if not render:
        with http() as c:
            r = c.get(url)
            r.raise_for_status()
            return r.text[:5_000_000]
    # Chrome itself can't be limited per request: in server mode run it where the network policy blocks private
    # addresses (see Dockerfile / README). The first address is checked here.
    with tempfile.TemporaryDirectory(prefix="cvmaker-chrome-") as profile:
        result = subprocess.run(
            chrome_args(
                f"--user-data-dir={profile}",
                f"--user-agent={settings.USER_AGENT}",
                "--virtual-time-budget=15000",
                "--dump-dom",
                url,
            ),
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
