"""Your profile from LinkedIn's own data export (Settings & Privacy → Data privacy → Get a copy of your data).

LinkedIn sends a zip of CSV files: Profile.csv, Positions.csv, Education.csv, Skills.csv, Languages.csv,
Certifications.csv, Projects.csv, Honors.csv, Volunteering.csv, Publications.csv, Courses.csv, Email Addresses.csv,
PhoneNumbers.csv and Jobs/Job Seeker Preferences.csv (the "open to work" settings: titles, locations, job types).
They are read as they are, without Claude: nothing is reworded or guessed. With a profile already there, LinkedIn only
adds: empty fields are filled, jobs, projects, schools, skills and sections it doesn't have yet are added, and
what you wrote yourself is kept. The job-seeker settings go into the profile's preferences."""

import csv
import io
import re
import zipfile
from pathlib import Path

from pydantic import BaseModel, Field

from .models import (
    Contact,
    Criteria,
    CustomEntry,
    CustomSection,
    Education,
    Experience,
    Profile,
    Project,
    SkillGroup,
)

# The archive can hold messages and media we don't read; only CSVs this small are opened.
MAX_CSV_BYTES = 5 * 1024 * 1024
SKILLS_GROUP = "Skills"


class LinkedInError(ValueError):
    pass


# --- reading the export ------------------------------------------------------------------------------------


def _key(name: str) -> str:
    """A file or column name as a lookup key: "Jobs/Job Seeker Preferences.csv" → "job seeker preferences"."""
    return re.sub(r"[^a-z0-9]+", " ", Path(name).stem.lower()).strip()


def _rows(text: str) -> list[dict[str, str]]:
    """The rows of one LinkedIn CSV. Some start with a "Notes:" paragraph before the header row; it is skipped."""
    lines = list(csv.reader(io.StringIO(text)))
    start = 0
    while start < len(lines) and (
        not any(c.strip() for c in lines[start])
        or lines[start][0].strip().lower().startswith("notes")
        or any(len(c) > 60 for c in lines[start])
    ):
        start += 1
    if start >= len(lines):
        return []
    header = [_key(h) for h in lines[start]]
    rows = []
    for line in lines[start + 1:]:
        row = {h: (line[i].strip() if i < len(line) else "") for i, h in enumerate(header) if h}
        if any(row.values()):
            rows.append(row)
    return rows


def read_export(data: bytes) -> dict[str, list[dict[str, str]]]:
    """Every CSV in the export by its name ("positions", "job seeker preferences"...), as rows keyed by column
    ("company name", "started on"...)."""
    return read_files([("export.zip", data)])


def read_files(files: list[tuple[str, bytes]]) -> dict[str, list[dict[str, str]]]:
    """The export as uploaded: the zip LinkedIn sends, or the CSV files from it (a browser or file manager often
    unpacks the zip, so people pick Profile.csv, Positions.csv... themselves), or both."""
    tables: dict[str, list[dict[str, str]]] = {}
    for name, data in files:
        if data[:4] == b"PK\x03\x04":
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile as e:
                raise LinkedInError("That zip file is damaged. Download the export from LinkedIn again.") from e
            with z:
                for info in z.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".csv") or info.file_size > MAX_CSV_BYTES:
                        continue
                    tables[_key(info.filename)] = _rows(z.read(info).decode("utf-8-sig", errors="replace"))
        elif name.lower().endswith(".csv"):
            if len(data) > MAX_CSV_BYTES:
                continue
            tables[_key(name)] = _rows(data.decode("utf-8-sig", errors="replace"))
        else:
            raise LinkedInError(f"{Path(name).name} isn't part of a LinkedIn export: upload the zip, or the .csv files from it.")
    if not any(k in tables for k in ("profile", "positions", "skills", "education")):
        raise LinkedInError(
            "That zip doesn't look like a LinkedIn data export (no Profile.csv, Positions.csv, Skills.csv or Education.csv). "
            "On LinkedIn: Settings & Privacy → Data privacy → Get a copy of your data."
        )
    return tables


