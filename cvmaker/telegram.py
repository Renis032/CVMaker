"""The Telegram bot: a daily summary of new, well-rated jobs on your phone, CVs on request, and the chat by message.

Desktop mode only. The bot asks Telegram for new messages (long polling), so nothing here listens on a port and the
app stays on 127.0.0.1. The token you get from @BotFather is kept encrypted with the other keys (keys.py).

Who it answers: each person in the app links one Telegram account with a one-time code from Settings. Anyone else
gets no answer, and every message and button press is checked against the linked account.

What it can do: the chat works as on /chat, so a profile change is only saved when you tap Apply. Claude's work goes
through the task queue (tasks.py), so it shows under Activity and the bot stays responsive.
"""

import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from . import assistant, chat, claims, cvdiff, db, keys, log, pack, people, settings, tasks, taste
from .sources import ensure_description

API_URL = "https://api.telegram.org"
POLL_SECONDS = 25
LINK_MINUTES = 10
# A digest shows this many jobs; /jobs sends the next ones.
JOBS_PER_MESSAGE = 5
# Only jobs found this recently count as new (and sent ids older than twice this are forgotten).
NEW_DAYS = 7
# Telegram's limit is 4096 characters per message; stay under it so a split falls between lines.
MESSAGE_CHARS = 3800
MAX_DIFF_LINES = 30
# Pictures sent together arrive as separate messages; wait this long so they become one question.
ALBUM_WAIT_SECONDS = 2
# Top-match alerts arrive without a sound or buzz between these hours.
QUIET_FROM, QUIET_UNTIL = 22, 8
TOKEN_FORMAT = re.compile(r"^\d{5,}:[\w-]{20,}$")
TIME_FORMAT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Job stages a Telegram button may hide (and undo back to): never ones you already applied at.
HIDEABLE = ("new", "liked")

HELP = """I send your new, well-rated jobs every day. You can also just write to me.

Try:
- "shorten my summary"
- "put my C++ project higher"
- "should I apply for this job?"

You can also send a PDF, Word file, photo or text file, with your question as the caption.

Commands:
/jobs - the next new jobs
/suggest - suggestions for improving your CV
/new - start a new conversation
/unlink - disconnect this Telegram account

Your profile only changes when you tap Apply."""


class TelegramError(RuntimeError):
    """Telegram refused the request. The text never contains the token."""

    def __init__(self, text: str, status: int = 0):
        super().__init__(text)
        self.status = status


class NetworkError(TelegramError):
    """Telegram could not be reached; trying again later can work."""


class Config(BaseModel):
    """One person's Telegram settings (telegram.json in their data folder)."""

    chat_id: int = 0
    username: str = ""
    link_code: str = ""
    link_expires: float = 0
    daily: bool = True
    daily_time: str = "08:30"
    min_rating: int = 7
    # Send a job as soon as it is rated this high, instead of waiting for the summary.
    alerts: bool = True
    alert_rating: int = 9
    last_day: str = ""
    last_error: str = ""
    # The conversation messages currently go to (a thread of the chat on /chat).
    thread_id: int = 0
    # Job id -> when it was sent, so a job is announced once.
    sent: dict[str, float] = Field(default_factory=dict)


_lock = threading.RLock()


def _write(path: Path, text: str) -> None:
    """Atomic and readable only by this user: the file holds the link code and chat id."""
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(temp, path)


def _config_path() -> Path:
    return settings.DATA_DIR / "telegram.json"


def load_config() -> Config:
    path = _config_path()
    return Config.model_validate_json(path.read_text()) if path.exists() else Config()


def update_config(**fields) -> Config:
    with _lock:
        config = load_config().model_copy(update=fields)
        _write(_config_path(), config.model_dump_json(indent=2))
        return config


def _bot_path() -> Path:
    return people.account_root() / "telegram_bot.json"


def _bot_state() -> dict:
    path = _bot_path()
    return json.loads(path.read_text()) if path.exists() else {}


def _save_bot_state(**fields) -> None:
    with _lock:
        _write(_bot_path(), json.dumps({**_bot_state(), **fields}))


def _token() -> str:
    return keys.get("telegram_token")


# --- talking to Telegram -------------------------------------------------------------------------------------------


