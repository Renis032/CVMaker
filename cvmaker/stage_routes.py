"""Routes for adding jobs by link, posting checks, outreach, the story bank, interview practice, debriefs and
offer review. Included by app.py; the page helpers come from web.py."""

import time
from datetime import datetime

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from . import assistant, auto, checks, claims, db, intake, stage, stories, tasks, uploads
from .job_routes import _cached_prep
from .web import _back, _job_label, _need_profile, _not_found, page
from .llm import LLMError
from .models import DraftStory, Story
from .sources import ensure_description

router = APIRouter()


def _job_or_none(job_id: str):
    found = db.get_job(job_id)
    return found[0] if found else None


def _lines(text: str) -> list[str]:
    return [x.strip() for x in text.replace(",", "\n").splitlines() if x.strip()]


# --- add a job by link or text ------------------------------------------------------------------------------


@router.get("/add", response_class=HTMLResponse)
def add_page(request: Request):
    recent = [t for t in db.tasks_recent(30) if t["kind"] == "add"][:8]
    return page(request, "add.html", steps=intake.STEPS, recent=recent, active=db.task_active("add"))


@router.post("/add")
def add_submit(text: str = Form(...), step: str = Form("cv")):
    step = step if step in intake.STEPS else "cv"
    if step != "save":
        _need_profile()
    url, pasted = intake.split_input(text)
    label = "Add job · " + (url[:60] if url else " ".join(pasted.split())[:60])

    tasks.submit("add", label, "add-job", url="/add", args={"text": text, "step": step})
    return _back("/add")


@tasks.runner("add-job")
def _add_job(progress, text: str, step: str) -> str:
    try:
        return intake.add_and_prepare(text, step, progress)
    except intake.IntakeError as e:
        raise LLMError(str(e)) from e


# --- posting checks -------------------------------------------------------------------------------------------


@router.post("/check/open")
def check_open_all():
    auto.submit_open_check(limit=200)
    return _back("/?status=closed")


@router.post("/check/{job_id:path}/live")
def check_live(job_id: str):
    checks.check_and_record(job_id)
    return _back(f"/job/{job_id}#overview")


@router.post("/check/{job_id:path}/still-open")
def check_mark_open(job_id: str):
    db.set_open_state(job_id, False)
    db.cache_put(f"live:{job_id}", {"state": "open", "why": "you marked it as still open"})
    return _back(f"/job/{job_id}#overview")


@router.post("/check/{job_id:path}/closed")
def check_mark_closed(job_id: str):
    db.set_open_state(job_id, True, "you marked it as closed")
    return _back(f"/job/{job_id}#overview")


@router.post("/check/{job_id:path}/review")
def check_review(job_id: str):
    _need_profile()
    job = _job_or_none(job_id)
    tasks.submit("postcheck", _job_label("Posting check", job), "postcheck", subject=f"postcheck:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#overview", args={"job_id": job_id})
    return _back(f"/job/{job_id}#overview")


@tasks.runner("postcheck")
def _postcheck(progress, job_id: str) -> str:
    checks.review(ensure_description(_job_or_none(job_id)), _need_profile(), progress)
    return f"/job/{job_id}#overview"


# --- outreach -------------------------------------------------------------------------------------------------


@router.post("/outreach/{job_id:path}/contact")
def outreach_contact(job_id: str, contact: str = Form(...)):
    app = db.get_application(job_id)
    current = app.get("contact") or ""
    if contact.strip() and contact.strip() not in current:
        db.save_application(job_id, contact=(current + "; " if current else "") + contact.strip())
    return _back(f"/job/{job_id}#outreach")


@router.post("/outreach/{job_id:path}")
def outreach_make(job_id: str, recipient: str = Form(""), language: str = Form("English"), notes: str = Form("")):
    _need_profile()
    job = _job_or_none(job_id)
    tasks.submit("outreach", _job_label("Outreach drafts", job), "outreach", subject=f"outreach:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#outreach", args={"job_id": job_id, "recipient": recipient, "language": language, "notes": notes})
    return _back(f"/job/{job_id}#outreach")


@tasks.runner("outreach")
def _outreach(progress, job_id: str, recipient: str, language: str, notes: str) -> str:
    stage.make_outreach(_need_profile(), ensure_description(_job_or_none(job_id)), recipient, language, notes, progress)
    return f"/job/{job_id}#outreach"


# --- story bank ---------------------------------------------------------------------------------------------


