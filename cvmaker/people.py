"""People: everyone using the app has their own profile, photo, jobs, CVs, letters, applications, saved searches,
role profiles and learned preferences, kept in their own data folder.

Folders are scoped twice:
- the account: in desktop mode there is one, "local", whose folder is the data root itself; in server mode every
  signed-up user is an account with its own folder data/accounts/<account id>/ (set per request from the login
  session, never from anything the browser can choose).
- the person inside the account: the default person keeps the account folder; everyone else gets
  <account folder>/people/<id>/. Which person is current comes from a cookie, but only people that exist in the
  current account can be picked.

Both are context variables: the web app sets them per request, background tasks carry them over from the request
that started them (or the queue restores them), and the scheduler sets them for each account and person in turn.
"""

import contextvars
import json
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from . import settings

LOCAL = "local"
DEFAULT = "me"
COOKIE = "cvmaker_person"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_current: contextvars.ContextVar[str] = contextvars.ContextVar("cvmaker_person", default=DEFAULT)
_account: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cvmaker_account", default=None if settings.SERVER else LOCAL
)


class NoAccount(RuntimeError):
    """Data was touched without an account (server mode, outside a logged-in request or task)."""


def account() -> str:
    value = _account.get()
    if not value:
        raise NoAccount("No account selected")
    return value


def account_root(account_id: str | None = None) -> Path:
    account_id = account_id or account()
    if account_id == LOCAL:
        return settings.DATA_ROOT
    if not _ID.match(account_id):
        raise NoAccount(f"Bad account id {account_id!r}")
    return settings.DATA_ROOT / "accounts" / account_id


def people_path() -> Path:
    return account_root() / "people.json"


@contextmanager
def in_account(account_id: str | None, person_id: str = DEFAULT):
    """Work for this account (and person in it) inside the block."""
    a = _account.set(account_id)
    p = _current.set(DEFAULT)
    try:
        if account_id:
            _current.set(person_id if get(person_id) else DEFAULT)
        yield
    finally:
        _current.reset(p)
        _account.reset(a)


def use_account(account_id: str | None) -> contextvars.Token:
    return _account.set(account_id)


def reset_account(token: contextvars.Token) -> None:
    _account.reset(token)


def all_accounts() -> list[str]:
    """Every account with a data folder (desktop: just the local one)."""
    if settings.DESKTOP:
        return [LOCAL]
    folder = settings.DATA_ROOT / "accounts"
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and _ID.match(p.name)) if folder.exists() else []


class Person(BaseModel):
    id: str
    name: str
    created: float = 0


def load() -> list[Person]:
    path = people_path()
    if path.exists():
        people = [Person.model_validate(p) for p in json.loads(path.read_text()) if _ID.match(p.get("id", ""))]
        if any(p.id == DEFAULT for p in people):
            return people
        return [_default_person(), *people]
    return [_default_person()]


def _default_person() -> Person:
    name = "Me"
    profile = account_root() / "profile.json"
    if profile.exists():
        try:
            name = json.loads(profile.read_text()).get("name") or name
        except (json.JSONDecodeError, OSError):
            pass
    return Person(id=DEFAULT, name=name)


def save(people: list[Person]) -> None:
    account_root().mkdir(parents=True, exist_ok=True)
    people_path().write_text(json.dumps([p.model_dump() for p in people], indent=2, ensure_ascii=False))


def get(person_id: str) -> Person | None:
    return next((p for p in load() if p.id == person_id), None)


def current() -> str:
    return _current.get()


def current_person() -> Person:
    return get(current()) or _default_person()


def is_default() -> bool:
    return current() == DEFAULT


def use(person_id: str) -> contextvars.Token:
    """Make `person_id` the current person; returns a token for `reset`."""
    return _current.set(person_id if get(person_id) else DEFAULT)


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


@contextmanager
def acting_as(person_id: str):
    token = use(person_id)
    try:
        yield
    finally:
        reset(token)


def data_dir(person_id: str | None = None) -> Path:
    person_id = person_id or current()
    if person_id != DEFAULT and not _ID.match(person_id):
        raise ValueError(f"Bad person id {person_id!r}")
    root = account_root()
    path = root if person_id == DEFAULT else root / "people" / person_id
    (path / "cvs").mkdir(parents=True, exist_ok=True)
    return path


def submit(pool: Executor, fn: Callable, *args, **kwargs) -> Future:
    """Like pool.submit, but the worker thread works for the same person as the caller."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def add(name: str) -> Person:
    name = name.strip() or "New person"
    people = load()
    base = (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "person")[:40]
    taken = {p.id for p in people} | {DEFAULT}
    person_id, n = base, 2
    while person_id in taken or (account_root() / "people" / person_id).exists():
        person_id, n = f"{base}-{n}", n + 1
    person = Person(id=person_id, name=name, created=time.time())
    save([*people, person])
    data_dir(person_id)
    return person


def rename(person_id: str, name: str) -> None:
    people = load()
    for p in people:
        if p.id == person_id and name.strip():
            p.name = name.strip()
    save(people)


def remove(person_id: str) -> Path | None:
    """Take a person out of the app. Their folder is moved to data/people/.removed/, not deleted."""
    if person_id == DEFAULT:
        raise ValueError("The default person can't be removed")
    save([p for p in load() if p.id != person_id])
    folder = account_root() / "people" / person_id
    if not folder.exists():
        return None
    target = account_root() / "people" / ".removed" / f"{person_id}-{int(time.time())}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(target))
    return target