def _api(method: str, data: dict | None = None, document: tuple[str, bytes] | None = None, timeout: float = 20, token: str = ""):
    """Call the Bot API. Errors carry Telegram's description only: httpx's own messages include the URL, and the URL
    includes the token."""
    token = token or _token()
    if not token:
        raise TelegramError("No bot token is set")
    url = f"{API_URL}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=timeout) as client:
            if document:
                response = client.post(url, data=data, files={"document": (document[0], document[1], "application/pdf")})
            else:
                response = client.post(url, json=data or {})
    except httpx.HTTPError as e:
        raise NetworkError(f"Couldn't reach Telegram ({type(e).__name__})") from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not body.get("ok"):
        raise TelegramError(str(body.get("description") or f"Telegram answered {response.status_code}")[:300], response.status_code)
    return body.get("result")


def _keyboard(rows: list[list[dict]] | None) -> dict:
    return {"inline_keyboard": rows} if rows else {}


def _callback(label: str, data: str) -> dict:
    return {"text": label, "callback_data": data}


def _send_html(chat_id: int, text: str, buttons: list[list[dict]] | None = None, quiet: bool = False) -> int:
    """Send one message written in Telegram's HTML. If Telegram can't parse it, send it as plain text instead."""
    common = {"chat_id": chat_id, "link_preview_options": {"is_disabled": True}, **_reply_markup(buttons), **({"disable_notification": True} if quiet else {})}
    payload = {**common, "text": text, "parse_mode": "HTML"}
    try:
        return _api("sendMessage", payload)["message_id"]
    except NetworkError:
        raise
    except TelegramError as e:
        if e.status != 400:
            raise
    plain = html.unescape(re.sub(r"</?(?:b|code)>", "", text))
    return _api("sendMessage", {**common, "text": plain})["message_id"]


def _reply_markup(buttons: list[list[dict]] | None) -> dict:
    return {"reply_markup": _keyboard(buttons)} if buttons else {}


def _chunks(text: str, limit: int = MESSAGE_CHARS) -> list[str]:
    """Split long text between lines (a single very long line is cut)."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current.strip():
        parts.append(current)
    return parts or [""]


def rich(text: str) -> str:
    """Claude's light markdown as Telegram HTML. Everything is escaped first: no text from a posting or from Claude
    can add its own markup or links."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return re.sub(r"(?m)^(\s*)[-*]\s+", r"\1• ", out)


def send(chat_id: int, text: str, buttons: list[list[dict]] | None = None, quiet: bool = False) -> int:
    """Send text of any length; buttons go on the last part."""
    parts = _chunks(text)
    message_id = 0
    for i, part in enumerate(parts):
        message_id = _send_html(chat_id, rich(part), buttons if i == len(parts) - 1 else None, quiet)
    return message_id


def _set_buttons(chat_id: int, message_id: int, buttons: list[list[dict]] | None) -> None:
    """Change the buttons under a message; old messages may refuse, which doesn't matter."""
    try:
        _api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": _keyboard(buttons)})
    except TelegramError:
        pass


def _toast(callback_id: str, text: str = "") -> None:
    try:
        _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})
    except TelegramError:  # a press older than a few minutes can't be answered; the action itself still happened
        pass


# --- jobs ----------------------------------------------------------------------------------------------------------


def _job_ref(job_id: str) -> str:
    """Button data is limited to 64 bytes and job ids can be longer, so a button carries a short reference."""
    ref = hashlib.sha1(job_id.encode()).hexdigest()[:16]
    db.cache_put(f"telegram-job:{ref}", {"id": job_id})
    return ref


def _job_from_ref(ref: str) -> str:
    found = db.cache_get(f"telegram-job:{ref}", 24 * 365)
    return found["id"] if found and db.get_job(found["id"]) else ""


def _job_buttons(job, has_cv: bool) -> list[list[dict]]:
    ref = _job_ref(job.id)
    top = [_callback("Hide", f"h:{ref}")]
    if re.match(r"https?://[^\s/]+", job.url or ""):
        top.insert(0, {"text": "Open job", "url": job.url})
    return [top, [_callback("Get CV" if has_cv else "Tailor CV", f"t:{ref}"), _callback("Cover letter", f"l:{ref}")],
            [_callback("Suggest changes", f"s:{ref}")]]


def job_card(row: dict) -> str:
    """One job as Telegram HTML. Everything from the posting is escaped."""
    job, fit = row["job"], row["fit_data"] or {}
    esc = lambda text: html.escape(str(text), quote=False)  # noqa: E731
    where = " · ".join(x for x in (job.company, job.location, "remote" if job.remote else "") if x)
    lines = [f"<b>{row['fit']}/10 · {esc(job.title)}</b>", esc(where)]
    if job.salary:
        lines.append(esc(job.salary))
    if fit.get("verdict"):
        lines += ["", esc(fit["verdict"])]
    if fit.get("strong"):
        lines.append("Strong: " + esc(", ".join(fit["strong"][:4])))
    if fit.get("missing"):
        lines.append("Gaps: " + esc(", ".join(fit["missing"][:4])))
    return "\n".join(lines)


