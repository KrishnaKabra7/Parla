from fastapi.testclient import TestClient


def test_root_redirects_to_login_when_not_authed(client: TestClient):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_study_requires_auth(client: TestClient):
    r = client.get("/study")
    assert r.status_code == 401


def test_login_creates_user_and_redirects_to_study(client: TestClient):
    r = client.post("/login", data={"name": "alice"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/study"
    assert "slux_session" in r.cookies


def test_study_accessible_after_login(client: TestClient):
    client.post("/login", data={"name": "alice"})
    r = client.get("/study")
    assert r.status_code == 200
    assert 'id="card"' in r.text


def test_same_name_returns_same_user(client: TestClient):
    from app import db
    client.post("/login", data={"name": "bob"})
    client.post("/logout")
    client.post("/login", data={"name": "bob"})
    conn = db.connect()
    rows = conn.execute("SELECT COUNT(*) AS n FROM users WHERE name='bob'").fetchone()
    conn.close()
    assert rows["n"] == 1


def test_blank_name_bounces_back_to_login(client: TestClient):
    r = client.post("/login", data={"name": "   "}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert "slux_session" not in r.cookies


def test_logout_clears_cookie(client: TestClient):
    client.post("/login", data={"name": "alice"})
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    # subsequent /study should 401 again
    r2 = client.get("/study")
    assert r2.status_code == 401
