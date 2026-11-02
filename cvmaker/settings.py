import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

COMPANIES_FILE = ROOT / "companies.yaml"

BACKEND = os.environ.get("CVMAKER_BACKEND", "claude-code")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
MODEL = os.environ.get("CVMAKER_MODEL", "claude-opus-5-5")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
GITHUB_USER = os.environ.get("GITHUB_USER", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
CHROME_BIN = os.environ.get("CHROME_BIN", "google-chrome")
CAREER_PAGE_CACHE_HOURS = int(os.environ.get("CAREER_PAGE_CACHE_HOURS", "24"))
# How many background tasks (searches, Claude calls) run at the same time.
TASK_WORKERS = int(os.environ.get("CVMAKER_TASK_WORKERS", "4"))
# Days after applying until the app reminds you to follow up.
FOLLOW_UP_DAYS = float(os.environ.get("CVMAKER_FOLLOW_UP_DAYS", "10"))

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) CVMaker/0.1 (personal job search)"

_PERSON_FILES = {
    "DB_PATH": "cvmaker.db",
    "PROFILE_PATH": "profile.json",
    "PHOTO_PATH": "photo.png",
    "OUTPUT_DIR": "cvs",
}


def __getattr__(name: str) -> Path:
    """DATA_DIR and the paths inside it belong to the current person (see people.py)."""
    from . import people

    if name == "DATA_DIR":
        return people.data_dir()
    if name in _PERSON_FILES:
        return people.data_dir() / _PERSON_FILES[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
