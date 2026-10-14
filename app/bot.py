import logging
import os
import random
from datetime import date, timedelta
from functools import lru_cache

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, FSInputFile, Message
from anthropic import Anthropic

from app import bot_render, db, srs, study, text_study
from app.langs import LANGS
from app.translit import to_cyrillic

LANG = "ru"
AUDIO_DIR = "data/audio"

log = logging.getLogger("app.bot")
router = Router()


class Flow(StatesGroup):
    awaiting_voice = State()
    awaiting_text = State()
    add_latin = State()
    add_english = State()
    add_cyrillic = State()


@lru_cache(maxsize=1)
def _anthropic() -> Anthropic:
    return Anthropic()


def _ensure_user(chat_id: int) -> int:
    name = f"tg:{chat_id}"
    conn = db.connect()
    try:
        conn.execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (name,))
        row = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        return row["id"]
    finally:
        conn.close()


def _mode(user_id: int) -> str:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT mode FROM user_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["mode"] if row else "mixed"


def _set_mode(user_id: int, mode: str) -> None:
    conn = db.connect()
    try:
        conn.execute(
            """
            INSERT INTO user_prefs(user_id, mode) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode
            """,
            (user_id, mode),
        )
    finally:
        conn.close()


async def _prepare_voice(user_id: int) -> tuple[int, str] | None:
    conn = db.connect()
    try:
        item = study.pick_next(conn, user_id, LANG)
        if item is None:
            return None
        await study.ensure_sentences(conn, item.word_id, item.lemma, LANG, user_id, _anthropic())
        if item.is_new:
            study.ensure_user_word(conn, user_id, item.word_id)
        s = study.pick_sentence(conn, item.word_id)
        if s is None:
            return None
        return s["id"], s["audio_path"]
    finally:
        conn.close()


def _prepare_text(user_id: int) -> text_study.TextCard | None:
    conn = db.connect()
    try:
        return text_study.pick_card(conn, user_id)
    finally:
        conn.close()


