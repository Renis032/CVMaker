"""Claude prompts for the later stages: reading a pasted posting, checking postings, outreach, the story bank,
interview practice, debriefs and offer review. The earlier stages (fit, CV, letter, prep) live in assistant.py."""

import json

from . import llm
from .assistant import TRUTH_RULES, _job_text
from .models import (
    DebriefReview,
    DraftStories,
    Job,
    OfferReview,
    OutreachKit,
    ParsedPosting,
    PostingReview,
    PracticeFeedback,
    PracticeQuestion,
    Profile,
    Story,
)

LINKEDIN_NOTE_MAX = 300


def _profile(profile: Profile) -> str:
    return profile.model_dump_json(indent=2)


def _stories(stories: list[Story]) -> str:
    if not stories:
        return "(the story bank is empty)"
    return json.dumps(
        [s.model_dump(include={"id", "title", "situation", "task", "action", "result", "reflection", "skills", "good_for"}) for s in stories],
        ensure_ascii=False, indent=2,
    )


def parse_posting(text: str, url: str = "") -> ParsedPosting:
    system = """You read a web page or pasted text and pull out one job posting.
- is_job_posting: false when the text is not a job posting (a login wall, a list of many jobs, an error page, a cookie notice only).
- title, company, location exactly as the posting states them; remote true only when the posting says the job can be done fully remotely.
- salary: the pay as written (with currency and period), or empty.
- posted_at: ISO date (YYYY-MM-DD) if the posting states one, else empty.
- apply_url: the application link if one appears in the text, else empty.
- description: the full posting text - responsibilities, requirements, benefits, about the company - cleaned of navigation, cookie banners, footers and lists of other jobs. Keep the original language and wording; keep line breaks between sections and bullets. Do not summarise or translate.
Never invent anything that is not in the text."""
    content = (f"Page address: {url}\n\n" if url else "") + f"Text:\n{text[:60000]}"
    return llm.ask(system, content, ParsedPosting, effort="low")


def review_posting(profile: Profile, job: Job, facts: list[str]) -> PostingReview:
    system = """You check a job posting for a candidate before they spend time on it. Be factual and fair: most postings are real, and recruiting agencies are normal.
- verdict: one or two sentences.
- trust: 1-10, how much it looks like a real, currently open job at a real employer (10 = clearly real and specific).
- work_permit: what the posting says about where the person must live, work permits, citizenship, visa sponsorship, security clearance or time zones - and what that means for this candidate given their location in the profile. Say plainly when the posting says nothing about it.
- flags: concrete findings only, each with the exact evidence (a short quote, or a fact from the app's checks), severity ("red" = likely a blocker or a scam sign, "amber" = worth checking, "info" = good to know) and what to do about it. Kinds: "legitimacy" (no real company, asks for money or bank details, chat-app interviews, unrealistic pay, copy-paste text), "work permit", "repost", "pay", "vague" (no real duties or stack), "other". No flag without evidence.
- questions_to_ask: what to ask the recruiter to clear up the flags.
The app's own checks (liveness, earlier sightings of the same job) are included as facts - use them, they are measured."""
    content = (
        f"Candidate location: {profile.contact.location or 'unknown'}\nCandidate languages: {', '.join(profile.languages) or 'unknown'}\n\n"
        f"Posting:\n{_job_text(job)}\nSalary as stated: {job.salary or 'not stated'}\nPosted: {job.posted_at or 'unknown'}\n"
        f"Found via: {job.source}\nLink: {job.url}\n\nThe app's checks:\n" + ("\n".join(f"- {f}" for f in facts) or "- none")
    )
    return llm.ask(system, content, PostingReview, effort="medium")


