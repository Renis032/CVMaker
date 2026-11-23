# Development

[← Back to the README](../README.md) · [Configuration](configuration.md)

## Running from source

```bash
uv sync                 # Python 3.12 and the dependencies, from uv.lock
./run.sh                # the app with auto-reload on http://127.0.0.1:8000
```

`./run.sh` runs the same app as `uv run cvmaker`, without the window. Setting `CVMAKER_BACKEND=claude-code` in
`.env` routes Claude calls through your local Claude Code login (desktop only, personal use; server mode refuses it).

## Tests and checks

```bash
uv run pytest                                            # everything; about 10 seconds
uv run ruff check                                        # lint
CVMAKER_TEST_DATABASE_URL=postgresql://… uv run pytest   # the same against Postgres
```

The tests run the app in server mode against a throw-away data folder; desktop mode is tested in a subprocess,
because the mode is fixed at start-up. The PDF export test is skipped when Chrome is not installed. They cover:

- users can't reach each other's data; sign-up, verification, login, reset and login links; CSRF and host checks;
  the private-address block; upload checks; Stripe webhooks; usage limits; data export and account deletion;
- the task queue: tasks survive a restart, and the worker process knows every task;
- every main page renders, and links only CSS and JS that exist;
- the core logic, without the web app: salary parsing and conversion, exchange rates, the skill check, search
  filters and scoring, the tailored-CV diff, posting text to HTML, job sources (against fixtures in
  `tests/fixtures/`), posting checks, learning from feedback, the jobs-list filters, and database migrations;
- the editor round trip and PDF export; the Telegram bot.

The source fixtures are written by hand in the shape each job board's API returns. They catch a change in how the
app reads a field; they cannot tell when a job board changes its API.

CI (`.github/workflows/ci.yml`) runs ruff and pytest, and builds the editor to check that the committed build in
`cvmaker/static/editor/` still matches `editor/src`.

## The CV editor

The editor is a small React app in `editor/`, built into `cvmaker/static/editor/`, so running the app doesn't need
Node. After changing it, run `npm install && npm run build` in `editor/` and commit the build. `npm run dev` gives
hot reload on :5179 against the running app.

## Pages, CSS and JavaScript

Pages are Jinja templates in `cvmaker/templates/`. Their CSS and JavaScript live in `cvmaker/static/app/`: `base.css`
and `base*.js` for every page, and one `<page>.css` / `<page>.js` per page that needs its own. A template links them
with `{{ static('index.css') }}`, which adds a hash of the file's content, so browsers keep the file for a year and
still pick up a change at once. No build step.

## Changing the database

Each person has a SQLite file (`cvmaker/db.py`). `PRAGMA user_version` records its schema version. To change the
schema, append a step to `STEPS` in `db.py`: a list of SQL statements, or a function that takes the connection. It
runs once per database, in one transaction, the next time that database is opened. Never edit or reorder a step
that has shipped. `tests/test_db_schema.py` shows the cases that must keep working.

The central database (accounts, sessions, usage, the task queue; SQLite or Postgres) is in `cvmaker/central.py`.

## Layout

