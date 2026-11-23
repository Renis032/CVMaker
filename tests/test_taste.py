"""Learning from feedback: hide reasons and likes move the right features, and suggest the right filter changes."""

import pytest
from conftest import new_user
from helpers import make_job, make_profile

from cvmaker import db, people, taste
from cvmaker.models import Criteria


@pytest.fixture
def account():
    _, acc = new_user()
    with people.in_account(acc["id"]):
        yield acc


def _react(job, status="hidden", reason=""):
    db.upsert_jobs([job], {job.id: 50})
    db.set_status(job.id, status)
    if reason:
        db.set_feedback(job.id, reason)
    return job


def test_features():
    job = make_job(title="Senior PHP Developer", company="  Acme  Corp ", location="Plovdiv, Bulgaria",
                   description="Laravel shop. Spring Boot on the side.")
    feats = taste.features(job, ["php", "spring", "spring boot", "java"])
    assert feats == {
        "level:senior": "senior roles", "company:acme corp": "  Acme  Corp ", "loc:plovdiv": "Plovdiv",
        "salary:none": "no salary stated", "tech:php": "php", "tech:spring boot": "spring boot",   # not "spring" as well
    }
    remote = taste.features(make_job(2, location="Remote", remote=True, salary="€5k"), [])
    assert "loc:remote" in remote and "salary:none" not in remote


def test_too_senior_lowers_senior_jobs_and_is_explained(account):
    profile = make_profile()
    _react(make_job(1, "Senior Java Developer", "Acme"), reason="too_senior")
    _react(make_job(2, "Staff Engineer", "Bluefin"), reason="too_senior")
    model = taste.learn(profile)
    assert model["weights"]["level:senior"] == -2.0
    assert model["hidden_levels"] == {"senior": 2}
    down, why = taste.score_job(make_job(3, "Principal Engineer", "Helix"), None, model)
    assert down == -20 and why == ["↓ senior roles"]
    assert taste.score_job(make_job(4, "Java Developer", "Helix"), None, model) == (0, [])


def test_wrong_stack_blames_the_tech_you_lack_in_the_title(account):
    profile = make_profile()   # has Java, SQL, Docker, PostgreSQL
    _react(make_job(1, "Angular Developer", "Acme", description="Angular, Java, SQL, agile, git and some Rust."), reason="stack")
    model = taste.learn(profile)
    assert model["stack_terms"] == {"angular": 1} and model["stack_title_terms"] == {"angular": 1}
    assert model["weights"]["tech:angular"] == -1.0
    assert "tech:java" not in model["weights"] and "tech:rust" not in model["weights"] and "tech:git" not in model["weights"]
    # nothing foreign in the title: the foreign tech in the description shares the blame
    _react(make_job(2, "Backend Developer", "Bluefin", description="Rust and Scala services, Java too."), reason="stack")
    model = taste.learn(profile)
    assert model["weights"]["tech:rust"] == pytest.approx(-1 / 2 ** 0.5) == model["weights"]["tech:scala"]
    assert model["stack_title_terms"] == {"angular": 1}


def test_features_are_not_shared_between_accounts_or_profiles():
    """The same job id with different text, or the same job with other skills looked for, is computed afresh."""
    assert "loc:varna" in taste.features(make_job(1, location="Varna"), ["java"])
    assert "loc:burgas" in taste.features(make_job(1, location="Burgas"), ["java"])
    job = make_job(2, description="Java and Rust.")
    assert "tech:java" in taste.features(job, ["java"])
    assert "tech:rust" in taste.features(job, ["rust"]) and "tech:java" not in taste.features(job, ["rust"])


def test_location_salary_and_company_reasons(account):
    _react(make_job(1, "Java Developer", "Acme", location="Varna, Bulgaria", salary="€2k/month"), reason="location")
    _react(make_job(2, "Java Developer", "Bluefin"), reason="salary")                # no salary stated
    _react(make_job(3, "Java Developer", "Helix", salary="€1,500/month"), reason="salary")
    _react(make_job(4, "Java Developer", "Initech"), reason="company")
    with db.connect() as c:
        c.execute("UPDATE jobs SET salary_month = 1500 WHERE id = 't:3'")
    model = taste.learn(None)
    w = model["weights"]
    assert (w["loc:varna"], w["salary:none"], w["company:initech"]) == (-1.0, -1.0, -3.0)
    assert model["low_salary"] == 1500
    assert taste.score_job(make_job(5, "Java Developer", "Other", salary="€1,400"), 1400, model) == (-10, ["↓ salary like ones you hid"])
    assert taste.score_job(make_job(6, "Java Developer", "Other", salary="€4,000"), 4000, model) == (0, [])
    assert taste.score_job(make_job(7, "Java Developer", "Initech", salary="€4,000"), 4000, model)[0] == -30   # at the limit


def test_likes_raise_similar_jobs_and_a_plain_hide_lowers_a_little(account):
    profile = make_profile()
    _react(make_job(1, "Java Developer", "Acme", location="Sofia, Bulgaria", description="Java and Docker."), status="applied")
    _react(make_job(2, "Sales Engineer", "Bluefin", location="Burgas"))               # hidden without a reason
    model = taste.learn(profile)
    w = model["weights"]
    assert w["loc:sofia"] == 0.5 and w["tech:java"] == pytest.approx(0.5 / 2 ** 0.5)
    assert w["loc:burgas"] == -0.2
    assert "salary:none" not in w                                                      # only an explicit reason moves it
    assert model["liked_titles"] == ["Java Developer"] and model["reasons"] == {"none": 1}
    up, why = taste.score_job(make_job(3, "Java Developer", "Helix", location="Sofia"), None, model)
    assert up > 0 and "↑ Sofia" in why


def test_suggestions_follow_repeated_feedback(account):
    profile = make_profile()
    for i, title in enumerate(["Senior Java Developer", "Lead Java Engineer"]):
        _react(make_job(i, title, f"Co{i}"), reason="too_senior")
        _react(make_job(10 + i, f"Angular Developer {i}", f"Shop{i}", description="Angular."), reason="stack")
    for i in range(3):
        _react(make_job(20 + i, "Java Developer", f"Quiet{i}"), reason="salary")
    model = taste.learn(profile)
    criteria = Criteria()
    found = {s["id"]: s for s in taste.suggestions(criteria, model)}
    assert set(found) == {"seniority:senior", "exclude:angular", "salary_only"}

    taste.dismiss("exclude:angular")
    assert "exclude:angular" not in {s["id"] for s in taste.suggestions(criteria, model)}

    for s in found.values():
        criteria = taste.apply_suggestion(criteria, s["kind"], s["value"])
    assert criteria.seniority == ["junior", "mid"] and "angular" in criteria.exclude and criteria.salary_only
    assert {s["id"] for s in taste.suggestions(criteria, model)} == set()              # already applied: not offered again


def test_blocked_companies(account):
    assert not taste.is_blocked("Acme")
    taste.block_company(" Acme  Corp ")
    taste.block_company("acme corp")
    assert taste.blocked_companies() == ["Acme  Corp"]
    assert taste.is_blocked("ACME CORP") and not taste.is_blocked("Acme")
    taste.unblock_company("Acme Corp")
    assert taste.blocked_companies() == []
