from pydantic import BaseModel, Field, field_validator


def clean_text(value: str) -> str:
    return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


class Job(BaseModel):
    id: str
    source: str
    company: str
    title: str
    location: str = ""
    remote: bool = False
    url: str = ""
    description: str = ""
    posted_at: str = ""
    salary: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, value):
        if isinstance(value, str):
            return clean_text(value)
        if isinstance(value, list):
            return [clean_text(v) if isinstance(v, str) else v for v in value]
        return value


class Criteria(BaseModel):
    # Kinds of job picked (categories.py); they add their title words, skills and board phrases when a search runs.
    categories: list[str] = Field(default_factory=list)
    role_keywords: list[str] = Field(default_factory=list)
    skill_keywords: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=lambda: ["sales", "manager", "director"])
    countries: list[str] = Field(default_factory=lambda: ["BG"])
    include_remote: bool = True
    remote_regions: list[str] = Field(
        default_factory=lambda: ["worldwide", "anywhere", "europe", "emea", "eu", "bulgaria"]
    )
    sources: list[str] = Field(default_factory=list)
    max_age_days: int = 30
    remote_only: bool = False
    seniority: list[str] = Field(default_factory=list)
    must_mention: list[str] = Field(default_factory=list)
    salary_only: bool = False
    min_score: int = 0
    # Filled in from the categories when a search runs (phrases the job boards are searched for); not saved.
    queries: list[str] = Field(default_factory=list, exclude=True)


class RoleProfile(BaseModel):
    id: str
    name: str
    headline: str = ""
    focus: str = ""
    key_skills: list[str] = Field(default_factory=list)
    role_keywords: list[str] = Field(default_factory=list)
    skill_keywords: list[str] = Field(default_factory=list)


class Contact(BaseModel):
    email: str
    phone: str
    linkedin: str
    github: str
    location: str
    # Anything else the CV lists as contact: website, portfolio, address, date of birth, nationality...
    other: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class Experience(BaseModel):
    title: str
    company: str
    start: str
    end: str
    location: str
    bullets: list[str]


class Project(BaseModel):
    name: str
    tech: str
    link: str
    bullets: list[str]


class Education(BaseModel):
    degree: str
    school: str
    years: str


class CustomEntry(BaseModel):
    title: str
    subtitle: str  # issuer, organisation, publisher, place...
    dates: str
    bullets: list[str]


class CustomSection(BaseModel):
    """A section the fixed ones don't cover: certifications, awards, volunteering, publications, courses,
    references... Kept with the CV's own heading."""
    title: str
    entries: list[CustomEntry]


class CVContent(BaseModel):
    name: str
    headline: str
    contact: Contact
    summary: str
    skills: list[SkillGroup]
    experience: list[Experience]
    projects: list[Project]
    education: list[Education]
    soft_skills: list[str]
    languages: list[str]
    interests: list[str]
    sections: list[CustomSection] = Field(default_factory=list)


class SkillDetail(BaseModel):
    skill: str
    level: str
    years: str
    evidence: str


class Profile(CVContent):
    skill_details: list[SkillDetail]
    extra_facts: list[str]
    preferences: list[str]


class TailoredCV(BaseModel):
    cv: CVContent
    match_score: int
    matched_requirements: list[str]
    missing_requirements: list[str]
    changes_made: list[str]
    questions_to_strengthen: list[str]


class InterviewQuestion(BaseModel):
    id: str
    question: str
    why: str


class InterviewQuestions(BaseModel):
    questions: list[InterviewQuestion]


class FitPoint(BaseModel):
    point: str
    rating: int


class ConPoint(BaseModel):
    point: str
    rating: int
    advice: str


class GapArea(BaseModel):
    area: str
    severity: int
    why_it_matters: str
    how_to_close: str
    time_to_close: str


class CVProjectType(BaseModel):
    title: str
    description: str
    demonstrates: list[str]
    why_employers_care: str


class GapPlan(BaseModel):
    recommendation: str
    advice: str
    lacking_areas: list[GapArea]
    project_types: list[CVProjectType]
    action_plan: list[str]
    interview_framing: list[str]
    closer_roles: list[str]


class FitAssessment(BaseModel):
    rating: int
    verdict: str
    pros: list[FitPoint]
    cons: list[ConPoint]
    gap_plan: GapPlan


class QuickFit(BaseModel):
    job_id: str
    rating: int
    strong: list[str]
    missing: list[str]
    verdict: str


class QuickFits(BaseModel):
    fits: list[QuickFit]


class RepoMatch(BaseModel):
    repo: str
    url: str
    language: str
    profile_project: str
    relevance: int
    why: str
    covers_requirements: list[str]
    cv_bullets: list[str]
    improvements: list[str]


class ProjectIdea(BaseModel):
    title: str
    pitch: str
    covers_requirements: list[str]
    tech_stack: list[str]
    features: list[str]
    mvp_plan: list[str]
    stretch_goals: list[str]
    estimated_effort: str
    cv_bullet_preview: str


class GithubReview(BaseModel):
    summary: str
    matches: list[RepoMatch]
    ideas: list[ProjectIdea]


class ExtractedPosting(BaseModel):
    title: str
    url: str
    location: str


class ExtractedPostings(BaseModel):
    postings: list[ExtractedPosting]


