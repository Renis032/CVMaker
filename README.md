# CVMaker

Finds software developer jobs in the countries you choose, plus fully remote jobs, from job boards and directly from company career pages. Then it uses Claude to tailor your CV to the job you pick.

## Setup

```bash
cp .env.example .env        # optional: RAPIDAPI_KEY for LinkedIn/Indeed
./run.sh                    # http://127.0.0.1:8000
```

By default every Claude call runs through your local **Claude Code** login (`claude -p`), so it uses your Claude Pro/Max plan with no API billing. Your global CLAUDE.md and auto-memory are switched off for these calls. To use a pay-as-you-go API key instead, set `CVMAKER_BACKEND=api` and `ANTHROPIC_API_KEY` in `.env`.

Requirements: Python 3.12 (installed with `uv`) and Google Chrome. Chrome loads JavaScript-heavy career pages and prints the CVs to PDF.

## How it works

1. **Profile** (`/profile`): import your CV as a PDF, or edit `data/profile.json` directly. This profile is the only source of facts for your CVs.
2. **Interview**: Claude asks about gaps in your profile, either in general or for a specific job. Your answers are added to the profile.
3. **Search** (`/`): set title keywords, skills, countries and whether remote jobs count. The app searches every source at once, then filters, removes duplicates and ranks the results.
4. **Per job**: check your fit, then **generate a tailored CV**. Claude reorders and rewords your profile for the posting and renders a PDF in your CV's design. Requirements you don't meet are listed separately and are never put into the CV.

## Job sources

| Source | What it covers | Key needed |
|---|---|---|
| `greenhouse`, `lever`, `ashby`, `smartrecruiters` | Company job boards on these hiring platforms (public APIs) | no |
| `careerpage` | Any company careers page; Claude picks out the job links (cached for 24h) | no (uses Claude) |
| `remotive`, `remoteok` | Remote job boards | no |
| `jsearch` | Google Jobs, which includes **LinkedIn**, Indeed and Glassdoor listings | RapidAPI |

LinkedIn is not scraped directly. That breaks LinkedIn's terms and gets accounts banned. JSearch provides the same listings through a legitimate API.

### Adding companies / countries

Edit `companies.yaml`. Companies are grouped by country code, and `REMOTE` holds remote-first companies. To find a company's hiring platform, open one of its postings and look at the URL (e.g. `boards.greenhouse.io/<slug>` means `type: greenhouse`). Anything else is `type: careerpage` with the URL of the jobs list. Add new countries' city names to `COUNTRY_TERMS` in `cvmaker/search.py`.

## Layout

```
cvmaker/
  app.py         web UI (FastAPI)
  search.py      runs sources in parallel, filters, removes duplicates, scores
  sources.py     one function per job source
  assistant.py   Claude prompts: CV import, interview, fit check, tailoring
  llm.py         Claude calls with structured output: Claude Code (subscription) or API
  render.py      CV HTML template -> PDF via headless Chrome
  templates/cv.html   the CV design
data/            profile.json, photo.png, SQLite DB, generated CVs (git-ignored)
```
