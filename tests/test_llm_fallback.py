"""When the API refuses a schema as too large for strict structured output, CVMaker asks for plain JSON and
validates it itself."""

import json
from types import SimpleNamespace

import anthropic
import httpx
from conftest import new_user
from pydantic import BaseModel

from cvmaker import llm, people


class Big(BaseModel):
    name: str
    skills: list[str]


def _bad_request():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error",
                                       "message": "The compiled grammar is too large, which would cause performance issues."}}
    return anthropic.BadRequestError("The compiled grammar is too large, which would cause performance issues.",
                                     response=httpx.Response(400, request=req, json=body), body=body)


def _reply(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


class _Stream:
    """What `client.beta.messages.stream(...)` returns: a context manager whose stream gives the final message.
    The real SDK sends the request (and raises API errors) on entering it."""

    def __init__(self, make):
        self.make = make

    def __enter__(self):
        return SimpleNamespace(get_final_message=self.make())

    def __exit__(self, *exc):
        return False


def _fake(replies):
    calls = {"strict": 0, "json": []}

    def stream(**kw):
        if "output_format" in kw:
            calls["strict"] += 1

            def refuse():
                raise _bad_request()
            return _Stream(refuse)
        calls["json"].append(kw)
        message = _reply(next(replies))
        return _Stream(lambda: lambda: message)

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream))), calls


def test_falls_back_to_plain_json_and_retries_once(monkeypatch):
    replies = iter(['{"name": "Ann"}',  # missing "skills" -> retry
                    '```json\n{"name": "Ann", "skills": ["Java"]}\n```',
                    '{"name": "Bo", "skills": []}'])
    fake, calls = _fake(replies)
    monkeypatch.setattr(llm, "_client_api", lambda: fake)
    llm._TOO_LARGE.discard(Big)
    _, account = new_user()
    with people.in_account(account["id"]):
        out = llm.ask("Extract.", "CV text", Big)
    assert out == Big(name="Ann", skills=["Java"])
    assert calls["strict"] == 1 and len(calls["json"]) == 2
    assert "JSON schema" in calls["json"][0]["system"]
    assert "doesn't match" in json.dumps(calls["json"][1]["messages"][-1])
    # Large outputs (a whole CV profile) need streaming: the SDK refuses this max_tokens without it.
    assert calls["json"][0]["max_tokens"] > 21_333
    # next time it goes straight to plain JSON
    with people.in_account(account["id"]):
        assert llm.ask("Extract.", "x", Big).name == "Bo"
    assert calls["strict"] == 1
