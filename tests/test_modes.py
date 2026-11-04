import random
import sqlite3

from fastapi.testclient import TestClient

from app import db, srs
from app.langs import candidates


def _login(client: TestClient, mode: str, length: str) -> int:
    client.post("/login", data={"name": "moder"})
    client.post("/langs", data={"langs": ["ru"]}, follow_redirects=False)
    client.post(f"/mode/{mode}")
    client.post(f"/length/{length}")
    conn = db.connect()
    uid = conn.execute("SELECT id FROM users WHERE name = 'moder'").fetchone()["id"]
    conn.close()
    return uid


def _seed_known(target="привет", romanized="privet", english="hi"):
    conn = db.connect()
    conn.execute("DELETE FROM known_words")
    conn.execute(
        "INSERT INTO known_words(lang, target, romanized, english) VALUES ('ru', ?, ?, ?)",
        (target, romanized, english),
    )
    kwid = conn.execute("SELECT id FROM known_words").fetchone()["id"]
    conn.close()
    return kwid


def test_candidates_put_most_due_first():
    due = {("text", "ru"): 3, ("voice", "de"): 7}
    for seed in range(20):
        got = candidates("mixed", ["ru", "de"], due, random.Random(seed))
        assert got[:2] == [("voice", "de"), ("text", "ru")]


def test_use_sentence_by_length_and_maturity():
    assert srs.use_sentence("sentence", None)
    assert not srs.use_sentence("word", "known")
    assert not srs.use_sentence("mixed", None)
    assert not srs.use_sentence("mixed", "learning")
    assert srs.use_sentence("mixed", "known")


def test_prefs_default_and_validation(tmp_path):
    conn = db.connect(tmp_path / "p.db")
    db.init_schema(conn)
    uid = conn.execute("INSERT INTO users(name) VALUES ('x') RETURNING id").fetchone()["id"]
    assert db.get_prefs(conn, uid) == {"mode": "mixed", "length": "mixed"}
    db.set_pref(conn, uid, "length", "word")
    db.set_pref(conn, uid, "mode", "voice")
    assert db.get_prefs(conn, uid) == {"mode": "voice", "length": "word"}
    try:
        db.set_pref(conn, uid, "length", "paragraph")
        assert False
    except ValueError:
        pass
    conn.close()


def test_migration_adds_length_pref(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT);
        CREATE TABLE user_prefs (user_id INTEGER PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'mixed');
        INSERT INTO users(id, name) VALUES (1, 'old');
        INSERT INTO user_prefs VALUES (1, 'text');
        """
    )
    raw.close()
    conn = db.connect(path)
    db.init_schema(conn)
    assert db.get_prefs(conn, 1) == {"mode": "text", "length": "mixed"}
    conn.close()


def test_voice_word_card_plays_the_word_and_grades_it(client: TestClient):
    uid = _login(client, "voice", "word")
    r = client.get("/study")
    assert 'name="word_id"' in r.text
    assert 'name="sentence_id"' not in r.text
    conn = db.connect()
    word_id, lemma = conn.execute(
        "SELECT w.id, w.lemma FROM user_words uw JOIN words w ON w.id = uw.word_id WHERE uw.user_id = ?", (uid,)
    ).fetchone()
    conn.close()

    r = client.post("/review", data={"word_id": word_id, "typed": lemma})
    assert lemma in r.text
    assert "tok good target" in r.text
    assert 'name="word_id"' in r.text

    client.post("/grade", data={"word_id": word_id, "grade": 4, "typed": lemma})
    conn = db.connect()
    uw = conn.execute("SELECT interval_days FROM user_words WHERE user_id = ? AND word_id = ?", (uid, word_id)).fetchone()
    rev = conn.execute("SELECT sentence_id FROM reviews WHERE user_id = ?", (uid,)).fetchone()
    conn.close()
    assert uw["interval_days"] == 1.0
    assert rev["sentence_id"] is None


def test_mixed_length_uses_sentences_once_word_is_known(client: TestClient):
    uid = _login(client, "voice", "mixed")
    r = client.get("/study")
    assert 'name="word_id"' in r.text
    conn = db.connect()
    conn.execute(
        "UPDATE user_words SET status = 'known', interval_days = 5, due_at = '2000-01-01' WHERE user_id = ?", (uid,)
    )
    conn.close()
    r = client.get("/study")
    assert 'name="sentence_id"' in r.text


def test_text_sentence_card_is_generated_and_self_graded(client: TestClient):
    uid = _login(client, "text", "sentence")
    kwid = _seed_known()
    r = client.get("/study")
    assert 'name="known_sentence_id"' in r.text
    system = client.fake_client.messages.parse.call_args.kwargs["system"][0]["text"]
    assert "translation-practice" in system and "Russian" in system

    conn = db.connect()
    ks = conn.execute("SELECT id, text, gloss_en FROM known_sentences WHERE known_word_id = ?", (kwid,)).fetchall()
    conn.close()
    assert len(ks) == 3
    direction = "en_to_tl" if any(s["gloss_en"] in r.text for s in ks) else "tl_to_en"
    shown = next(s for s in ks if s["gloss_en"] in r.text or s["text"] in r.text)

    r = client.post(
        "/review/text",
        data={"known_word_id": kwid, "direction": direction, "known_sentence_id": shown["id"], "typed": "whatever"},
    )
    assert shown["gloss_en"] in r.text
    assert "bad" not in r.text

    client.post("/grade/text", data={"known_word_id": kwid, "direction": direction, "grade": 4})
    conn = db.connect()
    row = conn.execute(
        "SELECT interval_days FROM user_known_words WHERE user_id = ? AND known_word_id = ? AND direction = ?",
        (uid, kwid, direction),
    ).fetchone()
    conn.close()
    assert row["interval_days"] == 1.0


def test_text_sentence_falls_back_to_word_when_generation_fails(client: TestClient):
    _login(client, "text", "sentence")
    _seed_known()
    client.fake_client.messages.parse.side_effect = Exception("boom")
    r = client.get("/study")
    assert 'name="known_word_id"' in r.text
    assert 'name="known_sentence_id"' not in r.text