async def _send_next(bot: Bot, chat_id: int, user_id: int, state: FSMContext) -> None:
    mode = _mode(user_id)
    kinds = [mode] if mode in ("voice", "text") else random.sample(["voice", "text"], 2)
    for kind in kinds:
        if kind == "voice":
            voice = await _prepare_voice(user_id)
            if voice is not None:
                sentence_id, audio_path = voice
                path = f"{AUDIO_DIR}/{audio_path}"
                await bot.send_audio(chat_id, FSInputFile(path))
                await bot.send_message(chat_id, bot_render.voice_prompt(), parse_mode=ParseMode.MARKDOWN_V2)
                await state.set_state(Flow.awaiting_voice)
                await state.update_data(sentence_id=sentence_id)
                return
        else:
            card = _prepare_text(user_id)
            if card is not None:
                await bot.send_message(
                    chat_id,
                    bot_render.text_prompt(card),
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                await state.set_state(Flow.awaiting_text)
                await state.update_data(
                    known_word_id=card.known_word_id, direction=card.direction
                )
                return
    await state.clear()
    await bot.send_message(chat_id, "all caught up — come back later\\.", parse_mode=ParseMode.MARKDOWN_V2)


@router.message(CommandStart())
async def on_start(msg: Message, state: FSMContext) -> None:
    user_id = _ensure_user(msg.chat.id)
    await state.clear()
    await msg.answer(
        "welcome\\. pick a mode:",
        reply_markup=bot_render.mode_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    await _send_next(msg.bot, msg.chat.id, user_id, state)


@router.message(Command("mode"))
async def on_mode(msg: Message, state: FSMContext) -> None:
    _ensure_user(msg.chat.id)
    await msg.answer(
        "pick a mode:",
        reply_markup=bot_render.mode_keyboard(),
    )


@router.callback_query(F.data.startswith("mode:"))
async def on_mode_pick(cq: CallbackQuery, state: FSMContext) -> None:
    user_id = _ensure_user(cq.message.chat.id)
    _, mode = cq.data.split(":", 1)
    _set_mode(user_id, mode)
    await cq.answer(f"mode: {mode}")
    await state.clear()
    await _send_next(cq.bot, cq.message.chat.id, user_id, state)


@router.message(Command("stats"))
async def on_stats(msg: Message) -> None:
    user_id = _ensure_user(msg.chat.id)
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT DATE(created_at) AS d, COUNT(*) AS n
            FROM reviews
            WHERE user_id = ? AND created_at >= DATE('now', '-29 days')
            GROUP BY DATE(created_at)
            """,
            (user_id,),
        ).fetchall()
        per_day = {r["d"]: r["n"] for r in rows}
        known = conn.execute(
            "SELECT COUNT(*) AS n FROM user_words WHERE user_id = ? AND status = 'known'",
            (user_id,),
        ).fetchone()["n"]
        streak_rows = conn.execute(
            "SELECT DISTINCT DATE(created_at) AS d FROM reviews WHERE user_id = ? ORDER BY d DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()

    today = date.today()
    total_30 = sum(per_day.values())
    streak_dates = {r["d"] for r in streak_rows}
    d = today
    if d.isoformat() not in streak_dates:
        d = d - timedelta(days=1)
    streak = 0
    while d.isoformat() in streak_dates:
        streak += 1
        d = d - timedelta(days=1)

    text = (
        f"*reviews last 30d:* {total_30}\n"
        f"*known words:* {known}\n"
        f"*streak:* {streak} days"
    )
    await msg.answer(text, parse_mode=ParseMode.MARKDOWN_V2)


@router.message(Command("add"))
async def on_add(msg: Message, state: FSMContext) -> None:
    _ensure_user(msg.chat.id)
    await state.set_state(Flow.add_latin)
    await msg.answer("send the latin spelling:")


@router.message(Flow.add_latin)
async def on_add_latin(msg: Message, state: FSMContext) -> None:
    await state.update_data(latin=(msg.text or "").strip())
    await state.set_state(Flow.add_english)
    await msg.answer("send the english gloss:")


@router.message(Flow.add_english)
async def on_add_english(msg: Message, state: FSMContext) -> None:
    await state.update_data(english=(msg.text or "").strip())
    await state.set_state(Flow.add_cyrillic)
    await msg.answer("send the cyrillic (or '-' to auto-transliterate):")


@router.message(Flow.add_cyrillic)
async def on_add_cyrillic(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    latin = data.get("latin", "")
    english = data.get("english", "")
    raw = (msg.text or "").strip()
    cyrillic = to_cyrillic(latin) if raw in ("", "-") else raw
    await state.clear()
    if not latin or not english or not cyrillic:
        await msg.answer("latin and english are required. try /add again.")
        return
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO known_words(lang, cyrillic, latin, english) VALUES (?, ?, ?, ?)",
            (LANG, cyrillic, latin, english),
        )
        if cur.rowcount:
            await msg.answer(f"added {cyrillic}")
        else:
            await msg.answer(f"already exists: {cyrillic}")
    finally:
        conn.close()


@router.message(Flow.awaiting_voice)
async def on_voice_answer(msg: Message, state: FSMContext) -> None:
    user_id = _ensure_user(msg.chat.id)
    data = await state.get_data()
    sentence_id = data.get("sentence_id")
    if sentence_id is None:
        await _send_next(msg.bot, msg.chat.id, user_id, state)
        return
    typed = msg.text or ""
    conn = db.connect()
    try:
        row = conn.execute(
            """
            SELECT s.text, s.gloss_en, w.lemma FROM sentences s
            JOIN words w ON w.id = s.word_id
            WHERE s.id = ?
            """,
            (sentence_id,),
        ).fetchone()
        if row is None:
            await state.clear()
            await _send_next(msg.bot, msg.chat.id, user_id, state)
            return
        user_lemmas = study.user_word_lemmas(conn, user_id, LANG)
    finally:
        conn.close()
    tokens = study.diff_and_highlight(row["text"], typed, row["lemma"], user_lemmas)
    text = bot_render.voice_reveal(tokens, row["gloss_en"])
    await msg.answer(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=bot_render.grade_keyboard("v", sentence_id),
    )


@router.message(Flow.awaiting_text)
async def on_text_answer(msg: Message, state: FSMContext) -> None:
    user_id = _ensure_user(msg.chat.id)
    data = await state.get_data()
    known_word_id = data.get("known_word_id")
    direction = data.get("direction")
    if known_word_id is None or direction is None:
        await _send_next(msg.bot, msg.chat.id, user_id, state)
        return
    typed = msg.text or ""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, cyrillic, latin, english FROM known_words WHERE id = ?",
            (known_word_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        await state.clear()
        await _send_next(msg.bot, msg.chat.id, user_id, state)
        return
    card = text_study.TextCard(
        known_word_id=row["id"],
        cyrillic=row["cyrillic"],
        latin=row["latin"],
        english=row["english"],
        direction=direction,
    )
    correct = text_study.check_answer(card, typed)
    text = bot_render.text_reveal(card, correct, typed)
    await msg.answer(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=bot_render.grade_keyboard("t", known_word_id, direction),
    )


@router.callback_query(F.data.startswith("g:"))
async def on_grade(cq: CallbackQuery, state: FSMContext) -> None:
    user_id = _ensure_user(cq.message.chat.id)
    parts = cq.data.split(":")
    grade = int(parts[1])
    kind = parts[2]
    conn = db.connect()
    try:
        if kind == "v":
            sentence_id = int(parts[3])
            row = conn.execute(
                "SELECT word_id FROM sentences WHERE id = ?", (sentence_id,)
            ).fetchone()
            if row is None:
                await cq.answer()
                await _send_next(cq.bot, cq.message.chat.id, user_id, state)
                return
            word_id = row["word_id"]
            uw = conn.execute(
                "SELECT ef, interval_days FROM user_words WHERE user_id=? AND word_id=?",
                (user_id, word_id),
            ).fetchone()
            ef = uw["ef"] if uw else srs.DEFAULT_EF
            interval = uw["interval_days"] if uw else 0.0
            ef_new, interval_new, due_at, status_new = srs.review(ef, interval, grade)
            conn.execute(
                """
                INSERT INTO user_words(user_id, word_id, status, ef, interval_days, due_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, word_id) DO UPDATE SET
                    status = excluded.status,
                    ef = excluded.ef,
                    interval_days = excluded.interval_days,
                    due_at = excluded.due_at
                """,
                (user_id, word_id, status_new, ef_new, interval_new, due_at.isoformat()),
            )
            conn.execute(
                "INSERT INTO reviews(user_id, sentence_id, grade, typed_answer) VALUES (?, ?, ?, NULL)",
                (user_id, sentence_id, grade),
            )
        elif kind == "t":
            known_word_id = int(parts[3])
            direction = parts[4]
            ukw = conn.execute(
                "SELECT ef, interval_days FROM user_known_words WHERE user_id=? AND known_word_id=? AND direction=?",
                (user_id, known_word_id, direction),
            ).fetchone()
            ef = ukw["ef"] if ukw else srs.DEFAULT_EF
            interval = ukw["interval_days"] if ukw else 0.0
            ef_new, interval_new, due_at, status_new = srs.review(ef, interval, grade)
            conn.execute(
                """
                INSERT INTO user_known_words(user_id, known_word_id, direction, status, ef, interval_days, due_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, known_word_id, direction) DO UPDATE SET
                    status = excluded.status,
                    ef = excluded.ef,
                    interval_days = excluded.interval_days,
                    due_at = excluded.due_at
                """,
                (user_id, known_word_id, direction, status_new, ef_new, interval_new, due_at.isoformat()),
            )
            conn.execute(
                "INSERT INTO reviews(user_id, sentence_id, grade, typed_answer) VALUES (?, NULL, ?, NULL)",
                (user_id, grade),
            )
    finally:
        conn.close()
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cq.answer()
    await _send_next(cq.bot, cq.message.chat.id, user_id, state)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


def build_bot() -> Bot | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    return Bot(token=token)
