import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "cvmaker.db"
PROFILE_PATH = DATA_DIR / "profile.json"
PHOTO_PATH = DATA_DIR / "photo.png"
OUTPUT_DIR = DATA_DIR / "cvs"
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

for d in (DATA_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)
