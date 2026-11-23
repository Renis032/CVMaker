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
