"""How much Claude each account uses, and the limits on it.

Server mode: calls made with the operator's key count against the account's monthly token allowance (by plan) and a
per-minute rate limit. Calls made with the account's own key are only counted, not limited. Desktop mode only counts.
"""

import time

from . import accounts, central, keys, people, settings


class LimitReached(RuntimeError):
    pass


def month() -> str:
    return time.strftime("%Y-%m")


def uses_own_key() -> bool:
    return bool(keys.own("anthropic"))


def this_month(account_id: str | None = None) -> dict:
    account_id = account_id or people.account()
    with central.connect() as c:
        row = c.one("SELECT * FROM usage WHERE account_id = ? AND month = ?", (account_id, month()))
    row = row or {"input_tokens": 0, "output_tokens": 0, "calls": 0, "own_key_calls": 0}
    plan = accounts.plan_of(account_id)
    allowance = settings.PLAN_TOKENS.get(plan, 0)
    used = row["input_tokens"] + row["output_tokens"]
    return {**row, "plan": plan, "allowance": allowance, "used": used, "left": max(0, allowance - used)}


def check() -> None:
    """Before a Claude call: raise LimitReached when this account may not make one now."""
    if settings.DESKTOP or settings.BACKEND != "api":
        return
    account_id = people.account()
    if not central.hit(f"ai:{account_id}", settings.AI_CALLS_PER_MINUTE, 60):
        raise LimitReached("Too many Claude requests in a minute - wait a moment and try again.")
    if uses_own_key():
        return
    now = this_month(account_id)
    if now["used"] >= now["allowance"]:
        hint = " Upgrade your plan or add your own Anthropic key in Settings." if settings.BILLING else " Add your own Anthropic key in Settings."
        raise LimitReached(f"You've used this month's AI allowance on the {now['plan']} plan.{hint}")


def record(input_tokens: int, output_tokens: int) -> None:
    try:
        account_id = people.account()
    except people.NoAccount:
        return
    own = int(uses_own_key())
    with central.connect() as c:
        row = c.one("SELECT 1 AS x FROM usage WHERE account_id = ? AND month = ?", (account_id, month()))
        if row:
            c.execute(
                "UPDATE usage SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, calls = calls + 1,"
                " own_key_calls = own_key_calls + ? WHERE account_id = ? AND month = ?",
                (0 if own else input_tokens, 0 if own else output_tokens, own, account_id, month()),
            )
        else:
            c.execute(
                "INSERT INTO usage (account_id, month, input_tokens, output_tokens, calls, own_key_calls) VALUES (?, ?, ?, ?, 1, ?)",
                (account_id, month(), 0 if own else input_tokens, 0 if own else output_tokens, own),
            )