def outreach(profile: Profile, job: Job, recipient: str, language: str, notes: str, posting_contacts: list[str]) -> OutreachKit:
    system = f"""You help a candidate reach people at a company about one job, politely and briefly. Nothing is sent by the app; the candidate copies and edits.
- contacts: 3-5 people worth contacting, most useful first. Use names from the posting when it has them (from_posting true). Otherwise describe the role to look for (e.g. "Engineering manager, payments team at <company>", "Technical recruiter at <company>") with why and search_terms to paste into LinkedIn people search. Never invent a person's name.
- linkedin_note: a connection request note of at most {LINKEDIN_NOTE_MAX} characters, counting spaces. Specific to the job, one concrete fact about the candidate, no flattery, no "I hope this finds you well".
- linkedin_message: the message to send after they accept, 60-110 words, ending with one easy question.
- application_email: subject and body for applying by email with the CV attached, 120-180 words.
- follow_up_email: subject and body for a polite follow-up about 7-10 days after applying with no reply, 60-100 words.
- tips: 2-4 short tips for this company and channel.
Write in {language}. Address the recipient by the name given if there is one; otherwise use a neutral greeting.
Sign with the candidate's name. Only mention facts from the profile.
{TRUTH_RULES}"""
    content = f"Candidate profile:\n{_profile(profile)}\n\nJob:\n{_job_text(job)}"
    if posting_contacts:
        content += "\n\nNames and addresses found in the posting:\n" + "\n".join(posting_contacts)
    if recipient.strip():
        content += f"\n\nThe candidate wants to write to: {recipient.strip()}"
    if notes.strip():
        content += f"\n\nExtra notes from the candidate:\n{notes.strip()}"
    kit = llm.ask(system, content, OutreachKit, effort="medium")
    if len(kit.linkedin_note) > LINKEDIN_NOTE_MAX:  # rare; trim at a word boundary rather than lose the draft
        kit.linkedin_note = kit.linkedin_note[: LINKEDIN_NOTE_MAX - 1].rsplit(" ", 1)[0] + "…"
    return kit


def draft_stories(profile: Profile, existing: list[Story]) -> DraftStories:
    system = f"""You build a story bank for behavioural interviews (STAR+R: situation, task, action, result, reflection) from the candidate's real experience.
- Write 6-8 stories that cover different question types: a hard technical problem, improving performance or quality, a mistake or failure, disagreement or teamwork, learning something new fast, ownership or initiative, a deadline under pressure, helping or mentoring someone.
- Use only facts in the profile. Where a good story needs a detail the profile lacks (who disagreed, what went wrong, what they learned), write the sentence with a [bracketed placeholder] and add a short question to fill_in asking the candidate for it. Never fill gaps with invented events or numbers.
- result: keep numbers exactly as the profile gives them.
- skills: the skills the story proves. good_for: the interview questions it answers, e.g. "Tell me about a time you failed".
- Skip stories that duplicate ones already in the bank.
{TRUTH_RULES}"""
    content = f"Candidate profile:\n{_profile(profile)}\n\nStories already in the bank:\n{_stories(existing)}"
    return llm.ask(system, content, DraftStories)


def practice_question(profile: Profile, job: Job, category: str, asked: list[str]) -> PracticeQuestion:
    system = """You are interviewing the candidate for this job. Ask exactly one realistic interview question of the requested category, as the interviewer would say it.
Make it specific to the job and, for behavioural or CV questions, to the candidate's background. Do not repeat or rephrase questions already asked."""
    content = (
        f"Category: {category}\n\nCandidate profile:\n{_profile(profile)}\n\nJob:\n{_job_text(job)}\n\n"
        "Already asked:\n" + ("\n".join(f"- {q}" for q in asked) or "- nothing yet")
    )
    return llm.ask(system, content, PracticeQuestion, effort="low")


