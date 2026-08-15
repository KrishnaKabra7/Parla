from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    client.post("/login", data={"name": "tester"})


def _seed_activity(client: TestClient) -> None:
    """Do one full card cycle so user_words and reviews have data."""
    _login(client)
    client.get("/study")
    from app import db
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]
    conn.close()
    client.post("/grade", data={"sentence_id": sid, "grade": 4, "typed": "и"})


def test_words_page_lists_seen_words(client: TestClient):
    _seed_activity(client)
    r = client.get("/words")
    assert r.status_code == 200
    assert "<table" in r.text
    # The first-seen word (rank #1 in ru_freq.txt) should appear as a row
    from app import db
    conn = db.connect()
    seen = conn.execute(
        "SELECT w.lemma FROM user_words uw JOIN words w ON w.id = uw.word_id LIMIT 1"
    ).fetchone()
    conn.close()
    assert seen is not None
    assert f">{seen['lemma']}<" in r.text


def test_words_search_partial_returns_only_rows(client: TestClient):
    _seed_activity(client)
    r = client.get("/words?q=и", headers={"HX-Request": "true"})
    assert r.status_code == 200
    # Partial should NOT include the full page chrome
    assert "<html" not in r.text
    assert "<tr" in r.text


def test_word_detail_shows_cached_sentences(client: TestClient):
    _seed_activity(client)
    from app import db
    conn = db.connect()
    wid = conn.execute("SELECT word_id FROM user_words LIMIT 1").fetchone()["word_id"]
    conn.close()
    r = client.get(f"/words/{wid}")
    assert r.status_code == 200
    assert "<audio" in r.text
    # Should show 3 cached sentences
    assert r.text.count("<audio") == 3


def test_stats_page_renders_svg_and_counts(client: TestClient):
    _seed_activity(client)
    r = client.get("/stats")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "known words" in r.text
    assert "streak" in r.text
    # 30 bars in the chart
    assert r.text.count("<rect") == 30


def test_stats_streak_is_one_after_a_review_today(client: TestClient):
    _seed_activity(client)
    r = client.get("/stats")
    assert "streak: <strong>1</strong>" in r.text


def test_words_and_stats_require_auth(client: TestClient):
    assert client.get("/words").status_code == 401
    assert client.get("/stats").status_code == 401
    assert client.get("/words/1").status_code == 401
