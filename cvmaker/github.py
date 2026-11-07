"""Reads the candidate's public GitHub repos: README, file list and build files of each."""

import re

import httpx

from . import db, keys, people, settings
from .models import Profile
from .web_fetch import http

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
CACHE_HOURS = 24
README_CHARS = 8000
BUILD_FILE_CHARS = 4000
MAX_PATHS = 300
SKIP_DIRS = ("node_modules/", ".git/", "target/", "build/", "dist/", "out/", ".idea/", ".vs/", "bin/", "obj/", "vendor/")
BUILD_FILES = (
    "pom.xml", "build.gradle", "build.gradle.kts", "package.json", "CMakeLists.txt",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "requirements.txt", "pyproject.toml",
    "application.yml", "application.yaml", "application.properties",
)


class GithubError(RuntimeError):
    pass


def username(profile: Profile | None) -> str:
    """The GitHub user in the profile's contact; the username in Settings only stands in for the default person."""
    link = profile.contact.github if profile else ""
    match = re.search(r"github\.com/([A-Za-z0-9-]+)", link) or re.fullmatch(r"([A-Za-z0-9-]+)", link.strip())
    if match:
        return match.group(1)
    if keys.own("github_user") and people.is_default():
        return keys.own("github_user")
    if settings.DESKTOP and settings.GITHUB_USER and people.is_default():
        return settings.GITHUB_USER
    raise GithubError("No GitHub username - add github.com/<username> to the profile's contact details")


def _api_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token := keys.get("github_token"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_get(client: httpx.Client, path: str):
    r = client.get(f"{API}{path}", headers=_api_headers())
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise GithubError("GitHub rate limit reached (60 requests/hour without a token) - add a GitHub token in Settings → API keys or try later")
    r.raise_for_status()
    return r.json()


def _raw(client: httpx.Client, user: str, repo: str, branch: str, path: str) -> str:
    r = client.get(f"{RAW}/{user}/{repo}/{branch}/{path}")
    return r.text if r.status_code == 200 else ""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n[... truncated, {len(text) - limit} more characters]"


def fetch_repos(user: str) -> list[dict]:
    cached = db.cache_get(f"github:{user}", CACHE_HOURS)
    if cached is not None:
        return cached
    repos = []
    with http() as c:
        listing = _api_get(c, f"/users/{user}/repos?per_page=100&sort=updated")
        for r in listing:
            if r.get("fork") or r.get("archived"):
                continue
            branch = r.get("default_branch") or "main"
            try:
                tree = _api_get(c, f"/repos/{user}/{r['name']}/git/trees/{branch}?recursive=1").get("tree", [])
            except httpx.HTTPStatusError:
                tree = []
            paths = [t["path"] for t in tree if t.get("type") == "blob" and not any(d in t["path"] + "/" for d in SKIP_DIRS)]
            readme_path = next((p for p in paths if re.fullmatch(r"readme(\.\w+)?", p, re.I)), "")
            build = {}
            for p in paths:
                if p.rsplit("/", 1)[-1] in BUILD_FILES and len(build) < 6:
                    build[p] = _clip(_raw(c, user, r["name"], branch, p), BUILD_FILE_CHARS)
            repos.append(
                {
                    "name": r["name"],
                    "url": r["html_url"],
                    "description": r.get("description") or "",
                    "language": r.get("language") or "",
                    "topics": r.get("topics") or [],
                    "stars": r.get("stargazers_count", 0),
                    "updated": (r.get("pushed_at") or "")[:10],
                    "file_count": len(paths),
                    "files": paths[:MAX_PATHS],
                    "readme": _clip(_raw(c, user, r["name"], branch, readme_path), README_CHARS) if readme_path else "",
                    "build_files": build,
                }
            )
    db.cache_put(f"github:{user}", repos)
    return repos


def repos_for_prompt(repos: list[dict], compact: bool = False) -> str:
    blocks = []
    for r in repos:
        if compact:
            blocks.append(
                f"=== Repo: {r['name']} ({r['url']}) | {r['language']} | updated {r['updated']}\n"
                f"Description: {r['description']}\nBuild/config files: {', '.join(r['build_files']) or '(none)'}\n"
                f"README:\n{_clip(r['readme'], 1500) or '(none)'}"
            )
            continue
        files = "\n".join(r["files"])
        if r["file_count"] > len(r["files"]):
            files += f"\n[... {r['file_count'] - len(r['files'])} more files]"
        build = "\n\n".join(f"--- {p} ---\n{content}" for p, content in r["build_files"].items())
        blocks.append(
            f"=== Repo: {r['name']} ({r['url']})\n"
            f"Language: {r['language']} | Updated: {r['updated']} | Stars: {r['stars']} | Topics: {', '.join(r['topics'])}\n"
            f"Description: {r['description']}\n\nREADME:\n{r['readme'] or '(none)'}\n\n"
            f"Files:\n{files or '(empty)'}\n\nBuild/config files:\n{build or '(none)'}"
        )
    return "\n\n".join(blocks)