def practice_feedback(profile: Profile, job: Job, question: str, answer: str, stories: list[Story]) -> PracticeFeedback:
    system = f"""You are an experienced interviewer giving honest, useful feedback on the candidate's spoken-style answer to one interview question for this job.
- score: 1-10 as a hiring interviewer would judge it (structure, specifics, relevance, honesty, length).
- verdict: one or two sentences.
- worked: what was good, specifically.
- improve: concrete changes, most important first.
- stronger_answer: a better version of their answer, 120-200 words, first person, built only from what they said and facts in their profile or story bank. If a better answer needs a fact they do not have, write it as a [bracketed placeholder] instead of inventing it.
- follow_up_question: the probing question an interviewer would most likely ask next.
- story_to_use: the title of the story-bank story that fits this question best, or empty.
{TRUTH_RULES}"""
    content = (
        f"Job:\n{_job_text(job)}\n\nCandidate profile:\n{_profile(profile)}\n\nStory bank:\n{_stories(stories)}\n\n"
        f"Question: {question}\n\nThe candidate's answer:\n{answer}"
    )
    return llm.ask(system, content, PracticeFeedback, effort="medium")


def debrief(profile: Profile, job: Job, notes: dict, stories: list[Story]) -> DebriefReview:
    system = f"""The candidate just had an interview for this job and wrote down how it went. Help them learn from it and prepare the next step.
- summary: two or three sentences.
- went_well: specific things to keep doing.
- improve: what to do better, each with rating (1-10, how much it matters for the next round) and concrete advice.
- prepare_next: what the next round will probably dig into, given what was asked, and how to prepare.
- signals: what the interview suggests about the outcome, with care - only signals from the notes, and say when they are weak.
- story_candidates: 0-3 new STAR+R stories for the story bank from what the candidate described (only facts they wrote or that are in the profile; placeholders and fill_in questions for gaps). Skip ones already in the bank.
- thank_you_email: a short thank-you note to the interviewers (80-120 words) that mentions something specific from the conversation. Use the interviewer names from the notes; never invent names.
{TRUTH_RULES}"""
    content = (
        f"Job:\n{_job_text(job)}\n\nCandidate profile:\n{_profile(profile)}\n\nStory bank:\n{_stories(stories)}\n\n"
        f"Debrief notes:\n{json.dumps(notes, ensure_ascii=False, indent=2)}"
    )
    return llm.ask(system, content, DebriefReview)


def review_offer(profile: Profile, job: Job, offer_text: str, wishes: str, market: str) -> OfferReview:
    system = """You help a software developer read a job offer or employment contract before they accept it. You are not their lawyer; say so once in the summary, briefly, and recommend a lawyer only for red clauses.
Read every clause. Judge it against what is normal for software jobs in the country of the job (for Bulgaria, the Labour Code: probation up to 6 months, notice periods, paid leave of at least 20 days, overtime rules, non-compete limits, and so on - only where relevant and you are sure).
- summary: what is being offered, in plain words, and the overall verdict.
- terms: the key terms as stated - salary (gross or net, monthly or yearly, currency), bonus, equity, start date, contract type, probation, notice period, working hours, overtime, remote/office policy, paid leave, sick pay, benefits, equipment, training budget, relocation. value exactly as written, or "not stated".
- clauses: the clauses that matter, each with a short quote, risk ("red" = unusual or harmful, push back or get advice; "amber" = ask or negotiate; "green" = fair and normal), why, and what to ask or propose.
- missing: important things the text does not mention.
- salary_view: how the pay compares with the candidate's wishes and the market numbers the app measured (they are given; say how many postings they come from and treat small samples carefully). Say whether the figure is gross or net and convert monthly/yearly so the numbers can be compared.
- negotiation: the 2-5 points most worth negotiating, each with why and polite wording the candidate can use.
- questions_before_signing: questions to send the employer.
- reply_email: a short, warm reply that thanks them and raises the questions and the main negotiation points (or accepts, if nothing needs changing).
Quote the offer exactly; never invent terms that are not in it."""
    content = (
        f"Job:\n{_job_text(job)}\n\nCandidate location: {profile.contact.location or 'unknown'}\n"
        f"Candidate's wishes and priorities: {wishes.strip() or 'not given'}\n\nMarket numbers from postings the app has seen:\n{market}\n\n"
        f"Offer / contract text:\n{offer_text[:80000]}"
    )
    return llm.ask(system, content, OfferReview)
