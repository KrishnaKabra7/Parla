import random
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, srs, study, text_study


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


@pytest.fixture()
def conn(client: TestClient):
    c = db.connect()
    c.execute("DELETE FROM known_words")
    yield c
    c.close()


def _user(conn, name="r") -> int:
    return conn.execute("INSERT INTO users(name) VALUES (?) RETURNING id", (name,)).fetchone()["id"]


def _word(conn, target, english="x", lang="de") -> int:
    return conn.execute(
        "INSERT INTO known_words(lang, target, romanized, english) VALUES (?, ?, '', ?) RETURNING id",
        (lang, target, english),
    ).fetchone()["id"]


def _grade_text(conn, uid, kw, direction, grade, **kwargs):
    srs.record_grade(conn, uid, grade, "user_known_words", {"known_word_id": kw, "direction": direction}, **kwargs)


def _grade_voice(conn, uid, wid, grade, **kwargs):
    srs.record_grade(conn, uid, grade, "user_words", {"word_id": wid}, **kwargs)


def _grade_other(conn, uid, n):
    for _ in range(n):
        conn.execute("INSERT INTO reviews(user_id, sentence_id, grade) VALUES (?, NULL, 4)", (uid,))


def test_text_card_due_minutes_ago_is_picked(conn):
    uid = _user(conn)
    kw = _word(conn, "ich")
    _word(conn, "sie")
    for d in text_study.DIRECTIONS:
        conn.execute(
            "INSERT INTO user_known_words(user_id, known_word_id, direction, interval_days, due_at) VALUES (?, ?, ?, 1, ?)",
            (uid, kw, d, _iso(timedelta(minutes=-5))),
        )
    card = text_study.pick_card(conn, uid, "de", random.Random(0))
    assert card.target == "ich"


def test_text_card_due_later_today_is_not_picked(conn):
    uid = _user(conn)
    kw = _word(conn, "ich")
    for d in text_study.DIRECTIONS:
        conn.execute(
            "INSERT INTO user_known_words(user_id, known_word_id, direction, interval_days, due_at) VALUES (?, ?, ?, 1, ?)",
            (uid, kw, d, _iso(timedelta(minutes=5))),
        )
    assert text_study.pick_card(conn, uid, "de", random.Random(0)) is None


def test_voice_word_due_minutes_ago_is_picked(conn):
    uid = _user(conn)
    wid = conn.execute("SELECT id FROM words WHERE lang='de' ORDER BY freq_rank DESC LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO user_words(user_id, word_id, interval_days, due_at) VALUES (?, ?, 1, ?)",
        (uid, wid, _iso(timedelta(minutes=-5))),
    )
    item = study.pick_next(conn, uid, "de")
    assert item.word_id == wid and not item.is_new


def test_again_comes_back_after_5_to_10_cards(conn):
    uid = _user(conn)
    kw = _word(conn, "ich", "I")
    for t in ("sie", "das", "du", "er", "es", "wir"):
        _word(conn, t)
    seen_at = []
    for seed in range(20):
        c = db.connect()
        c.execute("DELETE FROM user_known_words")
        c.execute("DELETE FROM reviews")
        _grade_text(c, uid, kw, text_study.EN_TO_TL, 0, rng=random.Random(seed))
        for n in range(1, 12):
            card = text_study.pick_card(c, uid, "de", random.Random(n))
            if (card.known_word_id, card.direction) == (kw, text_study.EN_TO_TL):
                seen_at.append(n)
                break
            _grade_other(c, uid, 1)
        c.close()
    assert seen_at and min(seen_at) >= 6 and max(seen_at) <= 11
    assert len(set(seen_at)) > 1


def test_again_card_is_not_served_immediately(conn):
    uid = _user(conn)
    kw = _word(conn, "ich")
    _word(conn, "sie")
    _grade_text(conn, uid, kw, text_study.EN_TO_TL, 0, rng=random.Random(0))
    for seed in range(20):
        card = text_study.pick_card(conn, uid, "de", random.Random(seed))
        assert (card.known_word_id, card.direction) != (kw, text_study.EN_TO_TL)


def test_ready_requeued_card_beats_other_due_cards(conn):
    uid = _user(conn)
    old = _word(conn, "sie")
    kw = _word(conn, "ich")
    conn.execute(
        "INSERT INTO user_known_words(user_id, known_word_id, direction, interval_days, due_at) VALUES (?, ?, 'tl_to_en', 3, ?)",
        (uid, old, _iso(timedelta(days=-3))),
    )
    _grade_text(conn, uid, kw, text_study.TL_TO_EN, 0, rng=random.Random(0))
    _grade_other(conn, uid, 10)
    for seed in range(10):
        card = text_study.pick_card(conn, uid, "de", random.Random(seed))
        assert (card.known_word_id, card.direction) == (kw, text_study.TL_TO_EN)


def test_good_after_again_schedules_tomorrow_and_clears_requeue(conn):
    uid = _user(conn)
    kw = _word(conn, "ich")
    _grade_text(conn, uid, kw, text_study.EN_TO_TL, 0)
    _grade_text(conn, uid, kw, text_study.EN_TO_TL, 4)
    row = conn.execute(
        "SELECT interval_days, requeue_after, due_at FROM user_known_words WHERE known_word_id = ?", (kw,)
    ).fetchone()
    assert row["interval_days"] == 1.0
    assert row["requeue_after"] is None
    due = datetime.fromisoformat(row["due_at"])
    assert timedelta(hours=23) < due - datetime.now(timezone.utc) < timedelta(hours=25)


