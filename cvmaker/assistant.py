"""Everything that asks Claude about the candidate: CV import, interview, fit check, tailoring."""

import json

from . import llm, settings
from .models import (
    CoverLetter,
    CVComparison,
    CVContent,
    FitAssessment,
    GapAnalysis,
    GithubReview,
    InterviewPrep,
    InterviewQuestions,
    Job,
    Profile,
    QuickFits,
    RoleProfile,
    TailoredCV,
)

TRUTH_RULES = """Ground rules - these override everything else:
- Only use facts that appear in the candidate's profile. Never invent employers, dates, titles, degrees, certifications, metrics, technologies or years of experience.
- You may reword, reorder, regroup, shorten and choose emphasis. You may describe existing work using the employer's vocabulary when it is an honest description of that work (e.g. "high-throughput simulation" for a simulation engine the profile describes).
- A skill the job wants but the profile does not show goes into missing_requirements, never into the CV.
- Keep numbers exactly as given in the profile.
- Keep qualifiers exactly: "not completed", "ongoing", "expected", "basic", "personal project" and the like must never be dropped or softened."""


TASTE_NOTE = """The candidate's own reactions to earlier postings are included. They show preferences (seniority, stack, location, salary, companies), not ability.
Rate lower when a job clearly matches something they rejected, and say so in the verdict (e.g. "you've hidden similar senior roles"). Never rate higher than their profile justifies."""


def _with_taste(content: str, taste: str) -> str:
    return f"{content}\n\nThe candidate's preferences, learned from what they hid and liked:\n{taste}" if taste else content


def load_profile() -> Profile | None:
    if not settings.PROFILE_PATH.exists():
        return None
    return Profile.model_validate_json(settings.PROFILE_PATH.read_text())


def save_profile(profile: Profile) -> None:
    settings.PROFILE_PATH.write_text(profile.model_dump_json(indent=2))


def import_cv(pdf_bytes: bytes) -> Profile:
    system = f"""You turn a CV into a structured candidate profile.
Copy every fact faithfully: contact details, roles, dates, bullet points, projects, skills, education, soft skills, interests, languages.
Keep the candidate's own wording for bullets. Use empty strings or empty lists for anything the CV does not contain.
skill_details: one entry per technical skill with level/years/evidence only where the CV supports it, otherwise empty strings.
extra_facts and preferences: empty lists (they are filled later by an interview).
{TRUTH_RULES}"""
    return llm.ask(system, "Extract the profile from this CV.", Profile, pdf=pdf_bytes)


def interview_questions(profile: Profile, job: Job | None) -> InterviewQuestions:
    target = (
        f"The candidate wants to apply for this job:\n{_job_text(job)}\n\n"
        "Focus on what the job asks for that the profile does not yet prove or quantify."
        if job
        else "Focus on gaps that would make the profile stronger for software developer roles in general."
    )
    system = """You are a technical recruiter interviewing a software developer to strengthen their CV.
Ask 5-8 short, specific questions whose answers would add true, concrete, CV-worthy facts: depth and years of a skill, scale and measurable impact, technologies used in a project, responsibilities, results.
Do not ask about things the profile already answers. Ask one thing per question. ids are q1, q2, ..."""
    return llm.ask(
        system,
        f"Candidate profile:\n{profile.model_dump_json(indent=2)}\n\n{target}",
        InterviewQuestions,
        effort="medium",
    )


def merge_answers(profile: Profile, qa: list[tuple[str, str]]) -> Profile:
    answered = [(q, a.strip()) for q, a in qa if a.strip()]
    if not answered:
        return profile
    transcript = "\n\n".join(f"Q: {q}\nA: {a}" for q, a in answered)
    system = f"""You update a candidate profile with the candidate's own interview answers.
Add what the answers state: new skills (to skills and skill_details), levels/years/evidence, extra bullets on the right role or project, new projects, preferences (desired roles, locations, salary, remote) into preferences, and anything else useful into extra_facts.
Keep everything already in the profile unless an answer explicitly corrects it. Do not embellish the answers.
{TRUTH_RULES}"""
    return llm.ask(
        system,
        f"Current profile:\n{profile.model_dump_json(indent=2)}\n\nInterview:\n{transcript}",
        Profile,
    )


