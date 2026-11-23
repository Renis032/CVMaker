"""Filtering, text search and facet counts for the saved-jobs list (the Jobs page, its exports and quick search).

The list is small enough (hundreds to a few thousand rows) to filter in Python, which keeps the rules in one place:
the filters in the URL, the counts shown next to each option, and the text query all read the same per-job facts.

Text query syntax (the search box):
    java sofia            every word must appear (title, company, location, tags, source or description)
    "spring boot"         an exact phrase
    -senior               must not appear
    company:acme          only in one field: title, company, location, source, tag (alias: skill)
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode

from markupsafe import Markup, escape

from .countries import COUNTRY_TERMS, country_name
from .search import SENIORITY_LEVELS, _contains, seniority

PAGE_SIZE = 50
MODES = [("remote", "Remote"), ("hybrid", "Hybrid"), ("onsite", "On-site")]
FIT_CHOICES = [(0, "Any"), (5, "5+"), (7, "7+"), (8, "8+")]
PAY_CHOICES = [0, 1500, 2500, 3500, 5000, 7000]
DAY_CHOICES = [(0, "Any time"), (1, "Last 24 hours"), (3, "Last 3 days"), (7, "Last week"), (14, "Last 2 weeks"), (30, "Last month")]
SORT_CHOICES = [
    ("match", "Best for you"), ("fit", "Claude fit"), ("score", "Keyword match"), ("newest", "Newly found"),
    ("posted", "Posted date"), ("salary", "Salary"), ("title", "Job title"), ("company", "Company"),
]
FIELDS = {"title": "title", "company": "company", "location": "location", "loc": "location", "source": "source",
          "tag": "tags", "skill": "tags"}
_TOKEN = re.compile(r'(-?)(?:([a-z]+):)?(?:"([^"]*)"|(\S+))', re.I)
# How much a hit in each field says about relevance.
_WEIGHTS = {"title": 10, "company": 6, "tags": 5, "location": 3, "source": 1, "description": 1}


@dataclass
class Term:
    text: str
    field: str = ""  # "" = anywhere
    negative: bool = False


def parse_query(q: str) -> list[Term]:
    terms = []
    for neg, fld, phrase, word in _TOKEN.findall(q or ""):
        text = (phrase or word).strip().lower()
        if fld and fld.lower() not in FIELDS:  # "c++:" or a URL: not a field, search the whole token
            text, fld = f"{fld}:{text}".lower(), ""
        if text:
            terms.append(Term(text, FIELDS.get(fld.lower(), ""), bool(neg)))
    return terms


def _multi(params, key: str, allowed=None) -> list[str]:
    seen = []
    for v in params.getlist(key) if hasattr(params, "getlist") else params.get(key, []):
        v = v.strip()
        if v and v not in seen and (allowed is None or v in allowed):
            seen.append(v)
    return seen


def _int(params, key: str, low: int = 0, high: int = 10**9) -> int:
    try:
        return max(low, min(high, int(params.get(key) or 0)))
    except ValueError:
        return 0


@dataclass
class Filters:
    q: str = ""
    where: list[str] = field(default_factory=list)  # remote / hybrid / onsite
    level: list[str] = field(default_factory=list)  # junior / mid / senior
    country: list[str] = field(default_factory=list)  # country codes, or "other"
    source: list[str] = field(default_factory=list)
    tag: list[str] = field(default_factory=list)  # every one must be on the job
    company: str = ""
    fit: int = 0  # at least this Claude fit (1-10)
    rated: str = ""  # "no": only jobs Claude has not rated yet
    score: int = 0  # at least this keyword score
    pay: int = 0  # at least this many EUR a month
    days: int = 0  # posted (or, with no date, found) in the last N days
    salary: int = 0
    only_last: int = 0
    unseen: int = 0
    status: str = ""
    sort: str = ""
    dir: str = ""
    limit: int = PAGE_SIZE

    @classmethod
    def from_params(cls, params) -> "Filters":
        modes = [m for m, _ in MODES]
        sort = params.get("sort") or ""
        return cls(
            q=(params.get("q") or "").strip()[:200],
            where=_multi(params, "where", modes),
            level=_multi(params, "level", SENIORITY_LEVELS),
            country=_multi(params, "country"),
            source=_multi(params, "source"),
            tag=[t.lower() for t in _multi(params, "tag")],
            company=(params.get("company") or "").strip()[:120],
            fit=_int(params, "fit", 0, 10),
            rated="no" if params.get("rated") == "no" else "",
            score=_int(params, "score", 0, 100),
            pay=_int(params, "pay"),
            days=_int(params, "days", 0, 3650),
            salary=1 if _int(params, "salary") else 0,
            only_last=1 if _int(params, "only_last") else 0,
            unseen=1 if _int(params, "unseen") else 0,
            status=params.get("status") if params.get("status") in ("liked", "hidden", "closed") else "",
            sort=sort if sort in dict(SORT_CHOICES) or sort == "relevance" else "",
            dir=params.get("dir") if params.get("dir") in ("asc", "desc") else "",
            limit=_int(params, "limit", PAGE_SIZE, 5000) or PAGE_SIZE,
        )

    # --- what the page should do with them ---

    @property
    def terms(self) -> list[Term]:
        return parse_query(self.q)

    @property
    def effective_sort(self) -> str:
        """Typed words rank by relevance unless a sort was picked; otherwise "best for you"."""
        if self.sort:
            return self.sort
        return "relevance" if any(not t.negative for t in self.terms) else "match"

    @property
    def sql_sort(self) -> str:
        return "match" if self.effective_sort == "relevance" else self.effective_sort

    @property
    def narrowing(self) -> int:
        """How many filters (beyond the tab and the sort) are narrowing the list."""
        return (
            bool(self.q) + len(self.where) + len(self.level) + len(self.country) + len(self.source) + len(self.tag)
            + bool(self.company) + bool(self.fit) + bool(self.rated) + bool(self.score) + bool(self.pay)
            + bool(self.days) + bool(self.salary) + bool(self.only_last)
        )

    def pairs(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for key in ("status", "unseen", "only_last", "q", "company"):
            if getattr(self, key):
                out.append((key, str(getattr(self, key))))
        for key in ("where", "level", "country", "source", "tag"):
            out.extend((key, v) for v in getattr(self, key))
        for key in ("fit", "rated", "score", "pay", "days", "salary", "sort", "dir"):
            if getattr(self, key):
                out.append((key, str(getattr(self, key))))
        if self.limit != PAGE_SIZE:
            out.append(("limit", str(self.limit)))
        return out

    def url(self, **changes) -> str:
        """The list's address with some filters changed. Changing a filter goes back to the first page."""
        pairs = [(k, v) for k, v in self.pairs() if k not in changes and (k != "limit" or "limit" in changes)]
        for k, v in changes.items():
            for item in v if isinstance(v, (list, tuple)) else [v]:
                if item not in ("", 0, None, False):
                    pairs.append((k, str(item)))
        return "/?" + urlencode(pairs) if pairs else "/"

    def without(self, key: str, value: str | None = None) -> str:
        current = getattr(self, key)
        if isinstance(current, list) and value is not None:
            return self.url(**{key: [v for v in current if v != value]})
        return self.url(**{key: ""})

    def cleared(self) -> str:
        """Everything off except the tab you are on and the sort."""
        return Filters(status=self.status, unseen=self.unseen, sort=self.sort, dir=self.dir).url()

    def chips(self) -> list[tuple[str, str]]:
        """(label, address without it) for every filter in use."""
        out = []
        if self.q:
            out.append((f"“{self.q}”", self.without("q")))
        if self.company:
            out.append((f"Company: {self.company}", self.without("company")))
        out.extend((dict(MODES)[v], self.without("where", v)) for v in self.where)
        out.extend((v.capitalize(), self.without("level", v)) for v in self.level)
        out.extend((_country_label(v), self.without("country", v)) for v in self.country)
        out.extend((v, self.without("source", v)) for v in self.source)
        out.extend((v, self.without("tag", v)) for v in self.tag)
        if self.fit:
            out.append((f"Fit {self.fit}+", self.without("fit")))
        if self.rated:
            out.append(("Not rated yet", self.without("rated")))
        if self.score:
            out.append((f"Keyword match {self.score}+", self.without("score")))
        if self.pay:
            out.append((f"€{self.pay:,}+ a month", self.without("pay")))
        if self.days:
            out.append((dict(DAY_CHOICES).get(self.days, f"Last {self.days} days"), self.without("days")))
        if self.salary:
            out.append(("States a salary", self.without("salary")))
        if self.only_last:
            out.append(("Last search only", self.without("only_last")))
        return out


