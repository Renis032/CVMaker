"""Search: which jobs pass the filters, the keyword score, seniority, and how a search merges its sources."""

from datetime import datetime, timedelta, timezone

from conftest import new_user
from helpers import make_job, make_profile

from cvmaker import db, people, search
from cvmaker.models import Criteria


def _criteria(**over) -> Criteria:
    data = dict(role_keywords=["developer", "engineer"], skill_keywords=["java", "spring"], countries=["BG"])
    data.update(over)
    return Criteria(**data)


def test_contains_matches_whole_words():
    assert search._contains("senior java developer", "java")
    assert not search._contains("javascript developer", "java")
    assert search._contains("c++ and c# work", "c++")       # symbols: plain substring
    assert search._contains("node.js services", "node.js")
    assert not search._contains("anything", "  ")


def test_seniority_from_title_and_tags():
    assert search.seniority(make_job(title="Senior Java Developer")) == "senior"
    assert search.seniority(make_job(title="Java Developer (Jr)")) == "junior"
    assert search.seniority(make_job(title="Java Developer", tags=["Internship"])) == "junior"
    assert search.seniority(make_job(title="Java Developer")) == "mid"
    assert search.seniority(make_job(title="Leadership Coach")) == "mid"   # "lead" only as a whole word


def test_title_must_match_a_role_word_and_no_excluded_word():
    c = _criteria()
    assert search._title_ok(make_job(title="Backend Engineer"), c)
    assert not search._title_ok(make_job(title="Accountant"), c)
    assert not search._title_ok(make_job(title="Engineering Manager"), c)       # "manager" is excluded by default
    assert search._title_ok(make_job(title="Accountant"), _criteria(role_keywords=[]))


def test_location_country_or_allowed_remote():
    c = _criteria()
    assert search._location_ok(make_job(location="Sofia, Bulgaria"), c)
    assert not search._location_ok(make_job(location="Berlin, Germany"), c)
    assert search._location_ok(make_job(location="Remote", remote=True), c)
    assert search._location_ok(make_job(location="", remote=True), c)
    assert search._location_ok(make_job(location="Remote - Europe", remote=True), c)
    assert not search._location_ok(make_job(location="Remote - US only", remote=True), c)
    assert not search._location_ok(make_job(location="Remote", remote=True), _criteria(include_remote=False))


def test_age_limit_keeps_undated_and_unreadable_dates():
    c = _criteria(max_age_days=30)
    now = datetime.now(timezone.utc)
    assert search._age_ok(make_job(posted_at=(now - timedelta(days=5)).isoformat()), c)
    assert not search._age_ok(make_job(posted_at=(now - timedelta(days=45)).isoformat()), c)
    assert not search._age_ok(make_job(posted_at=(now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")), c)
    assert not search._age_ok(make_job(posted_at=(now - timedelta(days=45)).replace(tzinfo=None).isoformat()), c)
    assert search._age_ok(make_job(posted_at=""), c)
    assert search._age_ok(make_job(posted_at="last week"), c)


def test_options():
    office = make_job(title="Senior Java Developer", description="Kafka and Spring.")
    assert not search._options_ok(office, _criteria(remote_only=True))
    assert search._options_ok(make_job(location="Remote"), _criteria(remote_only=True))
    assert not search._options_ok(office, _criteria(seniority=["junior", "mid"]))
    assert search._options_ok(office, _criteria(seniority=["senior"]))
    assert not search._options_ok(office, _criteria(salary_only=True))
    assert search._options_ok(make_job(salary="€5k/month"), _criteria(salary_only=True))
    assert search._options_ok(office, _criteria(must_mention=["kafka"]))
    assert not search._options_ok(office, _criteria(must_mention=["rust"]))
    # a source that lists titles only has no description yet: kept rather than dropped unseen
    assert search._options_ok(make_job(), _criteria(must_mention=["rust"]))


def test_score():
    c = _criteria()
    none = make_job(title="Accountant", description="Ledgers.")
    some = make_job(title="Developer", description="Java services.")
    title = make_job(title="Java Developer", description="Java services.")
    full = make_job(title="Java Spring Developer", description="java spring sql docker postgresql")
    assert search.score(none, c, None) == 0
    assert 0 < search.score(some, c, None) < search.score(title, c, None)
    assert search.score(full, c, make_profile()) == 100                      # capped
    assert search.score(some, c, make_profile()) >= search.score(some, c, None)  # profile skills count too
    assert search.score(full, _criteria(skill_keywords=[]), None) == 0       # nothing to look for


def test_run_search_filters_merges_duplicates_and_saves(monkeypatch):
    _, account = new_user()
    jobs_a = [
        make_job(1, "Java Developer", "Acme", description="short"),
        make_job(2, "Accountant", "Acme"),                                    # wrong title
        make_job(3, "Java Developer", "Elsewhere", location="Tokyo, Japan"),  # wrong place
        make_job(4, "Backend Engineer", "Bluefin", description="We pay a salary of €5,000 - €6,000 per month. Java."),
    ]
    jobs_b = [make_job(5, "Java  developer!", "ACME", description="the longer description wins. java spring")]

    def boom(criteria):
        raise RuntimeError("board is down")

    monkeypatch.setattr(search, "load_companies", lambda: {})
    monkeypatch.setattr(search, "BOARD_SOURCES", {"a": lambda c: jobs_a, "b": lambda c: jobs_b, "c": boom})
    monkeypatch.setattr(search, "board_applies", lambda name, c: True)
    monkeypatch.setattr(search, "board_has_key", lambda name: True)
    seen = []
    with people.in_account(account["id"]):
        result = search.run_search(_criteria(), None, seen.append)
        assert sorted(result.job_ids) == ["t:4", "t:5"]
        assert sorted(result.new_ids) == ["t:4", "t:5"]
        assert result.counts == {"a": 2, "b": 1}
        assert result.errors == ["c: board is down"]
        assert seen[0] == {"a": None, "b": None, "c": None}
        assert seen[-1] == {"a": 2, "b": 1, "c": "error"}
        assert db.get_job("t:4")[0].salary == "€5,000 - €6,000 per month"    # picked out of the text
        assert db.get_job("t:1") is None
        # the same jobs again are not new
        again = search.run_search(_criteria(), None)
        assert again.new_ids == [] and sorted(again.job_ids) == ["t:4", "t:5"]
        # a minimum keyword score drops the weak match
        strict = search.run_search(_criteria(min_score=40), None)
        assert strict.job_ids == ["t:5"]