def assess_fit(profile: Profile, job: Job, repos_text: str = "", taste: str = "") -> FitAssessment:
    system = """You are an honest technical recruiter and career mentor judging how well this candidate fits this job.
rating: overall fit from 1 (no realistic chance) to 10 (ideal candidate).
verdict: one sentence, addressed to the candidate as "you".
pros: concrete reasons the candidate fits, each rated 1-10 for how much it helps the application (10 = directly covers a must-have).
cons: concrete gaps or risks, each rated 1-10 for how serious it is (10 = a must-have that is clearly missing, likely rejection), each with advice: one or two sentences on the most effective way to improve it - what to learn or build, what to change on the CV or GitHub, or how to address it in the application - specific to this job and this candidate.
Count something as evidence if the profile or the candidate's GitHub repos (README, files, build/config files) show it.

gap_plan - how the candidate gets from where they are to being a strong candidate for this kind of job. Make it detailed when rating is 7 or lower, brief (top 2-3 gaps) when 8 or higher.
- recommendation: one of "Apply now", "Apply now, but address the gaps", "Build first, then apply", "Target a closer role first" - followed by a short reason.
- advice: a short paragraph of honest, practical guidance on how to proceed.
- lacking_areas: each missing or weak area, severity 1-10 (10 = deal-breaker), why this employer cares, how to close it concretely (what to learn, what to build, what to practise), realistic time_to_close for someone working evenings.
- project_types: 2-4 kinds of projects that would look strong on a CV for this listing - what they are, which requirements they prove, why employers in this domain value them. Prefer extending the candidate's existing projects when that is faster.
- action_plan: ordered, concrete steps with rough timing (e.g. "Weeks 1-2: ...").
- interview_framing: how to present the gaps honestly and positively (transferable experience, what they are doing about it). Never suggest overstating experience.
- closer_roles: 2-4 job titles that match the candidate better right now and are stepping stones toward this one.
Be realistic about seniority and must-have requirements; do not flatter."""
    content = f"Candidate profile:\n{profile.model_dump_json(indent=2)}\n\nJob:\n{_job_text(job)}"
    if repos_text:
        content += f"\n\nCandidate's public GitHub repositories:\n{repos_text}"
    if taste:
        system += "\n" + TASTE_NOTE
    return llm.ask(system, _with_taste(content, taste), FitAssessment)


QUICK_FIT_DESCRIPTION_CHARS = 5000


def quick_fit(profile: Profile, jobs: list[Job], taste: str = "") -> QuickFits:
    """A fast first-pass fit rating for several jobs at once, for ranking the job list."""
    system = """You are an honest technical recruiter doing a fast first screen: how well does this candidate fit each job?
For every job, return one entry with its exact job_id:
- rating: 1 (no realistic chance) to 10 (ideal candidate). Weigh must-have requirements and seniority most; be realistic, do not flatter.
- strong: 1-3 of the candidate's strongest matches for this job, each 1-4 words (e.g. "Java/Spring", "5 yrs C++").
- missing: 0-3 of the most important requirements the profile does not show, each 1-4 words.
- verdict: one short sentence, addressed to the candidate as "you".
Judge only from the profile and the posting text. If a posting has almost no description, rate from the title and say so in the verdict."""
    postings = [
        {
            "job_id": j.id,
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "description": j.description[:QUICK_FIT_DESCRIPTION_CHARS],
        }
        for j in jobs
    ]
    profile_text = profile.model_dump_json(exclude={"contact", "interests", "preferences"})
    content = f"Candidate profile:\n{profile_text}\n\nJobs:\n{json.dumps(postings, ensure_ascii=False, indent=1)}"
    return llm.ask(system + ("\n" + TASTE_NOTE if taste else ""), _with_taste(content, taste), QuickFits, effort="low")


