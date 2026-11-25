"""Deep dive into a suggested project: the brief, its page, the prompt for Claude and the .md download."""

from conftest import new_user
from helpers import make_job, make_profile

from cvmaker import db, deepdive, llm, people, settings, tasks
from cvmaker.models import CVProjectType, FitAssessment, GapPlan, Milestone, ProjectBrief, TechChoice

SEED = {"title": "Payments ledger API", "description": "A double-entry ledger service.", "demonstrates": ["Kafka", "AWS"],
        "why_employers_care": "Payments teams run ledgers."}


def brief(title: str = "Payments ledger API") -> ProjectBrief:
    return ProjectBrief(
        title=title, one_liner="A ledger service with an audit trail.", why_this_job=["Proves Kafka (required)"],
        what_you_build="A Spring Boot service...", in_scope=["Transfers"], out_of_scope=["A web UI"],
        tech_stack=[TechChoice(name="Spring Boot", why="the job's framework")], architecture=["API -> Kafka -> ledger"],
        data_model=["Account(id, balance)"], interfaces=["POST /transfers"],
        milestones=[Milestone(title="Skeleton", goal="It runs", tasks=["mvn archetype"], done_when=["GET /health is 200"])],
        repo_layout=["src/main/java"], testing=["Testcontainers for Kafka"], ci_and_deploy=["GitHub Actions"],
        readme_outline=["Why"], demo=["A recorded transfer"], learn_first=["Idempotency"],
        cv_bullets=["Built a **ledger** handling [N] transfers/s"], interview_points=["Why Kafka"], pitfalls=["Floats for money"],
    )


def setup_job():
    client, account = new_user()
    with people.in_account(account["id"]):
        settings.PROFILE_PATH.write_text(make_profile().model_dump_json())
        job = make_job(1, "Senior Java Developer", "Acme", description="Java, Kafka, AWS.")
        db.upsert_jobs([job], {job.id: 70})
        db.cache_put("fit2:t:1", FitAssessment(
            rating=7, verdict="Good fit.", pros=[], cons=[],
            gap_plan=GapPlan(recommendation="Apply", advice="", lacking_areas=[], project_types=[CVProjectType(**SEED)],
                             action_plan=[], interview_framing=[], closer_roles=[]),
        ).model_dump())
    return client, account


def test_deep_dive_end_to_end(monkeypatch):
    client, account = setup_job()
    seen = []
    monkeypatch.setattr(llm, "ask", lambda system, text, schema, **kw: seen.append(text) or brief())
    # the job page offers a deep dive for the suggestion
    page = client.get("/job/t:1").text
    assert 'action="/job/t:1/project/deepdive"' in page and 'value="Payments ledger API"' in page

    r = client.post("/job/t:1/project/deepdive", data={"title": "Payments ledger API", "size": "session", "mode": "guided"})
    k = deepdive.key("Payments ledger API", "session", "guided")
    assert r.status_code == 303 and r.headers["location"].endswith(f"/job/t:1/project/{k}")
    with people.in_account(account["id"]):
        tasks.run_inline("deepdive", {"job_id": "t:1", "title": "Payments ledger API", "size": "session", "mode": "guided"})
    assert "A double-entry ledger service." in seen[0] and "one session of about an hour" in seen[0] and "writes the code themselves" in seen[0]

    html = client.get(f"/job/t:1/project/{k}").text
    assert "Payments ledger API" in html and "Mentor me while I build" in html and "Floats for money" in html
    md = client.get(f"/job/t:1/project/{k}.md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]
    assert "## Milestone 1: Skeleton" in md.text and "GET /health is 200" in md.text
    # the job page now links to it
    assert f"/job/t:1/project/{k}" in client.get("/job/t:1").text


def test_claude_code_prompt_and_unknown_ids():
    job = make_job(1, "Senior Java Developer", "Acme")
    text = deepdive.prompt(brief(), job, "afternoon", "claude-code")
    assert text.startswith("# Build this project: Payments ledger API") and "commit" in text
    assert "Concepts I need to understand" not in text  # only for guided builds
    client, _ = setup_job()
    # a key with no deep dive (and no running task) is an error page, not a crash
    assert "Mentor me" not in client.get("/job/t:1/project/0123456789ab").text
    assert client.get("/job/t:1/project/0123456789ab").status_code in (200, 404)
    # only suggested projects can be deep-dived (the title comes from the form)
    r = client.post("/job/t:1/project/deepdive", data={"title": "Not a suggestion"})
    assert r.status_code != 303 and "no longer among the suggestions" in r.text


def test_deep_dive_made_with_an_old_size_still_opens():
    client, account = setup_job()
    with people.in_account(account["id"]):
        db.cache_put(deepdive.cache_key("t:1", "aaaaaaaaaaaa"), {"brief": brief().model_dump(), "title": "Payments ledger API",
                                                                 "size": "weeks", "mode": "claude-code", "source": "fit"})
    r = client.get("/job/t:1/project/aaaaaaaaaaaa")
    assert r.status_code == 200 and "An afternoon" in r.text
