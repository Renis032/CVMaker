"""Deep dive into a project suggested for a job (the fit check's "projects that look strong" or the GitHub
review's ideas): Claude works the idea out into a full spec, and the spec becomes a prompt the candidate pastes
into Claude Code (it builds the repo) or into a chat (a mentor while they build it themselves).

Briefs are cached per job, idea, size and build mode."""

import hashlib
import json

from . import db, llm
from .assistant import _job_text
from .models import Job, Profile, ProjectBrief

# Sizes are Claude Code working time, not the candidate's: Claude writes the code, the candidate reviews each
# milestone. The scope is set by milestones and features, so a guided build has the same scope (and takes the
# candidate longer).
SIZES = {
    "session": ("One session · about an hour of Claude",
                "small enough for Claude Code to build in one session of about an hour: 2-3 milestones, one core "
                "feature done well, tests and a good README"),
    "afternoon": ("An afternoon · 3-4 hours of Claude",
                  "about 3-4 hours of Claude Code work over a few sessions: 3-5 milestones, the core features, tests, "
                  "CI and a demo that runs with one command"),
    "flagship": ("Flagship · 1-2 days of Claude",
                 "a flagship built over 1-2 days of Claude Code sessions: 5-7 milestones, production-style "
                 "architecture, thorough tests, CI/CD, deployment, observability and documentation"),
}
MODES = {
    "claude-code": ("Claude Code builds it", "The spec will be handed to Claude Code, which writes the code in a repository while the candidate reviews it."),
    "guided": ("I build it, Claude guides", "The candidate writes the code themselves; the spec will be handed to Claude as a mentor that explains, gives hints and reviews, so the candidate fully understands every part for interviews. Keep the scope of the size (it is measured in Claude's time; the candidate will need several times longer)."),
}
DEFAULT_SIZE, DEFAULT_MODE = "afternoon", "claude-code"

SYSTEM = """You are a senior software engineer and hiring manager. A candidate wants to build a portfolio project that proves they fit one job. Work the project idea out into a complete, concrete build spec.

The spec must be specific enough to build from without more questions:
- why_this_job: which requirements of the posting it proves, and how (name them).
- tech_stack: the job's own stack where possible; one line why for each choice. Name versions only when they matter.
- architecture: the components and how they talk; data_model: entities with their key fields; interfaces: the API endpoints (method, path, what it does), CLI commands or screens.
- milestones: 3-7, in build order, each shippable on its own, with concrete tasks and done_when checks (a test passes, an endpoint returns X, a dashboard shows Y).
- repo_layout: the main folders and files. testing: what to test and how (unit, integration, load where relevant). ci_and_deploy: the pipeline and where it runs (free tiers are fine).
- readme_outline and demo: how a recruiter or interviewer sees it working in two minutes.
- learn_first: the concepts the candidate must understand well enough to explain and defend in an interview.
- cv_bullets: 2-3 bullets they can honestly write once it is built, with [placeholders] for numbers they must measure themselves. Never state results that don't exist yet.
- interview_points: the design decisions and trade-offs an interviewer will ask about, and a good answer direction.
- pitfalls: what usually goes wrong or wastes time on a project like this.
Fit the scope to the size given. Sizes are Claude Code working time (Claude writes the code, the candidate reviews each milestone), so measure scope in milestones and features, not human hours. Cut features rather than exceed it, and put the cuts in out_of_scope.
Build on what the candidate already knows (their profile) and extend one of their existing projects when that is faster; don't repeat what they already have.
Keep it realistic and honest: no fake users, fake traffic presented as real, or claims the candidate can't back up."""


def key(title: str, size: str, mode: str) -> str:
    return hashlib.sha1(f"{title.strip().lower()}|{size}|{mode}".encode()).hexdigest()[:12]


def cache_key(job_id: str, brief_key: str) -> str:
    return f"brief:{job_id}:{brief_key}"


def suggestions(job_id: str) -> list[dict]:
    """The projects suggested for this job so far: {title, source, seed}, seed being what the suggestion said."""
    out = []
    fit = db.cache_get(f"fit2:{job_id}", 24 * 365) or {}
    for p in (fit.get("gap_plan") or {}).get("project_types", []):
        out.append({"title": p.get("title", ""), "source": "fit", "seed": p})
    review = db.cache_get(f"ghreview:{job_id}", 24 * 365) or {}
    for idea in review.get("ideas", []):
        out.append({"title": idea.get("title", ""), "source": "github", "seed": idea})
    return [s for s in out if s["title"]]