# --- turning it into a profile -----------------------------------------------------------------------------


def _get(row: dict[str, str], *names: str) -> str:
    for n in names:
        if row.get(n):
            return row[n]
    return ""


def _bullets(text: str) -> list[str]:
    """A LinkedIn description as bullets: one per line, list markers dropped; a plain paragraph stays one bullet."""
    lines = [re.sub(r"^\s*(?:[-•*·▪●–]|\d+[.)])\s*", "", line).strip() for line in text.replace("\r", "").split("\n")]
    return [line for line in lines if line]


def _range(start: str, end: str, ongoing: str = "") -> str:
    if start and (end or ongoing):
        return f"{start} – {end or ongoing}"
    return start or end


def _websites(value: str) -> list[str]:
    """Profile.csv lists websites as "[PORTFOLIO:https://a.dev,OTHER:https://b.com]"."""
    return re.findall(r"https?://[^\s,\]]+", value)


def _split(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"\s*[|;]\s*", value) if v.strip()]


# Job Seeker Preferences.csv columns worth keeping, with how they read in the profile. Other columns are kept with
# their own name, except the ones below that aren't preferences (contact details, LinkedIn's own bookkeeping).
PREFERENCE_LABELS = {
    "job titles": "Job titles",
    "locations": "Locations",
    "preferred job types": "Job types",
    "job types": "Job types",
    "preferred work location types": "Work location",
    "work location types": "Work location",
    "industries": "Industries",
    "company employee count": "Company size",
    "dream companies": "Dream companies",
    "preferred start time range": "Start",
    "open to recruiters": "Open to recruiters",
    "max commute duration": "Max commute",
    "commute mode": "Commute by",
    "introduction statement": "Introduction",
}
PREFERENCE_SKIP = ("phone", "address", "email", "shared", "activity level", "updated", "date", "searching fast growing")


def _preferences(rows: list[dict[str, str]]) -> list[str]:
    out = []
    for row in rows[:1]:
        for col, value in row.items():
            if not value or any(s in col for s in PREFERENCE_SKIP):
                continue
            label = PREFERENCE_LABELS.get(col) or col.capitalize()
            if value.lower() in ("true", "false", "yes", "no"):
                value = "yes" if value.lower() in ("true", "yes") else "no"
            else:
                value = ", ".join(_split(value))
            line = f"{label}: {value}"
            if line not in out:
                out.append(line)
    return out


def _section(title: str, rows: list[dict[str, str]], name_cols, sub_cols, start_cols, end_cols, text_cols) -> CustomSection | None:
    entries = []
    for row in rows:
        name = _get(row, *name_cols)
        if not name:
            continue
        bullets = _bullets(_get(row, *text_cols)) if text_cols else []
        entries.append(CustomEntry(title=name, subtitle=_get(row, *sub_cols), dates=_range(_get(row, *start_cols), _get(row, *end_cols)), bullets=bullets))
    return CustomSection(title=title, entries=entries) if entries else None


