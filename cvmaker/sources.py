"""Job sources.

Company sources read one employer's job board (config in companies.yaml).
Board sources search a whole job site by keyword.
"""

import hashlib
from datetime import datetime, timezone

from . import db, llm, settings
from .countries import country_name
from .models import Criteria, ExtractedPostings, Job
from .web_fetch import fetch_html, html_to_text, http, page_links


def _iso_from_ms(ms) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def greenhouse(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    with http() as c:
        data = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
    return [
        Job(
            id=f"greenhouse:{slug}:{j['id']}",
            source="greenhouse",
            company=company["name"],
            title=j["title"],
            location=(j.get("location") or {}).get("name", ""),
            remote="remote" in (j.get("location") or {}).get("name", "").lower(),
            url=j.get("absolute_url", ""),
            description=html_to_text(j.get("content", "")),
            posted_at=j.get("first_published") or j.get("updated_at") or "",
        )
        for j in data.get("jobs", [])
    ]


def lever(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    with http() as c:
        data = c.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json()
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        sections = "\n\n".join(
            f"{s.get('text', '')}\n{html_to_text(s.get('content', ''))}" for s in j.get("lists", [])
        )
        jobs.append(
            Job(
                id=f"lever:{slug}:{j['id']}",
                source="lever",
                company=company["name"],
                title=j.get("text", ""),
                location=cats.get("location", ""),
                remote=j.get("workplaceType") == "remote",
                url=j.get("hostedUrl", ""),
                description=f"{j.get('descriptionPlain', '')}\n\n{sections}".strip(),
                posted_at=_iso_from_ms(j.get("createdAt")),
                tags=[t for t in (cats.get("team"), cats.get("commitment")) if t],
            )
        )
    return jobs


def ashby(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    with http() as c:
        data = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json()
    return [
        Job(
            id=f"ashby:{slug}:{j['id']}",
            source="ashby",
            company=company["name"],
            title=j.get("title", ""),
            location=j.get("location", ""),
            remote=bool(j.get("isRemote")),
            url=j.get("jobUrl", ""),
            description=j.get("descriptionPlain", ""),
            posted_at=j.get("publishedAt", ""),
        )
        for j in data.get("jobs", [])
    ]


def smartrecruiters(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    jobs = []
    countries = [c.lower() for c in criteria.countries] or [None]
    with http() as c:
        for country in countries:
            offset = 0
            while True:
                params = {"limit": 100, "offset": offset}
                if country:
                    params["country"] = country
                data = c.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params=params).json()
                for j in data.get("content", []):
                    loc = j.get("location") or {}
                    jobs.append(
                        Job(
                            id=f"smartrecruiters:{slug}:{j['id']}",
                            source="smartrecruiters",
                            company=company["name"],
                            title=j.get("name", ""),
                            location=", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x),
                            remote=bool(loc.get("remote")),
                            url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                            posted_at=j.get("releasedDate", ""),
                            tags=[(j.get("experienceLevel") or {}).get("label", "")],
                        )
                    )
                offset += 100
                if offset >= data.get("totalFound", 0) or offset >= 1000:
                    break
    return jobs


CAREER_PAGE_SYSTEM = """You read the links found on a company's careers page and pick out the ones that are individual job postings.
Return each job posting's title, absolute URL and location (empty string if the page does not show it).
Skip navigation, category, blog, login, social and generic "all jobs" links. Never invent postings that are not in the list."""


def careerpage(company: dict, criteria: Criteria) -> list[Job]:
    """Any company website: render the page, then let Claude pick the job-posting links."""
    url = company["url"]
    cache_key = f"careerpage:{url}"
    postings = db.cache_get(cache_key, settings.CAREER_PAGE_CACHE_HOURS)
    if postings is None:
        html = fetch_html(url, render=company.get("render", False))
        links = page_links(html, url)
        listing = "\n".join(f"- {text} -> {href}" for text, href in links)
        extracted = llm.ask(
            CAREER_PAGE_SYSTEM,
            f"Company: {company['name']}\nCareers page: {url}\n\nLinks on the page:\n{listing}",
            ExtractedPostings,
            effort="low",
        )
        postings = [p.model_dump() for p in extracted.postings]
        db.cache_put(cache_key, postings)
    return [
        Job(
            id=f"careerpage:{hashlib.sha1(p['url'].encode()).hexdigest()[:16]}",
            source="careerpage",
            company=company["name"],
            title=p["title"],
            location=p["location"] or company.get("default_location", ""),
            remote="remote" in p["location"].lower(),
            url=p["url"],
            tags=["render"] if company.get("render") else [],
        )
        for p in postings
    ]


def remotive(criteria: Criteria) -> list[Job]:
    jobs = {}
    with http() as c:
        for kw in criteria.skill_keywords or [""]:
            data = c.get(
                "https://remotive.com/api/remote-jobs",
                params={"category": "software-dev", "search": kw, "limit": 100},
            ).json()
            for j in data.get("jobs", []):
                jobs[j["id"]] = Job(
                    id=f"remotive:{j['id']}",
                    source="remotive",
                    company=j.get("company_name", ""),
                    title=j.get("title", ""),
                    location=j.get("candidate_required_location", ""),
                    remote=True,
                    url=j.get("url", ""),
                    description=html_to_text(j.get("description", "")),
                    posted_at=j.get("publication_date", ""),
                    salary=j.get("salary", ""),
                    tags=j.get("tags", []),
                )
    return list(jobs.values())


def remoteok(criteria: Criteria) -> list[Job]:
    jobs = {}
    with http() as c:
        for kw in criteria.skill_keywords or [""]:
            params = {"tag": kw.replace("+", "plus").replace(" ", "-")} if kw else {}
            data = c.get("https://remoteok.com/api", params=params).json()
            for j in data[1:]:
                salary = ""
                if j.get("salary_min") and str(j["salary_min"]) != "0":
                    salary = f"${j['salary_min']}-{j.get('salary_max', '')}"
                jobs[j["id"]] = Job(
                    id=f"remoteok:{j['id']}",
                    source="remoteok",
                    company=j.get("company", ""),
                    title=j.get("position", ""),
                    location=j.get("location", "") or "Worldwide",
                    remote=True,
                    url=j.get("url", ""),
                    description=html_to_text(j.get("description", "")),
                    posted_at=j.get("date", ""),
                    salary=salary,
                    tags=j.get("tags", []),
                )
    return list(jobs.values())


def _date_posted(max_age_days: int) -> str:
    for limit, value in ((1, "today"), (3, "3days"), (7, "week"), (31, "month")):
        if max_age_days <= limit:
            return value
    return "all"


def jsearch(criteria: Criteria) -> list[Job]:
    """Google Jobs aggregate (LinkedIn, Indeed, Glassdoor, ...) via JSearch on RapidAPI."""
    if not settings.RAPIDAPI_KEY:
        raise RuntimeError("RAPIDAPI_KEY not set - LinkedIn/Indeed results need a JSearch key")
    headers = {"X-RapidAPI-Key": settings.RAPIDAPI_KEY, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    queries = [f"{s} developer" for s in criteria.skill_keywords[:3]] or ["software developer"]
    searches = [(q, country, False) for country in criteria.countries for q in queries]
    if criteria.include_remote:
        searches += [(queries[0], country, True) for country in criteria.countries]
    jobs = {}
    with http() as c:
        for query, country, remote_only in searches:
            params = {
                "query": query,
                "page": 1,
                "num_pages": 1,
                "date_posted": _date_posted(criteria.max_age_days),
                "country": country.lower(),
                "location": country_name(country),
            }
            if remote_only:
                params["work_from_home"] = "true"
            r = c.get("https://jsearch.p.rapidapi.com/search-v2", params=params, headers=headers)
            r.raise_for_status()
            data = r.json().get("data") or {}
            for j in data.get("jobs", []) if isinstance(data, dict) else []:
                loc = j.get("job_location") or ", ".join(x for x in (j.get("job_city"), j.get("job_country")) if x)
                if j.get("job_country") and j["job_country"].lower() not in loc.lower():
                    loc = f"{loc}, {j['job_country']}"
                jobs[j["job_id"]] = Job(
                    id=f"jsearch:{j['job_id']}",
                    source=f"jsearch/{j.get('job_publisher') or 'web'}",
                    company=j.get("employer_name", ""),
                    title=j.get("job_title", ""),
                    location=loc,
                    remote=bool(j.get("job_is_remote")),
                    url=j.get("job_apply_link", ""),
                    description=j.get("job_description", ""),
                    posted_at=j.get("job_posted_at_datetime_utc") or "",
                    salary=j.get("job_salary_string") or "",
                )
    return list(jobs.values())


COMPANY_SOURCES = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "careerpage": careerpage,
}

BOARD_SOURCES = {
    "remotive": remotive,
    "remoteok": remoteok,
    "jsearch": jsearch,
}


def ensure_description(job: Job) -> Job:
    """Some sources only list titles; fetch the full posting before scoring or tailoring."""
    if len(job.description) > 300:
        return job
    if job.source == "smartrecruiters":
        _, slug, posting_id = job.id.split(":", 2)
        with http() as c:
            data = c.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}").json()
        sections = (data.get("jobAd") or {}).get("sections") or {}
        job.description = "\n\n".join(
            html_to_text(s.get("text", "")) for s in sections.values() if isinstance(s, dict)
        )
    elif job.url:
        text = html_to_text(fetch_html(job.url))
        if len(text) < 500 or "render" in job.tags:
            text = html_to_text(fetch_html(job.url, render=True))
        job.description = text
    db.update_job(job)
    return job
