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


def test_falls_back_to_plain_json_and_retries_once(monkeypatch):
    calls = {"parse": 0, "create": []}
    replies = iter(['{"name": "Ann"}',  # missing "skills" -> retry
                    '```json\n{"name": "Ann", "skills": ["Java"]}\n```'])

    def parse(**kw):
        calls["parse"] += 1
        raise _bad_request()

    def create(**kw):
        calls["create"].append(kw)
        return _reply(next(replies))

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse, create=create)))
    monkeypatch.setattr(llm, "_client_api", lambda: fake)
    llm._TOO_LARGE.discard(Big)
    _, account = new_user()
    with people.in_account(account["id"]):
        out = llm.ask("Extract.", "CV text", Big)
    assert out == Big(name="Ann", skills=["Java"])
    assert calls["parse"] == 1 and len(calls["create"]) == 2
    assert "JSON schema" in calls["create"][0]["system"]
    assert "doesn't match" in json.dumps(calls["create"][1]["messages"][-1])
    # next time it goes straight to plain JSON
    replies2 = iter(['{"name": "Bo", "skills": []}'])
    fake.beta.messages.create = lambda **kw: _reply(next(replies2))
    with people.in_account(account["id"]):
        assert llm.ask("Extract.", "x", Big).name == "Bo"
    assert calls["parse"] == 1
