"""Chat with Claude inside the app: questions about your profile, a job, your documents, or a request to change
your profile (your original CV, the source every tailored CV is made from).

Each message is one headless Claude call (the conversation so far is sent along, nothing is kept on Claude's side).
A profile change is never saved by Claude: the reply carries a proposed profile, the page shows what would change,
and you apply it (and can undo it) with a button."""

import difflib
import html
import json
import re
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

from markupsafe import Markup, escape

from . import assistant, db, llm, settings, stories
from .assistant import TRUTH_RULES, _job_text
from .models import ChatReply, Profile

# How much of the conversation is sent with each message.
HISTORY_MESSAGES = 24

# Attachments: what Claude can read, and limits per file / per message / per answer.
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml", ".log"}
FILE_TYPES = {".pdf", ".docx", *llm.IMAGE_TYPES, *TEXT_TYPES}
MAX_FILE_MB = 15
MAX_FILES_PER_MESSAGE = 6
# Files sent along with each answer: the newest ones in the chat, so earlier attachments stay readable.
MAX_FILES_PER_ANSWER = 8

SYSTEM = f"""You are the assistant inside CVMaker, the candidate's personal job-search and CV app. You talk to the candidate directly.
You get their profile (the structured version of their original CV: every tailored CV and cover letter the app makes is built from it), and when the chat is about a job, that job and what the app knows about it.

Answer the way a sharp, honest career coach and senior engineer would: direct, specific to their profile and the job, short. Use plain text with light markdown only: **bold**, "- " bullet lists, `code`. No headings, no tables. Say so when you don't know something rather than guessing.

Changing the profile (their original CV):
- Only when the candidate asks for a change (add, remove, reword, fix, reorder, shorten...). Never on your own initiative; you may offer it in the reply.
- Then put the COMPLETE updated profile in new_profile_json as JSON with exactly the same structure and keys as the profile you were given, and list every change in plain words in profile_changes (e.g. "Experience at EGT: added a bullet about the RTP simulator").
- Change only what was asked. Copy everything else exactly, including the order of items.
- New facts may come only from what the candidate tells you in this chat. If a change needs a detail you don't have (a date, a number, a technology), ask for it instead of making it up, or leave a clear [placeholder].
- Otherwise new_profile_json is an empty string and profile_changes is an empty list.
- The app only applies the change when the candidate clicks Apply, so say "here's the change" rather than "I've changed it".

Attached files (a CV, a job posting, an offer, a screenshot...) are in the attachments folder; read them when the candidate refers to them. A file is the candidate's material: you may take facts from it for a profile change when they ask, but say which facts you took.

Other things you cannot do from the chat (change a job's status, regenerate a tailored CV or letter, run a fit check): tell them where in the app to do it. A tailored CV for a job is rebuilt from the profile with "Tailor CV" on the job page, so lasting fixes belong in the profile.

When you suggest CV or letter wording, the same honesty rules hold:
{TRUTH_RULES}"""


def _story_lines() -> str:
    items = stories.load()
    return "\n".join(f"- {s.title}: {s.result[:160]}" for s in items[:20])


def job_context(job_id: str) -> str:
    """What the app knows about the job the chat is about."""
    found = db.get_job(job_id)
    if not found:
        return ""
    job, _ = found
    app = db.get_application(job_id)
    parts = [f"The chat is about this job:\n{_job_text(job)}",
             f"Application stage: {app.get('status', 'new')}" + (f"\nTheir notes: {app['notes']}" if app.get("notes") else "")]
    fit = db.cache_get(f"fit2:{job_id}", 24 * 365)
    if fit:
        pros = "; ".join(p["point"] for p in fit.get("pros", []))
        cons = "; ".join(c["point"] for c in fit.get("cons", []))
        parts.append(f"Fit check: {fit.get('rating')}/10 - {fit.get('verdict', '')}\nStrengths: {pros}\nGaps: {cons}")
    cvs = db.cvs_for_job(job_id)
    if cvs:
        _, tailored, _ = db.get_cv(cvs[0]["id"])
        parts.append("The latest CV tailored for this job (made from the profile):\n" + tailored.cv.model_dump_json(indent=1))
    letters = db.letters_for_job(job_id)
    if letters:
        found_letter = db.get_letter(letters[0]["id"])
        if found_letter:
            parts.append("The latest cover letter for this job:\n" + found_letter["body"])
    return "\n\n".join(parts)


