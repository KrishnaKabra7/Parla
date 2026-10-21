from fastapi.testclient import TestClient

from app import db


def _login(client: TestClient, langs: list[str] | None) -> None:
    client.post("/login", data={"name": "polyglot"})
    if langs is not None:
        client.post("/langs", data={"langs": langs}, follow_redirects=False)


def _uid() -> int:
    conn = db.connect()
    uid = conn.execute("SELECT id FROM users WHERE name = 'polyglot'").fetchone()["id"]
    conn.close()
    return uid


def _latest_sentence_lang() -> str:
    conn = db.connect()
    row = conn.execute(
        "SELECT w.lang FROM sentences s JOIN words w ON w.id = s.word_id ORDER BY s.id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row["lang"]


def test_study_redirects_to_picker_before_first_card(client: TestClient):
    _login(client, None)
    r = client.get("/study", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/langs"


def test_picker_lists_available_languages(client: TestClient):
    _login(client, None)
    r = client.get("/langs")
    assert r.status_code == 200
    assert 'value="ru"' in r.text and 'value="de"' in r.text
    assert "Russian" in r.text and "German" in r.text


def test_pick_languages_saves_and_redirects(client: TestClient):
    _login(client, None)
    r = client.post("/langs", data={"langs": ["de", "ru"]}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/study"
    conn = db.connect()
    assert db.get_langs(conn, _uid()) == ["ru", "de"]
    conn.close()


def test_pick_languages_rejects_empty_and_unknown(client: TestClient):
    _login(client, None)
    assert client.post("/langs", data={}).status_code == 400
    assert client.post("/langs", data={"langs": ["fr"]}).status_code == 400


def test_picker_requires_auth(client: TestClient):
    assert client.get("/langs").status_code == 401
    assert client.post("/langs", data={"langs": ["de"]}).status_code == 401


def test_german_only_voice_card_is_german_and_labelled(client: TestClient):
    _login(client, ["de"])
    client.cookies.set("slux_mode", "voice")
    r = client.get("/study")
    assert 'name="sentence_id"' in r.text
    assert "German" in r.text
    assert _latest_sentence_lang() == "de"


def test_german_only_text_card_asks_for_german(client: TestClient):
    _login(client, ["de"])
    client.cookies.set("slux_mode", "text")
    for _ in range(10):
        r = client.get("/study")
        assert 'name="known_word_id"' in r.text
        assert "answer in German" in r.text or "answer in English" in r.text
        assert "German" in r.text
        assert any(w in r.text for w in ("yes", "no", "ja", "nein"))


def test_german_reveal_has_no_translit_line(client: TestClient):
    _login(client, ["de"])
    client.get("/study")
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()
    r = client.post("/review", data={"sentence_id": sid, "typed": "x"})
    assert r.status_code == 200
    assert 'class="translit"' not in r.text


def test_russian_reveal_keeps_translit_line(client: TestClient):
    _login(client, ["ru"])
    client.get("/study")
    conn = db.connect()
    sid = conn.execute("SELECT id FROM sentences ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()
    r = client.post("/review", data={"sentence_id": sid, "typed": "x"})
    assert 'class="translit"' in r.text


def test_text_falls_through_to_other_language_when_one_is_exhausted(client: TestClient):
    _login(client, ["ru", "de"])
    uid = _uid()
    conn = db.connect()
    for row in conn.execute("SELECT id FROM known_words WHERE lang = 'de'").fetchall():
        for d in ("en_to_tl", "tl_to_en"):
            conn.execute(
                "INSERT INTO user_known_words(user_id, known_word_id, direction, due_at) VALUES (?, ?, ?, '2999-01-01')",
                (uid, row["id"], d),
            )
    conn.close()
    client.cookies.set("slux_mode", "text")
    for _ in range(10):
        r = client.get("/study")
        assert 'name="known_word_id"' in r.text
        assert "Russian" in r.text


def test_grade_text_serves_next_card_from_chosen_language(client: TestClient):
    _login(client, ["de"])
    client.cookies.set("slux_mode", "text")
    conn = db.connect()
    kwid = conn.execute("SELECT id FROM known_words WHERE lang='de' AND target='ja'").fetchone()["id"]
    conn.close()
    r = client.post("/grade/text", data={"known_word_id": kwid, "direction": "en_to_tl", "grade": 4, "typed": "ja"})
    assert r.status_code == 200
    assert "German" in r.text


def test_known_add_german_word(client: TestClient):
    _login(client, ["de"])
    r = client.post("/known/add", data={"lang": "de", "target": "Haus", "romanized": "", "english": "house"})
    assert "added Haus" in r.text
    conn = db.connect()
    row = conn.execute("SELECT romanized, english FROM known_words WHERE lang='de' AND target='Haus'").fetchone()
    conn.close()
    assert row["romanized"] == "" and row["english"] == "house"


def test_known_add_german_requires_target(client: TestClient):
    _login(client, ["de"])
    r = client.post("/known/add", data={"lang": "de", "target": "", "romanized": "haus", "english": "house"})
    assert "required" in r.text
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM known_words WHERE target='haus'").fetchone()[0] == 0
    conn.close()


def test_known_add_form_offers_language_choice(client: TestClient):
    _login(client, ["ru", "de"])
    r = client.get("/known/add")
    assert 'name="lang"' in r.text and 'value="de"' in r.text


def test_words_page_only_shows_chosen_languages(client: TestClient):
    _login(client, ["ru", "de"])
    client.cookies.set("slux_mode", "voice")
    for _ in range(6):
        client.get("/study")
    conn = db.connect()
    langs = {r["lang"] for r in conn.execute(
        "SELECT w.lang FROM user_words uw JOIN words w ON w.id = uw.word_id"
    )}
    conn.close()
    assert langs == {"ru", "de"}
    client.post("/langs", data={"langs": ["de"]})
    r = client.get("/words?kind=voice")
    assert "🇩🇪" in r.text
    assert "🇷🇺" not in r.text


def test_stats_show_known_counts_per_language(client: TestClient):
    _login(client, ["ru", "de"])
    r = client.get("/stats")
    assert "Russian" in r.text and "German" in r.text


def test_known_add_russian_latin_in_word_field_is_transliterated(client: TestClient):
    _login(client, ["ru"])
    client.post("/known/add", data={"lang": "ru", "target": "koshka", "romanized": "", "english": "cat"})
    conn = db.connect()
    row = conn.execute("SELECT target, romanized FROM known_words WHERE english='cat'").fetchone()
    conn.close()
    assert (row["target"], row["romanized"]) == ("кошка", "koshka")