def find(job_id: str, title: str) -> dict | None:
    return next((s for s in suggestions(job_id) if s["title"].strip().lower() == title.strip().lower()), None)


def make(profile: Profile, job: Job, seed: dict, size: str, mode: str) -> ProjectBrief:
    size_text = SIZES.get(size, SIZES[DEFAULT_SIZE])[1]
    mode_text = MODES.get(mode, MODES[DEFAULT_MODE])[1]
    content = (f"Project idea (what was suggested for this job):\n{json.dumps(seed, ensure_ascii=False, indent=1)}\n\n"
               f"Size: {size_text}.\nHow it will be built: {mode_text}\n\n"
               f"Job posting:\n{_job_text(job)}\n\nCandidate profile:\n{profile.model_dump_json()}")
    return llm.ask(SYSTEM, content, ProjectBrief, effort="high")


def _list(items: list[str], numbered: bool = False) -> str:
    return "\n".join(f"{i + 1}. {x}" if numbered else f"- {x}" for i, x in enumerate(items))


def prompt(brief: ProjectBrief, job: Job, size: str, mode: str) -> str:
    """The spec as one Markdown prompt to paste into Claude Code or a Claude chat."""
    if mode == "guided":
        intro = f"""# Mentor me while I build: {brief.title}

I'm building this project myself to show in job applications ({job.title} at {job.company}). Be my mentor, not my coder:
- Go milestone by milestone. For each one, explain the concepts first, then give me the steps and hints. Don't write whole files for me unless I ask.
- Review the code I paste: point out bugs, design problems and missing tests, and explain why.
- Ask me questions to check I understand the design decisions; I'll need to defend them in interviews.
- Keep to the spec below. If I want to change it, tell me the trade-off first.
"""
    else:
        intro = f"""# Build this project: {brief.title}

Build the project specified below in this repository. It's for my portfolio, to apply for {job.title} at {job.company}, so the code must be clean, idiomatic and something I can explain line by line.
- Work milestone by milestone, in order. Before you start a milestone, say briefly what you'll do; after it, run the tests and the "done when" checks, then commit with a clear message.
- Keep to the stack and scope below. Ask me before adding a dependency or a feature that isn't listed.
- Write tests as you go, and keep the README up to date.
- After each milestone, explain the key design decisions in a few sentences, so I can discuss them in an interview.
"""
    parts = [intro.strip(), f"## The project\n{brief.one_liner}\n\n{brief.what_you_build}",
             f"Size: {SIZES.get(size, SIZES[DEFAULT_SIZE])[0]}.",
             f"## Why it matters for this job\n{_list(brief.why_this_job)}",
             f"## Scope\nIn scope:\n{_list(brief.in_scope)}\n\nOut of scope (don't build these):\n{_list(brief.out_of_scope)}",
             "## Tech stack\n" + "\n".join(f"- **{t.name}**: {t.why}" for t in brief.tech_stack),
             f"## Architecture\n{_list(brief.architecture)}",
             f"## Data model\n{_list(brief.data_model)}",
             f"## Interfaces\n{_list(brief.interfaces)}"]
    for i, m in enumerate(brief.milestones, 1):
        parts.append(f"## Milestone {i}: {m.title}\nGoal: {m.goal}\n\nTasks:\n{_list(m.tasks)}\n\nDone when:\n{_list(m.done_when)}")
    parts += [f"## Repository layout\n{_list(brief.repo_layout)}",
              f"## Testing\n{_list(brief.testing)}",
              f"## CI and deployment\n{_list(brief.ci_and_deploy)}",
              f"## README\n{_list(brief.readme_outline)}",
              f"## Demo\n{_list(brief.demo)}",
              f"## Pitfalls to avoid\n{_list(brief.pitfalls)}"]
    if mode == "guided":
        parts.append(f"## Concepts I need to understand\n{_list(brief.learn_first)}")
    return "\n\n".join(p for p in parts if p.strip()) + "\n"