class CoverLetter(BaseModel):
    greeting: str
    paragraphs: list[str]
    closing: str
    emphasised: list[str]
    left_out: list[str]


class PrepQuestion(BaseModel):
    category: str
    question: str
    why_they_ask: str
    how_to_answer: str
    your_evidence: str


class StarStory(BaseModel):
    title: str
    situation: str
    task: str
    action: str
    result: str
    good_for: list[str]


class PrepTopic(BaseModel):
    topic: str
    priority: int
    why: str
    what_to_revise: list[str]


class GapAnswer(BaseModel):
    gap: str
    likely_question: str
    honest_answer: str


class InterviewPrep(BaseModel):
    overview: str
    about_the_role: list[str]
    likely_questions: list[PrepQuestion]
    star_stories: list[StarStory]
    technical_topics: list[PrepTopic]
    gap_answers: list[GapAnswer]
    questions_to_ask: list[str]
    checklist: list[str]


class CategoryScore(BaseModel):
    category: str
    score: int
    comment: str


class CVScore(BaseModel):
    overall: int
    categories: list[CategoryScore]
    strengths: list[str]
    weaknesses: list[str]


class CVComparison(BaseModel):
    baseline: CVScore
    tailored: CVScore
    verdict: str
    improved: list[str]
    got_worse: list[str]
    still_missing: list[str]
    next_steps: list[str]


class GapCluster(BaseModel):
    gap: str
    job_refs: list[str]
    severity: int
    proof_only: bool
    why: str
    how_to_close: str
    time_to_close: str


class GapAnalysis(BaseModel):
    summary: str
    learn_next: list[str]
    clusters: list[GapCluster]


# --- adding a job by link or pasted text ------------------------------------------------------------------


class ParsedPosting(BaseModel):
    is_job_posting: bool
    title: str
    company: str
    location: str
    remote: bool
    salary: str
    posted_at: str
    apply_url: str
    description: str


# --- posting checks -----------------------------------------------------------------------------------------


class PostingFlag(BaseModel):
    kind: str  # "legitimacy", "work permit", "repost", "pay", "vague", "other"
    severity: str  # "red", "amber", "info"
    finding: str
    evidence: str
    what_to_do: str


class PostingReview(BaseModel):
    verdict: str
    trust: int  # 1-10, how much the posting looks like a real, open job
    work_permit: str  # what the posting says about location / work permit / sponsorship, in the candidate's terms
    flags: list[PostingFlag]
    questions_to_ask: list[str]


# --- outreach ----------------------------------------------------------------------------------------------


class ContactLead(BaseModel):
    who: str  # role or name, e.g. "Engineering manager of the payments team"
    why: str
    search_terms: str  # what to type into LinkedIn people search
    from_posting: bool


class DraftEmail(BaseModel):
    subject: str
    body: str


class OutreachKit(BaseModel):
    contacts: list[ContactLead]
    linkedin_note: str  # connection request note, 300 characters at most
    linkedin_message: str  # after they accept
    application_email: DraftEmail
    follow_up_email: DraftEmail
    tips: list[str]


# --- story bank, practice, debriefs -----------------------------------------------------------------------


class Story(BaseModel):
    id: str = ""
    title: str
    situation: str
    task: str
    action: str
    result: str
    reflection: str = ""
    skills: list[str] = Field(default_factory=list)
    good_for: list[str] = Field(default_factory=list)
    fill_in: list[str] = Field(default_factory=list)  # details only the candidate can add
    source: str = "manual"
    updated: float = 0


class DraftStory(BaseModel):
    title: str
    situation: str
    task: str
    action: str
    result: str
    reflection: str
    skills: list[str]
    good_for: list[str]
    fill_in: list[str]


class DraftStories(BaseModel):
    stories: list[DraftStory]


class PracticeQuestion(BaseModel):
    category: str
    question: str
    why_they_ask: str


class PracticeFeedback(BaseModel):
    score: int  # 1-10
    verdict: str
    worked: list[str]
    improve: list[str]
    stronger_answer: str
    follow_up_question: str
    story_to_use: str


class DebriefReview(BaseModel):
    summary: str
    went_well: list[str]
    improve: list[ConPoint]
    prepare_next: list[str]
    signals: list[str]
    story_candidates: list[DraftStory]
    thank_you_email: DraftEmail


# --- offers --------------------------------------------------------------------------------------------------


class OfferTerm(BaseModel):
    term: str
    value: str
    comment: str


class OfferClause(BaseModel):
    clause: str
    quote: str
    risk: str  # "red", "amber", "green"
    explanation: str
    what_to_ask: str


class NegotiationPoint(BaseModel):
    point: str
    why: str
    wording: str


class OfferReview(BaseModel):
    summary: str
    terms: list[OfferTerm]
    clauses: list[OfferClause]
    missing: list[str]
    salary_view: str
    negotiation: list[NegotiationPoint]
    questions_before_signing: list[str]
    reply_email: DraftEmail


class ChatReply(BaseModel):
    """One answer in the chat. When the candidate asks to change their profile (their original CV), Claude puts
    the whole updated profile in new_profile_json and lists the changes; the candidate applies them with a click."""
    reply: str
    profile_changes: list[str]
    new_profile_json: str



class RewriteResult(BaseModel):
    """Claude's rewrites of one piece of CV text (CV editor)."""

    options: list[str] = Field(default_factory=list)
    note: str = ""
