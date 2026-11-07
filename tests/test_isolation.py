"""One account can never see or change another account's data."""

from conftest import new_user

from cvmaker import people, settings


def test_people_and_files_are_per_account():
    a, acc_a = new_user()
    b, acc_b = new_user()
    r = a.post("/people/add", data={"name": "Anna Secret"})
    assert r.status_code == 303
    # A sees Anna, B doesn't
    assert "Anna Secret" in a.get("/people").text
    assert "Anna Secret" not in b.get("/people").text
    # B can't switch to A's person even by setting the cookie to its id
    with people.in_account(acc_a["id"]):
        anna_id = next(p.id for p in people.load() if p.name == "Anna Secret")
    b.cookies.set(people.COOKIE, anna_id)
    assert "Anna Secret" not in b.get("/profile").text
    r = b.post("/people/switch", data={"person": anna_id})
    assert r.headers["location"] == "/people"
    # the folders are apart
    root_a = settings.DATA_ROOT / "accounts" / acc_a["id"]
    root_b = settings.DATA_ROOT / "accounts" / acc_b["id"]
    assert (root_a / "people" / anna_id).exists()
    assert not (root_b / "people" / anna_id).exists()


def test_editor_data_is_per_account():
    a, _ = new_user()
    b, _ = new_user()
    a.post("/editor/start", data={"starter": "java"})
    doc = a.get("/editor/api/data").json()
    doc["content"]["name"] = "Alice Only"
    assert a.post("/editor/api/data", json=doc).json()["ok"]
    assert a.get("/editor/api/data").json()["content"]["name"] == "Alice Only"
    assert b.get("/editor/api/data").json()["content"]["name"] != "Alice Only"


def test_cookie_path_tricks_are_refused():
    b, _ = new_user()
    for bad in ["../../etc", "..", "a/b", "../accounts/x"]:
        b.cookies.set(people.COOKIE, bad)
        assert b.get("/profile").status_code == 200  # falls back to the account's own default person


def test_files_of_other_accounts_cannot_be_fetched():
    a, _ = new_user()
    b, _ = new_user()
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "PNG")
    r = a.post("/editor/api/upload?name=logo.png", content=buf.getvalue())
    path = r.json()["path"]
    assert a.get(path).status_code == 200
    assert b.get(path).status_code == 404
