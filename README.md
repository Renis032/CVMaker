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
3. **Search** (`/`): set title keywords, skills, countries and whether remote jobs count. Optional filters: seniority (junior/mid/senior, read from the title), remote only, "description must mention", only jobs that state a salary, and a minimum keyword score. The app searches every source at once, then filters, removes duplicates and ranks the results. The list can be sorted by score, newly found, posted date, salary first or company.
4. **Salary**: shown whenever a posting states one. It comes from the source's salary fields where they exist (Ashby, Lever, Recruitee, Himalayas, Jobicy, dev.bg, Adzuna, Remotive, RemoteOK, JSearch); otherwise it is picked out of the posting text (e.g. "€62,000 to €87,200", "4 500 - 6 000 лв. бруто").
5. **Automatic search**: on the jobs page, turn on "run the saved search every N hours". Newly found jobs are marked **NEW** until you open them or press "Mark all as seen"; the header shows how many there are, and a desktop notification is sent (`notify-send`). The schedule runs while the app is open. To search while it is closed, add a cron job, e.g. `0 */6 * * * cd ~/Personal/Projects/CVMaker && ~/.local/bin/uv run python -m cvmaker.auto`.
6. **Per job**: check your fit, then **generate a tailored CV**. Claude reorders and rewords your profile for the posting and renders a PDF in your CV's design. Requirements you don't meet are listed separately and are never put into the CV.
7. **Rate the CV** (on the CV page): scores the tailored CV and your original (or another CV for the same job) on the same scale: overall chance of an interview, six categories, what improved and what got worse. Two checks are measured, not judged by Claude: which of the posting's keywords each version contains, and an ATS check of the PDF's text layer (name, email, phone, job titles and keywords readable by screening software). Scores vary a little between runs, so compare the two versions within one rating.
8. **Cover letter** (job page): in English, Bulgarian or German, choice of tone and length, based on your profile or a tailored CV. Edit the text on the letter page; saving re-renders the PDF. Requirements you don't meet are listed as "not claimed".
9. **Interview prep** (job page): likely questions by category with answer outlines from your own experience, STAR stories, topics to revise by priority, honest answers for your gaps, and questions to ask them.

## Job sources

| Source | What it covers | Key needed |
|---|---|---|
| `greenhouse`, `lever`, `ashby`, `smartrecruiters`, `workable`, `recruitee`, `personio` | Company job boards on these hiring platforms (public APIs) | no |
| `careerpage` | Any company careers page; Claude picks out the job links (cached for 24h) | no (uses Claude) |
| `devbg` | dev.bg, the main Bulgarian IT job board (runs when BG is in your countries) | no |
| `arbeitnow` | Jobs in Germany and remote jobs (runs for DE/AT or with remote) | no |
| `remotive`, `remoteok`, `himalayas`, `jobicy`, `weworkremotely` | Remote job boards | no |
| `jsearch` | Google Jobs, which includes **LinkedIn**, Indeed and Glassdoor listings | RapidAPI |
| `adzuna` | Large job search engine for DE, NL, GB, AT, PL, ES, FR, IT and more (not Bulgaria) | free key at developer.adzuna.com |

jobs.bg is not included: it blocks automated requests.

LinkedIn is not scraped directly. That breaks LinkedIn's terms and gets accounts banned. JSearch provides the same listings through a legitimate API.

### Adding companies / countries

The easiest way is the **Companies** page (`/companies`): enter a company name and its website. The app finds the careers page and checks whether it runs on a hiring platform with a public API. It looks at the page's links and embeds, then tries the platforms with a slug guessed from the domain and name. If none fits, it falls back to `careerpage`. It shows the postings it found before you save, and every saved company has a **Test** button.

You can also edit `companies.yaml` by hand. Companies are grouped by country code, and `REMOTE` holds remote-first companies. To find a company's hiring platform, open one of its postings and look at the URL (e.g. `boards.greenhouse.io/<slug>` means `type: greenhouse`). Anything else is `type: careerpage` with the URL of the jobs list. Add new countries' city names to `COUNTRY_TERMS` in `cvmaker/search.py`.

## Layout

```
cvmaker/
  app.py         web UI (FastAPI)
  search.py      runs sources in parallel, filters, removes duplicates, scores
  sources.py     one function per job source
  salary.py      formats salary fields; finds salaries in posting text
  companies.py   companies.yaml editing; finds a company's careers page / hiring platform
  auto.py        scheduled searches (also `python -m cvmaker.auto` for cron)
  assistant.py   Claude prompts: CV import, interview, fit check, tailoring, CV rating, cover letter, interview prep
  cvcheck.py     measured CV checks: job keyword coverage, PDF text layer (ATS)
  llm.py         Claude calls with structured output: Claude Code (subscription) or API
  render.py      CV HTML template -> PDF via headless Chrome
  templates/cv.html   the CV design
data/            profile.json, photo.png, SQLite DB, generated CVs (git-ignored)
```