def new_jobs(config: Config, minimum: int | None = None) -> list[dict]:
    """Jobs found lately, rated at least this (default min_rating) and not sent yet, best first. "liked" counts too:
    application packs move the best jobs there on their own, before you have seen them."""
    minimum = config.min_rating if minimum is None else minimum
    since = time.time() - NEW_DAYS * 86400
    rows = [
        r for r in db.list_jobs(statuses=list(HIDEABLE))
        if (r["fit"] or 0) >= minimum and r["first_seen"] >= since and r["job"].id not in config.sent
    ]
    return sorted(rows, key=lambda r: (r["fit"], r["score"] or 0), reverse=True)


def _mark_sent(job_id: str) -> None:
    with _lock:
        config = load_config()
        keep_after = time.time() - 2 * NEW_DAYS * 86400
        sent = {k: v for k, v in config.sent.items() if v >= keep_after}
        sent[job_id] = time.time()
        update_config(sent=sent)


def _send_jobs(config: Config, rows: list[dict], heading: str = "", quiet: bool = False) -> None:
    """Send the first few of these jobs, one message each, each remembered as soon as it has gone out."""
    for row in rows[:JOBS_PER_MESSAGE]:
        _send_html(config.chat_id, (f"{heading}\n" if heading else "") + job_card(row), _job_buttons(row["job"], bool(db.cvs_for_job(row["job"].id))), quiet)
        _mark_sent(row["job"].id)
    if len(rows) > JOBS_PER_MESSAGE:
        left = len(rows) - JOBS_PER_MESSAGE
        send(config.chat_id, f"{left} more waiting. Send /jobs to see them.", quiet=quiet)


def _schedule_on() -> bool:
    from . import auto  # auto imports this module for the daily summary

    return any(s.auto for s in auto.load_searches())


def send_digest() -> int:
    """The daily summary: new jobs and follow-ups due. Returns how many new jobs there were."""
    config = load_config()
    rows = new_jobs(config)
    n = len(rows)
    lines = [f"**{n} new job{'s' if n != 1 else ''} rated {config.min_rating}+/10**" if n else f"No new jobs rated {config.min_rating}+/10 today."]
    due = db.due_follow_ups()
    if due:
        names = ", ".join(d["job"].company for d in due[:5]) + (" …" if len(due) > 5 else "")
        lines.append(f"Time to follow up: {names}")
    if not n and not _schedule_on():
        lines.append("No saved search runs on a schedule, so nothing new is being looked for. Turn one on under Search settings → Advanced.")
    send(config.chat_id, "\n".join(lines))
    _send_jobs(config, rows)
    return n


def daily_tick() -> None:
    """Called by the scheduler each minute for each person. Runs once a day at or after the chosen time, so a summary
    missed while the computer was off goes out when the app starts again that day."""
    if not settings.DESKTOP:
        return
    try:
        config = load_config()
        today = time.strftime("%Y-%m-%d")
        if not config.chat_id or not config.daily or config.last_day == today or time.strftime("%H:%M") < config.daily_time or not _token():
            return
        try:
            send_digest()
        except NetworkError:  # no internet right now: the next tick tries again
            return
        except TelegramError as e:  # e.g. you blocked the bot: shown in Settings, tried again tomorrow
            update_config(last_day=today, last_error=str(e))
            return
        update_config(last_day=today, last_error="")
    except Exception as e:  # keep the scheduler alive
        log.error("telegram: %s", _clean(e))


_alert_lock = threading.Lock()


def _quiet_now() -> bool:
    hour = time.localtime().tm_hour
    return hour >= QUIET_FROM or hour < QUIET_UNTIL


def send_alerts() -> None:
    """Right after new jobs are rated: send the top ones now. Anything else waits for the summary. Rating must not
    fail because of this, so problems are only logged, and an unsent job still goes out with the summary."""
    if not settings.DESKTOP:
        return
    try:
        with _alert_lock:  # several rating batches can finish together; a job goes out once
            config = load_config()
            if config.chat_id and config.alerts and _token():
                _send_jobs(config, new_jobs(config, config.alert_rating), "New top match", _quiet_now())
    except Exception as e:
        log.error("telegram: %s", _clean(e))


# --- the conversation ----------------------------------------------------------------------------------------------


