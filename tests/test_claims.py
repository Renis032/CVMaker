"""The skill check: a technology named in an outgoing document must be backed by the profile or GitHub."""

from helpers import make_job, make_profile

from cvmaker import claims
from cvmaker.models import SkillGroup


def _terms(text, evidence, job=None):
    return [f["term"] for f in claims.unsupported(text, make_profile(), evidence, job)]


EVIDENCE = "java, sql, docker, postgresql; built rest services with spring boot"


def test_flags_tech_the_profile_does_not_show():
    found = claims.unsupported("I shipped services in Java and Kotlin on Kubernetes.", make_profile(), EVIDENCE)
    assert sorted(f["term"] for f in found) == ["Kotlin", "Kubernetes"]
    assert all(f["sentence"] == "I shipped services in Java and Kotlin on Kubernetes." for f in found)
    assert claims.problems(found) == found


def test_backed_terms_are_not_flagged():
    assert _terms("Java, SQL, Docker and PostgreSQL every day.", EVIDENCE) == []


def test_keeps_the_spelling_used_in_the_text():
    assert _terms("Provisioned with Terraform.", EVIDENCE) == ["Terraform"]


def test_whole_words_only():
    # "java" must not back "javascript", and "go" inside "going" is not a claim.
    assert _terms("Frontend in JavaScript.", "java") == ["JavaScript"]
    assert _terms("I am going to rest now.", "") == []


def test_longer_term_wins_over_the_one_inside_it():
    assert _terms("Secured it with Spring Security.", "java") == ["Spring Security"]
    # ... and backing the longer term is enough
    assert _terms("Built with Spring Boot.", EVIDENCE) == []


def test_aliases_and_repo_files_count_as_evidence():
    assert _terms("Deployed to Kubernetes.", "ran k8s clusters") == []
    assert _terms("Builds with Maven and Docker.", "files: pom.xml dockerfile") == []
    assert _terms("Typed with TypeScript.", "repo has tsconfig.json") == []
    assert _terms("Postgres-backed: PostgreSQL 16.", "used postgres") == []


def test_saying_you_have_not_used_it_is_honest():
    found = claims.unsupported("I have not yet worked with Angular, but I learn fast.\nI led a Kafka migration.", make_profile(), EVIDENCE)
    by_term = {f["term"]: f for f in found}
    assert by_term["Angular"]["honest"] is True
    assert by_term["Kafka"]["honest"] is False
    assert [f["term"] for f in claims.problems(found)] == ["Kafka"]


def test_naming_the_job_is_not_a_claim():
    job = make_job(title="Kotlin Developer", company="Redis Labs")
    text = "I am applying for the Kotlin Developer role at Redis Labs."
    assert _terms(text, EVIDENCE, job) == []
    assert sorted(_terms(text, EVIDENCE)) == ["Kotlin", "Redis"]
    # outside the title it still counts
    assert _terms(text + " I know Kotlin well.", EVIDENCE, job) == ["Kotlin"]


def test_general_words_are_not_claims():
    assert _terms("Strong on security, performance, agile delivery and cloud systems. Fluent English.", "") == []


def test_markdown_bold_is_ignored():
    assert _terms("Led the **Kafka** rollout.", EVIDENCE) == ["Kafka"]


def test_no_profile_or_no_text_gives_nothing():
    assert claims.unsupported("Kotlin", None) == []
    assert claims.unsupported("", make_profile(), EVIDENCE) == []


def test_evidence_comes_from_the_whole_profile(monkeypatch):
    monkeypatch.setattr(claims, "_repo_text", lambda profile: "README: deployed with Helm on kubernetes")
    profile = make_profile(extra_facts=["Used Kafka for event streaming at Acme"])
    found = claims.check_many({"letter": "Kafka, Kubernetes, Spring Boot and Rust.", "empty": ""}, profile)
    assert list(found) == ["letter"]
    assert [f["term"] for f in found["letter"]] == ["Rust"]


def test_add_skill_goes_to_its_own_group_once():
    profile = make_profile()
    claims.add_skill(profile, " Kafka ")
    claims.add_skill(profile, "kafka")
    claims.add_skill(profile, "java")       # already a skill
    claims.add_skill(profile, "  ")
    assert profile.skills[-1] == SkillGroup(category="Added by you", items=["Kafka"])
    claims.add_skill(profile, "Rust")
    assert profile.skills[-1].items == ["Kafka", "Rust"]
    assert len([g for g in profile.skills if g.category == "Added by you"]) == 1
