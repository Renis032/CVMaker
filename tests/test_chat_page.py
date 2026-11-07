"""The chat page opens with the latest messages only; earlier ones come in pages."""

import time

import pytest

from cvmaker import db, people


@pytest.fixture(autouse=True)
def person():
    person_id = f"chat-{time.time_ns()}"
    with people.in_account(people.LOCAL):
        people.save([*people.load(), people.Person(id=person_id, name="Test")])
        people.data_dir(person_id)
        with people.acting_as(person_id):
            yield
        people.save([p for p in people.load() if p.id != person_id])


def _thread(count: int) -> tuple[int, list[int]]:
    thread_id = db.chat_thread_add("Test", "")
    return thread_id, [db.chat_add(thread_id, "user", f"message {i}") for i in range(count)]


def _ids(page: list[dict]) -> list[int]:
    return [m["id"] for m in page]


def test_newest_page_oldest_first():
    thread_id, ids = _thread(5)
    messages, more = db.chat_messages_page(thread_id, 2)
    assert _ids(messages) == ids[-2:] and more


def test_pages_cover_every_message_once():
    thread_id, ids = _thread(5)
    seen, before, more = [], 0, True
    while more:
        messages, more = db.chat_messages_page(thread_id, 2, before=before)
        seen = _ids(messages) + seen
        before = messages[0]["id"]
    assert seen == ids


def test_exact_fit_has_no_earlier_page():
    thread_id, ids = _thread(2)
    messages, more = db.chat_messages_page(thread_id, 2)
    assert _ids(messages) == ids and not more


def test_include_reaches_back_to_an_old_message():
    thread_id, ids = _thread(6)
    messages, more = db.chat_messages_page(thread_id, 2, include=ids[1])
    assert _ids(messages) == ids[1:] and more


def test_include_inside_the_page_changes_nothing():
    thread_id, ids = _thread(4)
    messages, more = db.chat_messages_page(thread_id, 2, include=ids[-1])
    assert _ids(messages) == ids[-2:] and more


def test_other_threads_stay_out():
    thread_id, ids = _thread(2)
    other, other_ids = _thread(3)
    messages, more = db.chat_messages_page(thread_id, 10, include=other_ids[0])
    assert _ids(messages) == ids and not more