def _ask(config: Config, text: str, uploads: list[tuple[str, bytes]] | None = None) -> None:
    """Add the message to the current conversation (a new one if needed); Claude answers in the background."""
    thread = db.chat_thread_get(config.thread_id) if config.thread_id else None
    thread_id = thread["id"] if thread else db.chat_thread_add(chat.title_from(text), "")
    if not thread:
        update_config(thread_id=thread_id)
    if db.task_active("chat", f"chat:{thread_id}"):
        send(config.chat_id, "I'm still answering your last message. Send this again in a moment.")
        return
    message_id = db.chat_add(thread_id, "user", text)
    if uploads:
        chat.save_files(thread_id, message_id, uploads)
    tasks.submit("chat", "Telegram · " + chat.title_from(text), "telegram-chat", subject=f"chat:{thread_id}",
                 url=f"/chat/{thread_id}", args={"thread_id": thread_id})
    try:
        _api("sendChatAction", {"chat_id": config.chat_id, "action": "typing"})
    except TelegramError:
        pass


def _answer_text(message: dict) -> str:
    """Claude's answer, plus what a proposed profile change would change."""
    text = message["text"]
    if not message["proposal"]:
        return text
    lines = [text, "", "**Proposed changes**", *[f"- {c}" for c in message["changes"]]]
    details = [f"{'➕' if kind == '+' else '➖'} {section['section']}: {line}" for section in chat.diff(message) for kind, line in section["lines"]]
    lines += ["", *details[:MAX_DIFF_LINES]] if details else []
    if len(details) > MAX_DIFF_LINES:
        lines.append(f"…and {len(details) - MAX_DIFF_LINES} more lines (see Chat in the app).")
    return "\n".join(lines)


@tasks.runner("telegram-chat")
def _chat_task(progress, thread_id: int) -> str:
    config = load_config()
    try:
        chat.answer(thread_id)
    except Exception as e:
        if config.chat_id:
            send(config.chat_id, f"Sorry, I couldn't answer that: {str(e)[:300]}")
        raise
    message = db.chat_messages(thread_id)[-1]
    # Read again: you may have disconnected the bot while Claude was thinking.
    chat_id = load_config().chat_id
    if chat_id:
        buttons = [[_callback("Apply", f"a:{message['id']}"), _callback("Dismiss", f"x:{message['id']}")]] if message["proposal"] else None
        send(chat_id, _answer_text(message), buttons)
    return f"/chat/{thread_id}"


def _apply_buttons(message: dict) -> list[list[dict]]:
    """After applying: undo, and rebuild the CV when the conversation is about a job."""
    rows = [[_callback("Undo", f"z:{message['id']}")]]
    thread = db.chat_thread_get(message["thread_id"])
    if thread and thread["job_id"] and db.get_job(thread["job_id"]):
        rows.append([_callback("Tailor CV again", f"n:{_job_ref(thread['job_id'])}")])
    return rows


# --- files you send ------------------------------------------------------------------------------------------------


def _attachment(message: dict) -> dict | None:
    """The file in a message: name, Telegram's id and size. Of a photo, Telegram lists sizes smallest first."""
    if message.get("document"):
        doc = message["document"]
        return {"name": doc.get("file_name") or "file", "id": doc["file_id"], "size": doc.get("file_size", 0)}
    if message.get("photo"):
        photo = message["photo"][-1]
        return {"name": f"photo-{photo.get('file_unique_id', 'telegram')}.jpg", "id": photo["file_id"], "size": photo.get("file_size", 0)}
    return None


def _download(file_id: str) -> bytes:
    """A file you sent. As with _api, errors never carry the URL, because it contains the token."""
    path = _api("getFile", {"file_id": file_id})["file_path"]
    try:
        with httpx.Client(timeout=60) as client:
            response = client.get(f"{API_URL}/file/bot{_token()}/{path}")
    except httpx.HTTPError as e:
        raise NetworkError(f"Couldn't download the file from Telegram ({type(e).__name__})") from None
    if response.status_code != 200:
        raise TelegramError(f"Telegram answered {response.status_code} for the file", response.status_code)
    return response.content