```
cvmaker/
  app.py              creates the web app, the request guard, static files; includes the route modules
  web.py              what every page shares: templates and filters, page(), redirects, static()
  jobs_routes.py      jobs list, saved searches, rating a view, feedback suggestions, export
  job_routes.py       pipeline, insights, the job page, fit check, application pack, GitHub review, interview prep
  cv_routes.py        tailored CVs and cover letters: making, editing, layout, rating, previews, PDFs
  profile_routes.py   profile, profile interview, role profiles, the people of an account
  companies_routes.py the company list: find, add, test, remove
  stage_routes.py     add a job by link, posting checks, outreach, story bank, practice, debriefs, offers
  chat_routes.py      /chat pages
  editor_routes.py    /editor pages and API
  extras_routes.py    company logos, the notification bell, backups, history
  auth_routes.py      sign-up/login pages, Settings (API keys, plan and usage, export, delete), billing, legal pages
  telegram_routes.py  the Telegram and start-at-login actions in Settings (desktop mode)

  search.py      runs sources in parallel, filters, removes duplicates, scores
  sources.py     one function per job source
  categories.py  kinds of job: title words, skills and board phrases for the search; skill groups and sections for a blank CV
  countries.py   city and country words used to recognise where a job is
  companies.py   companies.yaml editing; finds a company's careers page / hiring platform
  jobfilter.py   the jobs list's text search, filters and the counts beside each option
  salary.py      formats salary fields; finds salaries in posting text; converts to € per month
  rates.py       exchange rates to EUR: the ECB's daily rates, with a fixed table as fallback
  auto.py        saved searches, schedules, follow-up reminders, Claude fit rating (`python -m cvmaker.auto` for cron)
  taste.py       learns from hide reasons and likes: taste score, filter suggestions, blocked companies, Claude context
  checks.py      posting checks: still open?, reposts, work-permit and scam wording, Claude's review
  intake.py      add a job from a pasted link or text
  logos.py       company logos: the company's site icon, found once and kept on disk
  posting.py     plain-text job posting -> readable HTML (headings, bullets, paragraphs)

  assistant.py   Claude prompts: CV import, interview, fit check, tailoring, CV rating, cover letter, interview prep
  coach.py       Claude prompts for the later stages: reading a posting, outreach, stories, practice, debriefs, offers
  llm.py         Claude calls with structured output: model tiers, the account's key, usage metering, untrusted-text wrapping
  pack.py        application packs for high-scoring jobs; the fit/CV/letter/prep/GitHub steps the job page uses
  stage.py       the steps after applying: outreach, practice, debriefs, offer review (one Claude call each)
  stories.py     the story bank: STAR+R stories per person
  insights.py    skill demand, salaries and weekly postings from the collected jobs; Claude gap analysis across fit checks
  github.py      reads the candidate's public GitHub repos: README, file list, build files
  roles.py       role profiles: saved target positions that steer tailoring and prefill searches
  chat.py        the chat: context for Claude, proposed profile changes (apply/undo, readable diff), reply formatting
  claims.py      skill check: technologies in outgoing documents that the profile and GitHub don't back
  cvdiff.py      tailored CV vs base CV: bullet/word diff, facts not found in the profile
  cvcheck.py     measured CV checks: job keyword coverage, PDF text layer (ATS)

  editor.py      CV editor glue: versions/style (editor.json), profile <-> editor content, item ids, PDF printing via the editor's page, CVEditor import
  render.py      CV -> PDF via headless Chrome (drawn by the CV editor's page); cover letters
  history.py     snapshots of the profile and the editor's layout, so any change can be undone
  backup.py      one-zip backups of everything of a person, and restoring from one

  models.py      the data shapes (pydantic): Job, Criteria, Profile, CV content, and what Claude returns
  db.py          each person's SQLite database: jobs, applications, CVs, letters, tasks, chat, cache; schema versions
  people.py      accounts and the people inside them: whose data folder a request or task works on
  tasks.py       background tasks: registered runners + a durable queue with leases
  worker.py      separate task-queue worker process (`python -m cvmaker.worker`)
  telegram.py    the Telegram bot (desktop mode): daily summary, job buttons, CVs, chat by message
  autostart.py   start at login (systemd user service, Linux)
  desktop.py     the desktop launcher (`cvmaker`)

  settings.py    configuration (desktop / server mode)
  security.py    host check, CSRF, login gate, security headers
  web_fetch.py   fetching pages from the internet: refuses private addresses, runs headless Chrome when needed
  uploads.py     upload size/type checks, image re-encoding
  accounts.py    accounts, passwords, sessions, email tokens, Google sign-in, plans, deletion
  central.py     the central database (SQLite or Postgres): accounts, sessions, usage, rate limits, task queue
  keys.py        each account's API keys, stored encrypted
  usage.py       AI usage per account and the plan limits
  billing.py     Stripe checkout, portal and webhook
  mailer.py      email (console, SMTP, Resend, Postmark)
  storage.py     S3-compatible storage for backups

  templates/     the pages (Jinja)
  static/app/    the pages' CSS and JavaScript
  static/editor/ the built CV editor (source in editor/)
editor/          the CV editor's source (React + Vite)
tests/           pytest suite; fixtures/ holds job-board answers for the source tests
docs/            this documentation and the screenshots
data/            desktop: profile.json, photo.png, searches.json, prefs.json, feedback.json, SQLite DB, generated CVs;
                 server: accounts/<id>/… per account, central.db (git-ignored)
Dockerfile, docker-compose.yml   server deployment
```
