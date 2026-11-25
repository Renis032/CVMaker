# CVMaker

A job-search workbench that runs on your own computer. It finds jobs in the countries you choose, plus fully remote ones, from job boards and directly from company career pages, then uses Claude to check your fit, tailor your CV and cover letter to the job you pick, and track the application to the offer.

It covers 13 kinds of job (software developer, Java, frontend, Python/data, DevOps/cloud, QA, automation tester, project manager, product manager, business analyst, data analyst, UI/UX designer, IT support), and anything else through your own keywords.

![Jobs list](docs/screenshots/jobs.png)

## Quick start

```bash
uv sync            # once; installs Python 3.12 and the dependencies
uv run cvmaker     # starts the app and opens it in its own window
```

Then, in the app:

1. **Settings → API keys**: paste your [Anthropic API key](https://console.anthropic.com/) and press *Test the Anthropic key*. Keys are stored encrypted on your computer.
2. **My profile → Import my CV**: a PDF, Word file, text file or photo, or **Import from LinkedIn** with the data export LinkedIn emails you. This profile is the only source of facts for everything the app writes.
3. **Jobs**: pick the kinds of job and the countries, and run a search.

You need [uv](https://docs.astral.sh/uv/) and a Chromium-based browser (Chrome, Chromium, Edge or Brave), which loads JavaScript-heavy career pages and prints the CVs to PDF. `uv sync --extra desktop` adds a native window (pywebview); without it the app opens in Chrome's app mode or your default browser.

Your data stays in a folder on your computer (`~/.local/share/CVMaker`, `~/Library/Application Support/CVMaker` or `%APPDATA%\CVMaker`; in a source checkout that already has `data/`, that folder). What leaves it: the text sent to Claude through your own key, and requests to the job boards.

## What it does

| | |
|---|---|
| **Find** | 17 sources: company job boards on Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee and Personio, any careers page, dev.bg, Arbeitnow, five remote boards, and Google Jobs / Adzuna with a key. Saved searches run on a schedule. [Job sources](docs/sources.md) |
| **Filter** | Search syntax (`"exact phrase"`, `-excluded`, `company:acme`), filters with counts, Claude's 1-10 fit rating, salaries converted to € per month, and a "for you" order that learns from the jobs you hide and like. |
| **Check** | Is the posting still open, is it a repost, does it need a work permit you don't have, does it look like a scam. |
| **Apply** | A fit check with a plan for each gap, a one-page tailored CV, a matching cover letter (English, Bulgarian, German), or all of it at once as an application pack. |
| **Stay honest** | Every skill and number in a tailored CV, letter or message is looked up in your profile and GitHub, without AI. What can't be found is flagged with its sentence. Requirements you don't meet are listed, never written into the CV. |
| **Design** | A CV editor that works like Canva: 16 templates, click anything to restyle it, drag anything anywhere, several versions of one CV. [CV editor](docs/editor.md) |
| **Track** | A pipeline from To apply to Offer, follow-up reminders, response rates with and without a tailored CV. |
| **Prepare** | Interview prep from your own experience, a STAR story bank, graded practice answers, debriefs, and a review of the offer or contract. |
| **Ask** | A chat that knows your profile and the job, and can change your profile (shown line by line, saved only when you apply it). Also as a Telegram bot. |

Everything is described in the [user guide](docs/guide.md).

| | |
|---|---|
| ![Job page](docs/screenshots/job.png) | ![CV editor](docs/screenshots/editor.png) |
| ![Application pipeline](docs/screenshots/pipeline.png) | ![Insights: your gaps](docs/screenshots/insights-gaps.png) |
| ![Jobs list, dark theme](docs/screenshots/jobs-dark.png) | ![CV editor, dark theme](docs/screenshots/editor-dark.png) |

The screenshots use a made-up demo profile and fictional companies. [All screenshots](docs/screenshots.md)

## Documentation

- [User guide](docs/guide.md): every feature, in the order you would use it
- [CV editor](docs/editor.md)
- [Job sources](docs/sources.md), and how to add companies and countries
- [Configuration](docs/configuration.md): the settings and their defaults
- [Server mode and security](docs/server.md): hosting it for many people with accounts, plans and billing
- [Development](docs/development.md): running from source, tests, code layout
- [Screenshots](docs/screenshots.md)

## Two modes

The same code runs as a **desktop** app (the default: one person, no login, answers only on 127.0.0.1, your own API key, free) or as a **server** (accounts, the operator's key metered per plan, Stripe billing, Postgres):

```bash
cp .env.example .env      # fill in the "Server mode" section
docker compose up -d      # web + worker + Postgres; put a TLS proxy in front of :8000
```

See [Server mode and security](docs/server.md). The legal pages it ships with are templates: have them checked by a lawyer before launch.

## Troubleshooting

- **"Add your Anthropic API key" on every page**: add the key under Settings → API keys. *Test the Anthropic key* says what is wrong with it.
- **CVs don't become PDFs, or a careers page finds no jobs**: no Chromium-based browser was found (Chrome, Chromium, Edge and Brave are found by themselves). Install one, or set `CHROME_BIN` in `.env` to the browser's program (for example `CHROME_BIN=/opt/chromium/chrome`).
- **A search finds nothing**: the title must contain one of your title keywords and none of the excluded words ("manager" is excluded by default), and the job must be in one of your countries or remote in an allowed region. A source that failed is listed with its error under the search.
- **A LinkedIn or jobs.bg link can't be added**: the app doesn't load those sites. Open the posting and paste its text into *Add job*.
- **Port 8000 is taken** (`./run.sh`): `PORT=8001 ./run.sh`. `uv run cvmaker` picks a free port itself.
- **The Telegram bot is silent**: it runs only while the app runs, and new jobs only arrive if a saved search runs on a schedule.
- **Server mode answers "Unknown host" (421)**: set `CVMAKER_PUBLIC_URL` to the address people use, or add the name to `CVMAKER_ALLOWED_HOSTS`.
- **Something failed in the background**: the app logs to the terminal; `CVMAKER_LOG_LEVEL=DEBUG` adds tracebacks.

## Development

```bash
./run.sh              # auto-reload on http://127.0.0.1:8000
uv run pytest         # the test suite
uv run ruff check     # lint
```

More in [Development](docs/development.md).

## License

No license has been chosen yet. Until one is added, the code is published for reading only and all rights are reserved.