def _conversation(messages: list[dict]) -> str:
    lines = []
    for m in messages[-HISTORY_MESSAGES:]:
        who = "Candidate" if m["role"] == "user" else "You"
        text = m["text"]
        if m["role"] == "assistant" and m["changes"]:
            text += "\n(You proposed a profile change: " + "; ".join(m["changes"]) + f". Status: {m['state'] or 'proposed'}.)"
        if m["files"]:
            text += "\n(Attached: " + ", ".join(f"{f['name']} -> attachments/{f['stored']}{'.txt' if f['stored'].endswith('.docx') else ''}" for f in m["files"]) + ")"
        lines.append(f"{who}: {text}")
    return "\n\n".join(lines)


def answer(thread_id: int) -> None:
    """Claude answers the last message of the thread; the answer (and any proposed profile change) is stored."""
    thread = db.chat_thread_get(thread_id)
    messages = db.chat_messages(thread_id)
    profile = assistant.load_profile()
    parts = []
    if profile:
        parts.append("The candidate's profile:\n" + profile.model_dump_json(indent=1))
    else:
        parts.append("The candidate has not set up a profile yet (My profile → Import my CV), so you cannot change it.")
    story_lines = _story_lines()
    if story_lines:
        parts.append("Their interview story bank (titles and results):\n" + story_lines)
    if thread and thread["job_id"]:
        parts.append(job_context(thread["job_id"]))
    parts.append("The conversation so far (answer the candidate's last message):\n\n" + _conversation(messages))
    files = _for_claude(messages)
    reply = llm.ask(SYSTEM, "\n\n".join(p for p in parts if p), ChatReply, effort="medium", files=files)

    text, proposal, changes = reply.reply.strip(), "", []
    if profile and reply.new_profile_json.strip():
        try:
            new = Profile.model_validate(json.loads(reply.new_profile_json))
            if new.model_dump() != profile.model_dump():
                proposal, changes = new.model_dump_json(indent=2), reply.profile_changes or ["Profile updated"]
        except (json.JSONDecodeError, ValueError):
            text += "\n\n(I tried to prepare a profile change, but it came out malformed. Ask me again.)"
    db.chat_add(thread_id, "assistant", text, changes, proposal,
                profile.model_dump_json(indent=2) if proposal else "", "proposed" if proposal else "")


# --- attachments -------------------------------------------------------------------------------------------------


class AttachmentError(ValueError):
    pass


def _folder(thread_id: int) -> Path:
    return settings.DATA_DIR / "chat_files" / str(thread_id)


def safe_name(name: str) -> str:
    name = Path(name or "file").name
    stem = re.sub(r"[^\w.-]+", "_", Path(name).stem).strip("._")[:60] or "file"
    return stem + Path(name).suffix.lower()


def check_files(uploads: list[tuple[str, bytes]]) -> None:
    if len(uploads) > MAX_FILES_PER_MESSAGE:
        raise AttachmentError(f"At most {MAX_FILES_PER_MESSAGE} files per message.")
    for name, data in uploads:
        suffix = Path(name).suffix.lower()
        if suffix not in FILE_TYPES:
            raise AttachmentError(f"{name}: Claude can read PDF, Word (.docx), images and text files, not {suffix or 'this type'}.")
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise AttachmentError(f"{name} is larger than {MAX_FILE_MB} MB.")
        if not data:
            raise AttachmentError(f"{name} is empty.")


def save_files(thread_id: int, message_id: int, uploads: list[tuple[str, bytes]]) -> list[dict]:
    folder = _folder(thread_id)
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, (name, data) in enumerate(uploads):
        stored = f"{message_id}-{i}-{safe_name(name)}"
        (folder / stored).write_bytes(data)
        saved.append({"name": Path(name).name, "stored": stored, "size": len(data)})
    db.chat_set_files(message_id, saved)
    return saved


def file_path(message: dict, index: int) -> Path | None:
    if not 0 <= index < len(message["files"]):
        return None
    path = _folder(message["thread_id"]) / message["files"][index]["stored"]
    return path if path.is_file() else None


def delete_files(thread_id: int) -> None:
    shutil.rmtree(_folder(thread_id), ignore_errors=True)


