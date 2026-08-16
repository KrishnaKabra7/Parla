from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    client.post("/login", data={"name": "tester"})


def test_study_shows_a_card(client: TestClient):
    _login(client)
    r = client.get("/study")
    assert r.status_code == 200
    assert 'id="audio"' in r.text
    assert 'name="sentence_id"' in r.text


def test_review_shows_reveal_with_diff(client: TestClient):
    _login(client)
    client.get("/study")
    # Look up sentence_id from DB
    from app import db
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]
    truth = conn.execute("SELECT text FROM sentences WHERE id = ?", (sid,)).fetchone()["text"]
    conn.close()
    # Type the first word of the truth (should show as matched)
    first_word = truth.split()[0].strip(".,!?").lower()
    r = client.post("/review", data={"sentence_id": sid, "typed": first_word})
    assert r.status_code == 200
    assert "grade" in r.text.lower()
    assert "good" in r.text  # css class present
    # 4 grade buttons
    for label in ["Again", "Hard", "Good", "Easy"]:
        assert label in r.text


def test_grade_updates_srs_and_returns_next_card(client: TestClient):
    _login(client)
    client.get("/study")
    from app import db
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]
    conn.close()
    r = client.post("/grade", data={"sentence_id": sid, "grade": 4, "typed": ""})
    assert r.status_code == 200
    # Should return a _card partial (either new card or "all caught up")
    assert 'id="card"' in r.text
    # SM-2 state should be updated
    conn = db.connect()
    uw = conn.execute("SELECT ef, interval_days, due_at, status FROM user_words").fetchone()
    review_row = conn.execute("SELECT grade FROM reviews").fetchone()
    conn.close()
    assert uw["interval_days"] == 1.0
    assert uw["due_at"] is not None
    assert review_row["grade"] == 4


def test_full_loop_generates_only_once_per_word(client: TestClient):
    _login(client)
    client.get("/study")
    calls_after_first = client.fake_client.messages.parse.call_count  # type: ignore[attr-defined]
    # Grade Good → get next card (may be same word or next)
    from app import db
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]
    word_id_first = conn.execute("SELECT word_id FROM sentences WHERE id = ?", (sid,)).fetchone()["word_id"]
    conn.close()
    client.post("/grade", data={"sentence_id": sid, "grade": 4, "typed": ""})
    # If next card is a NEW word, Anthropic is called once more. If same word, 0 more.
    conn = db.connect()
    sentence_counts = conn.execute("SELECT word_id, COUNT(*) AS n FROM sentences GROUP BY word_id").fetchall()
    conn.close()
    # Each word that has been surfaced should have exactly 3 sentences (never 6)
    for row in sentence_counts:
        assert row["n"] == 3


def test_words_seeded_from_freq_file(client: TestClient):
    _login(client)
    from app import db
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM words WHERE lang='ru'").fetchone()["n"]
    conn.close()
    assert n > 10  # seeded from data/ru_freq.txt (currently ~40 lemmas)


def test_prefetch_generates_next_word_after_study(client: TestClient):
    client.post("/login", data={"name": "tester"}, follow_redirects=False)
    client.get("/study")
    from app import db
    conn = db.connect()
    n = conn.execute("SELECT COUNT(DISTINCT word_id) AS n FROM sentences").fetchone()["n"]
    conn.close()
    # 1 for the served card + 1 prefetched in the background
    assert n == 2


def test_prefetch_makes_grade_a_cache_hit(client: TestClient):
    client.post("/login", data={"name": "tester"}, follow_redirects=False)
    client.get("/study")
    # After GET /study: 2 Anthropic calls (current + prefetch of next)
    assert client.fake_client.messages.parse.call_count == 2  # type: ignore[attr-defined]

    from app import db
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]
    conn.close()

    client.post("/grade", data={"sentence_id": sid, "grade": 4, "typed": ""})
    # /grade served the next card WITHOUT a new API call (cache hit from prefetch),
    # then prefetched the word after that (+1 call)
    assert client.fake_client.messages.parse.call_count == 3  # type: ignore[attr-defined]