def _country_label(code: str) -> str:
    return "Elsewhere" if code == "other" else country_name(code)


# --- facts about one job --------------------------------------------------------------------------------------


def _facts(row: dict) -> dict:
    j = row["job"]
    tags = [t.lower().strip() for t in j.tags if t.strip()]
    location = j.location.lower()
    title = j.title.lower()
    if j.remote or _contains(location, "remote"):
        mode = "remote"
    elif "hybrid" in tags or "hybrid" in location or "hybrid" in title:
        mode = "hybrid"
    else:
        mode = "onsite"
    padded = f" {location} "
    countries = [code for code, words in COUNTRY_TERMS.items() if any(_contains(padded, w) for w in words)]
    try:
        when = datetime.fromisoformat(j.posted_at[:10]).timestamp() if j.posted_at else row["first_seen"]
    except ValueError:
        when = row["first_seen"]
    return {
        "mode": mode,
        "level": seniority(j),
        "countries": countries or ["other"],
        "source": j.source,
        "tags": [t for t in tags if t != "hybrid"],
        "company": " ".join(j.company.lower().split()),
        "when": when,
        "text": {
            "title": title, "company": j.company.lower(), "location": location, "tags": " ".join(tags),
            "source": j.source.lower(), "description": j.description.lower(),
        },
    }