def to_profile(tables: dict[str, list[dict[str, str]]]) -> Profile:
    me = (tables.get("profile") or [{}])[0]
    sites = _websites(me.get("websites", ""))
    emails = tables.get("email addresses", [])
    primary = next((r for r in emails if r.get("primary", "").lower() == "yes"), emails[0] if emails else {})
    phones = tables.get("phonenumbers") or tables.get("phone numbers") or []

    contact = Contact(
        email=primary.get("email address", ""),
        phone=_get(phones[0], "number") if phones else "",
        linkedin="",
        github=next((s for s in sites if "github.com" in s.lower()), ""),
        location=_get(me, "geo location", "location"),
        other=[s for s in sites if "github.com" not in s.lower()],
    )
    experience = [
        Experience(
            title=_get(r, "title"), company=_get(r, "company name", "company"),
            start=_get(r, "started on", "start date"), end=_get(r, "finished on", "end date") or "Present",
            location=_get(r, "location"), bullets=_bullets(_get(r, "description")),
        )
        for r in tables.get("positions", []) if _get(r, "title", "company name")
    ]
    projects = [
        Project(name=_get(r, "title", "name"), tech="", link=_get(r, "url"), bullets=_bullets(_get(r, "description")))
        for r in tables.get("projects", []) if _get(r, "title", "name")
    ]
    education = [
        Education(
            degree=", ".join(x for x in (_get(r, "degree name", "degree"), _get(r, "field of study")) if x),
            school=_get(r, "school name", "school"),
            years=_range(_get(r, "start date", "started on"), _get(r, "end date", "finished on")),
        )
        for r in tables.get("education", []) if _get(r, "school name", "school")
    ]
    skills = [s for s in dict.fromkeys(_get(r, "name", "skill") for r in tables.get("skills", [])) if s]
    languages = [
        f"{_get(r, 'name')} ({_get(r, 'proficiency')})" if _get(r, "proficiency") else _get(r, "name")
        for r in tables.get("languages", []) if _get(r, "name")
    ]
    sections = [s for s in (
        _section("Certifications", tables.get("certifications", []), ["name"], ["authority"], ["started on"], ["finished on"], []),
        _section("Honors & awards", tables.get("honors", []), ["title"], [], ["issued on"], [], ["description"]),
        _section("Volunteering", tables.get("volunteering", []), ["role"], ["company name"], ["started on"], ["finished on"], ["description"]),
        _section("Publications", tables.get("publications", []), ["name", "title"], ["publisher"], ["published on"], [], ["description"]),
        _section("Courses", tables.get("courses", []), ["name"], [], [], [], []),
    ) if s]

    return Profile(
        name=" ".join(x for x in (_get(me, "first name"), _get(me, "last name")) if x),
        headline=_get(me, "headline"),
        contact=contact,
        summary=_get(me, "summary"),
        skills=[SkillGroup(category=SKILLS_GROUP, items=skills)] if skills else [],
        experience=experience,
        projects=projects,
        education=education,
        soft_skills=[],
        languages=languages,
        interests=[],
        sections=sections,
        skill_details=[],
        extra_facts=[],
        preferences=_preferences(tables.get("job seeker preferences", [])),
    )