def _receive_files(person: str, messages: list[dict]) -> None:
    """Fetch the files of one message or album and pass them to the chat, with the same checks as the chat page. Runs
    on its own thread, so a large file doesn't hold up the bot."""
    try:
        with people.in_account(people.LOCAL, person):
            config = load_config()
            if not config.chat_id:
                return
            try:
                files = [f for f in map(_attachment, messages) if f]
                too_big = [f["name"] for f in files if f["size"] > chat.MAX_FILE_MB * 1024 * 1024]
                if too_big:
                    raise chat.AttachmentError(f"{too_big[0]} is larger than {chat.MAX_FILE_MB} MB.")
                # Check the types first, so nothing is downloaded for a file that would be refused anyway.
                chat.check_files([(f["name"], b"-") for f in files])
                uploads = [(f["name"], _download(f["id"])) for f in files]
                chat.check_files(uploads)
            except chat.AttachmentError as e:
                send(config.chat_id, str(e))
                return
            captions = [m["caption"].strip() for m in messages if (m.get("caption") or "").strip()]
            text = "\n".join(captions) or ("Have a look at the attached file." if len(uploads) == 1 else "Have a look at the attached files.")
            _ask(config, text, uploads)
    except TelegramError as e:
        log.error("telegram: %s", _clean(e))
        try:
            with people.in_account(people.LOCAL, person):
                send(load_config().chat_id, "Sorry, I couldn't get that file. Please send it again.")
        except TelegramError:
            pass
    except Exception as e:
        log.error("telegram: %s", _clean(e))


_albums: dict[str, list[dict]] = {}


def _flush_album(person: str, group: str) -> None:
    with _lock:
        messages = _albums.pop(group, [])
    _receive_files(person, messages)


def _queue_files(person: str, message: dict) -> None:
    group = message.get("media_group_id")
    if not group:
        threading.Thread(target=_receive_files, args=(person, [message]), daemon=True).start()
        return
    with _lock:
        first = group not in _albums
        _albums.setdefault(group, []).append(message)
    if first:
        timer = threading.Timer(ALBUM_WAIT_SECONDS, _flush_album, args=(person, group))
        timer.daemon = True
        timer.start()


# --- CVs and letters -----------------------------------------------------------------------------------------------


def _cv_summary(job, tailored) -> str:
    """What to know before sending this CV: its match, what was changed, and anything the checks couldn't back up."""
    lines = [f"**CV for {job.title} at {job.company}** · match {tailored.match_score}/100"]
    if tailored.changes_made:
        lines += ["", "**What changed**", *[f"- {c}" for c in tailored.changes_made[:6]]]
    if tailored.missing_requirements:
        lines += ["", "**Not claimed (your profile doesn't show it)**", *[f"- {m}" for m in tailored.missing_requirements[:6]]]
    profile = assistant.load_profile()
    if profile:
        check = cvdiff.diff(profile, tailored.cv, job)
        doubts = [*check["unsupported_skills"], *[n for n, _ in check["unsupported_numbers"]],
                  *[f["term"] for f in claims.problems(check["unsupported_tech"])]]
        if doubts:
            lines += ["", "**Check before sending, not found in your profile:** " + ", ".join(dict.fromkeys(doubts))]
    return "\n".join(lines)


@tasks.runner("telegram-tailor")
def _tailor_task(progress, job_id: str, make_new: bool = False) -> str:
    from .cv_routes import refresh_cv_pdf  # cv_routes.py imports this module

    config = load_config()
    found = db.get_job(job_id)
    profile = assistant.load_profile()
    if not found or not profile:
        if config.chat_id:
            send(config.chat_id, "That job is gone." if not found else "There's no profile yet. Import your CV under My profile first.")
        return f"/job/{job_id}"
    existing = db.cvs_for_job(job_id)
    try:
        if existing and not make_new:
            cv_id = existing[0]["id"]
            refresh_cv_pdf(cv_id)  # it may have been edited since
        else:
            layout = {"font_pt": None, "one_page": True, "instructions": ""}
            cv_id = pack.tailored_cv(profile, ensure_description(found[0]), None, layout, progress)
    except Exception as e:
        if config.chat_id:
            send(config.chat_id, f"Sorry, I couldn't make that CV: {str(e)[:300]}")
        raise
    chat_id = load_config().chat_id
    if chat_id:
        _, tailored, pdf_path = db.get_cv(cv_id)
        name = re.sub(r"[^\w.-]+", "_", f"{profile.name}_CV_{found[0].company}") + ".pdf"
        _api("sendDocument", {"chat_id": str(chat_id)}, document=(name, Path(pdf_path).read_bytes()), timeout=60)
        ref = _job_ref(job_id)
        buttons = [[_callback("Make a new one", f"n:{ref}"), _callback("Cover letter", f"l:{ref}")], [_callback("Suggest changes", f"s:{ref}")]]
        send(chat_id, _cv_summary(found[0], tailored), buttons)
    return f"/cv/{cv_id}"