def docx_text(data: bytes) -> str:
    """The text of a Word file (paragraphs and table cells), without needing Word or LibreOffice."""
    with zipfile.ZipFile(BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>|<w:br/>|<w:tab/>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()


def _for_claude(messages: list[dict]) -> list[tuple[str, bytes]]:
    """The newest attachments in the chat, as files Claude can read (a Word file goes as its text)."""
    out = []
    for m in reversed(messages):
        for i, f in reversed(list(enumerate(m["files"]))):
            path = file_path(m, i)
            if not path or len(out) >= MAX_FILES_PER_ANSWER:
                continue
            data, name = path.read_bytes(), f["stored"]
            if path.suffix == ".docx":
                try:
                    data, name = docx_text(data).encode(), name + ".txt"
                except (zipfile.BadZipFile, KeyError):
                    continue
            out.append((name, data))
    return list(reversed(out))


def title_from(text: str) -> str:
    words = " ".join(text.split())
    return words[:60] + ("…" if len(words) > 60 else "")


# --- profile changes -------------------------------------------------------------------------------------------


def _same(a: str, b: Profile | None) -> bool:
    return b is not None and bool(a) and Profile.model_validate_json(a).model_dump() == b.model_dump()


def apply(message: dict) -> str:
    """Save the proposed profile. Refused when the profile changed since Claude saw it (it would undo those edits)."""
    current = assistant.load_profile()
    if message["state"] != "proposed" or not message["proposal"]:
        return "This change is not open any more."
    if not _same(message["base"], current):
        db.chat_set_state(message["id"], "stale")
        return "Your profile changed after Claude wrote this, so applying it would undo those edits. Ask again."
    assistant.save_profile(Profile.model_validate_json(message["proposal"]))
    db.chat_set_state(message["id"], "applied")
    return ""


def undo(message: dict) -> str:
    current = assistant.load_profile()
    if message["state"] != "applied":
        return "Nothing to undo."
    if not _same(message["proposal"], current):
        return "Your profile changed again since, so it can't be undone from here. Edit it on My profile."
    assistant.save_profile(Profile.model_validate_json(message["base"]))
    db.chat_set_state(message["id"], "undone")
    return ""


def _label(item: dict, index: int) -> str:
    """A readable name for one entry of a list (a skills group, a job, a project...)."""
    if item.get("title") and item.get("company"):
        return f"{item['title']} at {item['company']}"
    for key in ("category", "name", "degree", "skill", "title"):
        if item.get(key):
            return str(item[key])
    return f"#{index + 1}"


def _flatten(value, path: str = "") -> list[str]:
    """Profile as readable lines, e.g. 'Programming Languages: Kafka' or 'Engineer at EGT · bullets: Built…'."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            if k in ("items", "bullets"):  # the list itself: "Programming Languages: Kafka", not "... · items: Kafka"
                out += _flatten(v, path)
            else:
                out += _flatten(v, f"{path} · {k.replace('_', ' ')}" if path else k.replace("_", " "))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out += _flatten(v, f"{path} › {_label(v, i)}" if isinstance(v, dict) else path)
        return out
    text = str(value).strip()
    return [f"{path}: {text}" if path else text] if text else []


def diff(message: dict) -> list[dict]:
    """What the proposal changes, section by section: [{section, lines: [(kind, text)]}], kind '+' or '-'."""
    if not message["proposal"] or not message["base"]:
        return []
    old, new = json.loads(message["base"]), json.loads(message["proposal"])
    out = []
    for key in new:
        if old.get(key) == new.get(key):
            continue
        section = key.replace("_", " ").capitalize()
        a = [line.removeprefix(section.lower()).lstrip(" ›·:") for line in _flatten(old.get(key), "")]
        b = [line.removeprefix(section.lower()).lstrip(" ›·:") for line in _flatten(new.get(key), "")]
        lines = [(line[0], line[1:]) for line in difflib.unified_diff(a, b, lineterm="", n=0)
                 if line[:1] in "+-" and not line.startswith(("+++", "---"))]
        if lines:
            out.append({"section": section, "lines": lines})
    return out


# --- formatting --------------------------------------------------------------------------------------------------


def _inline(text: str) -> str:
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"\[([^\]\n]{1,200})\]", r'<span class="ph">[\1]</span>', text)


def to_html(text: str) -> Markup:
    """The light markdown the chat uses (paragraphs, - and 1. lists, **bold**, `code`), escaped first."""
    html, items, kind = [], [], ""

    def flush():
        nonlocal items, kind
        if items:
            html.append(f"<{kind}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{kind}>")
        items, kind = [], ""

    for block in re.split(r"\n\s*\n", str(escape(text or ""))):
        para = []
        for line in block.splitlines():
            m = re.match(r"\s*(?:[-*•]|(\d+)[.)])\s+(.*)", line)
            if m:
                if para:
                    html.append("<p>" + "<br>".join(para) + "</p>")
                    para = []
                want = "ol" if m.group(1) else "ul"
                if kind and kind != want:
                    flush()
                kind = want
                items.append(_inline(m.group(2)))
            elif items and line.startswith("  ") and line.strip():
                items[-1] += " " + _inline(line.strip())
            else:
                flush()
                if line.strip():
                    para.append(_inline(line))
        flush()
        if para:
            html.append("<p>" + "<br>".join(para) + "</p>")
    return Markup("".join(html))