def gap_analysis(profile: Profile, jobs: list[dict]) -> GapAnalysis:
    """Group the gaps found by many fit checks into a few skills, ranked by how many jobs they would open."""
    system = """You are a career coach looking at many fit checks of one candidate against different jobs.
Each job has a ref (J1, J2, ...), its fit rating and the gaps found for it.
Group gaps that mean the same thing ("no professional Java", "Java only from personal projects") into one cluster and return:
- clusters: the 5-12 most important, most frequent first. For each:
  - gap: a short name, 2-6 words (e.g. "Professional Java/Spring experience").
  - job_refs: every job ref where this gap appears.
  - severity: 1-10, how much it costs the candidate across those jobs.
  - proof_only: true when the profile shows the skill but the jobs want stronger evidence of it (it is a CV/portfolio problem, not a learning one).
  - why: one sentence on why employers care.
  - how_to_close: concrete steps that fit this candidate's background (a project, a course, a certification, a change to the CV), 1-3 sentences.
  - time_to_close: a realistic estimate, e.g. "2-3 weeks of evenings".
- learn_next: 3-5 steps in the order to do them, each one sentence, starting with the one that improves the most jobs for the least effort.
- summary: 2-3 sentences, addressed to the candidate as "you".
The refs are only for job_refs: in summary, learn_next and the texts, name a job by its title and company, never by ref.
Use only what the profile and the gaps say. Never claim the candidate has experience the profile does not show."""
    profile_text = profile.model_dump_json(exclude={"contact", "interests", "preferences"})
    content = f"Candidate profile:\n{profile_text}\n\nJobs and their gaps:\n{json.dumps(jobs, ensure_ascii=False, indent=1)}"
    return llm.ask(system, content, GapAnalysis, effort="medium")


def shorten_cv(cv: CVContent, job: Job | None, overflow: float, instructions: str = "") -> CVContent:
    """Cut a CV that runs over one page, removing the least relevant material first."""
    percent = max(8, min(60, round(overflow * 100) + 5))
    target = f"Job it is tailored for:\n{_job_text(job)}" if job else "It is a general CV (no specific job)."
    system = f"""You shorten a CV so it fits on one A4 page. It currently overflows: about {percent}% of the content has to go.
- Remove the least relevant material for the target first: weakest bullets, least relevant projects, extra soft skills and interests, redundant skills.
- Tighten long bullets and the summary. Keep at least 2 bullets per role, keep every role and every education entry.
- Never add anything. Never change facts, numbers, dates, names or qualifiers. Keep **bold** markers on the phrases that stay.
{TRUTH_RULES}"""
    content = f"{target}\n\nCV to shorten:\n{cv.model_dump_json(indent=2)}"
    if instructions.strip():
        content += f"\n\nThe candidate's instructions for this CV (keep following them):\n{instructions.strip()}"
    return llm.ask(system, content, CVContent, effort="medium")


def github_review(profile: Profile, job: Job, repos_text: str) -> GithubReview:
    system = """You are a senior engineer helping a candidate decide which of their public GitHub projects to show for one job, and what to build if none fits.

matches: every repo with relevance 4 or higher (1-10 = how much it would help this application), most relevant first.
- why / covers_requirements: tie the repo to specific requirements of the job.
- Evidence comes only from the repo's README, file list and build/config files. A technology counts only if those show it (e.g. a Dockerfile or docker-compose.yml shows Docker; a pom.xml dependency shows a library). Never assume features the files do not show.
- cv_bullets: 1-3 CV-ready bullets describing what the repo demonstrably contains, phrased for this job, with **bold** on key terms.
- profile_project: the exact name of the project in the candidate profile that this repo is the code for (compare names, descriptions and tech), or "" if the profile does not contain it.
- improvements: concrete, small changes that would make the repo a stronger match (README, tests, CI, deployment, a missing feature the job wants).

ideas:
- If no repo has relevance 7 or higher, give 2 project ideas built for this job.
- Otherwise give at most 1 idea, aimed at the most important requirement the repos still do not cover (or none if nothing important is missing).
- An idea may extend an existing repo when that is the faster path.
- Each idea: realistic for one developer in 1-3 weeks of evenings, uses the job's required stack, covers its most important gaps, and is something an interviewer would want to discuss. mvp_plan is ordered steps; cv_bullet_preview is the bullet the candidate could honestly write once it is built.

summary: two or three sentences - what to show for this job and what to build next.
Also use the candidate profile to avoid suggesting things they already have."""
    return llm.ask(
        system,
        f"Candidate profile:\n{profile.model_dump_json(indent=2)}\n\nJob:\n{_job_text(job)}\n\nPublic GitHub repositories:\n{repos_text}",
        GithubReview,
    )


