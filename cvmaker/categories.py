"""Kinds of job (Java developer, DevOps, QA tester, automation tester, project manager...), used in two places:

- the job search: picking categories adds their title words (a job's title must contain one), skills (rank jobs and
  search the boards) and board search phrases to a search, next to anything typed by hand;
- a blank CV: a category sets its structure only (skill group labels, extra sections, headings), never any wording.
"""

from .models import Criteria

CATEGORIES: dict[str, dict] = {
    "developer": {
        "name": "Software developer",
        "titles": ["developer", "software engineer", "programmer", "software"],
        "skills": [],
        "queries": ["software developer"],
        "cv_skills": ["Languages", "Frameworks", "Databases", "Tools"],
    },
    "java": {
        "name": "Java developer",
        "titles": ["java", "backend", "back-end", "back end"],
        "skills": ["java", "spring", "spring boot", "hibernate", "microservices"],
        "queries": ["java developer"],
        "cv_skills": ["Languages", "Java & Spring", "Databases", "Build & CI", "Tools"],
    },
    "frontend": {
        "name": "Frontend developer",
        "titles": ["frontend", "front-end", "front end", "react", "angular", "vue", "ui developer", "web developer"],
        "skills": ["javascript", "typescript", "react", "angular", "vue", "css"],
        "queries": ["frontend developer"],
        "cv_skills": ["Languages", "Frameworks", "Styling", "Testing", "Tools"],
    },
    "python": {
        "name": "Python / data engineer",
        "titles": ["python", "django", "data engineer"],
        "skills": ["python", "django", "fastapi", "pandas", "spark", "airflow"],
        "queries": ["python developer", "data engineer"],
        "cv_skills": ["Languages", "Frameworks & libraries", "Data", "Cloud", "Tools"],
    },
    "devops": {
        "name": "DevOps / cloud engineer",
        "titles": ["devops", "sre", "site reliability", "platform engineer", "cloud engineer", "infrastructure engineer"],
        "skills": ["kubernetes", "docker", "terraform", "aws", "azure", "ci/cd", "ansible"],
        "queries": ["devops engineer", "cloud engineer"],
        "cv_skills": ["Cloud", "Containers & orchestration", "Infrastructure as code", "CI/CD", "Monitoring", "Scripting"],
        "cv_sections": ["Certifications"],
    },
    "qa": {
        "name": "QA / manual tester",
        "titles": ["qa", "quality assurance", "tester", "test engineer", "test analyst"],
        "skills": ["manual testing", "test cases", "regression testing", "jira", "testrail"],
        "queries": ["qa engineer", "software tester"],
        "cv_skills": ["Testing types", "Test management", "Bug tracking", "Tools"],
        "cv_sections": ["Certifications"],
    },
    "automation": {
        "name": "Automation tester",
        "titles": ["automation", "sdet", "qa automation", "test automation"],
        "skills": ["selenium", "cypress", "playwright", "appium", "api testing", "postman"],
        "queries": ["qa automation engineer", "sdet"],
        "cv_skills": ["Languages", "Automation frameworks", "API testing", "CI/CD", "Tools"],
        "cv_sections": ["Certifications"],
    },
    "pm": {
        "name": "Project manager",
        "titles": ["project manager", "delivery manager", "scrum master", "program manager", "programme manager"],
        "skills": ["agile", "scrum", "jira", "pmp", "stakeholder management", "budget"],
        "queries": ["it project manager", "scrum master"],
        "cv_skills": ["Methodologies", "Planning & delivery", "Tools"],
        "cv_sections": ["Certifications"],
        "cv_headings": {"projects": "Key projects"},
    },
    "product": {
        "name": "Product manager",
        "titles": ["product manager", "product owner"],
        "skills": ["product management", "roadmap", "user research", "analytics", "a/b testing"],
        "queries": ["product manager", "product owner"],
        "cv_skills": ["Product", "Analytics", "Tools"],
        "cv_headings": {"projects": "Products"},
    },
    "ba": {
        "name": "Business analyst",
        "titles": ["business analyst", "systems analyst", "business systems analyst"],
        "skills": ["requirements", "bpmn", "uml", "sql", "user stories"],
        "queries": ["business analyst"],
        "cv_skills": ["Analysis", "Modelling", "Tools"],
        "cv_sections": ["Certifications"],
    },
    "data": {
        "name": "Data analyst",
        "titles": ["data analyst", "bi analyst", "bi developer", "analytics", "reporting analyst"],
        "skills": ["sql", "power bi", "tableau", "excel", "python"],
        "queries": ["data analyst", "bi analyst"],
        "cv_skills": ["Languages", "BI & visualisation", "Databases", "Statistics"],
    },
    "design": {
        "name": "UI / UX designer",
        "titles": ["designer", "ux", "ui/ux", "ui ux", "product design"],
        "skills": ["figma", "user research", "prototyping", "design systems", "wireframes"],
        "queries": ["ui ux designer", "product designer"],
        "cv_skills": ["Design", "Prototyping", "Research", "Tools"],
        "cv_headings": {"projects": "Portfolio"},
    },
    "support": {
        "name": "IT support / sysadmin",
        "titles": ["support", "helpdesk", "help desk", "system administrator", "sysadmin", "it specialist", "service desk"],
        "skills": ["windows server", "linux", "active directory", "networking", "itil", "office 365"],
        "queries": ["it support", "system administrator"],
        "cv_skills": ["Systems", "Networking", "Ticketing", "Tools"],
        "cv_sections": ["Certifications"],
    },
}


def names(ids: list[str]) -> list[str]:
    return [CATEGORIES[i]["name"] for i in ids if i in CATEGORIES]


def _merge(*lists: list[str]) -> list[str]:
    out: list[str] = []
    for items in lists:
        for x in items:
            if x and x.lower() not in {o.lower() for o in out}:
                out.append(x)
    return out


def expand(criteria: Criteria) -> Criteria:
    """The search as it runs: the picked categories' title words, skills and board phrases added to what was typed.
    An excluded word that a picked category needs in its titles ("manager" for project managers) is not applied."""
    picked = [CATEGORIES[i] for i in criteria.categories if i in CATEGORIES]
    if not picked:
        return criteria
    titles = [t for c in picked for t in c["titles"]]
    return criteria.model_copy(update={
        "role_keywords": _merge(criteria.role_keywords, titles),
        "skill_keywords": _merge(criteria.skill_keywords, *[c["skills"] for c in picked]),
        "queries": _merge(*[c["queries"] for c in picked]),
        "exclude": [x for x in criteria.exclude if not any(x.lower() in t.lower() for t in titles)],
    })