# --- adding it to a profile that's already there -----------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _names(items: list[str], limit: int = 4) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def merge(current: Profile | None, linkedin: Profile) -> tuple[Profile, list[str]]:
    """The profile with what LinkedIn adds, and a line per kind of change for the person to read."""
    if current is None:
        p = linkedin
        report = [f"Profile created from LinkedIn: {len(p.experience)} jobs, {len(p.education)} schools, "
                  f"{sum(len(g.items) for g in p.skills)} skills, {len(p.projects)} projects."]
        if p.skills:
            report.append("LinkedIn doesn't group skills: they are all under \"Skills\". Group them in the CV editor.")
        if p.preferences:
            report.append(f"Job preferences: {len(p.preferences)} from your LinkedIn job-seeker settings.")
        return p, report

    p = current.model_copy(deep=True)
    report: list[str] = []

    filled, kept = [], []
    for field, label in (("name", "name"), ("headline", "headline"), ("summary", "summary")):
        mine, theirs = getattr(p, field), getattr(linkedin, field)
        if theirs and not mine:
            setattr(p, field, theirs)
            filled.append(label)
        elif theirs and _norm(theirs) != _norm(mine):
            kept.append(label)
    for field in ("email", "phone", "github", "location"):
        theirs = getattr(linkedin.contact, field)
        if theirs and not getattr(p.contact, field):
            setattr(p.contact, field, theirs)
            filled.append(field)
    have = {_norm(x) for x in p.contact.other}
    new_other = [x for x in linkedin.contact.other if _norm(x) not in have]
    p.contact.other += new_other
    if new_other:
        filled.append("websites")
    if filled:
        report.append(f"Filled in: {', '.join(filled)}.")
    if kept:
        report.append(f"Kept your own {', '.join(kept)} (LinkedIn's differs).")

    added, extended = [], []
    for job in linkedin.experience:
        same = next((e for e in p.experience if _norm(e.company) == _norm(job.company) and _norm(e.title) == _norm(job.title)), None)
        if same is None:
            p.experience.insert(_position(p.experience, job.start), job)
            added.append(f"{job.title} at {job.company}")
            continue
        changed = False
        for field in ("start", "end", "location"):
            if getattr(job, field) and not getattr(same, field):
                setattr(same, field, getattr(job, field))
                changed = True
        if job.bullets and not same.bullets:
            same.bullets = job.bullets
            changed = True
        if changed:
            extended.append(f"{same.title} at {same.company}")
    if added:
        report.append(f"Added {len(added)} job{'s' if len(added) != 1 else ''}: {_names(added)}.")
    if extended:
        report.append(f"Filled in missing dates, place or description of {_names(extended)}.")

    new_projects = [x for x in linkedin.projects if _norm(x.name) not in {_norm(y.name) for y in p.projects}]
    p.projects += new_projects
    if new_projects:
        report.append(f"Added {len(new_projects)} project{'s' if len(new_projects) != 1 else ''}: {_names([x.name for x in new_projects])}.")

    new_schools = [x for x in linkedin.education if (_norm(x.school), _norm(x.degree)) not in {(_norm(y.school), _norm(y.degree)) for y in p.education}]
    p.education += new_schools
    if new_schools:
        report.append(f"Added education: {_names([x.school for x in new_schools])}.")

    have = {_norm(s) for g in p.skills for s in g.items} | {_norm(s) for s in p.soft_skills}
    new_skills = [s for g in linkedin.skills for s in g.items if _norm(s) not in have]
    if new_skills:
        group = next((g for g in p.skills if g.category == SKILLS_GROUP), None)
        if group is None:
            group = SkillGroup(category=SKILLS_GROUP, items=[])
            p.skills.append(group)
        group.items += new_skills
        report.append(f"Added {len(new_skills)} skill{'s' if len(new_skills) != 1 else ''} under \"{SKILLS_GROUP}\": {_names(new_skills, 6)}.")

    lang = lambda s: _norm(s.split("(")[0])  # noqa: E731 - "English (Native)" and "English" are the same language
    new_langs = [x for x in linkedin.languages if lang(x) not in {lang(y) for y in p.languages}]
    p.languages += new_langs
    if new_langs:
        report.append(f"Added languages: {', '.join(new_langs)}.")

    for section in linkedin.sections:
        mine = next((s for s in p.sections if _norm(s.title) == _norm(section.title)), None)
        if mine is None:
            p.sections.append(section)
            report.append(f"Added a \"{section.title}\" section ({len(section.entries)}).")
            continue
        new = [e for e in section.entries if _norm(e.title) not in {_norm(x.title) for x in mine.entries}]
        mine.entries += new
        if new:
            report.append(f"Added to \"{mine.title}\": {_names([e.title for e in new])}.")

    if linkedin.preferences:
        labels = {line.split(":", 1)[0] for line in linkedin.preferences}
        before = p.preferences
        p.preferences = [x for x in p.preferences if x.split(":", 1)[0] not in labels] + linkedin.preferences
        if p.preferences != before:
            report.append(f"Job preferences from LinkedIn: {'; '.join(linkedin.preferences[:3])}{'…' if len(linkedin.preferences) > 3 else ''}")

    if p == current:
        report = ["Nothing new: your profile already has everything in this LinkedIn export."] + [x for x in report if x.startswith("Kept")]
    return p, report


MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _when(date: str) -> tuple[int, int] | None:
    """ "Mar 2021", "03/2021", "2021" as (year, month); None when it can't be read."""
    year = re.search(r"(19|20)\d{2}", date)
    if not year:
        return None
    month = re.search(r"[a-zA-Z]{3}", date)
    num = re.match(r"\s*(\d{1,2})[./-]", date)
    m = MONTHS.get(month.group(0).lower(), 0) if month else (int(num.group(1)) if num else 0)
    return int(year.group(0)), m


def _position(jobs: list[Experience], start: str) -> int:
    """Where a new job goes in a newest-first list: before the first job that started earlier (else at the end)."""
    new = _when(start)
    if new is None:
        return len(jobs)
    for i, job in enumerate(jobs):
        theirs = _when(job.start)
        if theirs is not None and theirs < new:
            return i
    return len(jobs)


