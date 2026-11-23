# Server mode and security

[← Back to the README](../README.md) · [Configuration](configuration.md)

CVMaker runs in two modes from the same code (`CVMAKER_MODE`). Desktop mode is the default and needs none of this
page except [Security](#security-both-modes). Server mode is a hosted site with accounts.

| | **Desktop** (default) | **Server** |
|---|---|---|
| Who | One person on their own computer | Many people with accounts |
| Login | None; it only answers on 127.0.0.1 | Email + password, emailed login links, Google sign-in |
| AI | The user's **own Anthropic API key** (Settings → API keys) | The operator's key, metered per plan; users may add their own |
| Data | A folder on the computer | One folder per account under `data/accounts/`, Postgres or SQLite for accounts and the task queue |
| Payment | Free | Stripe (off until configured) |

## Running it

```bash
cp .env.example .env         # fill in the "Server mode" section: secret key, public URL, Postgres password, email…
docker compose up -d         # web + worker + Postgres; put a TLS proxy (Caddy/Traefik/nginx) in front of :8000
```

The image installs exactly the versions in `uv.lock`. `docker-compose.yml` starts three services: the web app
(2 uvicorn workers, no task threads), one worker process that runs the task queue and the scheduler, and Postgres.
`/healthz` answers without a login, for the proxy's health check.

## What server mode adds

- **Accounts:** `/signup` (email confirmation required before use), `/login`, emailed login links, password reset, Google sign-in (with `GOOGLE_CLIENT_ID`/`SECRET`). Passwords are scrypt hashes. Session and email tokens are random, and only their hashes are stored.
- **Keeping users apart:** the account comes only from the session cookie. All data paths go through `people.account_root()`, so each account lives in its own folder. "People" are profiles inside an account, and the person cookie only picks people of that account.
- **AI:** simple work uses cheaper models (`fast` = Haiku: rating many jobs, reading postings and career pages; `standard` = Sonnet: reviews, analysis, chat, CV import). Opus is kept for what goes to employers (tailored CVs, letters, prep, offers). Every call is metered per account. Plans get a monthly token allowance (`CVMAKER_FREE_TOKENS`, `CVMAKER_PRO_TOKENS`) and a per-minute limit. Users with their own key aren't limited.
- **Background tasks** are a queue in the central database and survive restarts: `python -m cvmaker.worker 4 --scheduler`. A task whose worker dies is picked up again when its lease runs out. Scheduled searches per account are capped by plan.
- **Billing:** Stripe Checkout, Customer Portal and a signed webhook (`/billing/webhook`). Turn on Stripe Tax for EU VAT.
- **Email:** Resend, Postmark or SMTP (`CVMAKER_MAIL`). `console` writes emails to `data/outbox/` for development.
- **Backups:** every account and person is backed up automatically (`CVMAKER_BACKUP_EVERY_HOURS`) and copied to S3-compatible storage when `S3_BUCKET` is set.
- **GDPR:** Settings → *Download all my data* (a zip of everything, account details and AI usage included) and *Delete my account* (data, sessions and S3 backups go at once, or after `CVMAKER_DELETED_RETENTION_DAYS`). Privacy, terms and cookie pages are at `/legal/…` and use `CVMAKER_OPERATOR*`. **Have them checked by a lawyer before launch.**

## Security (both modes)

- **Host check and CSRF:** the app only answers to its own host names (desktop: localhost, which stops DNS rebinding). Anything that changes data must come from the app's own pages (Origin/Referer check). Cookies are HttpOnly and SameSite=Lax, and security headers are on every response.
- **Fetching links:** the page fetcher refuses private and local addresses (also after redirects), odd ports and non-http schemes.
- **Chrome:** it keeps its sandbox. `CVMAKER_CHROME_NO_SANDBOX=1` is only for containers that can't run it. Pages printed to PDF get a Content-Security-Policy, so nothing in a CV can load local files or reach the network.
- **Uploads:** size limits, the real type is checked from the content, pictures are decoded and saved again by Pillow, zip bombs are refused, and uploaded files are served sandboxed.
- **Prompt injection:** job postings, web pages and GitHub text go to Claude wrapped as untrusted data, with an instruction never to follow what's inside.

## Logs

The app logs to stderr with a time and a level. Background work (the task worker, the scheduler, the Telegram bot)
reports failures there, with a traceback. `CVMAKER_LOG_LEVEL=DEBUG` also adds a traceback to every failed task and
posting check, which are otherwise one line.
