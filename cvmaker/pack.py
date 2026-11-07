"""Application packs: everything for one job, made without asking, for jobs whose keyword score is high enough.

A pack is the fit check, a tailored one-page CV, a cover letter that matches it, interview prep and the GitHub
review with projects worth building - the same documents the job page makes one button at a time. Each pack is
also written to data/packs/<job>/ (CV.pdf, CoverLetter.pdf, PACK.md) so it can be opened without the app.

The single steps live here too, so the job page buttons and the packs make documents the same way.
"""

import shutil
import time
from pathlib import Path

from . import assistant, db, github, render, settings, taste, tasks
from .models import CVContent, FitAssessment, GithubReview, InterviewPrep, Job, Profile, RoleProfile, TailoredCV
from .sources import ensure_description


def packs_dir():
    return settings.DATA_DIR / "packs"

MAX_SHORTEN_ATTEMPTS = 2
APP_URL = "http://127.0.0.1:8000"


# --- the single steps (also used by the job page) ---------------------------------------------------------


def fit_check(profile: Profile, job: Job, progress=None) -> FitAssessment:
    if progress:
        progress.set(step="reading your GitHub")
    try:
        repos_text = github.repos_for_prompt(github.fetch_repos(github.username(profile)), compact=True)
    except Exception:
        repos_text = ""
    if progress:
        progress.set(step="Claude is judging your fit")
    fit = assistant.assess_fit(profile, job, repos_text, taste.for_prompt(profile))
    db.cache_put(f"fit2:{job.id}", fit.model_dump())
    db.set_fit(job.id, fit.rating, {"strong": [], "missing": [], "verdict": fit.verdict, "quick": False})
    return fit


def github_check(profile: Profile, job: Job, repos: list[dict]) -> GithubReview:
    review = assistant.github_review(profile, job, github.repos_for_prompt(repos))
    db.cache_put(f"ghreview:{job.id}", review.model_dump())
    return review


def render_fitting(tailored: TailoredCV, pdf_path: Path, layout: dict, job: Job | None, progress=None) -> tuple[int, float]:
    """Render the CV; when it must fit on one page and does not, have Claude shorten it and render again.
    Updates tailored.cv in place. Returns (pages, font size used)."""
    _, pages, used = render.cv_pdf(tailored.cv, pdf_path, layout.get("font_pt"), layout)
    attempt = 0
    while layout.get("one_page") and pages > 1 and attempt < MAX_SHORTEN_ATTEMPTS:
        attempt += 1
        if progress:
            progress.set(step=f"shortening to one page (attempt {attempt})")
        tailored.cv = assistant.shorten_cv(tailored.cv, job, render.overflow(pdf_path), layout.get("instructions", ""))
        _, pages, used = render.cv_pdf(tailored.cv, pdf_path, layout.get("font_pt"), layout)
    if attempt:
        tailored.changes_made.append(
            f"Shortened by Claude to fit one page ({attempt} pass{'es' if attempt > 1 else ''})"
            + ("" if pages == 1 else " - still over one page; try a smaller font or edit it")
        )
    return pages, used


def save_cv(profile: Profile, owner_id: str, tailored: TailoredCV, label: str, layout: dict, job: Job | None, progress=None) -> int:
    safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:80]
    pdf_path = settings.OUTPUT_DIR / f"{profile.name.replace(' ', '_')}_{safe}_{int(time.time())}.pdf"
    if progress:
        progress.set(step="rendering the PDF")
    pages, used = render_fitting(tailored, pdf_path, layout, job, progress)
    return db.save_cv(owner_id, tailored, str(pdf_path), {**layout, "font_used": used}, pages)


def tailored_cv(profile: Profile, job: Job, role: RoleProfile | None, layout: dict, progress=None) -> int:
    if progress:
        progress.set(step="Claude is tailoring your CV")
    tailored = assistant.tailor(profile, job, role, layout.get("instructions", ""))
    cv_id = save_cv(profile, job.id, tailored, f"{job.company}_{job.title}", layout, job, progress)
    db.promote(job.id, "liked")
    return cv_id


def letter(
    profile: Profile, job: Job, cv: CVContent | None, language: str = "English", tone: str = "professional",
    length: str = "standard", instructions: str = "", progress=None,
) -> int:
    if progress:
        progress.set(step="Claude is writing your letter")
    written = assistant.cover_letter(profile, job, cv, language, tone, length, instructions)
    body = render.letter_body(written, profile.name)
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{job.company}_{job.title}")[:80]
    pdf_path = settings.OUTPUT_DIR / f"{profile.name.replace(' ', '_')}_CoverLetter_{safe}_{int(time.time())}.pdf"
    if progress:
        progress.set(step="rendering the PDF")
    render.letter_pdf(body, profile, job, pdf_path)
    return db.save_letter(job.id, written, body, str(pdf_path))


