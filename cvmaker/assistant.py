"""Everything that asks Claude about the candidate: CV import, interview, fit check, tailoring."""

import json

from . import llm, settings
from .models import FitAssessment, GithubReview, InterviewQuestions, Job, Profile, RoleProfile, TailoredCV

TRUTH_RULES = """Ground rules - these override everything else:
- Only use facts that appear in the candidate's profile. Never invent employers, dates, titles, degrees, certifications, metrics, technologies or years of experience.
- You may reword, reorder, regroup, shorten and choose emphasis. You may describe existing work using the employer's vocabulary when it is an honest description of that work (e.g. "high-throughput simulation" for a simulation engine the profile describes).
- A skill the job wants but the profile does not show goes into missing_requirements, never into the CV.
- Keep numbers exactly as given in the profile."""


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


def assess_fit(profile: Profile, job: Job, repos_text: str = "") -> FitAssessment:
    system = """You are an honest technical recruiter and career mentor judging how well this candidate fits this job.
rating: overall fit from 1 (no realistic chance) to 10 (ideal candidate).
verdict: one sentence.
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
    return llm.ask(system, content, FitAssessment)


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
- skills: group and order so the job's required technologies that the candidate has appear first; omit irrelevant tools if the list is long.
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
