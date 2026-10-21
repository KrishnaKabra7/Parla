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
    result = await bot._prepare_voice(uid)
    assert result is not None
    sentence_id, audio_path = result
    assert isinstance(sentence_id, int)
    assert audio_path.endswith(".mp3")


def test_prepare_text_returns_card(bot_setup):
    bot = bot_setup["bot_mod"]
    uid = bot._ensure_user(8)
    card = bot._prepare_text(uid)
    assert card is not None
    assert card.target and card.english


async def test_grade_callback_updates_voice_srs(bot_setup):
    bot = bot_setup["bot_mod"]
    db = bot_setup["db"]
    uid = bot._ensure_user(100)
    # Pre-create a voice card so we have a sentence_id to grade.
    result = await bot._prepare_voice(uid)
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
    out = render.voice_reveal(tokens, "and")
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