def prep(profile: Profile, job: Job, fit: FitAssessment | None, progress=None) -> InterviewPrep:
    if progress:
        progress.set(step="Claude is preparing your interview")
    made = assistant.interview_prep(profile, job, fit)
    db.cache_put(f"prep:{job.id}", made.model_dump())
    return made


# --- packs ---------------------------------------------------------------------------------------------


def get_pack(job_id: str) -> dict | None:
    return db.cache_get(f"pack:{job_id}", 24 * 365 * 10)


def candidates(job_ids: list[str], min_score: int, limit: int) -> list[str]:
    """Jobs from this list with a keyword score of at least min_score and no pack yet, best first."""
    rows = db.list_jobs(ids=job_ids, sort="score")
    picked = [r["job"].id for r in rows if (r["score"] or 0) >= min_score and r["status"] != "hidden"]
    return [i for i in picked if not get_pack(i) and not db.task_active("pack", f"pack:{i}")][:limit]


def make_pack(job_id: str, progress=None) -> str:
    """Make every document for one job and write the pack folder. Returns the job page URL."""
    profile = assistant.load_profile()
    if not profile:
        raise RuntimeError("No profile yet - import your CV on /profile first")
    job, _ = db.get_job(job_id)
    job = ensure_description(job)
    steps: list[str] = []

    def step(name: str) -> None:
        steps.append(name)
        if progress:
            progress.set(step=name, done=len(steps) - 1, total=5)

    step("fit check")
    fit = fit_check(profile, job)
    step("GitHub review and project ideas")
    try:
        review = github_check(profile, job, github.fetch_repos(github.username(profile)))
    except Exception as e:  # no GitHub user or the API is down: the pack is still useful without it
        review, review_error = None, str(e)[:200]
    else:
        review_error = ""
    step("tailored CV")
    cv_id = tailored_cv(profile, job, None, {"font_pt": None, "one_page": True, "instructions": ""})
    _, tailored, cv_pdf = db.get_cv(cv_id)
    step("cover letter")
    letter_id = letter(profile, job, tailored.cv)
    step("interview prep")
    made_prep = prep(profile, job, fit)

    folder = _write_folder(job, fit, review, review_error, tailored, cv_id, Path(cv_pdf), letter_id, made_prep)
    db.cache_put(f"pack:{job_id}", {"dir": str(folder), "cv_id": cv_id, "letter_id": letter_id, "made_at": time.time(), "fit": fit.rating})
    if progress:
        progress.set(step="done", done=5, total=5)
    return f"/job/{job_id}"


def submit_pack(job_id: str) -> int:
    job, _ = db.get_job(job_id)
    return tasks.submit(
        "pack", f"Application pack · {job.title} at {job.company}", "pack",
        subject=f"pack:{job_id}", job_id=job_id, url=f"/job/{job_id}", args={"job_id": job_id},
    )


@tasks.runner("pack")
def _pack(progress, job_id: str) -> str:
    return make_pack(job_id, progress)


def run_packs(job_ids: list[str], min_score: int, limit: int, inline: bool = False) -> list[str]:
    """Make packs for the best-scoring jobs: as background tasks, or one after another when inline (cron).
    Returns the job ids that got (or were queued for) a pack."""
    chosen = candidates(job_ids, min_score, limit)
    if not inline:
        for job_id in chosen:
            submit_pack(job_id)
        return chosen
    done = []
    for job_id in chosen:
        try:
            make_pack(job_id)
            done.append(job_id)
        except Exception as e:
            print(f"pack for {job_id} failed: {e}")
    if done:
        names = ", ".join(db.get_job(i)[0].company for i in done[:3]) + (" …" if len(done) > 3 else "")
        tasks.notify(f"{len(done)} application pack{'s' if len(done) != 1 else ''} ready", names, url="/#auto", kind="pack")
    return done


def recent_packs(limit: int = 10) -> list[dict]:
    with db.connect() as c:
        rows = c.execute(
            "SELECT key FROM cache WHERE key LIKE 'pack:%' ORDER BY fetched_at DESC LIMIT ?", (limit,)
        ).fetchall()
    packs = []
    for r in rows:
        job_id = r["key"][5:]
        found, info = db.get_job(job_id), get_pack(job_id)
        if found and info:
            packs.append({"job": found[0], **info})
    return packs


