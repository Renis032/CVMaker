"""Job sources: each one's answer is mapped onto a Job. The fixtures in tests/fixtures are written by hand in the
shape each API returns (the fields the source reads), so a change in how a field is read shows up here; they are
not recordings, and can't tell when a job board changes its API."""

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from cvmaker import sources
from cvmaker.models import Criteria

FIXTURES = Path(__file__).parent / "fixtures"
CRITERIA = Criteria(role_keywords=["developer"], skill_keywords=["java"])


@pytest.fixture
def serve(monkeypatch):
    """serve({url prefix: fixture file}) answers the source's requests from files and records the URLs asked for."""
    asked: list[str] = []

    def install(routes: dict[str, str]):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            asked.append(url)
            for prefix, name in routes.items():
                if url.startswith(prefix):
                    return httpx.Response(200, content=(FIXTURES / name).read_bytes())
            return httpx.Response(404)

        monkeypatch.setattr(sources, "http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
        return asked

    return install


def test_greenhouse(serve):
    asked = serve({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": "greenhouse.json"})
    jobs = sources.greenhouse({"name": "Acme", "slug": "acme"}, CRITERIA)
    assert asked == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"]
    assert [j.id for j in jobs] == ["greenhouse:acme:4012345", "greenhouse:acme:4012346", "greenhouse:acme:4012347"]
    first, remote, bare = jobs
    assert (first.company, first.title, first.location, first.remote) == ("Acme", "Senior Java Developer", "Sofia, Bulgaria", False)
    assert first.url == "https://boards.greenhouse.io/acme/jobs/4012345"
    assert first.description == "About the role\nBuild payment services in Java.\nSpring Boot"   # tags and scripts gone
    assert first.posted_at == "2026-11-02T09:00:00-05:00"
    assert remote.remote and remote.posted_at == "2026-11-11T09:00:00-05:00"                     # falls back to updated_at
    assert (bare.location, bare.url, bare.description) == ("", "", "")


def test_lever(serve):
    serve({"https://api.lever.co/v0/postings/bluefin": "lever.json"})
    first, bare = sources.lever({"name": "Bluefin", "slug": "bluefin"}, CRITERIA)
    assert first.id == "lever:bluefin:a1b2c3d4-0000-4000-8000-000000000001"
    assert (first.title, first.location, first.remote) == ("Backend Engineer", "Berlin", True)
    assert first.description == "We run Kotlin services.\n\nRequirements\nKotlin\nPostgreSQL"
    assert first.tags == ["Platform", "Full-time"]
    assert first.posted_at == datetime.fromtimestamp(1762000000, tz=timezone.utc).isoformat()
    assert first.salary.startswith("€70k-90k")
    assert (bare.title, bare.remote, bare.salary, bare.posted_at, bare.tags) == ("QA Engineer", False, "", "", [])


def test_ashby(serve):
    serve({"https://api.ashbyhq.com/posting-api/job-board/helix": "ashby.json"})
    first, second = sources.ashby({"name": "Helix", "slug": "helix"}, CRITERIA)
    assert (first.id, first.title, first.remote, first.salary) == ("ashby:helix:9f0e", "Platform Engineer", True, "£80K - £100K")
    assert first.posted_at == "2026-11-05T10:00:00.000+00:00"
    assert (second.remote, second.salary, second.description) == (False, "", "SQL.")


def test_recruitee(serve):
    serve({"https://northwind.recruitee.com/api/offers/": "recruitee.json"})
    (job,) = sources.recruitee({"name": "Northwind", "slug": "northwind"}, CRITERIA)
    assert job.id == "recruitee:northwind:77"
    assert job.description == "React and TypeScript.\n3 years"
    assert job.posted_at == "2026-11-01 08:30:00+00:00"
    assert job.salary == "€4,000-5,500/month"
    assert job.tags == ["hybrid"] and not job.remote


def test_personio(serve):
    serve({"https://initech.jobs.personio.de/xml": "personio.xml"})
    (job,) = sources.personio({"name": "Initech", "slug": "initech"}, CRITERIA)
    assert job.id == "personio:initech:555"
    assert (job.title, job.location, job.remote) == ("DevOps Engineer", "Munich, Remote", True)
    assert job.url == "https://initech.jobs.personio.de/job/555"
    assert job.description == "Your tasks\nRun Terraform\n\nYour profile\nAWS experience"
    assert job.tags == ["experienced", "full-time"]


def test_devbg(serve):
    asked = serve({"https://dev.bg/": "devbg.html"})
    jobs = sources.devbg(CRITERIA)
    assert len(asked) == 1 and "s=java" in asked[0]          # fewer than 20 on the page: no second page
    first, second = jobs                                      # the advert without a link is skipped
    assert first.id == "devbg:901"
    assert (first.company, first.title, first.location, first.remote) == ("Acme", "Java Developer", "София, Bulgaria", False)
    assert first.salary == "4 500 - 6 000 лв. gross/month"
    assert first.tags == ["hybrid", "Java", "Spring"]
    assert first.posted_at.endswith("-11-03T00:00:00+00:00")
    assert second.id.startswith("devbg:") and len(second.id) == len("devbg:") + 16     # no id on the page: hash of the link
    assert (second.location, second.remote, second.salary) == ("Remote, Bulgaria", True, "3 000 € net/month")


def test_devbg_dates_are_never_in_the_future():
    now = datetime.now(timezone.utc)
    for text in ("1 яну.", "31 дек.", f"{now.day} {sources.BG_MONTHS[now.month - 1]}."):
        assert datetime.fromisoformat(sources._devbg_date(text)) <= now
    assert sources._devbg_date("yesterday") == "" and sources._devbg_date("31 фев.") == ""


def test_search_terms_put_category_phrases_first():
    c = Criteria(skill_keywords=["Java", "spring", "java"], queries=["qa automation engineer", "JAVA"])
    assert sources.search_terms(c, 5) == ["qa automation engineer", "JAVA", "spring"]
    assert sources.search_terms(c, 1) == ["qa automation engineer"]
    assert sources.search_terms(Criteria(skill_keywords=[]), 5) == ["developer"]
    assert sources.search_terms(Criteria(skill_keywords=[]), 5, ["tester"]) == ["tester"]


def test_a_source_error_is_raised_not_swallowed(serve):
    serve({})
    with pytest.raises(httpx.HTTPStatusError):
        sources.devbg(CRITERIA)