def _request_cv(config: Config, job_id: str, make_new: bool) -> str:
    """Queue the CV; returns the short answer for the button press."""
    if db.task_active("tailor", f"tailor:{job_id}") or db.task_active("pack", f"pack:{job_id}"):
        return "That CV is already being made. Tap again in a few minutes."
    job, _ = db.get_job(job_id)
    has_cv = bool(db.cvs_for_job(job_id))
    tasks.submit("tailor", f"Telegram CV · {job.title} at {job.company}", "telegram-tailor", subject=f"tailor:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#docs", args={"job_id": job_id, "make_new": make_new})
    return "Sending your CV…" if has_cv and not make_new else "Tailoring your CV. This takes a few minutes."


@tasks.runner("telegram-letter")
def _letter_task(progress, job_id: str, make_new: bool = False) -> str:
    config = load_config()
    found = db.get_job(job_id)
    profile = assistant.load_profile()
    if not found or not profile:
        if config.chat_id:
            send(config.chat_id, "That job is gone." if not found else "There's no profile yet. Import your CV under My profile first.")
        return f"/job/{job_id}"
    existing = db.letters_for_job(job_id)
    try:
        if existing and not make_new:
            letter_id = existing[0]["id"]
        else:
            # Written to match the latest CV for this job, when there is one.
            cvs = db.cvs_for_job(job_id)
            cv = db.get_cv(cvs[0]["id"])[1].cv if cvs else None
            letter_id = pack.letter(profile, ensure_description(found[0]), cv, progress=progress)
    except Exception as e:
        if config.chat_id:
            send(config.chat_id, f"Sorry, I couldn't write that letter: {str(e)[:300]}")
        raise
    chat_id = load_config().chat_id
    if chat_id:
        letter = db.get_letter(letter_id)
        name = re.sub(r"[^\w.-]+", "_", f"{profile.name}_CoverLetter_{found[0].company}") + ".pdf"
        _api("sendDocument", {"chat_id": str(chat_id)}, document=(name, Path(letter["pdf_path"]).read_bytes()), timeout=60)
        lines = [f"**Cover letter for {found[0].title} at {found[0].company}**"]
        doubts = [f["term"] for f in claims.problems(claims.unsupported(letter["body"], profile, job=found[0]))]
        if doubts:
            lines.append("**Check before sending, not found in your profile:** " + ", ".join(dict.fromkeys(doubts)))
        send(chat_id, "\n".join(lines), [[_callback("Write a new one", f"m:{_job_ref(job_id)}")]])
    return f"/letter/{letter_id}"


def _request_letter(job_id: str, make_new: bool) -> str:
    """Queue the letter; returns the short answer for the button press."""
    if db.task_active("letter", f"letter:{job_id}") or db.task_active("pack", f"pack:{job_id}"):
        return "That letter is already being written. Tap again in a few minutes."
    job, _ = db.get_job(job_id)
    has_letter = bool(db.letters_for_job(job_id))
    tasks.submit("letter", f"Telegram letter · {job.title} at {job.company}", "telegram-letter", subject=f"letter:{job_id}", job_id=job_id,
                 url=f"/job/{job_id}#letter", args={"job_id": job_id, "make_new": make_new})
    return "Sending your letter…" if has_letter and not make_new else "Writing your letter. This takes a minute or two."


# --- messages and button presses -----------------------------------------------------------------------------------


def _person_for(chat_id: int) -> str:
    """The person this Telegram account is linked to ('' for anyone else)."""
    for person in people.load():
        with people.acting_as(person.id):
            if load_config().chat_id == chat_id:
                return person.id
    return ""


def _link(code: str, chat_id: int, username: str) -> None:
    """Link this Telegram account to the person who created the code. The code works once."""
    now = time.time()
    for person in people.load():
        with people.acting_as(person.id):
            config = load_config()
            if config.link_code and config.link_expires > now and secrets.compare_digest(config.link_code, code):
                _unlink_chat(chat_id)  # one Telegram account belongs to one person
                update_config(chat_id=chat_id, username=username, link_code="", link_expires=0, thread_id=0, last_error="")
                send(chat_id, "Connected to CVMaker. Your first summary comes at the time set in Settings.\n\n" + HELP)
                return
    send(chat_id, "That link has expired. Make a new one in CVMaker under Settings → Telegram.")


def _unlink_chat(chat_id: int) -> None:
    for person in people.load():
        with people.acting_as(person.id):
            if load_config().chat_id == chat_id:
                update_config(chat_id=0, username="", thread_id=0)