MAIN_FILES = {"positions": "Positions", "education": "Education", "skills": "Skills", "job seeker preferences": "Job seeker preferences"}


def missing_note(tables: dict) -> str:
    """A line for the report when the export lacks the main files (LinkedIn's export holds only what was ticked)."""
    missing = [label for key, label in MAIN_FILES.items() if key not in tables]
    if not missing:
        return ""
    return (f"Not in this export: {', '.join(missing)}. Ask LinkedIn for those too (Data privacy → Get a copy of your data: "
            f"tick them, or pick the larger archive) and import it again: what's already here isn't added twice.")


def import_export(data: bytes | list[tuple[str, bytes]], current: Profile | None) -> tuple[Profile, list[str], "Wishes"]:
    tables = read_export(data) if isinstance(data, bytes) else read_files(data)
    profile, report = merge(current, to_profile(tables))
    if note := missing_note(tables):
        report.append(note)
    return profile, report, wishes(tables)


# --- your job-seeker settings as changes to a saved search --------------------------------------------------


class Wishes(BaseModel):
    """What the LinkedIn job-seeker settings ask for, in the terms a search understands."""
    titles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote: bool = False


class SearchChanges(BaseModel):
    """What a saved search would gain from the job-seeker settings: offered to the person, applied only on their
    say-so (each part can be left out)."""
    titles: list[str] = Field(default_factory=list)          # job titles to add to the title words
    countries: list[str] = Field(default_factory=list)       # country codes to add
    remote: bool = False                                     # start including remote jobs
    unexclude: list[str] = Field(default_factory=list)       # excluded words that would hide a wanted title
    not_covered: list[str] = Field(default_factory=list)     # locations the search has no country for
    narrows: bool = False                                    # the search takes any title now; titles would limit it

    def empty(self) -> bool:
        return not (self.titles or self.countries or self.remote or self.unexclude)


def wishes(tables: dict[str, list[dict[str, str]]]) -> Wishes:
    row = (tables.get("job seeker preferences") or [{}])[0]
    locations = _split(row.get("locations", ""))
    work_types = " ".join(v for k, v in row.items() if "work location" in k or "workplace" in k).lower()
    remote = "remote" in work_types or any("remote" in x.lower() for x in locations)
    return Wishes(
        titles=list(dict.fromkeys(_split(row.get("job titles", "")))),
        locations=[x for x in locations if "remote" not in x.lower()],
        remote=remote,
    )


def _country(location: str) -> str | None:
    from .countries import COUNTRY_TERMS
    from .search import _contains

    text = f" {location.lower()} "
    return next((code for code, terms in COUNTRY_TERMS.items() if any(_contains(text, t) for t in terms)), None)


def search_changes(w: Wishes, criteria: Criteria) -> SearchChanges:
    have = {_norm(x) for x in criteria.role_keywords}
    titles = [t for t in w.titles if _norm(t) not in have]
    countries, not_covered = [], []
    for loc in w.locations:
        code = _country(loc)
        if code is None:
            not_covered.append(loc)
        elif code not in criteria.countries and code not in countries:
            countries.append(code)
    return SearchChanges(
        titles=titles,
        countries=countries,
        remote=w.remote and not criteria.include_remote,
        unexclude=[x for x in criteria.exclude if any(x.lower() in t.lower() for t in w.titles)],
        not_covered=not_covered,
        narrows=bool(titles) and not criteria.role_keywords and not criteria.categories,
    )


def apply_changes(criteria: Criteria, changes: SearchChanges) -> Criteria:
    c = criteria.model_copy(deep=True)
    c.role_keywords += [t for t in changes.titles if _norm(t) not in {_norm(x) for x in c.role_keywords}]
    c.countries += [x for x in changes.countries if x not in c.countries]
    if changes.remote:
        c.include_remote = True
    c.exclude = [x for x in c.exclude if x not in changes.unexclude]
    return c