@router.get("/stories", response_class=HTMLResponse)
def stories_page(request: Request, edit: str = "", new: int = 0):
    items = stories.load()
    editing = stories.get(edit) if edit else (Story(title="", situation="", task="", action="", result="") if new else None)
    return page(
        request, "stories.html", items=items, editing=editing, incomplete={s.id for s in items if stories.incomplete(s)},
        active=db.task_active("stories"),
    )


@router.post("/stories/save")
def stories_save(
    id: str = Form(""), title: str = Form(...), situation: str = Form(""), task: str = Form(""), action: str = Form(""),
    result: str = Form(""), reflection: str = Form(""), skills: str = Form(""), good_for: str = Form(""),
    fill_in: str = Form(""),
):
    old = stories.get(id) if id else None
    saved = stories.upsert(Story(
        id=id, title=title.strip(), situation=situation.strip(), task=task.strip(), action=action.strip(),
        result=result.strip(), reflection=reflection.strip(), skills=_lines(skills),
        good_for=[x.strip() for x in good_for.splitlines() if x.strip()],
        fill_in=[x.strip() for x in fill_in.splitlines() if x.strip()], source=old.source if old else "manual",
    ))
    return _back(f"/stories#s-{saved.id}")


@router.post("/stories/{story_id}/delete")
def stories_delete(story_id: str):
    stories.delete(story_id)
    return _back("/stories")


@router.post("/stories/draft")
def stories_draft():
    _need_profile()
    tasks.submit("stories", "Draft stories for the story bank", "draft-stories", subject="stories:draft", url="/stories")
    return _back("/stories")


@tasks.runner("draft-stories")
def _draft_stories(progress) -> str:
    from . import coach

    progress.set(step="Claude is drafting stories from your profile")
    stories.add_drafts(coach.draft_stories(_need_profile(), stories.load()).stories, "profile")
    return "/stories"


# --- interview practice -----------------------------------------------------------------------------------


@router.get("/practice/{job_id:path}", response_class=HTMLResponse)
def practice_page(request: Request, job_id: str):
    job = _job_or_none(job_id)
    if not job:
        return page(request, "error.html", message=_not_found("Job"))
    prep = _cached_prep(job_id)
    items = db.practice_for_job(job_id)
    scored = [p["score"] for p in items if p["score"] is not None]
    return page(
        request, "practice.html", job=job, items=items, prep=prep, categories=stage.PRACTICE_CATEGORIES,
        average=round(sum(scored) / len(scored), 1) if scored else None,
        active={t["subject"]: t for t in db.task_active() if t["job_id"] == job_id and t["kind"] in ("practice-q", "practice-a")},
        story_titles=[s.title for s in stories.load()],
    )


@router.post("/practice/{job_id:path}/ask")
def practice_ask(job_id: str, question: str = Form(""), custom: str = Form(""), category: str = Form("behavioural")):
    job = _job_or_none(job_id)
    text = custom.strip() or question.strip()
    if text:
        pid = db.practice_add(job_id, "your question" if custom.strip() else "from your prep", text)
        return _back(f"/practice/{job_id}#q{pid}")
    _need_profile()
    tasks.submit("practice-q", _job_label("Practice question", job), "practice-q", subject=f"practice-q:{job_id}", job_id=job_id,
                 url=f"/practice/{job_id}", args={"job_id": job_id, "category": category})
    return _back(f"/practice/{job_id}")


@tasks.runner("practice-q")
def _practice_q(progress, job_id: str, category: str) -> str:
    pid = stage.ask_question(_need_profile(), ensure_description(_job_or_none(job_id)), category, progress)
    return f"/practice/{job_id}#q{pid}"


@router.post("/practice-answer/{practice_id}")
def practice_answer(practice_id: int, answer: str = Form(...)):
    item = db.practice_get(practice_id)
    if not item:
        raise LLMError("That practice question no longer exists.")
    _need_profile()
    job = _job_or_none(item["job_id"])
    db.practice_update(practice_id, answer=answer)
    tasks.submit("practice-a", _job_label("Answer feedback", job), "practice-a", subject=f"practice-a:{practice_id}", job_id=job.id,
                 url=f"/practice/{job.id}#q{practice_id}", args={"job_id": job.id, "practice_id": practice_id, "answer": answer})
    return _back(f"/practice/{job.id}#q{practice_id}")


@tasks.runner("practice-a")
def _practice_a(progress, job_id: str, practice_id: int, answer: str) -> str:
    stage.give_feedback(_need_profile(), _job_or_none(job_id), practice_id, answer, progress)
    return f"/practice/{job_id}#q{practice_id}"