def _on_message(message: dict) -> None:
    chat_info, sender = message.get("chat") or {}, message.get("from") or {}
    chat_id = chat_info.get("id")
    # Private chats with a person only: the bot takes no instructions from groups or other bots.
    if chat_info.get("type") != "private" or sender.get("id") != chat_id or sender.get("is_bot"):
        return
    text = (message.get("text") or "").strip()
    start = re.fullmatch(r"/start(?:@\w+)?\s+([\w-]{1,64})", text)
    if start:
        _link(start.group(1), chat_id, sender.get("username") or sender.get("first_name") or "")
        return
    person = _person_for(chat_id)
    if not person:
        return
    with people.acting_as(person):
        config = load_config()
        if _attachment(message):
            _queue_files(person, message)
        elif not text:
            send(chat_id, "I can read text, PDF, Word files, photos and text files, but not this kind of message.")
        elif re.match(r"/(start|help)\b", text):
            send(chat_id, HELP)
        elif re.match(r"/jobs\b", text):
            rows = new_jobs(config)
            if rows:
                _send_jobs(config, rows)
            else:
                send(chat_id, f"No more new jobs rated {config.min_rating}+/10.")
        elif re.match(r"/new\b", text):
            update_config(thread_id=0)
            send(chat_id, "New conversation started. What would you like to ask?")
        elif re.match(r"/suggest\b", text):
            _ask(config, "What are the weakest parts of my CV? Suggest specific changes and say why.")
        elif re.match(r"/unlink\b", text):
            update_config(chat_id=0, username="", thread_id=0)
            send(chat_id, "Disconnected. To connect again, use Settings → Telegram in CVMaker.")
        elif text.startswith("/"):
            send(chat_id, HELP)
        else:
            _ask(config, text)


def _on_job_button(config: Config, action: str, ref: str, extra: str, message_id: int) -> str:
    """Hide, undo, CV and suggestions on a job. Returns the short answer for the button press."""
    job_id = _job_from_ref(ref)
    if not job_id:
        return "That job is no longer here."
    job = db.get_job(job_id)[0]
    status = db.get_application(job_id).get("status", "new")
    if action == "h":
        if status not in HIDEABLE:
            return "Already hidden." if status == "hidden" else "You've already moved this one along, so I left it."
        db.set_feedback(job_id, "")
        db.set_status(job_id, "hidden", note="Hidden from Telegram")
        taste.recompute()
        _set_buttons(config.chat_id, message_id, [[_callback("Undo hide", f"u:{ref}:{status}")]])
        return "Hidden."
    if action == "u":
        if status == "hidden" and extra in HIDEABLE:
            db.set_status(job_id, extra, note="Hide undone from Telegram")
            taste.recompute()
            _set_buttons(config.chat_id, message_id, _job_buttons(job, bool(db.cvs_for_job(job_id))))
        return "Back in your list."
    if action in ("t", "n"):
        return _request_cv(config, job_id, make_new=action == "n")
    if action in ("l", "m"):
        return _request_letter(job_id, make_new=action == "m")
    # "s": a conversation about this job; what you write next stays about it, until /new.
    thread_id = db.chat_thread_add(chat.title_from(f"{job.title} at {job.company}"), job_id)
    config = update_config(thread_id=thread_id)
    _ask(config, "Suggest specific changes to my CV for this job and say why. Write what you'd change, and ask me before adding anything my profile doesn't show.")
    return "Asking Claude…"


def _on_change_button(config: Config, action: str, message_id: int, telegram_message_id: int) -> str:
    """Apply, dismiss or undo a proposed profile change, under the same rules as the chat page."""
    message = db.chat_message(message_id)
    if not message:
        return "That change is gone."
    if action == "a":
        problem = chat.apply(message)
        if problem:
            _set_buttons(config.chat_id, telegram_message_id, None)
            return problem
        _set_buttons(config.chat_id, telegram_message_id, _apply_buttons(message))
        return "Applied to your profile."
    if action == "z":
        problem = chat.undo(message)
        _set_buttons(config.chat_id, telegram_message_id, None)
        return problem or "Undone."
    if message["state"] == "proposed":
        db.chat_set_state(message["id"], "dismissed")
    _set_buttons(config.chat_id, telegram_message_id, None)
    return "Dismissed."


def _on_button(callback: dict) -> None:
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    sender = (callback.get("from") or {}).get("id")
    # The press must come from the linked account, in its own chat with the bot.
    if not chat_id or sender != chat_id:
        return
    person = _person_for(chat_id)
    if not person:
        return
    with people.acting_as(person):
        config = load_config()
        action, _, rest = (callback.get("data") or "").partition(":")
        answer = ""
        try:
            if action in ("h", "u", "t", "n", "l", "m", "s"):
                ref, _, extra = rest.partition(":")
                answer = _on_job_button(config, action, ref, extra, message.get("message_id"))
            elif action in ("a", "x", "z") and rest.isdigit():
                answer = _on_change_button(config, action, int(rest), message.get("message_id"))
        except NetworkError:
            answer = "Couldn't finish that. Please try again."
        _toast(callback.get("id", ""), answer)


