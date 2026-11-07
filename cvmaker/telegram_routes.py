"""The Telegram and start-at-login cards' actions on the Settings page (desktop mode; they don't exist on a server).
Included at the end of app.py."""

from urllib.parse import quote

from fastapi import APIRouter, Form

from . import autostart, telegram
from .app import _back

router = APIRouter()
BACK = "/settings"


def _result(error: str = "") -> object:
    return _back(f"{BACK}?error={quote(error)}#telegram" if error else f"{BACK}?saved=1#telegram")


@router.post("/settings/telegram/token")
def telegram_token(token: str = Form("")):
    try:
        telegram.save_token(token)
        telegram.new_link_code()
    except telegram.TelegramError as e:
        return _result(f"Telegram didn't accept that token: {e}")
    return _result()


@router.post("/settings/telegram/remove")
def telegram_remove():
    telegram.remove_token()
    return _result()


@router.post("/settings/telegram/connect")
def telegram_connect():
    telegram.new_link_code()
    return _back(f"{BACK}#telegram")


@router.get("/settings/telegram/status")
def telegram_status():
    """The card asks this while it waits for you to tap Start in Telegram."""
    return {"linked": telegram.status()["linked"]}


@router.post("/settings/telegram/options")
def telegram_options(daily: str = Form(""), daily_time: str = Form("08:30"), min_rating: int = Form(7), alerts: str = Form(""), alert_rating: int = Form(9)):
    if not telegram.TIME_FORMAT.match(daily_time):
        return _result("Pick a time for the summary.")
    telegram.update_config(daily=bool(daily), daily_time=daily_time, min_rating=max(1, min(10, min_rating)),
                           alerts=bool(alerts), alert_rating=max(1, min(10, alert_rating)))
    return _result()


@router.post("/settings/telegram/send")
def telegram_send():
    try:
        telegram.send_digest()
    except telegram.TelegramError as e:
        return _result(f"Couldn't send it: {e}")
    return _result()


@router.post("/settings/autostart")
def autostart_set(on: str = Form("")):
    if not autostart.available():
        return _back(f"{BACK}?error=" + quote("Starting at login isn't available on this system yet.") + "#autostart")
    try:
        autostart.enable() if on else autostart.disable()
    except (RuntimeError, OSError) as e:
        return _back(f"{BACK}?error=" + quote(f"Couldn't change that: {e}") + "#autostart")
    return _back(f"{BACK}?saved=1#autostart")


@router.post("/settings/telegram/disconnect")
def telegram_disconnect():
    telegram.update_config(chat_id=0, username="", thread_id=0, last_error="")
    return _result()