# --- the folder ----------------------------------------------------------------------------------------


def _folder_name(job: Job) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{job.company}_{job.title}")
    return "_".join(filter(None, safe.split("_")))[:90]


def _bullets(items, fmt=str) -> list[str]:
    return [f"- {fmt(i)}" for i in items] or ["- (none)"]


def _write_folder(
    job: Job, fit: FitAssessment, review: GithubReview | None, review_error: str, tailored: TailoredCV,
    cv_id: int, cv_pdf: Path, letter_id: int, made_prep: InterviewPrep,
) -> Path:
    folder = packs_dir() / _folder_name(job)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(cv_pdf, folder / "CV.pdf")
    shutil.copy(db.get_letter(letter_id)["pdf_path"], folder / "CoverLetter.pdf")
    plan = fit.gap_plan
    lines = [
        f"# {job.title} - {job.company}",
        "",
        f"{job.location}{' · remote' if job.remote else ''}{f' · {job.salary}' if job.salary else ''}",
        f"Posting: {job.url or '(no link)'}",
        f"In the app: {APP_URL}/job/{job.id} · CV: {APP_URL}/cv/{cv_id} · letter: {APP_URL}/letter/{letter_id} · prep: {APP_URL}/prep/{job.id}",
        "",
        f"## Fit: {fit.rating}/10 · CV match {tailored.match_score}/100",
        "",
        fit.verdict,
        "",
        f"**{plan.recommendation}**",
        "",
        plan.advice,
        "",
        "What helps you:",
        *_bullets(sorted(fit.pros, key=lambda p: -p.rating)[:5], lambda p: f"{p.point} ({p.rating}/10)"),
        "",
        "What counts against you:",
        *_bullets(sorted(fit.cons, key=lambda c: -c.rating)[:5], lambda c: f"{c.point} ({c.rating}/10) - {c.advice}"),
        "",
        "## Files in this folder",
        "",
        "- CV.pdf - tailored to this posting, one page",
        "- CoverLetter.pdf - English, professional, consistent with the CV",
        "",
        "Requirements the CV does not claim (your profile doesn't show them):",
        *_bullets(tailored.missing_requirements),
        "",
        "## Projects you can build for this job",
        "",
    ]
    if review:
        lines += [review.summary, ""]
        for idea in review.ideas:
            lines += [
                f"### {idea.title} ({idea.estimated_effort})",
                "",
                idea.pitch,
                "",
                f"Proves: {', '.join(idea.covers_requirements)}",
                f"Stack: {', '.join(idea.tech_stack)}",
                "",
                "MVP plan:",
                *[f"{n}. {s}" for n, s in enumerate(idea.mvp_plan, 1)],
                "",
                f"CV bullet once it's built: {idea.cv_bullet_preview}",
                "",
            ]
        strong = [m for m in review.matches if m.relevance >= 7]
        if strong:
            lines += ["Repos you already have that fit:", *_bullets(strong, lambda m: f"{m.repo} ({m.relevance}/10): {m.why}"), ""]
    elif review_error:
        lines += [f"(GitHub review skipped: {review_error})", ""]
    if plan.project_types:
        lines += ["Kinds of projects employers value for this role:", ""]
        lines += _bullets(plan.project_types, lambda p: f"**{p.title}** - {p.description} (proves {', '.join(p.demonstrates)})")
        lines.append("")
    if plan.action_plan:
        lines += ["## Action plan", "", *[f"{n}. {s}" for n, s in enumerate(plan.action_plan, 1)], ""]
    lines += [
        "## Interview prep",
        "",
        made_prep.overview,
        "",
        "Revise first:",
        *_bullets(sorted(made_prep.technical_topics, key=lambda t: -t.priority)[:6],
                  lambda t: f"{t.topic} ({t.priority}/10): {', '.join(t.what_to_revise)}"),
        "",
        "Likely questions:",
        *_bullets(made_prep.likely_questions[:8], lambda q: f"[{q.category}] {q.question} - {q.how_to_answer}"),
        "",
        "Answers for your gaps:",
        *_bullets(made_prep.gap_answers, lambda g: f"{g.likely_question} - {g.honest_answer}"),
        "",
        "Questions to ask them:",
        *_bullets(made_prep.questions_to_ask),
        "",
        "Before the interview:",
        *_bullets(made_prep.checklist),
        "",
    ]
    (folder / "PACK.md").write_text("\n".join(lines))
    return folder