def handle_update(update: dict) -> None:
    with people.in_account(people.LOCAL):
        if update.get("callback_query"):
            _on_button(update["callback_query"])
        elif update.get("message"):
            _on_message(update["message"])


# --- the polling thread --------------------------------------------------------------------------------------------


def _clean(error: Exception) -> str:
    """An error's text for the log, in case anything put the token in it."""
    text, token = str(error), ""
    try:
        with people.in_account(people.LOCAL):
            token = _token()
    except Exception:
        pass
    return text.replace(token, "***") if token else text


def _poll_once() -> None:
    with people.in_account(people.LOCAL):
        offset = _bot_state().get("offset", 0)
    updates = _api("getUpdates", {"offset": offset, "timeout": POLL_SECONDS, "allowed_updates": ["message", "callback_query"]},
                   timeout=POLL_SECONDS + 15)
    for update in updates:
        # Remembered first: a message that crashes the handler must not come back on every restart.
        with people.in_account(people.LOCAL):
            _save_bot_state(offset=update["update_id"] + 1)
        try:
            handle_update(update)
        except Exception as e:
            log.error("telegram: %s", _clean(e))


def _loop() -> None:
    commands_for = ""
    while True:
        with people.in_account(people.LOCAL):
            token = _token()
        if not token:
            time.sleep(5)
            continue
        try:
            with people.in_account(people.LOCAL):
                if token != commands_for:
                    _api("setMyCommands", {"commands": [
                        {"command": "jobs", "description": "The next new jobs"},
                        {"command": "suggest", "description": "Suggestions for improving your CV"},
                        {"command": "new", "description": "Start a new conversation"},
                        {"command": "help", "description": "What I can do"},
                    ]})
                    commands_for = token
                _poll_once()
        except NetworkError:
            time.sleep(15)
        except TelegramError as e:
            # 401: the token was revoked. 409: another copy of the app is polling with this bot, or one just restarted.
            log.error("telegram: %s", _clean(e))
            time.sleep(60 if e.status == 401 else 10)
        except Exception as e:
            log.error("telegram: %s", _clean(e))
            time.sleep(15)


_thread: threading.Thread | None = None


def start() -> None:
    """Start the bot (once per process, desktop mode). It waits quietly until a token is saved."""
    global _thread
    if settings.DESKTOP and _thread is None:
        _thread = threading.Thread(target=_loop, name="cvmaker-telegram", daemon=True)
        _thread.start()


# --- Settings ------------------------------------------------------------------------------------------------------


def save_token(token: str) -> str:
    """Check the token with Telegram and keep it. A different bot needs linking again. Returns the bot's username."""
    token = token.strip()
    if not TOKEN_FORMAT.match(token):
        raise TelegramError("That doesn't look like a bot token. @BotFather's looks like 123456789:AAH…")
    me = _api("getMe", token=token)
    if token != keys.own("telegram_token"):
        _forget_links()
    keys.set_many({"telegram_token": token})
    _save_bot_state(username=me.get("username", ""), offset=0)
    return me.get("username", "")


def remove_token() -> None:
    keys.set_many({"telegram_token": ""})
    _forget_links()
    _save_bot_state(username="", offset=0)


def _forget_links() -> None:
    for person in people.load():
        with people.acting_as(person.id):
            update_config(chat_id=0, username="", link_code="", link_expires=0, thread_id=0, last_error="")


def new_link_code() -> None:
    update_config(link_code=secrets.token_urlsafe(24), link_expires=time.time() + LINK_MINUTES * 60)


def status() -> dict:
    """What the Settings card shows for the current person."""
    config = load_config()
    bot = _bot_state().get("username", "")
    link = f"https://t.me/{bot}?start={config.link_code}" if bot and config.link_code and config.link_expires > time.time() else ""
    return {
        "has_token": bool(_token()), "bot": bot, "linked": bool(config.chat_id), "username": config.username, "link": link,
        "daily": config.daily, "daily_time": config.daily_time, "min_rating": config.min_rating, "last_error": config.last_error,
        "alerts": config.alerts, "alert_rating": config.alert_rating,
        "schedule_on": _schedule_on(),
    }
