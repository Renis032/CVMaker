"""The Telegram bot, with Telegram itself replaced: who it answers, what a summary holds, and that buttons obey the
same rules as the pages. The rest of the suite runs in server mode, where the bot is off; these call its functions
directly for the local account."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import httpx
import pytest

from cvmaker import autostart, chat, db, people, settings, tasks, telegram
from cvmaker.models import Contact, CVContent, Job, Profile, TailoredCV

ME, STRANGER = 111, 999
_CV = dict(name="Test User", headline="", contact=Contact(email="", phone="", linkedin="", github="", location=""), summary="Old summary",
           skills=[], experience=[], projects=[], education=[], soft_skills=[], languages=[], interests=[])


def _profile(**fields) -> Profile:
    return Profile(**{**_CV, **fields}, skill_details=[], extra_facts=[], preferences=[])


@pytest.fixture
def bot(monkeypatch):
    """A clean person for the local account, with every Bot API call recorded."""
    calls: list[tuple[str, dict]] = []

    def fake_api(method, data=None, document=None, timeout=20, token=""):
        calls.append((method, {**(data or {}), **({"_document": document} if document else {})}))
        return {"message_id": 500 + len(calls), "username": "my_cv_bot"}

    monkeypatch.setattr(telegram, "_api", fake_api)
    monkeypatch.setattr(telegram, "_token", lambda: "123456:token")
    monkeypatch.setattr(settings, "DESKTOP", True)
    queued = []
    monkeypatch.setattr(tasks, "submit", lambda kind, label, fn, subject="", job_id="", url="", args=None: queued.append((fn, args)) or 1)
    person_id = f"tg-{time.time_ns()}"
    with people.in_account(people.LOCAL):
        people.save([*people.load(), people.Person(id=person_id, name="Test")])
        people.data_dir(person_id)
        with people.acting_as(person_id):
            yield type("Bot", (), {"calls": calls, "queued": queued, "person": person_id})
        people.save([p for p in people.load() if p.id != person_id])
        for name in ("telegram_bot.json",):
            (people.account_root() / name).unlink(missing_ok=True)


def _private(text, sender=ME, kind="private"):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": sender, "type": kind}, "from": {"id": sender, "username": "me"}}}


def _press(data, sender=ME, chat_id=ME):
    return {"update_id": 2, "callback_query": {"id": "c", "data": data, "from": {"id": sender},
                                               "message": {"message_id": 77, "chat": {"id": chat_id}}}}


def _sent(bot, method="sendMessage"):
    return [d for m, d in bot.calls if m == method]


def _texts(bot):
    return "\n".join(d["text"] for d in _sent(bot))


def _linked():
    telegram.update_config(chat_id=ME)
    return telegram.load_config()


def _job(job_id, fit=8, status="new", age_days=0, score=70, **fields) -> Job:
    job = Job(id=job_id, source="test", company=fields.pop("company", "Acme"), title=fields.pop("title", "Developer"), url=fields.pop("url", "https://example.com/job"), **fields)
    db.upsert_jobs([job], {job_id: score})
    if fit:
        db.set_fit(job_id, fit, {"strong": ["Java"], "missing": ["Kafka"], "verdict": "Good match", "quick": True})
    if status != "new":
        db.set_status(job_id, status)
    if age_days:
        with db.connect() as c:
            c.execute("UPDATE jobs SET first_seen = ? WHERE id = ?", (time.time() - age_days * 86400, job_id))
    return job


# --- linking and who gets an answer -----------------------------------------------------------------------------


def test_link_code_works_once_and_expires(bot):
    telegram.new_link_code()
    code = telegram.load_config().link_code
    telegram.handle_update(_private(f"/start {code}"))
    assert telegram.load_config().chat_id == ME
    assert telegram.load_config().link_code == ""

    # used already: another account can't take over with the same code
    telegram.handle_update(_private(f"/start {code}", sender=STRANGER))
    assert telegram.load_config().chat_id == ME

    telegram.update_config(chat_id=0)
    telegram.new_link_code()
    code = telegram.load_config().link_code
    telegram.update_config(link_expires=time.time() - 1)
    telegram.handle_update(_private(f"/start {code}"))
    assert telegram.load_config().chat_id == 0


def test_strangers_and_groups_get_nothing(bot):
    _linked()
    telegram.handle_update(_private("rewrite my CV", sender=STRANGER))
    telegram.handle_update(_private("rewrite my CV", kind="group"))
    # someone else forwarding into a chat: sender and chat differ
    forged = _private("rewrite my CV")
    forged["message"]["from"]["id"] = STRANGER
    telegram.handle_update(forged)
    assert not bot.calls and not bot.queued


def test_button_press_from_another_account_does_nothing(bot):
    _linked()
    job = _job("press-1")
    ref = telegram._job_ref(job.id)
    telegram.handle_update(_press(f"h:{ref}", sender=STRANGER))
    telegram.handle_update(_press(f"h:{ref}", sender=STRANGER, chat_id=STRANGER))
    assert db.get_application(job.id)["status"] == "new"


# --- the summary -------------------------------------------------------------------------------------------------


def test_summary_picks_well_rated_new_jobs_once(bot):
    _linked()
    _job("good", fit=9, title="Best job")
    _job("okay", fit=7, title="Fine job")
    _job("packed", fit=8, status="liked", title="Pack job")
    _job("low", fit=6, title="Weak job")
    _job("unrated", fit=None, title="Unrated job")
    _job("old", fit=9, age_days=30, title="Old job")
    _job("hidden", fit=9, status="hidden", title="Hidden job")
    _job("applied", fit=9, status="applied", title="Applied job")

    assert telegram.send_digest() == 3
    text = _texts(bot)
    assert text.index("Best job") < text.index("Pack job") < text.index("Fine job")
    for absent in ("Weak job", "Unrated job", "Old job", "Hidden job", "Applied job"):
        assert absent not in text

    bot.calls.clear()
    assert telegram.send_digest() == 0
    assert "Best job" not in _texts(bot)


def test_long_summary_sends_the_rest_on_jobs(bot):
    _linked()
    for i in range(telegram.JOBS_PER_MESSAGE + 2):
        _job(f"many-{i}", title=f"Job number {i}")
    telegram.send_digest()
    assert sum("Job number" in d["text"] for d in _sent(bot)) == telegram.JOBS_PER_MESSAGE
    assert "2 more waiting" in _texts(bot)

    bot.calls.clear()
    telegram.handle_update(_private("/jobs"))
    assert sum("Job number" in d["text"] for d in _sent(bot)) == 2


def test_posting_text_cannot_add_markup_or_links(bot):
    _linked()
    _job("evil", title='<a href="http://evil.example">Click</a> & win', company="<b>Bad</b>", url="javascript:alert(1)")
    telegram.send_digest()
    card = next(d for d in _sent(bot) if "Click" in d["text"])
    assert '<a href' not in card["text"] and "&lt;a href" in card["text"] and "&lt;b&gt;Bad" in card["text"]
    # a link that isn't http(s) gets no button (and would make Telegram refuse the message)
    assert not [b for row in card["reply_markup"]["inline_keyboard"] for b in row if "url" in b]


def test_callback_data_fits_telegram_limit(bot):
    job = _job("x" * 300)
    for row in telegram._job_buttons(job, False):
        for button in row:
            assert len(button.get("callback_data", "").encode()) <= 64
    assert telegram._job_from_ref(telegram._job_ref(job.id)) == job.id


def test_daily_runs_once_a_day_after_its_time(bot):
    _linked()
    telegram.update_config(daily_time="00:00")
    telegram.daily_tick()
    assert len(_sent(bot)) == 1
    telegram.daily_tick()
    assert len(_sent(bot)) == 1

    bot.calls.clear()
    telegram.update_config(daily_time="23:59", last_day="")
    if time.strftime("%H:%M") < "23:59":
        telegram.daily_tick()
        assert not bot.calls

    telegram.update_config(daily_time="00:00", last_day="", daily=False)
    telegram.daily_tick()
    assert not bot.calls


def test_daily_retries_without_internet_but_not_when_refused(bot, monkeypatch):
    _linked()
    telegram.update_config(daily_time="00:00")

    def offline(*a, **k):
        raise telegram.NetworkError("offline")

    monkeypatch.setattr(telegram, "_api", offline)
    telegram.daily_tick()
    assert telegram.load_config().last_day == ""

    def blocked(*a, **k):
        raise telegram.TelegramError("Forbidden: bot was blocked by the user", 403)

    monkeypatch.setattr(telegram, "_api", blocked)
    telegram.daily_tick()
    assert telegram.load_config().last_day == time.strftime("%Y-%m-%d")
    assert "blocked" in telegram.load_config().last_error


# --- job buttons -------------------------------------------------------------------------------------------------


def test_hide_and_undo_keep_the_previous_stage(bot):
    _linked()
    job = _job("hide-me", status="liked")
    ref = telegram._job_ref(job.id)
    telegram.handle_update(_press(f"h:{ref}"))
    assert db.get_application(job.id)["status"] == "hidden"
    undo = next(d for m, d in bot.calls if m == "editMessageReplyMarkup")["reply_markup"]["inline_keyboard"][0][0]
    assert undo["callback_data"] == f"u:{ref}:liked"
    telegram.handle_update(_press(undo["callback_data"]))
    assert db.get_application(job.id)["status"] == "liked"


def test_hide_leaves_jobs_you_applied_to(bot):
    _linked()
    job = _job("applied-1", status="applied")
    telegram.handle_update(_press(f"h:{telegram._job_ref(job.id)}"))
    assert db.get_application(job.id)["status"] == "applied"


def test_tailor_button_queues_the_cv(bot):
    _linked()
    job = _job("tailor-1")
    telegram.handle_update(_press(f"t:{telegram._job_ref(job.id)}"))
    assert bot.queued == [("telegram-tailor", {"job_id": job.id, "make_new": False})]


def test_existing_cv_is_sent_without_calling_claude(bot, monkeypatch, tmp_path):
    _linked()
    job = _job("has-cv")
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    tailored = TailoredCV(cv=CVContent(**_CV), match_score=80, matched_requirements=[], missing_requirements=["Kafka"],
                          changes_made=["Moved Java first"], questions_to_strengthen=[])
    db.save_cv(job.id, tailored, str(pdf), {}, 1)
    monkeypatch.setattr(telegram.assistant, "load_profile", lambda: _profile())
    monkeypatch.setattr(telegram.pack, "tailored_cv", lambda *a, **k: pytest.fail("Claude was called"))

    telegram._tailor_task(None, job.id)
    method, data = next((m, d) for m, d in bot.calls if m == "sendDocument")
    assert data["_document"][1] == b"%PDF-1.4 test" and data["_document"][0].endswith(".pdf")
    text = _texts(bot)
    assert "Moved Java first" in text and "Kafka" in text


# --- the conversation --------------------------------------------------------------------------------------------


def test_message_goes_to_the_chat_and_answer_comes_back(bot, monkeypatch):
    _linked()
    telegram.handle_update(_private("should I learn Kafka?"))
    fn, args = bot.queued[0]
    assert fn == "telegram-chat"
    thread_id = args["thread_id"]
    assert db.chat_messages(thread_id)[0]["text"] == "should I learn Kafka?"

    monkeypatch.setattr(chat, "answer", lambda t: db.chat_add(t, "assistant", "Yes, **briefly**."))
    telegram._chat_task(None, thread_id)
    assert "Yes, <b>briefly</b>." in _texts(bot)

    # the same conversation continues, until /new
    telegram.handle_update(_private("and Docker?"))
    assert bot.queued[-1][1]["thread_id"] == thread_id
    telegram.handle_update(_private("/new"))
    telegram.handle_update(_private("something else"))
    assert bot.queued[-1][1]["thread_id"] != thread_id


def _proposal(new_summary: str) -> tuple[int, Profile]:
    profile = _profile()
    telegram.assistant.save_profile(profile)
    new = profile.model_copy(update={"summary": new_summary})
    thread_id = db.chat_thread_add("Edit", "")
    message_id = db.chat_add(thread_id, "assistant", "Here's the change.", ["Summary shortened"], new.model_dump_json(indent=2),
                             profile.model_dump_json(indent=2), "proposed")
    return message_id, profile


def test_profile_changes_only_on_apply_and_can_be_undone(bot):
    _linked()
    message_id, _ = _proposal("New summary")

    # proposing changes nothing, and the answer shows what would change
    assert telegram.assistant.load_profile().summary == "Old summary"
    text = telegram._answer_text(db.chat_message(message_id))
    assert "Summary shortened" in text and "New summary" in text and "Old summary" in text

    telegram.handle_update(_press(f"a:{message_id}"))
    assert telegram.assistant.load_profile().summary == "New summary"
    telegram.handle_update(_press(f"z:{message_id}"))
    assert telegram.assistant.load_profile().summary == "Old summary"


def test_apply_refused_when_profile_changed_since(bot):
    _linked()
    message_id, profile = _proposal("New summary")
    telegram.assistant.save_profile(profile.model_copy(update={"summary": "Edited in the app"}))
    telegram.handle_update(_press(f"a:{message_id}"))
    assert telegram.assistant.load_profile().summary == "Edited in the app"
    assert db.chat_message(message_id)["state"] == "stale"


def test_failed_answer_is_reported(bot, monkeypatch):
    _linked()
    thread_id = db.chat_thread_add("T", "")
    db.chat_add(thread_id, "user", "hi")

    def broken(t):
        raise RuntimeError("no key")

    monkeypatch.setattr(chat, "answer", broken)
    with pytest.raises(RuntimeError):
        telegram._chat_task(None, thread_id)
    assert "couldn't answer" in _texts(bot)


# --- top-job alerts ----------------------------------------------------------------------------------------------


def test_alert_sends_only_top_jobs_and_once(bot, monkeypatch):
    _linked()
    monkeypatch.setattr(telegram, "_quiet_now", lambda: False)
    _job("top", fit=9, title="Top job")
    _job("good", fit=8, title="Good job")
    telegram.send_alerts()
    text = _texts(bot)
    assert "New top match" in text and "Top job" in text and "Good job" not in text
    assert "disable_notification" not in _sent(bot)[0]

    # not repeated, and the summary still has the other one, but not this one
    bot.calls.clear()
    telegram.send_alerts()
    assert not bot.calls
    telegram.send_digest()
    assert "Good job" in _texts(bot) and "Top job" not in _texts(bot)


def test_alert_is_silent_at_night_and_can_be_turned_off(bot, monkeypatch):
    _linked()
    _job("night", fit=10, title="Night job")
    telegram.update_config(alerts=False)
    telegram.send_alerts()
    assert not bot.calls

    telegram.update_config(alerts=True)
    monkeypatch.setattr(telegram, "_quiet_now", lambda: True)
    telegram.send_alerts()
    assert _sent(bot)[0]["disable_notification"] is True


def test_quiet_hours(monkeypatch):
    for hour, quiet in ((21, False), (22, True), (3, True), (7, True), (8, False), (12, False)):
        monkeypatch.setattr(telegram.time, "localtime", lambda *a, hour=hour: time.struct_time((2026, 11, 6, hour, 0, 0, 4, 310, 0)))
        assert telegram._quiet_now() is quiet, hour


def test_alert_problem_never_breaks_rating(bot, monkeypatch):
    _linked()
    _job("boom", fit=9)

    def broken(*a, **k):
        raise telegram.NetworkError("offline")

    monkeypatch.setattr(telegram, "_api", broken)
    telegram.send_alerts()
    assert "boom" not in telegram.load_config().sent  # so the summary still sends it


def test_rating_triggers_alerts(bot, monkeypatch):
    from cvmaker import auto
    from cvmaker.models import QuickFit, QuickFits

    _linked()
    monkeypatch.setattr(telegram, "_quiet_now", lambda: False)
    _job("rate-me", fit=None, title="Fresh job", description="Java")
    monkeypatch.setattr(auto.assistant, "load_profile", lambda: _profile())
    monkeypatch.setattr(auto.assistant, "quick_fit", lambda *a: QuickFits(fits=[QuickFit(job_id="rate-me", rating=9, strong=[], missing=[], verdict="Great")]))
    monkeypatch.setattr(auto.taste, "for_prompt", lambda p: "")
    auto.rate_jobs(["rate-me"])
    assert "Fresh job" in _texts(bot)


# --- cover letters -------------------------------------------------------------------------------------------------


def test_letter_button_queues_the_letter(bot):
    _linked()
    job = _job("letter-1")
    telegram.handle_update(_press(f"l:{telegram._job_ref(job.id)}"))
    assert bot.queued == [("telegram-letter", {"job_id": job.id, "make_new": False})]
    telegram.handle_update(_press(f"m:{telegram._job_ref(job.id)}"))
    assert bot.queued[-1] == ("telegram-letter", {"job_id": job.id, "make_new": True})


def _saved_letter(job_id, tmp_path, body="Dear team") -> None:
    from cvmaker.models import CoverLetter

    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(b"%PDF-letter")
    fields = {name: ([] if "list" in str(field.annotation) else "") for name, field in CoverLetter.model_fields.items()}
    db.save_letter(job_id, CoverLetter(**fields), body, str(pdf))


def test_existing_letter_is_sent_without_calling_claude(bot, monkeypatch, tmp_path):
    _linked()
    job = _job("has-letter")
    _saved_letter(job.id, tmp_path)
    monkeypatch.setattr(telegram.assistant, "load_profile", lambda: _profile())
    monkeypatch.setattr(telegram.pack, "letter", lambda *a, **k: pytest.fail("Claude was called"))
    telegram._letter_task(None, job.id)
    document = next(d for m, d in bot.calls if m == "sendDocument")["_document"]
    assert document[1] == b"%PDF-letter" and "CoverLetter" in document[0]


def test_new_letter_matches_the_latest_cv(bot, monkeypatch, tmp_path):
    _linked()
    job = _job("new-letter", description="Java")
    cv = CVContent(**{**_CV, "headline": "Matching CV"})
    tailored = TailoredCV(cv=cv, match_score=80, matched_requirements=[], missing_requirements=[], changes_made=[], questions_to_strengthen=[])
    db.save_cv(job.id, tailored, str(tmp_path / "cv.pdf"), {}, 1)
    monkeypatch.setattr(telegram.assistant, "load_profile", lambda: _profile())
    monkeypatch.setattr(telegram, "ensure_description", lambda j: j)
    got = {}

    def fake_letter(profile, job_, cv_, progress=None):
        got["cv"] = cv_
        _saved_letter(job_.id, tmp_path, "I used Terraform daily.")
        return db.letters_for_job(job_.id)[0]["id"]

    monkeypatch.setattr(telegram.pack, "letter", fake_letter)
    telegram._letter_task(None, job.id, make_new=True)
    assert got["cv"].headline == "Matching CV"
    assert "Terraform" in _texts(bot)  # flagged: the profile doesn't show it


# --- files you send ------------------------------------------------------------------------------------------------


def _file_message(name="cv.pdf", size=10, caption="", group="", sender=ME):
    message = {"message_id": 3, "chat": {"id": sender, "type": "private"}, "from": {"id": sender},
               "document": {"file_id": f"id-{name}", "file_name": name, "file_size": size}}
    if caption:
        message["caption"] = caption
    if group:
        message["media_group_id"] = group
    return message


def test_file_goes_to_the_chat_with_its_caption(bot, monkeypatch):
    _linked()
    monkeypatch.setattr(telegram, "_download", lambda file_id: b"file for " + file_id.encode())
    telegram._receive_files(bot.person, [_file_message(caption="what do you think?")])
    fn, args = bot.queued[0]
    assert fn == "telegram-chat"
    message = db.chat_messages(args["thread_id"])[0]
    assert message["text"] == "what do you think?" and message["files"][0]["name"] == "cv.pdf"
    assert chat.file_path(message, 0).read_bytes() == b"file for id-cv.pdf"


def test_photo_uses_the_largest_size(bot, monkeypatch):
    _linked()
    got = []
    monkeypatch.setattr(telegram, "_download", lambda file_id: got.append(file_id) or b"jpg")
    message = {"chat": {"id": ME, "type": "private"}, "from": {"id": ME},
               "photo": [{"file_id": "small", "file_unique_id": "u"}, {"file_id": "large", "file_unique_id": "u"}]}
    telegram._receive_files(bot.person, [message])
    assert got == ["large"]
    assert db.chat_messages(bot.queued[0][1]["thread_id"])[0]["text"] == "Have a look at the attached file."


def test_refused_files_are_not_downloaded(bot, monkeypatch):
    _linked()
    monkeypatch.setattr(telegram, "_download", lambda file_id: pytest.fail("downloaded"))
    telegram._receive_files(bot.person, [_file_message("virus.exe")])
    telegram._receive_files(bot.person, [_file_message("big.pdf", size=(chat.MAX_FILE_MB + 1) * 1024 * 1024)])
    assert "virus.exe" in _texts(bot) and "larger than" in _texts(bot)
    assert not bot.queued


def test_album_becomes_one_question(bot, monkeypatch):
    _linked()
    monkeypatch.setattr(telegram, "_download", lambda file_id: b"x")
    monkeypatch.setattr(telegram, "ALBUM_WAIT_SECONDS", 0.05)
    for name, caption in (("a.pdf", "compare these"), ("b.pdf", "")):
        telegram.handle_update({"update_id": 5, "message": _file_message(name, caption=caption, group="album1")})
    for _ in range(100):
        if bot.queued:
            break
        time.sleep(0.02)
    assert len(bot.queued) == 1
    message = db.chat_messages(bot.queued[0][1]["thread_id"])[0]
    assert message["text"] == "compare these" and [f["name"] for f in message["files"]] == ["a.pdf", "b.pdf"]


def test_stranger_files_are_ignored(bot, monkeypatch):
    _linked()
    monkeypatch.setattr(telegram, "_download", lambda file_id: pytest.fail("downloaded"))
    telegram.handle_update({"update_id": 6, "message": _file_message(sender=STRANGER)})
    time.sleep(0.05)
    assert not bot.queued and not bot.calls


def test_other_messages_get_a_clear_answer(bot):
    _linked()
    telegram.handle_update({"update_id": 7, "message": {"chat": {"id": ME, "type": "private"}, "from": {"id": ME}, "sticker": {}}})
    assert "not this kind of message" in _texts(bot)


def test_download_error_hides_the_token(monkeypatch):
    def refuse(self, request):
        raise httpx.ConnectError(f"failed for {request.url}", request=request)

    monkeypatch.setattr(telegram, "_token", lambda: "123456789:SECRET-token-value-abcdefghijk")
    monkeypatch.setattr(telegram, "_api", lambda *a, **k: {"file_path": "documents/x.pdf"})
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    with pytest.raises(telegram.NetworkError) as caught:
        telegram._download("id")
    assert "SECRET" not in str(caught.value) and caught.value.__suppress_context__


# --- start at login ------------------------------------------------------------------------------------------------


def test_autostart_unit(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/home/me/.local/bin:/usr/bin")
    monkeypatch.setattr(autostart, "_systemctl", lambda *a: runs.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    autostart.enable()
    unit = (tmp_path / "systemd/user/cvmaker.service").read_text()
    assert f'ExecStart="{sys.executable}" -m cvmaker.desktop' in unit
    assert 'Environment="PATH=/home/me/.local/bin:/usr/bin"' in unit
    assert "CVMAKER_NO_WINDOW=1" in unit and f"PORT={autostart.PORT}" in unit and "WantedBy=default.target" in unit
    assert ("enable", autostart.UNIT) in runs
    assert not [r for r in runs if r[0] in ("start", "restart")]  # this copy is already running

    autostart.disable()
    assert not (tmp_path / "systemd/user/cvmaker.service").exists()
    assert ("disable", autostart.UNIT) in runs


def test_autostart_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(autostart, "_systemctl", lambda *a: subprocess.CompletedProcess(a, 1, "", "no user bus"))
    with pytest.raises(RuntimeError, match="no user bus"):
        autostart.enable()


# --- formatting and the wire ---------------------------------------------------------------------------------------


def test_long_text_is_split_and_buttons_stay_on_the_last_part(bot):
    long = "\n".join(f"Line {i} " + "x" * 80 for i in range(200))
    telegram.send(ME, long, [[telegram._callback("Apply", "a:1")]])
    parts = _sent(bot)
    assert len(parts) > 1
    assert all(len(p["text"]) <= 4096 for p in parts)
    assert "reply_markup" in parts[-1] and not any("reply_markup" in p for p in parts[:-1])


def test_markup_telegram_rejects_falls_back_to_plain(monkeypatch):
    sent = []

    def fake_api(method, data=None, **k):
        if data.get("parse_mode"):
            raise telegram.TelegramError("Bad Request: can't parse entities", 400)
        sent.append(data)
        return {"message_id": 1}

    monkeypatch.setattr(telegram, "_api", fake_api)
    telegram._send_html(ME, "<b>a &amp; b</b>")
    assert sent[0]["text"] == "a & b"


def test_token_never_appears_in_errors(monkeypatch):
    secret = "123456789:SECRET-token-value-abcdefghijk"

    def refuse(self, request):
        raise httpx.ConnectError(f"failed for {request.url}", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    with pytest.raises(telegram.NetworkError) as caught:
        telegram._api("getMe", token=secret)
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_real_requests_have_the_shape_telegram_expects(monkeypatch):
    """The actual HTTP requests, including the PDF upload."""
    seen = []

    def answer(self, request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}}, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", answer)
    telegram._api("sendMessage", {"chat_id": ME, "text": "hi"}, token="123:abc")
    assert str(seen[0].url) == "https://api.telegram.org/bot123:abc/sendMessage"
    assert json.loads(seen[0].content) == {"chat_id": ME, "text": "hi"}

    telegram._api("sendDocument", {"chat_id": str(ME)}, document=("cv.pdf", b"%PDF"), token="123:abc")
    body = seen[1].read()
    assert seen[1].headers["content-type"].startswith("multipart/form-data")
    assert b'name="chat_id"' in body and b'filename="cv.pdf"' in body and b"%PDF" in body


# --- modes ---------------------------------------------------------------------------------------------------------


def test_no_bot_on_a_server(user):
    client, _ = user
    assert client.post("/settings/telegram/connect").status_code == 404
    assert client.post("/settings/autostart", data={"on": "1"}).status_code == 404
    page = client.get("/settings").text
    assert 'id="telegram"' not in page and "Telegram bot token" not in page and 'id="autostart"' not in page


DESKTOP_SCRIPT = textwrap.dedent('''
    from fastapi.testclient import TestClient
    from cvmaker import keys, people, telegram
    from cvmaker.app import app

    calls = []
    def fake_api(method, data=None, document=None, timeout=20, token=""):
        calls.append(method)
        if method == "getMe" and token.endswith("refused-token-aaaaaaaaaaaaaaaaaaaa"):
            raise telegram.TelegramError("Unauthorized", 401)
        return {"message_id": 1, "username": "my_cv_bot"}
    telegram._api = fake_api

    c = TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)
    page = c.get("/settings").text
    assert "@BotFather" in page and "Telegram bot token" not in page

    # a bad token is refused before anything is saved, and never echoed
    r = c.post("/settings/telegram/token", data={"token": "nonsense"})
    assert "error=" in r.headers["location"] and not keys.own("telegram_token")
    r = c.post("/settings/telegram/token", data={"token": "12345:refused-token-aaaaaaaaaaaaaaaaaaaa"})
    assert "error=" in r.headers["location"] and "refused-token" not in r.headers["location"] and not keys.own("telegram_token")

    # another web site can't change it
    r = c.post("/settings/telegram/remove", headers={"Origin": "http://attacker.example"})
    assert r.status_code == 403

    token = "12345:good-token-aaaaaaaaaaaaaaaaaaaaaaaa"
    assert c.post("/settings/telegram/token", data={"token": token}).status_code == 303
    assert keys.own("telegram_token") == token
    assert token.encode() not in keys._path().read_bytes()
    page = c.get("/settings").text
    assert token not in page
    assert "https://t.me/my_cv_bot?start=" in page

    # linking through the bot, then the options
    code = telegram.load_config().link_code
    telegram.handle_update({"update_id": 1, "message": {"text": f"/start {code}", "chat": {"id": 5, "type": "private"}, "from": {"id": 5, "username": "renis"}}})
    assert c.get("/settings/telegram/status").json() == {"linked": True}
    page = c.get("/settings").text
    assert "Connected to" in page and "renis" in page
    assert c.post("/settings/telegram/options", data={"daily": "1", "daily_time": "07:15", "min_rating": "8", "alerts": "1", "alert_rating": "10"}).status_code == 303
    config = telegram.load_config()
    assert (config.daily, config.daily_time, config.min_rating, config.alerts, config.alert_rating) == (True, "07:15", 8, True, 10)
    assert "Tell me straight away" in c.get("/settings").text
    assert "error=" in c.post("/settings/telegram/options", data={"daily_time": "99:99"}).headers["location"]
    assert c.post("/settings/telegram/send").status_code == 303

    # start at login: the card and its switch, with systemctl replaced
    from cvmaker import autostart
    import subprocess as sp
    state = {"on": False}
    def fake_systemctl(*args):
        if args[0] == "enable": state["on"] = True
        if args[0] == "disable": state["on"] = False
        return sp.CompletedProcess(args, 0, "enabled" if state["on"] else "disabled", "")
    autostart._systemctl = fake_systemctl
    autostart.available = lambda: True
    assert 'id="autostart"' in c.get("/settings").text and "Turn on" in c.get("/settings").text
    assert c.post("/settings/autostart", data={"on": "1"}).status_code == 303
    assert state["on"] and "Turn off" in c.get("/settings").text
    assert c.post("/settings/autostart", headers={"Origin": "http://attacker.example"}).status_code == 403 and state["on"]
    assert c.post("/settings/autostart").status_code == 303
    assert not state["on"]

    # the file with the chat id is private, and stays out of backups
    assert oct(telegram._config_path().stat().st_mode)[-3:] == "600"
    from cvmaker import backup
    assert "telegram.json" in backup.SKIP_TOP

    # removing the token forgets the link too
    assert c.post("/settings/telegram/remove").status_code == 303
    assert not keys.own("telegram_token") and telegram.load_config().chat_id == 0
    print("TELEGRAM DESKTOP OK")
''')


def test_settings_card_on_the_desktop():
    with tempfile.TemporaryDirectory(prefix="cvmaker-telegram-") as data:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CVMAKER_", "STRIPE_", "DATABASE_URL", "ANTHROPIC", "TELEGRAM"))}
        env.update({"CVMAKER_MODE": "desktop", "CVMAKER_DATA": data, "CVMAKER_BACKEND": "api", "CVMAKER_TASK_WORKERS": "0", "ANTHROPIC_API_KEY": "",
                    "XDG_CONFIG_HOME": data})
        r = subprocess.run([sys.executable, "-c", DESKTOP_SCRIPT], cwd=Path(__file__).parent.parent, env=env, capture_output=True, text=True, timeout=120)
        assert "TELEGRAM DESKTOP OK" in r.stdout, r.stdout + r.stderr
