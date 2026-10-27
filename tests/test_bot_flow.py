from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def bot_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    import importlib
    from app import bot, bot_render, db, gen, main, tts
    importlib.reload(main)
    importlib.reload(bot)
    importlib.reload(bot_render)

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    monkeypatch.setattr(tts, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(bot, "AUDIO_DIR", str(audio_dir))

    async def fake_synth(text: str, voice: str) -> Path:
        p = audio_dir / f"{abs(hash(text + voice))}.mp3"
        p.write_bytes(b"\xff\xfb\x90\x00")
        return p

    monkeypatch.setattr("app.study.tts.synthesize", fake_synth)

    parsed = gen._GenResultOut(
        gloss_en="and",
        sentences=[
            gen._SentenceOut(text="Mama i papa.", gloss_en="Mom and dad."),
            gen._SentenceOut(text="Ya i ty.", gloss_en="You and I."),
            gen._SentenceOut(text="On i ona.", gloss_en="He and she."),
        ],
    )
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = MagicMock(parsed_output=parsed)
    monkeypatch.setattr(bot, "_anthropic", lambda: fake_client)

    with TestClient(main.app) as c:
        yield {
            "client": c,
            "bot_mod": bot,
            "render": bot_render,
            "db": db,
            "main_mod": main,
        }


def test_ensure_user_creates_and_resolves(bot_setup):
    bot = bot_setup["bot_mod"]
    uid1 = bot._ensure_user(42)
    uid2 = bot._ensure_user(42)
    assert uid1 == uid2
    uid3 = bot._ensure_user(99)
    assert uid3 != uid1


def test_mode_roundtrip(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(1)
    assert bot._mode(uid) == "mixed"
    bot._set_mode(uid, "voice")
    assert bot._mode(uid) == "voice"
    bot._set_mode(uid, "text")
    assert bot._mode(uid) == "text"


async def test_prepare_voice_returns_sentence(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(7)
    result = await bot._prepare_voice(uid, "ru")
    assert result is not None
    sentence_id, audio_path = result
    assert isinstance(sentence_id, int)
    assert audio_path.endswith(".mp3")


def test_prepare_text_returns_card(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(8)
    card = bot._prepare_text(uid, "ru")
    assert card is not None
    assert card.target and card.english


async def test_grade_callback_updates_voice_srs(bot_setup):
    bot = bot_setup["bot_mod"]
    db = bot_setup["db"]
    uid = bot._ensure_user(100)
    # Pre-create a voice card so we have a sentence_id to grade.
    result = await bot._prepare_voice(uid, "ru")
    assert result is not None
    sentence_id, _ = result
    # user_words row exists after _prepare_voice for a new word.

    cq = MagicMock()
    cq.data = f"g:5:v:{sentence_id}"
    cq.message = MagicMock()
    cq.message.chat.id = 100
    cq.message.edit_reply_markup = AsyncMock()
    cq.answer = AsyncMock()
    cq.bot = MagicMock()
    cq.bot.send_audio = AsyncMock()
    cq.bot.send_message = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()

    await bot.on_grade(cq, state)

    conn = db.connect()
    uw = conn.execute(
        "SELECT ef, interval_days FROM user_words WHERE user_id = ?", (uid,)
    ).fetchone()
    reviews = conn.execute(
        "SELECT grade FROM reviews WHERE user_id = ?", (uid,)
    ).fetchall()
    conn.close()
    assert uw is not None
    assert uw["interval_days"] == 1.0
    assert reviews[-1]["grade"] == 5


def test_voice_reveal_escapes_markdown(bot_setup):
    render = bot_setup["render"]
    from app.study import Token

    tokens = [
        Token(text="Mama", matched=True, is_target=False),
        Token(text="i", matched=False, is_target=True),
        Token(text="papa.", matched=False, is_target=False),
    ]
    out = render.voice_reveal(tokens, "and", "ru")
    assert "*Mama*" in out  # matched
    assert "__i__" in out  # target
    assert "~papa" in out  # unmatched (text has a '.' which gets escaped)
    assert "\\." in out  # MarkdownV2 dot escape present


def test_webhook_rejects_bad_secret(bot_setup, monkeypatch):
    # Set a secret so bot module believes we're configured.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    # Install a fake bot into the module so the webhook endpoint is wired.
    main = bot_setup["main_mod"]
    main._tg_bot = MagicMock()
    main._tg_dp = MagicMock()
    main._tg_dp.feed_webhook_update = AsyncMock()
    main._tg_webhook_secret = "s3cret"

    c = bot_setup["client"]
    r = c.post("/tg/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 401

    main._tg_bot = None
    main._tg_dp = None
    main._tg_webhook_secret = None


def _fake_bot() -> MagicMock:
    b = MagicMock()
    b.send_audio = AsyncMock()
    b.send_message = AsyncMock()
    return b


def _state(chat_id: int):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


def _cq(chat_id: int, data: str, b: MagicMock) -> MagicMock:
    cq = MagicMock()
    cq.data = data
    cq.message.chat.id = chat_id
    cq.message.edit_reply_markup = AsyncMock()
    cq.answer = AsyncMock()
    cq.bot = b
    return cq


def _msg(chat_id: int, text: str, b: MagicMock) -> MagicMock:
    m = MagicMock()
    m.chat.id = chat_id
    m.text = text
    m.answer = AsyncMock()
    m.bot = b
    return m


def _buttons(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _langs(bot_setup, uid):
    db = bot_setup["db"]
    conn = db.connect()
    try:
        return db.get_langs(conn, uid)
    finally:
        conn.close()


def _set_langs(bot_setup, uid, langs):
    db = bot_setup["db"]
    conn = db.connect()
    db.set_langs(conn, uid, langs)
    conn.close()


async def test_send_next_shows_picker_when_no_langs(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(500)
    b = _fake_bot()
    await bot._send_next(b, 500, uid, _state(500))
    b.send_audio.assert_not_called()
    markup = b.send_message.call_args.kwargs["reply_markup"]
    assert {"lang:ru", "lang:de", "lang:done"} <= set(_buttons(markup))


async def test_lang_toggle_adds_removes_and_keeps_one(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(501)
    b = _fake_bot()
    await bot.on_lang_pick(_cq(501, "lang:de", b), _state(501))
    assert _langs(bot_setup, uid) == ["de"]
    await bot.on_lang_pick(_cq(501, "lang:ru", b), _state(501))
    assert _langs(bot_setup, uid) == ["ru", "de"]
    await bot.on_lang_pick(_cq(501, "lang:de", b), _state(501))
    assert _langs(bot_setup, uid) == ["ru"]
    cq = _cq(501, "lang:ru", b)
    await bot.on_lang_pick(cq, _state(501))
    assert _langs(bot_setup, uid) == ["ru"]
    assert "at least one" in cq.answer.call_args.args[0]


async def test_lang_toggle_marks_chosen_in_keyboard(bot_setup):
    bot = bot_setup["bot_mod"]
    bot._ensure_user(502)
    cq = _cq(502, "lang:de", _fake_bot())
    await bot.on_lang_pick(cq, _state(502))
    markup = cq.message.edit_reply_markup.call_args.kwargs["reply_markup"]
    texts = {btn.callback_data: btn.text for row in markup.inline_keyboard for btn in row}
    assert texts["lang:de"].startswith("✅")
    assert not texts["lang:ru"].startswith("✅")


async def test_lang_done_without_choice_refuses(bot_setup):
    bot = bot_setup["bot_mod"]
    bot._ensure_user(503)
    b = _fake_bot()
    cq = _cq(503, "lang:done", b)
    await bot.on_lang_pick(cq, _state(503))
    b.send_audio.assert_not_called()
    assert "pick" in cq.answer.call_args.args[0]


async def test_lang_done_starts_session(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(504)
    _set_langs(bot_setup, uid, ["de"])
    bot._set_mode(uid, "voice")
    b = _fake_bot()
    await bot.on_lang_pick(_cq(504, "lang:done", b), _state(504))
    b.send_audio.assert_called_once()


async def test_german_only_session_sends_german_card(bot_setup):
    bot = bot_setup["bot_mod"]
    db = bot_setup["db"]
    uid = bot._ensure_user(505)
    _set_langs(bot_setup, uid, ["de"])
    bot._set_mode(uid, "voice")
    b = _fake_bot()
    await bot._send_next(b, 505, uid, _state(505))
    b.send_audio.assert_called_once()
    assert "German" in b.send_message.call_args.args[1]
    conn = db.connect()
    lang = conn.execute(
        "SELECT w.lang FROM sentences s JOIN words w ON w.id = s.word_id ORDER BY s.id DESC LIMIT 1"
    ).fetchone()["lang"]
    conn.close()
    assert lang == "de"


async def test_german_text_card_prompt_names_language(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(506)
    _set_langs(bot_setup, uid, ["de"])
    bot._set_mode(uid, "text")
    b = _fake_bot()
    for _ in range(4):
        await bot._send_next(b, 506, uid, _state(506))
        assert "German" in b.send_message.call_args.args[1]


async def test_stale_grade_with_old_direction_is_ignored(bot_setup):
    bot = bot_setup["bot_mod"]
    db = bot_setup["db"]
    uid = bot._ensure_user(507)
    _set_langs(bot_setup, uid, ["ru"])
    conn = db.connect()
    kwid = conn.execute("SELECT id FROM known_words WHERE lang='ru' LIMIT 1").fetchone()["id"]
    conn.close()
    b = _fake_bot()
    cq = _cq(507, f"g:4:t:{kwid}:en_to_ru", b)
    await bot.on_grade(cq, _state(507))
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM user_known_words WHERE user_id = ?", (uid,)).fetchone()[0]
    conn.close()
    assert n == 0
    cq.answer.assert_called()


async def test_add_german_word_flow(bot_setup):
    bot = bot_setup["bot_mod"]
    db = bot_setup["db"]
    uid = bot._ensure_user(508)
    _set_langs(bot_setup, uid, ["de"])
    b = _fake_bot()
    st = _state(508)
    await bot.on_add(_msg(508, "/add", b), st)
    await bot.on_add_target(_msg(508, "Katze", b), st)
    m = _msg(508, "cat", b)
    await bot.on_add_english(m, st)
    assert "added Katze" in m.answer.call_args.args[0]
    conn = db.connect()
    row = conn.execute("SELECT romanized, english FROM known_words WHERE lang='de' AND target='Katze'").fetchone()
    conn.close()
    assert row["romanized"] == "" and row["english"] == "cat"
    assert await st.get_state() is None


async def test_add_russian_word_flow_still_transliterates(bot_setup):
    bot = bot_setup["bot_mod"]
    db = bot_setup["db"]
    uid = bot._ensure_user(509)
    _set_langs(bot_setup, uid, ["ru"])
    b = _fake_bot()
    st = _state(509)
    await bot.on_add(_msg(509, "/add", b), st)
    await bot.on_add_latin(_msg(509, "koshka", b), st)
    await bot.on_add_english(_msg(509, "cat", b), st)
    await bot.on_add_cyrillic(_msg(509, "-", b), st)
    conn = db.connect()
    row = conn.execute("SELECT target, romanized FROM known_words WHERE english='cat'").fetchone()
    conn.close()
    assert (row["target"], row["romanized"]) == ("кошка", "koshka")


async def test_add_asks_language_when_several_chosen(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(510)
    _set_langs(bot_setup, uid, ["ru", "de"])
    b = _fake_bot()
    st = _state(510)
    m = _msg(510, "/add", b)
    await bot.on_add(m, st)
    assert set(_buttons(m.answer.call_args.kwargs["reply_markup"])) == {"addlang:ru", "addlang:de"}
    await bot.on_add_lang_pick(_cq(510, "addlang:de", b), st)
    assert (await st.get_data())["add_lang"] == "de"


def test_german_voice_reveal_has_no_latin_line(bot_setup):
    render = bot_setup["render"]
    from app.study import Token

    tokens = [Token(text="Ja.", matched=True, is_target=True)]
    out = render.voice_reveal(tokens, "Yes.", "de")
    assert len(out.split("\n")) == 2


def test_russian_voice_reveal_keeps_latin_line(bot_setup):
    render = bot_setup["render"]
    from app.study import Token

    tokens = [Token(text="Да.", matched=True, is_target=True)]
    out = render.voice_reveal(tokens, "Yes.", "ru")
    assert len(out.split("\n")) == 3


async def test_telegram_again_requeues_text_card(bot_setup):
    bot = bot_setup["bot_mod"]
    db = bot_setup["db"]
    uid = bot._ensure_user(509)
    _set_langs(bot_setup, uid, ["de"])
    conn = db.connect()
    kwid = conn.execute("SELECT id FROM known_words WHERE lang='de' AND target='ich'").fetchone()["id"]
    conn.close()
    b = _fake_bot()
    await bot.on_grade(_cq(509, f"g:0:t:{kwid}:en_to_tl", b), _state(509))
    conn = db.connect()
    row = conn.execute(
        "SELECT interval_days, requeue_after FROM user_known_words WHERE user_id = ? AND known_word_id = ?",
        (uid, kwid),
    ).fetchone()
    reviews = conn.execute("SELECT COUNT(*) FROM reviews WHERE user_id = ?", (uid,)).fetchone()[0]
    conn.close()
    assert row["interval_days"] == 0
    assert row["requeue_after"] is not None
    assert reviews == 1
