"""People: everyone using the app has their own profile, photo, jobs, CVs, letters, applications, saved searches,
role profiles and learned preferences, kept in their own data folder.

The default person keeps the original data/ folder; everyone else gets data/people/<id>/. Which person a piece
of code works for is a context variable: the web app sets it per request from a cookie, background tasks carry
it over from the request that started them, and the scheduler sets it for each person in turn.
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

BASE_DIR = Path(__file__).resolve().parent.parent / "data"
PEOPLE_PATH = BASE_DIR / "people.json"
DEFAULT = "me"
COOKIE = "cvmaker_person"

_current: contextvars.ContextVar[str] = contextvars.ContextVar("cvmaker_person", default=DEFAULT)


class Person(BaseModel):
    id: str
    name: str
    created: float = 0


def load() -> list[Person]:
    if PEOPLE_PATH.exists():
        people = [Person.model_validate(p) for p in json.loads(PEOPLE_PATH.read_text())]
        if any(p.id == DEFAULT for p in people):
            return people
        return [_default_person(), *people]
    return [_default_person()]


def _default_person() -> Person:
    name = "Me"
    profile = BASE_DIR / "profile.json"
    if profile.exists():
        try:
            name = json.loads(profile.read_text()).get("name") or name
        except (json.JSONDecodeError, OSError):
            pass
    return Person(id=DEFAULT, name=name)


def save(people: list[Person]) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    PEOPLE_PATH.write_text(json.dumps([p.model_dump() for p in people], indent=2, ensure_ascii=False))


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
    path = BASE_DIR if person_id == DEFAULT else BASE_DIR / "people" / person_id
    (path / "cvs").mkdir(parents=True, exist_ok=True)
    return path


def submit(pool: Executor, fn: Callable, *args, **kwargs) -> Future:
    """Like pool.submit, but the worker thread works for the same person as the caller."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def add(name: str) -> Person:
    name = name.strip() or "New person"
    people = load()
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "person"
    taken = {p.id for p in people} | {DEFAULT}
    person_id, n = base, 2
    while person_id in taken or (BASE_DIR / "people" / person_id).exists():
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
    folder = BASE_DIR / "people" / person_id
    if not folder.exists():
        return None
    target = BASE_DIR / "people" / ".removed" / f"{person_id}-{int(time.time())}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(target))
    return target
