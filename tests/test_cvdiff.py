"""What a tailored CV changed, and the facts in it that the profile doesn't hold."""

import pytest
from helpers import make_profile

from cvmaker import claims, cvdiff
from cvmaker.models import CVContent, SkillGroup


@pytest.fixture(autouse=True)
def _no_github(monkeypatch):
    monkeypatch.setattr(claims, "_repo_text", lambda profile: "")


def _cv(profile, **over) -> CVContent:
    cv = CVContent.model_validate(profile.model_dump(include=set(CVContent.model_fields)))
    for k, v in over.items():
        setattr(cv, k, v)
    return cv


def test_word_diff_marks_and_escapes():
    assert cvdiff.word_diff("Built REST services", "Built fast REST APIs") == "Built <ins>fast</ins> REST <del>services</del> <ins>APIs</ins>"
    assert "<b>" not in cvdiff.word_diff("a", "<b>x</b>") and "&lt;b&gt;" in cvdiff.word_diff("a", "<b>x</b>")


def test_unchanged_cv_has_no_changes():
    profile = make_profile()
    d = cvdiff.diff(profile, _cv(profile))
    assert d["counts"] == {"same": 3, "reworded": 0, "new": 0, "dropped": 0}
    assert d["headline"] is None and d["summary"] is None
    assert not (d["skills_added"] or d["skills_removed"] or d["unsupported_skills"] or d["unsupported_numbers"] or d["unsupported_tech"])
    assert not d["experience"]["reordered"] and not d["experience"]["removed"]


def test_reworded_new_dropped_and_moved_bullets():
    profile = make_profile()
    cv = _cv(profile)
    cv.experience[0].bullets = [
        "Mentored two junior developers",                               # new
        "Built REST services with Spring Boot used by 40 product teams",  # reworded, and moved down
    ]                                                                    # "Cut build time..." dropped
    d = cvdiff.diff(profile, cv)
    bullets = d["experience"]["entries"][0]["bullets"]
    assert [b["kind"] for b in bullets] == ["new", "reworded"]
    assert bullets[1]["moved"] and "<ins>product</ins>" in bullets[1]["diff"]
    assert d["experience"]["entries"][0]["dropped"] == ["Cut build time by 30% with Gradle caching"]
    assert d["counts"] == {"same": 1, "reworded": 1, "new": 1, "dropped": 1}


def test_removed_sections_count_their_bullets_as_dropped():
    profile = make_profile()
    d = cvdiff.diff(profile, _cv(profile, projects=[]))
    assert [p.name for p in d["projects"]["removed"]] == ["Ledger"]
    assert d["counts"]["dropped"] == 1


def test_skills_added_and_removed():
    profile = make_profile(extra_facts=["Uses Kafka at work"])
    cv = _cv(profile, skills=[SkillGroup(category="Languages", items=["Java", "Kafka", "Rust"])])
    d = cvdiff.diff(profile, cv)
    assert d["skills_added"] == ["Kafka", "Rust"]
    assert sorted(d["skills_removed"]) == ["Docker", "PostgreSQL", "SQL"]
    assert d["unsupported_skills"] == ["Rust"]           # Kafka is in the profile's facts
    assert d["unsupported_tech"] == []                   # Rust is reported once, not twice


def test_numbers_must_come_from_the_profile():
    profile = make_profile()
    cv = _cv(profile, summary="Backend developer with 6 years of Java, serving 40 teams.")
    cv.experience[0].bullets[1] = "Cut build time by 55% with Gradle caching"
    d = cvdiff.diff(profile, cv)
    assert [n for n, _ in d["unsupported_numbers"]] == ["6", "55%"]
    assert d["unsupported_numbers"][1][1] == "Cut build time by 55% with Gradle caching"


def test_tech_named_in_a_bullet_is_checked_too():
    profile = make_profile()
    cv = _cv(profile)
    cv.experience[0].bullets[0] = "Built REST services with Spring Boot and Kubernetes used by 40 teams"
    d = cvdiff.diff(profile, cv)
    assert [f["term"] for f in d["unsupported_tech"]] == ["Kubernetes"]
