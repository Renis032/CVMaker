"""Company logos: a guessed domain only gives its icon when its home page names the company."""

import httpx

from cvmaker import logos

PNG = b"\x89PNG" + b"\x00" * 200


def client(pages: dict[str, str]) -> httpx.Client:
    """Every domain has an icon; `pages` maps a domain to its home page HTML (others answer 404)."""
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if path == "/apple-touch-icon.png" or host == "icons.duckduckgo.com":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if path == "/" and host in pages:
            return httpx.Response(200, text=pages[host], headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_names_company_matches_title_and_site_name():
    assert logos.names_company("Northstar Bank", "<title>Northstar Bank | Personal banking</title>")
    assert logos.names_company("Helix Cloud Ltd", "<title>HelixCloud - developer platform</title>")
    assert logos.names_company("Acme", '<meta property="og:site_name" content="ACME">')
    assert logos.names_company("Sparrow Labs", "<title>Sparrow</title>")  # "labs" is a filler word


def test_names_company_rejects_other_sites():
    assert not logos.names_company("Northstar Bank", "<title>Northstar Outdoor Gear</title>")
    assert not logos.names_company("Orbitly", "<title>This domain is for sale</title>")
    assert not logos.names_company("Orbitly", "<html><body>Orbitly</body></html>")  # only the title counts


def test_guess_needs_the_home_page_to_name_the_company():
    with client({"northstar.com": "<title>Northstar Outdoor Gear</title>"}) as c:
        assert logos.find("Northstar Bank", [], logos._guesses("Northstar Bank", ""), c) is None
    with client({"northstarbank.com": "<title>Northstar Bank</title>"}) as c:
        assert logos.find("Northstar Bank", [], logos._guesses("Northstar Bank", ""), c) == (PNG, "image/png")


def test_known_domain_is_trusted_without_a_home_page():
    with client({}) as c:
        assert logos.find("Northstar Bank", ["nsb.example"], [], c) == (PNG, "image/png")
