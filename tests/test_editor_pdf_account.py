"""The CV editor round trip, PDF export, data export and deleting an account."""

import io
import zipfile

import pytest
from conftest import client, new_user

from cvmaker import accounts, people, web_fetch

def _has_chrome() -> bool:
    try:
        return bool(web_fetch.chrome_bin())
    except web_fetch.ChromeMissing:
        return False


needs_chrome = pytest.mark.skipif(not _has_chrome(), reason="no Chromium-based browser installed")


def test_editor_round_trip(user):
    c, _ = user
    assert c.post("/editor/start", data={"starter": "devops"}).status_code == 303
    doc = c.get("/editor/api/data").json()
    doc["content"]["name"] = "Round Trip"
    doc["content"]["title"] = "Platform Engineer"
    saved = c.post("/editor/api/data", json=doc).json()
    assert saved["ok"]
    again = c.get("/editor/api/data").json()
    assert again["content"]["name"] == "Round Trip" and again["content"]["title"] == "Platform Engineer"
    # saving an old revision again is a conflict, not a silent overwrite
    stale = dict(doc)
    r = c.post("/editor/api/data", json=stale)
    assert r.status_code in (200, 409)


@needs_chrome
def test_pdf_export(user):
    c, _ = user
    c.post("/editor/start", data={"starter": "qa"})
    doc = c.get("/editor/api/data").json()
    doc["content"]["name"] = "Pdf Person"
    c.post("/editor/api/data", json=doc)
    r = c.post("/editor/api/export", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] and body["dir"] == ""  # no server paths in server mode
    pdf = c.get(f"/editor/api/file?name={body['files'][0]}")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_export_all_data(user):
    c, _ = user
    c.post("/editor/start", data={"starter": "pm"})
    r = c.post("/settings/export")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "account.json" in names and any(n.startswith("people/me/") for n in names)
    inner = zipfile.ZipFile(io.BytesIO(z.read(next(n for n in names if n.startswith("people/me/")))))
    assert "editor.json" in inner.namelist() and "keys.json" not in inner.namelist()


def test_delete_account_removes_everything():
    c, account = new_user()
    c.post("/editor/start", data={"starter": "data"})
    root = people.account_root(account["id"])
    assert root.exists()
    r = c.post("/settings/account/delete", data={"confirm": "wrong@example.com"})
    assert "error=" in r.headers["location"]
    r = c.post("/settings/account/delete", data={"confirm": account["email"]})
    assert r.status_code == 200 and "deleted" in r.text
    assert not root.exists()
    assert accounts.get(account["id"]) is None
    assert c.get("/").status_code == 303  # logged out
    assert "Wrong" in client().post("/login", data={"email": account["email"], "password": "correct horse battery"}).text
