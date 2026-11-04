"""Job sources.

Company sources read one employer's job board (config in companies.yaml).
Board sources search a whole job site by keyword.
"""

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from . import db, llm, settings
from .countries import country_name
from .models import Criteria, ExtractedPostings, Job
from .salary import find_salary, format_salary
from .web_fetch import fetch_html, html_to_text, http, page_links



def search_terms(criteria: Criteria, limit: int, default: list[str] | None = None) -> list[str]:
    """What to type into a job board's search: the picked job categories' phrases ("qa automation engineer") first,
    then the skills."""
    terms: list[str] = []
    for t in [*criteria.queries, *criteria.skill_keywords]:
        if t and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)
    return terms[:limit] or (default if default is not None else ["developer"])

def _iso_from_ms(ms) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def _iso_from_s(seconds) -> str:
    return _iso_from_ms(int(seconds) * 1000) if seconds else ""


def _short_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


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
                salary=format_salary(
                    (j.get("salaryRange") or {}).get("min"),
                    (j.get("salaryRange") or {}).get("max"),
                    (j.get("salaryRange") or {}).get("currency"),
                    (j.get("salaryRange") or {}).get("interval", "").replace("per-", ""),
                ),
                tags=[t for t in (cats.get("team"), cats.get("commitment")) if t],
            )
        )
    return jobs


def ashby(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    with http() as c:
        data = c.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"}
        ).json()
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
            salary=(j.get("compensation") or {}).get("scrapeableCompensationSalarySummary")
            or (j.get("compensation") or {}).get("compensationTierSummary")
            or "",
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


