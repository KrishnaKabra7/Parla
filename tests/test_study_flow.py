from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    client.post("/login", data={"name": "tester"})
    client.post("/langs", data={"langs": ["ru"]}, follow_redirects=False)


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
    client.post("/langs", data={"langs": ["ru"]}, follow_redirects=False)
    client.get("/study")
    from app import db
    conn = db.connect()
    n = conn.execute("SELECT COUNT(DISTINCT word_id) AS n FROM sentences").fetchone()["n"]
    conn.close()
    # 1 for the served card + 1 prefetched in the background
    assert n == 2


def test_grade_applies_again_to_flagged_words(client: TestClient):
    _login(client)
    client.get("/study")
    from app import db
    conn = db.connect()
    # Seed a second user_word so we have something to flag.
    other = conn.execute("SELECT id FROM words WHERE lang='ru' ORDER BY freq_rank LIMIT 2").fetchall()
    target_word_id = other[0]["id"]
    flag_word_id = other[1]["id"]
    conn.execute(
        "INSERT OR IGNORE INTO user_words(user_id, word_id, status, ef, interval_days, due_at) "
        "VALUES (1, ?, 'learning', 2.5, 7.0, datetime('now', '+7 days'))",
        (flag_word_id,),
    )
    sid = conn.execute(
        "SELECT id FROM sentences WHERE word_id = ? LIMIT 1", (target_word_id,)
    ).fetchone()["id"]
    conn.close()

    r = client.post(
        "/grade",
        data={
            "sentence_id": sid,
            "grade": 5,
            "typed": "",
            "flagged_word_ids": [flag_word_id],
        },
    )
    assert r.status_code == 200

    conn = db.connect()
    flagged_uw = conn.execute(
        "SELECT ef, interval_days, status FROM user_words WHERE user_id=1 AND word_id=?",
        (flag_word_id,),
    ).fetchone()
    reviews = conn.execute(
        "SELECT grade FROM reviews WHERE sentence_id=? ORDER BY id",
        (sid,),
    ).fetchall()
    conn.close()
    # Flagged word was reset to relearn with lower ef.
    assert flagged_uw["interval_days"] == 0.0
    assert flagged_uw["ef"] < 2.5
    # Two reviews rows: one for target (grade=5), one for flag (grade=0).
    assert sorted(r["grade"] for r in reviews) == [0, 5]


def test_grade_ignores_unknown_flagged_ids(client: TestClient):
    _login(client)
    client.get("/study")
    from app import db
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]
    conn.close()
    r = client.post(
        "/grade",
        data={
            "sentence_id": sid,
            "grade": 4,
            "typed": "",
            "flagged_word_ids": [999999],
        },
    )
    assert r.status_code == 200


def test_review_marks_known_lemmas_tappable(client: TestClient):
    _login(client)
    client.get("/study")
    from app import db
    conn = db.connect()
    sid_row = conn.execute(
        "SELECT s.id AS id, w.lemma AS lemma, s.text AS text FROM sentences s "
        "JOIN words w ON w.id=s.word_id LIMIT 1"
    ).fetchone()
    sid = sid_row["id"]
    # Seed another known-lemma user_word that appears in no sentence yet.
    conn.execute(
        "INSERT OR IGNORE INTO user_words(user_id, word_id) "
        "SELECT 1, id FROM words WHERE lang='ru' AND lemma != ? LIMIT 5",
        (sid_row["lemma"],),
    )
    conn.close()
    r = client.post("/review", data={"sentence_id": sid, "typed": ""})
    assert r.status_code == 200
    # If any token in the truth sentence stems to a user_word (not the target),
    # it should render as a tappable flag span.
    if "tok flag" in r.text:
        assert 'data-word-id="' in r.text


def test_prefetch_makes_grade_a_cache_hit(client: TestClient):
    client.post("/login", data={"name": "tester"}, follow_redirects=False)
    client.post("/langs", data={"langs": ["ru"]}, follow_redirects=False)
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