def _term_hit(term: Term, text: dict) -> int:
    """0 when the term is absent, else the weight of the best field it is in."""
    fields = [term.field] if term.field else list(_WEIGHTS)
    best = 0
    for f in fields:
        at = text[f].find(term.text)
        if at < 0:
            continue
        weight = _WEIGHTS[f]
        if f == "title" and (at == 0 or not text[f][at - 1].isalnum()):
            weight += 4  # starts a word in the title
        best = max(best, weight)
    return best


def _relevance(terms: list[Term], text: dict) -> int | None:
    """None when the job does not match the query, else how well it does."""
    total = 0
    for term in terms:
        hit = _term_hit(term, text)
        if term.negative:
            if hit:
                return None
        elif not hit:
            return None
        else:
            total += hit
    return total


def _snippet(terms: list[Term], job) -> Markup | None:
    """A line of the description around a word that is found only there, with the words marked."""
    visible = f"{job.title} {job.company} {job.location} {' '.join(job.tags)}".lower()
    words = [t.text for t in terms if not t.negative and t.field in ("", "description")]
    hidden = [w for w in words if w not in visible]
    desc = " ".join(job.description.split())
    low = desc.lower()
    at = next((low.find(w) for w in hidden if low.find(w) >= 0), -1)
    if at < 0:
        return None
    start, end = max(0, at - 70), min(len(desc), at + 110)
    piece = desc[start:end]
    pattern = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I)
    out, last = [], 0
    for m in pattern.finditer(piece):
        out.append(escape(piece[last : m.start()]))
        out.append(Markup("<mark>") + escape(m.group()) + Markup("</mark>"))
        last = m.end()
    out.append(escape(piece[last:]))
    return Markup("…" if start else "") + Markup("").join(out) + Markup("…" if end < len(desc) else "")


# --- the list -------------------------------------------------------------------------------------------------

_TESTS = {
    "where": lambda f, x, row: not f.where or x["mode"] in f.where,
    "level": lambda f, x, row: not f.level or x["level"] in f.level,
    "country": lambda f, x, row: not f.country or any(c in f.country for c in x["countries"]),
    "source": lambda f, x, row: not f.source or x["source"] in f.source,
    "tag": lambda f, x, row: all(t in x["tags"] for t in f.tag),
    "company": lambda f, x, row: not f.company or x["company"] == " ".join(f.company.lower().split()),
    "fit": lambda f, x, row: not f.fit or (row["fit"] is not None and row["fit"] >= f.fit),
    "rated": lambda f, x, row: not f.rated or row["fit"] is None,
    "score": lambda f, x, row: row["score"] >= f.score,
    "pay": lambda f, x, row: not f.pay or (row["salary_month"] or 0) >= f.pay,
    "days": lambda f, x, row: not f.days or x["when"] >= time.time() - f.days * 86400,
}


