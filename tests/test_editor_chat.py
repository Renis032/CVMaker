"""The chat in the CV editor: Claude's answer, and a proposed change checked against the CV's shape."""

import json

import pytest
from conftest import new_user

from cvmaker import editor_routes, llm
from cvmaker.models import EditorChatReply


@pytest.fixture
def doc(monkeypatch):
    c, _ = new_user()
    c.post("/editor/start", data={"starter": "developer"})
    return c, c.get("/editor/api/data").json()["content"]


def fake_reply(monkeypatch, reply: EditorChatReply, seen: list):
    def ask(system, text, schema, effort="high", **kw):
        seen.append(text)
        return reply
    monkeypatch.setattr(llm, "ask", ask)


def test_suggestion_without_change(doc, monkeypatch):
    c, content = doc
    seen = []
    fake_reply(monkeypatch, EditorChatReply(reply="Cut the second project.", changes=[], new_content_json=""), seen)
    r = c.post("/editor/api/chat", json={"messages": [{"role": "user", "text": "What would you improve?"}],
                                         "content": content, "hidden": ["interests"], "version": "Main"})
    assert r.status_code == 200, r.text
    assert r.json() == {"reply": "Cut the second project.", "changes": [], "content": None}
    assert "What would you improve?" in seen[0] and "Hidden in this version" in seen[0]


def test_change_keeps_photo_and_logos(doc, monkeypatch):
    c, content = doc
    content["experience"][0]["logo"] = "/editor/uploads/acme.png"
    new = json.loads(json.dumps(content))
    new["summary"] = "Shorter summary."
    new["photo"] = "https://evil.example/x.png"
    new["experience"][0]["logo"] = ""
    new["projects"] = [{"id": "new-1", "title": "Payments simulator", "tech": "Java", "bullets": []}]
    fake_reply(monkeypatch, EditorChatReply(reply="Here's the change.", changes=["Shorter summary", "New project"],
                                            new_content_json=json.dumps(new)), [])
    r = c.post("/editor/api/chat", json={"messages": [{"role": "user", "text": "Shorten it"}], "content": content})
    body = r.json()
    assert body["changes"] == ["Shorter summary", "New project"]
    out = body["content"]
    assert out["summary"] == "Shorter summary." and out["projects"][0]["title"] == "Payments simulator"
    assert out["projects"][0]["tech"] == "(Java)"
    assert out["photo"] == content["photo"]
    assert out["experience"][0]["logo"] == "/editor/uploads/acme.png"


def test_bad_proposal_is_dropped():
    current = {"summary": "a", "experience": [], "photo": ""}
    assert editor_routes.proposed_content("not json", current) is None
    assert editor_routes.proposed_content(json.dumps({"experience": "oops"}), current) is None
    assert editor_routes.proposed_content(json.dumps({"summary": "a"}), current) is None  # nothing changed
    assert editor_routes.proposed_content(json.dumps({"summary": "b"}), current)["summary"] == "b"


def test_needs_a_question(doc):
    c, content = doc
    r = c.post("/editor/api/chat", json={"messages": [], "content": content})
    assert r.status_code == 400
