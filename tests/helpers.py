"""Small builders shared by the tests of the pure logic (no web client needed)."""

from cvmaker.models import Contact, Experience, Job, Profile, Project, SkillGroup


def make_profile(**over) -> Profile:
    data = dict(
        name="Alex Morgan", headline="Java Developer",
        contact=Contact(email="alex@example.com", phone="+359 88 000 0000", linkedin="", github="", location="Sofia, Bulgaria"),
        summary="Backend developer with 4 years of Java.",
        skills=[SkillGroup(category="Languages", items=["Java", "SQL"]), SkillGroup(category="Tools", items=["Docker", "PostgreSQL"])],
        experience=[Experience(title="Java Developer", company="Acme", start="2021", end="2025", location="Sofia", bullets=[
            "Built REST services with Spring Boot used by 40 teams",
            "Cut build time by 30% with Gradle caching",
        ])],
        projects=[Project(name="Ledger", tech="Java, PostgreSQL", link="", bullets=["Double-entry bookkeeping library"])],
        education=[], soft_skills=["Teamwork"], languages=["English"], interests=[],
        skill_details=[], extra_facts=[], preferences=[],
    )
    data.update(over)
    return Profile(**data)


def make_job(i=1, title="Java Developer", company="Acme", **over) -> Job:
    data = dict(id=f"t:{i}", source="test", company=company, title=title, location="Sofia, Bulgaria")
    data.update(over)
    return Job(**data)
