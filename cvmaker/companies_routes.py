"""The company list: find a company's careers page or hiring platform, add, test and remove companies."""


from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from . import companies
from .sources import COMPANY_SOURCES
from .web import _back, page

router = APIRouter()


# --- companies -----------------------------------------------------------------------------------------------


@router.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request):
    return page(
        request,
        "companies.html",
        groups=companies.load(),
        types=list(COMPANY_SOURCES),
        found=None,
        tested=None,
    )


@router.post("/companies/discover", response_class=HTMLResponse)
def companies_discover(request: Request, name: str = Form(...), url: str = Form(...), group: str = Form("BG")):
    error = ""
    found = postings = None
    try:
        found = companies.discover(url.strip(), name.strip())
        postings = companies.test(companies.entry(name.strip(), found))
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return page(
        request,
        "companies.html",
        groups=companies.load(),
        types=list(COMPANY_SOURCES),
        found=found,
        postings=postings,
        error=error,
        form={"name": name.strip(), "url": url.strip(), "group": group.strip().upper()},
        tested=None,
    )


@router.post("/companies/add")
def companies_add(
    name: str = Form(...),
    group: str = Form(...),
    type: str = Form(...),
    slug: str = Form(""),
    url: str = Form(""),
    render_js: bool = Form(False),
    default_location: str = Form(""),
):
    found = {"type": type, "slug": slug.strip(), "url": url.strip(), "render": render_js}
    companies.add(group.strip().upper() or "REMOTE", companies.entry(name.strip(), found, default_location.strip()))
    return _back("/companies")


@router.post("/companies/remove")
def companies_remove(group: str = Form(...), name: str = Form(...)):
    companies.remove(group, name)
    return _back("/companies")


@router.post("/companies/test", response_class=HTMLResponse)
def companies_test(request: Request, group: str = Form(...), name: str = Form(...)):
    company = next((c for c in companies.load().get(group, []) if c["name"] == name), None)
    tested = {"name": name, "error": "", "postings": []}
    try:
        tested["postings"] = companies.test(company)
    except Exception as e:
        tested["error"] = f"{type(e).__name__}: {e}"
    return page(request, "companies.html", groups=companies.load(), types=list(COMPANY_SOURCES), found=None, tested=tested)
