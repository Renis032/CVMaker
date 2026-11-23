"""Posting checks that need no Claude: is it still open, and the warning signs in the text."""

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from helpers import make_job, make_profile

from cvmaker import checks, db
from cvmaker.models import Contact


def _answers(monkeypatch, handler):
    monkeypatch.setattr(checks, "http", lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))


LONG_PAGE = "<html><body><h1>Java Developer</h1>" + "<p>We build payroll software and you will work on it.</p>" * 20 + "</body></html>"


@pytest.mark.parametrize("job_id, status, body, expected", [
    ("greenhouse:acme:1", 200, {}, "open"),
    ("greenhouse:acme:1", 404, {}, "closed"),
    ("lever:acme:1", 410, {}, "closed"),
    ("smartrecruiters:acme:1", 200, {"active": False}, "closed"),
    ("smartrecruiters:acme:1", 200, {"active": True}, "open"),
    ("ashby:acme:1", 200, {"jobs": [{"id": "1"}]}, "open"),
    ("ashby:acme:1", 200, {"jobs": [{"id": "2"}]}, "closed"),
])
def test_asks_the_job_boards_own_api(monkeypatch, job_id, status, body, expected):
    _answers(monkeypatch, lambda request: httpx.Response(status, json=body))
    assert checks.check_live(make_job(id=job_id, url="https://example.com/job"))[0] == expected


@pytest.mark.parametrize("status, html, expected", [
    (200, LONG_PAGE, "open"),
    (404, "gone", "closed"),
    (410, "gone", "closed"),
    (503, "busy", "unknown"),
    (200, LONG_PAGE + "<p>This position has been filled.</p>", "closed"),
    (200, LONG_PAGE + "<p>Обявата е изтекла</p>", "closed"),
    (200, "<div id=root></div>", "unknown"),            # a page built by JavaScript says nothing
])
def test_loads_the_posting_when_there_is_no_api(monkeypatch, status, html, expected):
    _answers(monkeypatch, lambda request: httpx.Response(status, html=html))
    assert checks.check_live(make_job(url="https://example.com/job"))[0] == expected


def test_redirect_to_an_expired_page_is_closed(monkeypatch):
    def handler(request):
        if request.url.path == "/job":
            return httpx.Response(302, headers={"location": "https://example.com/jobs/expired"})
        return httpx.Response(200, html=LONG_PAGE)

    _answers(monkeypatch, handler)
    assert checks.check_live(make_job(url="https://example.com/job"))[0] == "closed"


def test_unknown_when_it_cannot_be_checked(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("no route")

    _answers(monkeypatch, refuse)
    assert checks.check_live(make_job(url="https://example.com/job"))[0] == "unknown"
    assert checks.check_live(make_job(id="x", url=""))[0] == "unknown"
    # LinkedIn and jobs.bg are never loaded
    _answers(monkeypatch, lambda request: pytest.fail("must not be requested"))
    assert checks.check_live(make_job(url="https://www.linkedin.com/jobs/view/1"))[0] == "unknown"
    assert checks.check_live(make_job(url="https://www.jobs.bg/job/1"))[0] == "unknown"


@pytest.fixture
def no_other_listings(monkeypatch):
    monkeypatch.setattr(db, "similar_jobs", lambda *a: [])


def _flags(description, profile=None, app=None, **job):
    return checks.local_flags(make_job(description=description, **job), profile, app)


def test_work_permit_wording_is_compared_with_where_you_live(no_other_listings):
    in_sofia = make_profile()
    us = _flags("You must be authorized to work in the United States. We do not offer visa sponsorship.", in_sofia)
    assert [(f["finding"], f["severity"]) for f in us] == [("Work authorisation required", "amber"), ("No visa sponsorship", "amber")]
    eu = _flags("Candidates must be eligible to work in the EU.", in_sofia)
    assert [(f["finding"], f["severity"]) for f in eu] == [("Work authorisation required", "info")]
    bg = _flags("You must be based in Bulgaria or Romania.", in_sofia)
    assert bg[0]["severity"] == "info"
    elsewhere = make_profile(contact=Contact(email="", phone="", linkedin="", github="", location="Belgrade, Serbia"))
    assert _flags("Candidates must be eligible to work in the EU.", elsewhere)[0]["severity"] == "amber"
    no_home = _flags("US citizens only. Security clearance needed.")
    assert {f["finding"] for f in no_home} == {"Citizenship required", "Security clearance"}
    assert all("Add your location" in f["what_to_do"] for f in no_home)


def test_scam_wording_comes_first(no_other_listings):
    flags = _flags("No visa sponsorship. Pay the training fee, then the interview is held over Telegram chat.", make_profile())
    assert [f["severity"] for f in flags] == ["red", "red", "amber"]
    assert {f["finding"] for f in flags[:2]} == {"Asks for a fee", "Interview over a chat app"}
    assert flags[0]["evidence"].startswith("Pay the training fee")


def test_an_ordinary_posting_has_no_flags(no_other_listings):
    assert _flags("We build payroll software in Java. Hybrid in Sofia, 25 days of leave.", make_profile()) == []


def test_reposts_and_old_postings(monkeypatch):
    earlier = [{"id": "t:9", "first_seen": time.time() - 90 * 86400, "closed_at": time.time() - 30 * 86400}]
    monkeypatch.setattr(db, "similar_jobs", lambda *a: earlier)
    (flag,) = _flags("Java.")
    assert (flag["finding"], flag["severity"], flag["links"]) == ("Reposted after closing", "amber", ["t:9"])
    earlier[0]["closed_at"] = None
    assert _flags("Java.")[0]["finding"] == "Listed more than once"

    monkeypatch.setattr(db, "similar_jobs", lambda *a: [])
    now = datetime.now(timezone.utc)
    refreshed = _flags("Java.", app={"first_seen": time.time() - 40 * 86400}, posted_at=(now - timedelta(days=2)).isoformat())
    assert [f["finding"] for f in refreshed] == ["Posting date refreshed"]
    old = _flags("Java.", app={"first_seen": time.time() - 80 * 86400}, posted_at=(now - timedelta(days=80)).isoformat())
    assert [f["finding"] for f in old] == ["Open for about 80 days"]
    assert _flags("Java.", app={"first_seen": time.time() - 86400}, posted_at=(now - timedelta(days=2)).isoformat()) == []
