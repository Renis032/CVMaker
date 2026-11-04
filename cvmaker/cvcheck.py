"""Measured CV checks (no AI): job keyword coverage, and whether the PDF's text layer reads cleanly (ATS)."""

import re
import subprocess
from pathlib import Path

from .models import CVContent, Job, Profile
from .search import _contains

# Common technical terms looked for in postings, in addition to the candidate's own skills.
TECH_TERMS = [
    "java", "spring", "spring boot", "hibernate", "jpa", "maven", "gradle", "junit", "mockito",
    "c++", "c#", ".net", "python", "go", "golang", "rust", "kotlin", "scala", "javascript", "typescript",
    "node.js", "react", "angular", "vue", "html", "css", "sql", "postgresql", "mysql", "oracle", "mongodb",
    "redis", "elasticsearch", "kafka", "rabbitmq", "rest", "graphql", "grpc", "microservices", "docker",
    "kubernetes", "aws", "azure", "gcp", "terraform", "ci/cd", "jenkins", "github actions", "gitlab", "git",
    "linux", "bash", "cmake", "qt", "stl", "multithreading", "concurrency", "performance", "profiling",
    "design patterns", "oop", "solid", "tdd", "unit testing", "integration testing", "agile", "scrum",
    "jira", "security", "oauth", "jwt", "spring security", "cloud", "distributed systems", "algorithms",
    "data structures", "embedded", "opengl", "unreal", "unity", "machine learning", "english", "german",
]


def cv_text(cv: CVContent) -> str:
    parts = [cv.name, cv.headline, cv.summary]
    parts += [f"{g.category} {' '.join(g.items)}" for g in cv.skills]
    for e in cv.experience:
        parts += [e.title, e.company, *e.bullets]
    for p in cv.projects:
        parts += [p.name, p.tech, *p.bullets]
    parts += [f"{e.degree} {e.school}" for e in cv.education]
    parts += cv.soft_skills + cv.languages + cv.interests
    for sec in cv.sections:
        for e in sec.entries:
            parts += [e.title, e.subtitle, *e.bullets]
    return re.sub(r"\*\*", "", "\n".join(parts))


def job_keywords(job: Job | None, profile: Profile, extra: list[str] = ()) -> list[str]:
    """Terms the posting asks for: known tech terms and the candidate's own skills that the posting mentions."""
    terms = {t.lower() for t in TECH_TERMS}
    terms |= {i.lower() for g in profile.skills for i in g.items if 1 < len(i) < 40}
    terms |= {d.skill.lower() for d in profile.skill_details if 1 < len(d.skill) < 40}
    terms |= {t.lower() for t in extra if t}
    if job is None:
        return sorted({t.lower() for t in extra if t})
    text = f"{job.title}\n{job.description}".lower()
    found = [t for t in terms if _contains(text, t)]
    # Drop a term when a longer found term contains it ("spring" inside "spring boot").
    return sorted(t for t in found if not any(t != o and _contains(o, t) for o in found))


def coverage(keywords: list[str], text: str) -> dict[str, bool]:
    low = text.lower()
    return {k: _contains(low, k) for k in keywords}


def pdf_text(pdf_path: str | Path) -> str:
    try:
        result = subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return ""
    return result.stdout if result.returncode == 0 else ""


def ats_checks(cv: CVContent, pdf_path: str | Path, keywords: list[str]) -> list[dict]:
    """What a screening system reads from the PDF: is the text there, in order, with contact details and keywords."""
    text = pdf_text(pdf_path)
    flat = " ".join(text.split()).lower()
    checks = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    if not text:
        add("Text layer", False, "No text could be extracted - is pdftotext installed (poppler-utils)?")
        return checks
    add("Text layer", len(flat) > 500, f"{len(flat)} characters of selectable text")
    add("Name readable", cv.name.lower() in flat, cv.name)
    add("Email readable", bool(cv.contact.email) and cv.contact.email.lower() in flat, cv.contact.email)
    phone_digits = re.sub(r"\D", "", cv.contact.phone)
    add("Phone readable", bool(phone_digits) and phone_digits in re.sub(r"\D", "", text), cv.contact.phone)
    titles = [e.title for e in cv.experience if e.title]
    broken = [t for t in titles if " ".join(t.split()).lower() not in flat]
    add("Job titles in one piece", not broken, ", ".join(broken) if broken else f"all {len(titles)} found")
    headings = [h for h in ("experience", "skills", "education") if h in flat]
    add("Standard section headings", len(headings) == 3, ", ".join(headings) or "none found")
    if keywords:
        lost = [k for k, ok in coverage(keywords, cv_text(cv)).items() if ok and not _contains(flat, k)]
        add("Keywords survive in the PDF", not lost, ", ".join(lost) if lost else "every keyword in the CV is readable")
    return checks
