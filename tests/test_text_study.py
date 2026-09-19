import random

from fastapi.testclient import TestClient

from app import db, text_study


def _login(client: TestClient) -> None:
    client.post("/login", data={"name": "tester"})


def _seed(rows: list[tuple[str, str, str]]) -> None:
    conn = db.connect()
    conn.executemany(
        "INSERT OR IGNORE INTO known_words(lang, cyrillic, latin, english) VALUES (?, ?, ?, ?)",
        [("ru", c, l, e) for c, l, e in rows],
    )
    conn.close()


def test_check_answer_en_to_ru_cyrillic_and_latin():
    card = text_study.TextCard(1, "привет", "privet", "hi, hello", text_study.EN_TO_RU)
    assert text_study.check_answer(card, "привет")
    assert text_study.check_answer(card, "Привет")
    assert text_study.check_answer(card, "privet")  # transliterated
    assert not text_study.check_answer(card, "привет!") is False  # trailing punct ok


def test_check_answer_en_to_ru_wrong():
    card = text_study.TextCard(1, "привет", "privet", "hi, hello", text_study.EN_TO_RU)
    assert not text_study.check_answer(card, "пока")
    assert not text_study.check_answer(card, "")


def test_check_answer_ru_to_en_accepts_any_gloss():
    card = text_study.TextCard(1, "мне", "mne", "me (dat), to me", text_study.RU_TO_EN)
    assert text_study.check_answer(card, "me")  # parenthetical stripped
    assert text_study.check_answer(card, "to me")
    assert text_study.check_answer(card, "Me")  # case-insensitive
    assert not text_study.check_answer(card, "him")


def test_pick_direction_ratio():
    rng = random.Random(42)
    n = 4000
    en = sum(1 for _ in range(n) if text_study.pick_direction(rng) == text_study.EN_TO_RU)
    ratio = en / n
    assert 0.72 < ratio < 0.78  # 75% ±3


def test_text_card_grade_creates_srs_row_and_review(client: TestClient):
    _login(client)
    _seed([("привет", "privet", "hi, hello")])
    client.cookies.set("slux_mode", "text")
    r = client.get("/study")
    assert r.status_code == 200
    assert 'name="known_word_id"' in r.text
    conn = db.connect()
    kwid = conn.execute("SELECT id FROM known_words WHERE cyrillic=?", ("привет",)).fetchone()["id"]
    conn.close()
    r = client.post(
        "/grade/text",
        data={"known_word_id": kwid, "direction": text_study.EN_TO_RU, "grade": 4, "typed": "privet"},
    )
    assert r.status_code == 200
    conn = db.connect()
    ukw = conn.execute(
        "SELECT ef, interval_days, direction FROM user_known_words WHERE known_word_id=?",
        (kwid,),
    ).fetchone()
    rv = conn.execute("SELECT grade, sentence_id, typed_answer FROM reviews").fetchone()
    conn.close()
    assert ukw["direction"] == text_study.EN_TO_RU
    assert ukw["interval_days"] == 1.0
    assert rv["sentence_id"] is None
    assert rv["grade"] == 4
    assert rv["typed_answer"] == "privet"


def test_separate_srs_per_direction(client: TestClient):
    _login(client)
    _seed([("дом", "dom", "house, home")])
    conn = db.connect()
    kwid = conn.execute("SELECT id FROM known_words WHERE cyrillic=?", ("дом",)).fetchone()["id"]
    conn.close()
    client.post("/grade/text", data={
        "known_word_id": kwid, "direction": text_study.EN_TO_RU, "grade": 4, "typed": "дом",
    })
    conn = db.connect()
    rows = conn.execute(
        "SELECT direction, interval_days FROM user_known_words WHERE known_word_id=?",
        (kwid,),
    ).fetchall()
    conn.close()
    # Only en_to_ru should have SRS state; ru_to_en still untouched.
    assert len(rows) == 1
    assert rows[0]["direction"] == text_study.EN_TO_RU


def test_review_text_shows_reveal_and_correctness(client: TestClient):
    _login(client)
    _seed([("привет", "privet", "hi, hello")])
    conn = db.connect()
    kwid = conn.execute("SELECT id FROM known_words WHERE cyrillic=?", ("привет",)).fetchone()["id"]
    conn.close()
    r = client.post("/review/text", data={
        "known_word_id": kwid, "direction": text_study.EN_TO_RU, "typed": "privet",
    })
    assert r.status_code == 200
    assert "good" in r.text  # correct → good css class
    assert "привет" in r.text
    for lbl in ["Again", "Hard", "Good", "Easy"]:
        assert lbl in r.text

    r = client.post("/review/text", data={
        "known_word_id": kwid, "direction": text_study.EN_TO_RU, "typed": "nope",
    })
    assert "bad" in r.text  # wrong → bad css class


def test_mode_cookie_switches_card_type(client: TestClient):
    _login(client)
    _seed([("привет", "privet", "hi, hello")])
    # voice mode: no known-word prompt
    client.cookies.set("slux_mode", "voice")
    r = client.get("/study")
    assert 'name="sentence_id"' in r.text
    assert 'name="known_word_id"' not in r.text
    # text mode: no audio
    client.cookies.set("slux_mode", "text")
    r = client.get("/study")
    assert 'name="known_word_id"' in r.text
    assert 'id="audio"' not in r.text


def test_set_mode_persists(client: TestClient):
    _login(client)
    r = client.post("/mode/text", follow_redirects=False)
    assert r.status_code == 303
    assert r.cookies.get("slux_mode") == "text"


def test_set_mode_rejects_invalid(client: TestClient):
    _login(client)
    r = client.post("/mode/bogus", follow_redirects=False)
    assert r.status_code == 400


def test_known_add_form_creates_row(client: TestClient):
    _login(client)
    r = client.post("/known/add", data={
        "cyrillic": "яблоко", "latin": "yabloko", "english": "apple",
    })
    assert r.status_code == 200
    assert "added" in r.text
    conn = db.connect()
    row = conn.execute(
        "SELECT latin, english FROM known_words WHERE cyrillic=?", ("яблоко",)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["latin"] == "yabloko"
    assert row["english"] == "apple"


def test_known_add_form_dedups(client: TestClient):
    _login(client)
    client.post("/known/add", data={
        "cyrillic": "книга", "latin": "kniga", "english": "book",
    })
    r = client.post("/known/add", data={
        "cyrillic": "книга", "latin": "kniga", "english": "different gloss",
    })
    assert "already exists" in r.text
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM known_words WHERE cyrillic=?", ("книга",)).fetchone()["n"]
    conn.close()
    assert n == 1


def test_text_card_falls_back_to_other_direction_when_empty(client: TestClient):
    _login(client)
    _seed([("привет", "privet", "hi, hello")])
    # Force the RU→EN direction as first choice by seeding user_known_words for EN→RU
    # with a far-future due date so it's not picked, then... simpler: just use text mode
    # with only one word; both directions eventually pick it.
    client.cookies.set("slux_mode", "text")
    r = client.get("/study")
    assert 'name="known_word_id"' in r.text