def tailor(
    profile: Profile, job: Job | None, role: RoleProfile | None = None, extra_instructions: str = ""
) -> TailoredCV:
    system = f"""You are an expert CV writer. Produce a one-page CV from the candidate's profile, tailored to a specific job posting, to a target role profile, or both.
When a role profile is given, it is the candidate's chosen positioning: use its headline as a starting point, follow its focus, and lead with its key skills that the profile actually supports. When a job posting is also given, the posting's requirements win where the two conflict.
When there is no job posting, tailor to the role profile in general, and judge match_score, matched_requirements and missing_requirements against what employers typically require for that role.
How to tailor:
- headline and summary: aim them at this role, using the job's own terms where they truthfully apply.
- experience bullets: put the most relevant first, lead with impact and numbers, mirror the job's keywords honestly, drop or merge bullets that do not help for this job (keep at least 3 per role).
- projects: order by relevance, drop clearly irrelevant ones if space is tight.
- skills: group and order so the job's required technologies that the candidate has appear first; omit irrelevant tools if the list is long. Never omit a technology, practice or skill the posting mentions when the profile shows it (e.g. Docker, CI/CD, a database, concurrency, testing) - those are the keywords screeners look for.
- keep soft_skills, interests, languages and education accurate; trim soft_skills to the ones that matter for the job.
- in bullets, wrap the 1-3 most important phrases in **double asterisks** so they render bold.
Then report: match_score (0-100, honest), matched_requirements, missing_requirements (job requirements the profile does not show), changes_made (what you changed and why, short), questions_to_strengthen (questions to ask the candidate whose answers could close the gaps truthfully).
{TRUTH_RULES}"""
    content = f"Candidate profile:\n{profile.model_dump_json(indent=2)}"
    if role:
        content += f"\n\nTarget role profile:\n{role.model_dump_json(include={'name', 'headline', 'focus', 'key_skills'}, indent=2)}"
    if job:
        content += f"\n\nJob:\n{_job_text(job)}"
    if extra_instructions.strip():
        content += f"\n\nExtra instructions from the candidate:\n{extra_instructions.strip()}"
    return llm.ask(system, content, TailoredCV)


LETTER_LENGTHS = {"short": "about 150-200 words, 3 paragraphs", "standard": "about 250-350 words, 4 paragraphs"}


def cover_letter(
    profile: Profile,
    job: Job,
    cv: CVContent | None = None,
    language: str = "English",
    tone: str = "professional",
    length: str = "standard",
    extra_instructions: str = "",
) -> CoverLetter:
    system = f"""You write a cover letter for one job application, in {language}, with a {tone} tone, {LETTER_LENGTHS.get(length, LETTER_LENGTHS["standard"])}.
- greeting: address the hiring manager by name only if the posting names them, otherwise a neutral greeting such as "Dear Hiring Team,".
- paragraphs: the body only (no address block, no greeting, no sign-off). Open with the specific role and one concrete reason this candidate fits it - no clichés like "I am writing to apply". Then 1-2 paragraphs that connect the candidate's strongest, most relevant evidence (projects, results, numbers) to the posting's most important requirements, using the posting's own terms where they truthfully apply. Say briefly why this company or role appeals, using only what the posting says about the company. End with a short, confident closing paragraph.
- closing: the sign-off line only, e.g. "Kind regards,". The candidate's name is added separately.
- emphasised: the requirements of the job the letter addresses, short.
- left_out: requirements the candidate's profile does not support, which the letter therefore does not claim.
Plain text, no markdown. Do not repeat the CV line by line; pick the 2-3 strongest points.
When a tailored CV is given, stay consistent with it.
{TRUTH_RULES}"""
    content = f"Candidate profile:\n{profile.model_dump_json(indent=2)}\n\nJob:\n{_job_text(job)}"
    if cv:
        content += f"\n\nTailored CV sent with this letter:\n{cv.model_dump_json(indent=2)}"
    if extra_instructions.strip():
        content += f"\n\nExtra instructions from the candidate:\n{extra_instructions.strip()}"
    return llm.ask(system, content, CoverLetter)


