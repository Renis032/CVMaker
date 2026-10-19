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
    role_keywords: list[str] = Field(
        default_factory=lambda: ["developer", "engineer", "programmer"]
    )
    skill_keywords: list[str] = Field(default_factory=lambda: ["java", "c++", "spring"])
    exclude: list[str] = Field(default_factory=lambda: ["sales", "manager", "director"])
    countries: list[str] = Field(default_factory=lambda: ["BG"])
    include_remote: bool = True
    remote_regions: list[str] = Field(
        default_factory=lambda: ["worldwide", "anywhere", "europe", "emea", "eu", "bulgaria"]
    )
    sources: list[str] = Field(default_factory=list)
    max_age_days: int = 30


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