def workable(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    jobs = []
    payload: dict = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    with http() as c:
        for _ in range(20):
            r = c.post(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs", json=payload)
            r.raise_for_status()
            data = r.json()
            for j in data.get("results", []):
                loc = j.get("location") or {}
                jobs.append(
                    Job(
                        id=f"workable:{slug}:{j['shortcode']}",
                        source="workable",
                        company=company["name"],
                        title=j.get("title", ""),
                        location=", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                        remote=bool(j.get("remote")) or j.get("workplace") == "remote",
                        url=f"https://apply.workable.com/{slug}/j/{j['shortcode']}/",
                        posted_at=j.get("published", ""),
                        tags=[t for t in [j.get("workplace", "")] + (j.get("department") or []) if t],
                    )
                )
            if not data.get("nextPage"):
                break
            payload["token"] = data["nextPage"]
    return jobs


def recruitee(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    with http() as c:
        data = c.get(f"https://{slug}.recruitee.com/api/offers/").json()
    jobs = []
    for j in data.get("offers", []):
        pay = j.get("salary") or {}
        jobs.append(
            Job(
                id=f"recruitee:{slug}:{j['id']}",
                source="recruitee",
                company=company["name"],
                title=j.get("title", ""),
                location=j.get("location", ""),
                remote=bool(j.get("remote")),
                url=j.get("careers_url", ""),
                description=html_to_text(f"{j.get('description', '')}\n{j.get('requirements', '')}"),
                posted_at=(j.get("published_at") or "").replace(" UTC", "+00:00"),
                salary=format_salary(pay.get("min"), pay.get("max"), pay.get("currency"), pay.get("period")),
                tags=["hybrid"] if j.get("hybrid") else [],
            )
        )
    return jobs


def personio(company: dict, criteria: Criteria) -> list[Job]:
    slug = company["slug"]
    with http() as c:
        r = c.get(f"https://{slug}.jobs.personio.de/xml", params={"language": "en"})
        r.raise_for_status()
    jobs = []
    for pos in ET.fromstring(r.content).iter("position"):
        offices = [pos.findtext("office", "")] + [o.text or "" for o in pos.iterfind("additionalOffices/office")]
        sections = "\n\n".join(
            f"{d.findtext('name', '')}\n{html_to_text(d.findtext('value', ''))}"
            for d in pos.iterfind("jobDescriptions/jobDescription")
        )
        location = ", ".join(o for o in offices if o)
        jobs.append(
            Job(
                id=f"personio:{slug}:{pos.findtext('id')}",
                source="personio",
                company=company["name"],
                title=pos.findtext("name", ""),
                location=location,
                remote="remote" in location.lower(),
                url=f"https://{slug}.jobs.personio.de/job/{pos.findtext('id')}",
                description=sections.strip(),
                posted_at=pos.findtext("createdAt", ""),
                tags=[t for t in (pos.findtext("seniority"), pos.findtext("schedule")) if t],
            )
        )
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
            id=f"careerpage:{_short_id(p['url'])}",
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
        for kw in search_terms(criteria, 6, [""]):
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


BG_MONTHS = ("яну", "фев", "мар", "апр", "май", "юни", "юли", "авг", "сеп", "окт", "ное", "дек")


def _devbg_date(text: str) -> str:
    """dev.bg shows '23 окт.' without a year: assume the latest such date that is not in the future."""
    m = re.match(r"(\d{1,2})\s+(\w{3})", text.strip().lower())
    if not m or m.group(2) not in BG_MONTHS:
        return ""
    now = datetime.now(timezone.utc)
    month = BG_MONTHS.index(m.group(2)) + 1
    year = now.year if (month, int(m.group(1))) <= (now.month, now.day) else now.year - 1
    try:
        return datetime(year, month, int(m.group(1)), tzinfo=timezone.utc).isoformat()
    except ValueError:
        return ""


def _devbg_item(item) -> Job | None:
    link = item.select_one("a.overlay-link")
    title = item.select_one(".job-title")
    if not link or not title:
        return None
    location, salary, tags, remote = "", "", [], False
    for badge in item.select(".tags-wrap .badge"):
        for tip in badge.select(".tooltip"):
            tip.decompose()
        text = " ".join(badge.get_text(" ").split())
        if re.search(r"\d.*(€|лв|BGN|EUR)", text):
            kind = " net" if "Нетна" in text else " gross" if "Брутна" in text else ""
            salary = re.split(r"Нетна|Брутна", text)[0].strip() + kind + "/month"
        elif text:
            remote = remote or "remote" in text.lower()
            if "hybrid" in text.lower():
                tags.append("hybrid")
            place = re.sub(r"\s*(Hybrid|Fully Remote)\s*", " ", text).strip()
            location = f"{place}, Bulgaria" if place else "Remote, Bulgaria"
    company = item.select_one(".company-name")
    date = item.select_one(".date")
    tags += [img["title"] for img in item.select(".tech-stack-wrap img[title]")]
    return Job(
        id=f"devbg:{item.get('data-job-id') or _short_id(link['href'])}",
        source="devbg",
        company=company.get_text(strip=True) if company else "",
        title=title.get_text(strip=True),
        location=location or "Bulgaria",
        remote=remote,
        url=link["href"],
        posted_at=_devbg_date(date.get_text()) if date else "",
        salary=salary,
        tags=tags,
    )


def devbg(criteria: Criteria) -> list[Job]:
    """dev.bg, the main Bulgarian IT job board (keyword search, 20 jobs per page)."""
    jobs = {}
    with http() as c:
        for kw in search_terms(criteria, 5, criteria.role_keywords[:5] or ["developer"]):
            for page in (1, 2, 3):
                r = c.get("https://dev.bg/", params={"s": kw, "post_type": "job_listing", "_paged": page})
                r.raise_for_status()
                items = BeautifulSoup(r.text, "html.parser").select("div.job-list-item")
                for item in items:
                    job = _devbg_item(item)
                    if job:
                        jobs[job.id] = job
                if len(items) < 20:
                    break
    return list(jobs.values())


def arbeitnow(criteria: Criteria) -> list[Job]:
    """Arbeitnow: jobs in Germany and remote jobs from many company job boards, newest first."""
    jobs = []
    with http() as c:
        for page in (1, 2):
            data = c.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page}).json()
            for j in data.get("data", []):
                jobs.append(
                    Job(
                        id=f"arbeitnow:{j['slug']}",
                        source="arbeitnow",
                        company=j.get("company_name", ""),
                        title=j.get("title", ""),
                        location=j.get("location", ""),
                        remote=bool(j.get("remote")),
                        url=j.get("url", ""),
                        description=html_to_text(j.get("description", "")),
                        posted_at=_iso_from_s(j.get("created_at")),
                        tags=(j.get("tags") or []) + (j.get("job_types") or []),
                    )
                )
            if not (data.get("links") or {}).get("next"):
                break
    return jobs


def himalayas(criteria: Criteria) -> list[Job]:
    """Himalayas: remote jobs, with the countries each job accepts and often a salary."""
    jobs = {}
    with http() as c:
        for kw in search_terms(criteria, 4):
            for page in (1, 2):
                data = c.get("https://himalayas.app/jobs/api/search", params={"q": kw, "page": page}).json()
                for j in data.get("jobs", []):
                    url = j.get("applicationLink") or j.get("guid", "")
                    countries = j.get("locationRestrictions") or []
                    jobs[url] = Job(
                        id=f"himalayas:{_short_id(j.get('guid') or url)}",
                        source="himalayas",
                        company=j.get("companyName", ""),
                        title=j.get("title", ""),
                        location=", ".join(countries) if countries else "Worldwide",
                        remote=True,
                        url=url,
                        description=html_to_text(j.get("description", "")),
                        posted_at=_iso_from_s(j.get("pubDate")),
                        salary=format_salary(
                            j.get("minSalary"), j.get("maxSalary"), j.get("currency"), j.get("salaryPeriod")
                        ),
                        tags=(j.get("seniority") or []) + [j.get("employmentType") or ""],
                    )
                if len(data.get("jobs", [])) < 20:
                    break
    return list(jobs.values())


def jobicy(criteria: Criteria) -> list[Job]:
    """Jobicy: remote jobs with region restrictions and often a salary."""
    jobs = {}
    with http() as c:
        for kw in search_terms(criteria, 4):
            data = c.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": kw}).json()
            for j in data.get("jobs", []):
                jobs[j["id"]] = Job(
                    id=f"jobicy:{j['id']}",
                    source="jobicy",
                    company=j.get("companyName", ""),
                    title=j.get("jobTitle", ""),
                    location=j.get("jobGeo", "") or "Anywhere",
                    remote=True,
                    url=j.get("url", ""),
                    description=html_to_text(j.get("jobDescription", "")),
                    posted_at=j.get("pubDate", ""),
                    salary=format_salary(
                        j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency"), j.get("salaryPeriod")
                    ),
                    tags=[t for t in [j.get("jobLevel", "")] + (j.get("jobType") or []) if t],
                )
    return list(jobs.values())


WWR_CATEGORIES = (
    "remote-programming-jobs",
    "remote-full-stack-programming-jobs",
    "remote-back-end-programming-jobs",
    "remote-devops-sysadmin-jobs",
)


def weworkremotely(criteria: Criteria) -> list[Job]:
    """We Work Remotely: RSS feeds of the programming categories."""
    jobs = {}
    with http() as c:
        for category in WWR_CATEGORIES:
            r = c.get(f"https://weworkremotely.com/categories/{category}.rss")
            r.raise_for_status()
            for item in ET.fromstring(r.content).iter("item"):
                company, _, title = (item.findtext("title") or "").partition(": ")
                link = item.findtext("link") or ""
                try:
                    posted = parsedate_to_datetime(item.findtext("pubDate") or "").isoformat()
                except (TypeError, ValueError):
                    posted = ""
                jobs[link] = Job(
                    id=f"weworkremotely:{_short_id(link)}",
                    source="weworkremotely",
                    company=company if title else "",
                    title=title or company,
                    location=item.findtext("region") or "Anywhere",
                    remote=True,
                    url=link,
                    description=html_to_text(item.findtext("description") or ""),
                    posted_at=posted,
                    tags=[item.findtext("category") or ""],
                )
    return list(jobs.values())


ADZUNA_COUNTRIES = {"AT", "AU", "BE", "BR", "CA", "CH", "DE", "ES", "FR", "GB", "IN", "IT", "MX", "NL", "NZ", "PL", "SG", "US", "ZA"}


def adzuna(criteria: Criteria) -> list[Job]:
    """Adzuna: large job search engine for the countries it covers (not Bulgaria). Free API key."""
    if not (settings.ADZUNA_APP_ID and settings.ADZUNA_APP_KEY):
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set - get a free key at developer.adzuna.com")
    countries = [c for c in criteria.countries if c.upper() in ADZUNA_COUNTRIES]
    jobs = {}
    with http() as c:
        for country in countries:
            for kw in search_terms(criteria, 3):
                r = c.get(
                    f"https://api.adzuna.com/v1/api/jobs/{country.lower()}/search/1",
                    params={
                        "app_id": settings.ADZUNA_APP_ID,
                        "app_key": settings.ADZUNA_APP_KEY,
                        "what": kw,
                        "max_days_old": criteria.max_age_days,
                        "results_per_page": 50,
                        "content-type": "application/json",
                    },
                )
                r.raise_for_status()
                for j in r.json().get("results", []):
                    predicted = str(j.get("salary_is_predicted")) == "1"
                    loc = (j.get("location") or {}).get("display_name", "")
                    jobs[j["id"]] = Job(
                        id=f"adzuna:{j['id']}",
                        source="adzuna",
                        company=(j.get("company") or {}).get("display_name", ""),
                        title=html_to_text(j.get("title", "")),
                        location=f"{loc}, {country_name(country)}" if loc else country_name(country),
                        remote="remote" in f"{j.get('title', '')} {j.get('description', '')}".lower(),
                        url=j.get("redirect_url", ""),
                        description=html_to_text(j.get("description", "")),
                        posted_at=j.get("created", ""),
                        salary="" if predicted else format_salary(j.get("salary_min"), j.get("salary_max"), "", "year"),
                        tags=[t for t in (j.get("contract_time"), j.get("contract_type")) if t],
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
    queries = criteria.queries[:3] or [f"{s} developer" for s in criteria.skill_keywords[:3]] or ["software developer"]
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
    "workable": workable,
    "recruitee": recruitee,
    "personio": personio,
    "careerpage": careerpage,
}

BOARD_SOURCES = {
    "devbg": devbg,
    "arbeitnow": arbeitnow,
    "remotive": remotive,
    "remoteok": remoteok,
    "himalayas": himalayas,
    "jobicy": jobicy,
    "weworkremotely": weworkremotely,
    "jsearch": jsearch,
    "adzuna": adzuna,
}

# When each board runs: countries it covers (None = any), whether it lists remote jobs, which key it needs.
BOARD_INFO = {
    "devbg": {"label": "dev.bg (Bulgaria)", "countries": {"BG"}, "remote": False, "key": ""},
    "arbeitnow": {"label": "Arbeitnow (Germany + remote)", "countries": {"DE", "AT"}, "remote": True, "key": ""},
    "remotive": {"label": "Remotive (remote)", "countries": set(), "remote": True, "key": ""},
    "remoteok": {"label": "RemoteOK (remote)", "countries": set(), "remote": True, "key": ""},
    "himalayas": {"label": "Himalayas (remote)", "countries": set(), "remote": True, "key": ""},
    "jobicy": {"label": "Jobicy (remote)", "countries": set(), "remote": True, "key": ""},
    "weworkremotely": {"label": "We Work Remotely (remote)", "countries": set(), "remote": True, "key": ""},
    "jsearch": {"label": "JSearch (LinkedIn/Indeed/Glassdoor)", "countries": None, "remote": True, "key": "RAPIDAPI_KEY"},
    "adzuna": {"label": "Adzuna (DE, NL, GB, AT, PL, ES, ...)", "countries": ADZUNA_COUNTRIES, "remote": False, "key": "ADZUNA_APP_KEY"},
}


def board_has_key(name: str) -> bool:
    key = BOARD_INFO[name]["key"]
    return not key or bool(getattr(settings, key, ""))


def board_applies(name: str, criteria: Criteria) -> bool:
    info = BOARD_INFO[name]
    if info["countries"] is None:
        return True
    countries = {c.upper() for c in criteria.countries}
    if criteria.remote_only:
        return info["remote"] and criteria.include_remote
    return bool(countries & info["countries"]) or (info["remote"] and criteria.include_remote)


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
    elif job.source == "workable":
        _, slug, shortcode = job.id.split(":", 2)
        with http() as c:
            data = c.get(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}").json()
        job.description = html_to_text(
            "\n".join(data.get(k) or "" for k in ("description", "requirements", "benefits"))
        )
    elif job.source == "devbg":
        body = BeautifulSoup(fetch_html(job.url), "html.parser").select_one(".job_description")
        job.description = body.get_text("\n", strip=True) if body else ""
    elif job.url:
        text = html_to_text(fetch_html(job.url))
        if len(text) < 500 or "render" in job.tags:
            text = html_to_text(fetch_html(job.url, render=True))
        job.description = text
    if not job.salary:
        job.salary = find_salary(job.description)
    db.update_job(job)
    return job