def interview_prep(profile: Profile, job: Job, fit: FitAssessment | None = None) -> InterviewPrep:
    system = f"""You are a senior engineer and interviewer preparing a candidate for interviews for this specific job.
Ground every answer suggestion in the candidate's real profile; never suggest claiming experience they do not have.
- overview: 2-3 sentences on what this interview process will most likely test and where the candidate is strongest and weakest.
- about_the_role: the key facts from the posting to remember (team, product, stack, responsibilities, must-haves). Only what the posting says - no outside knowledge about the company presented as fact.
- likely_questions: 12-16 questions mixing categories "technical", "behavioural", "role & motivation", "system design" (only if the seniority makes it likely) and "about your CV". For each: why_they_ask, how_to_answer (concrete outline, 2-4 sentences), your_evidence (the specific project, role or fact from the profile to use, or "none - see gap answers").
- star_stories: 3-5 stories built from the candidate's real experience in STAR form, each with the question types it answers (good_for).
- technical_topics: the topics to revise before the interview, priority 1-10 (10 = certain to come up and the candidate is weak on it), why, and what_to_revise as a short list of concrete subtopics.
- gap_answers: for each important requirement the candidate lacks, the question they are likely to get and an honest, positive answer (transferable experience, what they are doing about it).
- questions_to_ask: 5-7 smart questions the candidate can ask the interviewers about this role.
- checklist: short practical to-dos for the days before the interview.
{TRUTH_RULES}"""
    content = f"Candidate profile:\n{profile.model_dump_json(indent=2)}\n\nJob:\n{_job_text(job)}"
    if fit:
        content += f"\n\nEarlier fit assessment of this candidate for this job:\n{fit.model_dump_json(include={'rating', 'verdict', 'pros', 'cons'}, indent=2)}"
    return llm.ask(system, content, InterviewPrep)


CV_CATEGORIES = [
    "Keyword match",
    "Relevant experience first",
    "Impact & numbers",
    "Headline & summary",
    "Skills section",
    "Clarity & length",
]


def compare_cvs(
    baseline: CVContent, tailored: CVContent, job: Job | None, role: RoleProfile | None, keyword_report: str
) -> CVComparison:
    target = f"Job:\n{_job_text(job)}" if job else f"Target role:\n{role.model_dump_json(include={'name', 'headline', 'focus', 'key_skills'}, indent=2)}"
    categories = ", ".join(f'"{c}"' for c in CV_CATEGORIES)
    system = f"""You are a strict recruiter who screens CVs for this {"job" if job else "kind of role"}. Score two versions of the same candidate's CV against it, on the same scale, so they can be compared.
For each CV (baseline = before, tailored = after):
- overall: 0-100, how likely this CV is to get the candidate an interview for this {"job" if job else "role"}.
- categories: exactly these, in this order, each scored 0-10 with a one-sentence comment: {categories}.
- strengths and weaknesses: short, specific to this {"job" if job else "role"}.
Then compare:
- verdict: one or two sentences - is the tailored CV better, by how much, and why.
- improved: what got better from baseline to tailored.
- got_worse: anything useful that was lost or weakened in the tailored version (be honest; empty if nothing).
- still_missing: important requirements neither CV shows.
- next_steps: concrete edits that would raise the tailored CV's score further without inventing facts.
Judge only what is written in each CV. A keyword report measured by the app is included - use it, it is exact.
Do not reward a CV for claims; both CVs come from the same profile, and neither may contain invented facts."""
    content = (
        f"{target}\n\nBaseline CV:\n{baseline.model_dump_json(indent=2)}\n\n"
        f"Tailored CV:\n{tailored.model_dump_json(indent=2)}\n\nKeyword report:\n{keyword_report}"
    )
    return llm.ask(system, content, CVComparison)


def _job_text(job: Job) -> str:
    return json.dumps(
        {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "remote": job.remote,
            "description": job.description,
        },
        ensure_ascii=False,
        indent=2,
    )