@router.post("/practice-delete/{practice_id}")
def practice_delete(practice_id: int):
    item = db.practice_get(practice_id)
    db.practice_delete(practice_id)
    return _back(f"/practice/{item['job_id']}" if item else "/")


# --- debriefs -------------------------------------------------------------------------------------------------


@router.post("/debrief/{job_id:path}")
def debrief_add(
    job_id: str, date: str = Form(""), round: str = Form("Other"), interviewers: str = Form(""), questions: str = Form(""),
    went_well: str = Form(""), went_badly: str = Form(""), feeling: int = Form(3), next_steps: str = Form(""),
):
    _need_profile()
    job = _job_or_none(job_id)
    notes = {"date": date, "round": round, "interviewers": interviewers, "questions_asked": questions,
             "went_well": went_well, "was_hard": went_badly, "feeling_1_to_5": feeling, "next_steps_they_mentioned": next_steps}
    debrief_id = db.debrief_add(job_id, notes)
    try:
        at = datetime.strptime(date, "%Y-%m-%d").timestamp() if date else time.time()
    except ValueError:
        at = time.time()
    status = db.get_job(job_id)[1]
    db.add_event(job_id, at, status, f"Debrief: {round.lower()}" + (f" with {interviewers}" if interviewers else ""))

    tasks.submit("debrief", _job_label("Debrief", job), "debrief", subject=f"debrief:{debrief_id}", job_id=job_id,
                 url=f"/job/{job_id}#interview", args={"job_id": job_id, "debrief_id": debrief_id})
    return _back(f"/job/{job_id}#interview")


@tasks.runner("debrief")
def _debrief(progress, job_id: str, debrief_id: int) -> str:
    stage.review_debrief(_need_profile(), ensure_description(_job_or_none(job_id)), debrief_id, progress)
    return f"/job/{job_id}#interview"


@router.post("/debrief-story/{debrief_id}")
def debrief_story(debrief_id: int, index: int = Form(...)):
    item = db.debrief_get(debrief_id)
    candidates = (item or {}).get("review", {}) or {}
    drafts = candidates.get("story_candidates", [])
    if 0 <= index < len(drafts):
        added = stories.add_drafts([DraftStory.model_validate(drafts[index])], "debrief")[0]
        drafts[index]["added_as"] = added.id
        db.debrief_set_review(debrief_id, candidates)
    return _back(f"/job/{item['job_id']}#interview" if item else "/stories")


@router.post("/debrief-delete/{debrief_id}")
def debrief_delete(debrief_id: int):
    item = db.debrief_get(debrief_id)
    db.debrief_delete(debrief_id)
    return _back(f"/job/{item['job_id']}#interview" if item else "/")


# --- offers ---------------------------------------------------------------------------------------------------


@router.post("/offer/{job_id:path}")
async def offer_review(job_id: str, text: str = Form(""), wishes: str = Form(""), file: UploadFile | None = File(None)):
    _need_profile()
    job = _job_or_none(job_id)
    try:
        data = await uploads.read(file, kinds={"pdf"}) if file and file.filename else b""
    except uploads.UploadError as e:
        raise LLMError(str(e)) from e
    if data:
        try:
            text = (text + "\n\n" if text.strip() else "") + stage.pdf_text(data)
        except RuntimeError as e:
            raise LLMError(str(e)) from e
    if not text.strip():
        raise LLMError("Paste the offer or contract text, or upload it as a PDF.")

    tasks.submit("offer", _job_label("Offer review", job), "offer", subject=f"offer:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#offer", args={"job_id": job_id, "text": text, "wishes": wishes})
    return _back(f"/job/{job_id}#offer")


@tasks.runner("offer")
def _offer(progress, job_id: str, text: str, wishes: str) -> str:
    stage.review_offer(_need_profile(), _job_or_none(job_id), text, wishes, progress)
    return f"/job/{job_id}#offer"


# --- skill check --------------------------------------------------------------------------------------------


@router.post("/claims/add-skill")
def claims_add_skill(term: str = Form(...), back: str = Form("/profile")):
    """The candidate confirmed a flagged technology is true: add it to the profile so it stops being flagged."""
    profile = _need_profile()
    claims.add_skill(profile, term)
    assistant.save_profile(profile)
    return _back(back if back.startswith("/") and not back.startswith("//") else "/profile")
