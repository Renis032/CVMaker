"""The steps after applying: outreach, interview practice, debriefs and offer review. Each runs one Claude call
(see coach.py) as a background task and keeps the result: outreach and offer reviews in the cache table,
practice answers and debriefs in their own tables, new stories in the story bank."""

import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote_plus

from . import coach, db, stories
from .models import DebriefReview, Job, OfferReview, OutreachKit, PracticeFeedback, Profile

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CONTACT_RE = re.compile(
    r"(?:contact(?: person)?|recruiter|hiring manager|reach out to|questions\?? (?:contact|email)|лице за контакт)\s*[:\-–]?\s*"
    r"([A-ZА-Я][\w\-]+(?:\s+[A-ZА-Я][\w\-]+){1,2})",
    re.I,
)
PRACTICE_CATEGORIES = ["behavioural", "technical", "about your CV", "role & motivation", "system design"]
DEBRIEF_ROUNDS = ["Recruiter screen", "HR interview", "Technical interview", "Live coding", "Take-home review",
                  "System design", "Team / culture fit", "Final round", "Other"]


# --- outreach ----------------------------------------------------------------------------------------------


def posting_contacts(job: Job) -> list[str]:
    """Names and e-mail addresses written in the posting itself."""
    found = []
    for m in EMAIL_RE.finditer(job.description):
        if not m.group(0).lower().startswith(("noreply", "no-reply")):
            found.append(m.group(0))
    for m in CONTACT_RE.finditer(job.description):
        found.append(m.group(1).strip())
    return list(dict.fromkeys(found))


def linkedin_people_search(terms: str) -> str:
    """A LinkedIn people search link to open yourself; the app never loads LinkedIn."""
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(terms)}"


def make_outreach(profile: Profile, job: Job, recipient: str, language: str, notes: str, progress=None) -> OutreachKit:
    if progress:
        progress.set(step="Claude is drafting your messages")
    kit = coach.outreach(profile, job, recipient, language, notes, posting_contacts(job))
    db.cache_put(f"outreach:{job.id}", {"kit": kit.model_dump(), "recipient": recipient, "language": language, "notes": notes})
    return kit


def cached_outreach(job_id: str) -> dict | None:
    cached = db.cache_get(f"outreach:{job_id}", 24 * 365)
    if not cached:
        return None
    try:
        cached["kit"] = OutreachKit.model_validate(cached["kit"])
    except ValueError:
        return None
    return cached


# --- interview practice --------------------------------------------------------------------------------------


def ask_question(profile: Profile, job: Job, category: str, progress=None) -> int:
    if progress:
        progress.set(step="Claude is thinking of a question")
    asked = [p["question"] for p in db.practice_for_job(job.id)]
    q = coach.practice_question(profile, job, category, asked)
    return db.practice_add(job.id, q.category or category, q.question)


def give_feedback(profile: Profile, job: Job, practice_id: int, answer: str, progress=None) -> PracticeFeedback:
    item = db.practice_get(practice_id)
    db.practice_update(practice_id, answer=answer)
    if progress:
        progress.set(step="Claude is judging your answer")
    fb = coach.practice_feedback(profile, job, item["question"], answer, stories.load())
    db.practice_update(practice_id, feedback=fb.model_dump(), score=fb.score)
    return fb


# --- debriefs ------------------------------------------------------------------------------------------------


def review_debrief(profile: Profile, job: Job, debrief_id: int, progress=None) -> DebriefReview:
    item = db.debrief_get(debrief_id)
    if progress:
        progress.set(step="Claude is reading your debrief")
    made = coach.debrief(profile, job, item["notes"], stories.load())
    db.debrief_set_review(debrief_id, made.model_dump())
    return made


# --- offers --------------------------------------------------------------------------------------------------


def pdf_text(data: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="cvmaker-offer-") as d:
        src = Path(d) / "offer.pdf"
        src.write_bytes(data)
        out = subprocess.run(["pdftotext", "-layout", str(src), "-"], capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError("Could not read text from that PDF (is it a scan?). Paste the text instead.")
    return out.stdout


def market_summary(job: Job) -> str:
    """Monthly EUR salaries of saved postings with a similar title, for comparison with an offer."""
    words = {w for w in db.title_key_of(job.title).split() if len(w) > 2} - {"senior", "junior", "mid", "the", "and", "for"}
    same, all_rows = [], []
    for r in db.market_rows():
        if r["salary_month"] is None or r["job"].id == job.id:
            continue
        all_rows.append(r["salary_month"])
        if words & set(db.title_key_of(r["job"].title).split()):
            same.append(r["salary_month"])

    def line(label: str, xs: list[float]) -> str:
        if len(xs) < 3:
            return f"{label}: only {len(xs)} posting{'s' if len(xs) != 1 else ''} with a salary - too few to compare."
        q = statistics.quantiles(xs, n=4)
        return f"{label}: {len(xs)} postings, median €{statistics.median(xs):,.0f}/month, middle half €{q[0]:,.0f}-€{q[2]:,.0f} (gross where stated; posted salaries mix gross and net)."

    return "\n".join([line(f"Similar titles ({', '.join(sorted(words)) or 'any'})", same), line("All saved postings", all_rows)])


def review_offer(profile: Profile, job: Job, text: str, wishes: str, progress=None) -> OfferReview:
    if progress:
        progress.set(step="Claude is reading the offer")
    market = market_summary(job)
    made = coach.review_offer(profile, job, text, wishes, market)
    db.cache_put(f"offer:{job.id}", {"review": made.model_dump(), "text": text, "wishes": wishes, "market": market})
    return made


def cached_offer(job_id: str) -> dict | None:
    cached = db.cache_get(f"offer:{job_id}", 24 * 365 * 5)
    if not cached:
        return None
    try:
        cached["review"] = OfferReview.model_validate(cached["review"]) if cached.get("review") else None
    except ValueError:
        cached["review"] = None
    return cached
