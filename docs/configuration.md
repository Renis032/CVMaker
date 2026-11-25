# Configuration

[← Back to the README](../README.md) · [Server mode](server.md)

Settings are environment variables, read from a `.env` file in the project folder (`cp .env.example .env`).
**Desktop mode needs none of them**: API keys go in **Settings → API keys** in the app. The tables below list the
ones you are most likely to change; [`.env.example`](../.env.example) has every setting with a comment.

## Both modes

| Variable | Default | What it does |
|---|---|---|
| `CVMAKER_MODE` | `desktop` | `desktop` (one person, no login, 127.0.0.1 only) or `server` (accounts). |
| `CVMAKER_DATA` | `./data`, or the system's per-user folder | Where everything is stored. |
| `ANTHROPIC_API_KEY` | none | Desktop: a default key for this computer (Settings overrides it). Server: the operator's key. |
| `CVMAKER_MODEL_FAST` / `_STANDARD` / `_BEST` | `claude-haiku-5-5` / `claude-sonnet-5-5` / `claude-opus-5-5` | The model for each kind of work: rating many jobs / analysis and chat / what goes to employers. |
| `CVMAKER_BACKEND` | `api` | `claude-code` runs Claude calls through your local Claude Code login (desktop only, personal use). |
| `CHROME_BIN` | the first one found | The Chromium-based browser used for PDFs and JavaScript-built pages. Unset, CVMaker looks for Chrome, Chromium, Edge and Brave. |
| `CVMAKER_TASK_WORKERS` | `4` | Background tasks run at the same time. `0` when separate worker processes run the queue. |
| `CVMAKER_TASK_ATTEMPTS` | `3` | How many times a task cut off by a restart is started again. |
| `CVMAKER_FOLLOW_UP_DAYS` | `10` | Days after applying until the app reminds you to follow up. |
| `CVMAKER_LIVE_RATES` | `1` | Fetch the European Central Bank's exchange rates once a day when a search runs, to compare salaries in EUR. `0` uses the fixed rates built in. |
| `CVMAKER_LOG_LEVEL` | `INFO` | `DEBUG` adds tracebacks for failed tasks and posting checks. |
| `CVMAKER_MAX_UPLOAD_MB` | `15` | Largest upload accepted. |
| `RAPIDAPI_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `GITHUB_TOKEN` | none | Defaults for the job boards that need a key and for GitHub; each user can set their own in Settings. |

## Server mode

| Variable | Default | What it does |
|---|---|---|
| `CVMAKER_SECRET_KEY` | **required** | Signs and encrypts. Make one with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. |
| `CVMAKER_PUBLIC_URL` | `http://127.0.0.1:8000` | The site's address, used in emails and for the host check. |
| `CVMAKER_ALLOWED_HOSTS` | none | Extra host names the app answers to, comma-separated. |
| `DATABASE_URL` | SQLite in the data folder | `postgresql://…` for accounts, sessions, usage and the task queue. |
| `CVMAKER_FREE_TOKENS` / `CVMAKER_PRO_TOKENS` | `300000` / `6000000` | Monthly AI tokens per plan on the operator's key. |
| `CVMAKER_AI_CALLS_PER_MINUTE` | `12` | Per-account rate limit. |
| `CVMAKER_FREE_SCHEDULED` / `CVMAKER_PRO_SCHEDULED` | `1` / `10` | Scheduled searches per plan. |
| `CVMAKER_MAIL` | `console` | `smtp`, `resend` or `postmark`; `console` writes emails to `data/outbox/`. With it: `CVMAKER_MAIL_FROM`, `RESEND_API_KEY`, `POSTMARK_TOKEN`, `SMTP_*`. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | none | Google sign-in (redirect URI: `<PUBLIC_URL>/auth/google/callback`). |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO` | none | Billing; off without the secret key. |
| `CVMAKER_BACKUP_EVERY_HOURS` | `24` | Automatic backups; copied to S3-compatible storage when `S3_BUCKET` is set (`S3_ENDPOINT`, `S3_REGION`, `S3_PREFIX`, AWS keys). |
| `CVMAKER_DELETED_RETENTION_DAYS` | `0` | Days a deleted account's data is kept before it is wiped. |
| `CVMAKER_OPERATOR`, `CVMAKER_OPERATOR_EMAIL`, `CVMAKER_HOSTING_PROVIDER` | placeholders | Who runs the site, for the legal pages. |
| `CVMAKER_CHROME_NO_SANDBOX` | off | Only inside a container that can't run Chrome's sandbox (the Docker image sets it). |