@dataclass
class Result:
    rows: list[dict]  # this page
    total: int  # all that match
    facets: dict
    more_url: str = ""

    @property
    def shown(self) -> int:
        return len(self.rows)


def _options(counts: dict[str, int], chosen: list[str], labels, order: list[str] | None = None, top: int = 0) -> list[dict]:
    keys = order if order is not None else sorted(counts, key=lambda k: (-counts[k], k.lower()))
    keys = [k for k in keys if counts.get(k) or k in chosen]
    keys += [k for k in chosen if k not in keys]
    if top:
        keys = keys[:top] + [k for k in keys[top:] if k in chosen]
    return [{"value": k, "label": labels(k), "count": counts.get(k, 0), "on": k in chosen} for k in keys]


def apply(rows: list[dict], f: Filters) -> Result:
    """Filter rows (already limited to a tab by the database), count the facet options, and cut to a page.
    Each facet's counts ignore that facet's own choice, so you can see what picking another option would give."""
    terms = f.terms
    matched: list[tuple[dict, dict, int]] = []
    for row in rows:
        facts = _facts(row)
        relevance = _relevance(terms, facts["text"]) if terms else 0
        if relevance is not None:
            matched.append((row, facts, relevance))

    def passing(skip: str = ""):
        tests = [t for key, t in _TESTS.items() if key != skip]
        return [(row, x, rel) for row, x, rel in matched if all(t(f, x, row) for t in tests)]

    def count(key: str, values) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row, x, _ in passing(key):
            for v in values(x):
                counts[v] = counts.get(v, 0) + 1
        return counts

    kept = passing()
    tag_counts: dict[str, int] = {}
    for _, x, _ in kept:
        for t in set(x["tags"]):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    countries = _options(count("country", lambda x: x["countries"]), f.country, _country_label)
    facets = {
        "where": _options(count("where", lambda x: [x["mode"]]), f.where, dict(MODES).get, [m for m, _ in MODES]),
        "level": _options(count("level", lambda x: [x["level"]]), f.level, str.capitalize, SENIORITY_LEVELS),
        # A single place is not a choice worth showing.
        "country": countries if len(countries) > 1 or f.country else [],
        "source": _options(count("source", lambda x: [x["source"]]), f.source, str),
        "tag": _options(tag_counts, f.tag, str, top=16),
    }

    if f.effective_sort == "relevance":
        kept.sort(key=lambda item: -item[2])  # stable: equal relevance keeps the "best for you" order
    out = []
    for row, _, _ in kept[: f.limit]:
        row = dict(row)
        row["snippet"] = _snippet(terms, row["job"]) if terms else None
        out.append(row)
    more = f.url(limit=f.limit + PAGE_SIZE) if len(kept) > f.limit else ""
    return Result(rows=out, total=len(kept), facets=facets, more_url=more)


def suggest(rows: list[dict], q: str, limit: int = 6) -> dict:
    """Quick-search suggestions: the best matching jobs and the companies whose name matches."""
    terms = parse_query(q)
    if not terms:
        return {"jobs": [], "companies": []}
    scored = []
    for row in rows:
        relevance = _relevance(terms, _facts(row)["text"])
        if relevance is not None:
            scored.append((relevance, row))
    scored.sort(key=lambda item: -item[0])
    needle = q.strip().lower()
    companies: dict[str, int] = {}
    for row in rows:
        name = row["job"].company
        if needle in name.lower():
            companies[name] = companies.get(name, 0) + 1
    return {
        "total": len(scored),
        "jobs": [
            {"id": r["job"].id, "title": r["job"].title, "company": r["job"].company, "location": r["job"].location,
             "fit": r["fit"]}
            for _, r in scored[:limit]
        ],
        "companies": [{"name": n, "count": c} for n, c in sorted(companies.items(), key=lambda kv: -kv[1])[:3]],
    }
