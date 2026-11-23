"""Every main page renders for a fresh account and for one with a profile and a job (a template or route that
refers to something that no longer exists shows up here as a 500)."""

import pytest
from conftest import new_user
from helpers import make_job, make_profile

from cvmaker import db, people, settings

PAGES = ["/", "/pipeline", "/insights", "/profile", "/roles", "/companies", "/people", "/interview", "/add",
         "/stories", "/chat", "/settings", "/editor", "/api/tasks", "/api/suggest?q=ja", "/export.csv", "/export.xlsx"]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders_for_a_new_account(path):
    client, _ = new_user()
    r = client.get(path)
    assert r.status_code in (200, 303), (path, r.status_code, r.text[:300])


def test_pages_render_with_a_profile_and_a_job():
    client, account = new_user()
    with people.in_account(account["id"]):
        settings.PROFILE_PATH.write_text(make_profile().model_dump_json())
        job = make_job(1, "Senior Java Developer", "Acme", description="Requirements:\n- Java\n- Spring Boot\n\nSalary €5,000 - €6,000 per month.",
                       salary="€5,000 - €6,000 per month", tags=["java"])
        db.upsert_jobs([job], {job.id: 70})
        db.set_status(job.id, "applied")
    for path in [*PAGES, "/job/t:1", "/prep/t:1", "/practice/t:1", "/interview?job_id=t:1", "/?q=java&mode=onsite", "/?q=company:acme"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
    assert "Senior Java Developer" in client.get("/job/t:1").text
    assert "Senior Java Developer" in client.get("/pipeline").text
    assert "Acme" in client.get("/export.csv?pipeline=1").text
    assert client.get("/job/nope").status_code in (200, 404)        # an error page, not a crash


def test_pages_link_css_and_js_that_exist_and_can_be_cached():
    import re

    from conftest import client

    user, _ = new_user()
    anonymous = client()
    login = anonymous.get("/login")
    assert login.status_code == 200
    links = set(re.findall(r'(?:href|src)="(/static/app/[^"]+)"', login.text))
    assert any("auth.css" in x for x in links)                  # the login page needs its styles before anyone logs in
    for path in PAGES[:13]:
        links |= set(re.findall(r'(?:href|src)="(/static/app/[^"]+)"', user.get(path).text))
    assert len(links) > 10
    for link in links:
        r = anonymous.get(link)
        assert r.status_code == 200 and r.content, link
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert r.headers["content-type"].startswith(("text/css", "text/javascript"))
        assert r.headers["x-content-type-options"] == "nosniff"
        # the same file under another (stale) version must not be cached as if it were that version
        assert anonymous.get(link.split("?")[0] + "?v=old").headers["cache-control"] == "no-cache"


def test_static_serves_only_its_own_css_and_js():
    from conftest import client

    c = client()
    for name in ["nope.css", "..%2f..%2fsettings.py", "%2e%2e", "base.css.bak", "../editor/index.html", "..%2feditor%2feditor.js"]:
        assert c.get(f"/static/app/{name}").status_code in (303, 404), name