def test_record_grade_logs_a_review(conn):
    uid = _user(conn)
    kw = _word(conn, "ich")
    _grade_text(conn, uid, kw, text_study.EN_TO_TL, 4, typed="ich")
    row = conn.execute("SELECT grade, typed_answer, sentence_id FROM reviews WHERE user_id = ?", (uid,)).fetchone()
    assert tuple(row) == (4, "ich", None)


def test_web_again_requeues(client: TestClient):
    client.post("/login", data={"name": "webby"})
    client.post("/langs", data={"langs": ["de"]}, follow_redirects=False)
    conn = db.connect()
    kw = conn.execute("SELECT id FROM known_words WHERE lang='de' AND target='ich'").fetchone()["id"]
    client.post("/grade/text", data={"known_word_id": kw, "direction": "en_to_tl", "grade": 0, "typed": "x"})
    row = conn.execute("SELECT interval_days, requeue_after FROM user_known_words WHERE known_word_id=?", (kw,)).fetchone()
    conn.close()
    assert row["interval_days"] == 0
    assert row["requeue_after"] is not None


def test_migration_adds_requeue_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE user_known_words (user_id INTEGER, known_word_id INTEGER, direction TEXT, status TEXT DEFAULT 'new',"
        " ef REAL DEFAULT 2.5, interval_days REAL DEFAULT 0, due_at TEXT, PRIMARY KEY (user_id, known_word_id, direction))"
    )
    raw.commit()
    raw.close()
    c = db.connect(path)
    db.init_schema(c)
    db.init_schema(c)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(user_known_words)")}
    c.close()
    assert "requeue_after" in cols


def test_again_card_comes_back_early_when_nothing_else_is_left(conn):
    uid = _user(conn)
    kw = _word(conn, "ich")
    _grade_text(conn, uid, kw, text_study.EN_TO_TL, 4)
    _grade_text(conn, uid, kw, text_study.TL_TO_EN, 0, rng=random.Random(0))
    card = text_study.pick_card(conn, uid, "de", random.Random(0))
    assert (card.known_word_id, card.direction) == (kw, text_study.TL_TO_EN)


def _new_word(conn, n=0) -> int:
    return conn.execute("SELECT id FROM words WHERE lang='de' ORDER BY freq_rank LIMIT 1 OFFSET ?", (n,)).fetchone()["id"]


def test_voice_again_comes_back_after_5_to_10_cards(conn):
    uid = _user(conn)
    wid = _new_word(conn)
    seen_at = []
    for seed in range(20):
        c = db.connect()
        c.execute("DELETE FROM user_words")
        c.execute("DELETE FROM reviews")
        _grade_voice(c, uid, wid, 0, rng=random.Random(seed))
        for n in range(1, 12):
            item = study.pick_next(c, uid, "de")
            if item.word_id == wid:
                seen_at.append(n)
                assert not item.is_new
                break
            _grade_voice(c, uid, item.word_id, 4)
        c.close()
    assert len(seen_at) == 20 and min(seen_at) >= 6 and max(seen_at) <= 11
    assert len(set(seen_at)) > 1


def test_voice_again_sets_zero_interval_and_good_after_is_tomorrow(conn):
    uid = _user(conn)
    wid = _new_word(conn)
    _grade_voice(conn, uid, wid, 0)
    row = conn.execute("SELECT interval_days, requeue_after FROM user_words WHERE word_id = ?", (wid,)).fetchone()
    assert row["interval_days"] == 0 and row["requeue_after"] is not None
    _grade_voice(conn, uid, wid, 4)
    row = conn.execute("SELECT interval_days, requeue_after FROM user_words WHERE word_id = ?", (wid,)).fetchone()
    assert row["interval_days"] == 1.0 and row["requeue_after"] is None


def test_hard_lowers_ef_for_voice_and_text(conn):
    uid = _user(conn)
    wid = _new_word(conn)
    kw = _word(conn, "ich")
    _grade_voice(conn, uid, wid, 3)
    _grade_text(conn, uid, kw, text_study.EN_TO_TL, 3)
    assert conn.execute("SELECT ef FROM user_words WHERE word_id = ?", (wid,)).fetchone()[0] < srs.DEFAULT_EF
    assert conn.execute("SELECT ef FROM user_known_words WHERE known_word_id = ?", (kw,)).fetchone()[0] < srs.DEFAULT_EF


def test_web_voice_again_requeues_word_and_flagged_words(client: TestClient):
    client.post("/login", data={"name": "voicey"})
    client.post("/langs", data={"langs": ["ru"]}, follow_redirects=False)
    client.cookies.set("slux_mode", "voice")
    client.get("/study")
    conn = db.connect()
    s1 = conn.execute("SELECT id, word_id FROM sentences ORDER BY id DESC LIMIT 1").fetchone()
    client.post("/grade", data={"sentence_id": s1["id"], "grade": 4})
    s2 = conn.execute("SELECT id, word_id FROM sentences ORDER BY id DESC LIMIT 1").fetchone()
    client.post("/grade", data={"sentence_id": s2["id"], "grade": 0, "flagged_word_ids": [s1["word_id"]]})
    rows = {r["word_id"]: r for r in conn.execute("SELECT word_id, interval_days, requeue_after FROM user_words")}
    conn.close()
    for wid in (s1["word_id"], s2["word_id"]):
        assert rows[wid]["interval_days"] == 0
        assert rows[wid]["requeue_after"] is not None


def test_migration_adds_requeue_column_to_user_words(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE user_words (user_id INTEGER, word_id INTEGER, status TEXT DEFAULT 'new', ef REAL DEFAULT 2.5,"
        " interval_days REAL DEFAULT 0, due_at TEXT, PRIMARY KEY (user_id, word_id))"
    )
    raw.commit()
    raw.close()
    c = db.connect(path)
    db.init_schema(c)
    db.init_schema(c)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(user_words)")}
    c.close()
    assert "requeue_after" in cols
