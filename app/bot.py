import logging
import os
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
from app.langs import LANGS, candidates

AUDIO_DIR = "data/audio"

log = logging.getLogger("app.bot")
router = Router()


class Flow(StatesGroup):
    awaiting_voice = State()
    awaiting_text = State()
    add_latin = State()
    add_target = State()
    add_english = State()
    add_cyrillic = State()


@lru_cache(maxsize=1)
def _anthropic() -> Anthropic:
    return Anthropic()


def _ensure_user(chat_id: int) -> int:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT user_id FROM tg_users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row:
            return row["user_id"]
        name = f"tg:{chat_id}"
        conn.execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (name,))
        urow = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        conn.execute(
            "INSERT INTO tg_users(chat_id, user_id) VALUES (?, ?)",
            (chat_id, urow["id"]),
        )
        return urow["id"]
    finally:
        conn.close()


def _link_user(chat_id: int, name: str) -> int:
    conn = db.connect()
    try:
        conn.execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (name,))
        urow = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        conn.execute(
            """
            INSERT INTO tg_users(chat_id, user_id) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET user_id = excluded.user_id
            """,
            (chat_id, urow["id"]),
        )
        return urow["id"]
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


def _user_langs(user_id: int) -> list[str]:
    conn = db.connect()
    try:
        return db.get_langs(conn, user_id)
    finally:
        conn.close()


def _set_user_langs(user_id: int, langs: list[str]) -> None:
    conn = db.connect()
    try:
        db.set_langs(conn, user_id, langs)
    finally:
        conn.close()


async def _prepare_voice(user_id: int, lang: str) -> tuple[int, str] | None:
    conn = db.connect()
    try:
        item = study.pick_next(conn, user_id, lang)
        if item is None:
            return None
        await study.ensure_sentences(conn, item.word_id, item.lemma, lang, user_id, _anthropic())
        if item.is_new:
            study.ensure_user_word(conn, user_id, item.word_id)
        s = study.pick_sentence(conn, item.word_id)
        if s is None:
            return None
        return s["id"], s["audio_path"]
    finally:
        conn.close()


def _prepare_text(user_id: int, lang: str) -> text_study.TextCard | None:
    conn = db.connect()
    try:
        return text_study.pick_card(conn, user_id, lang)
    finally:
        conn.close()


async def _send_next(bot: Bot, chat_id: int, user_id: int, state: FSMContext) -> None:
    langs = _user_langs(user_id)
    if not langs:
        await state.clear()
        await bot.send_message(
            chat_id,
            "pick the languages you want to practice:",
            reply_markup=bot_render.lang_keyboard([]),
        )
        return
    for kind, lang in candidates(_mode(user_id), langs):
        if kind == "voice":
            voice = await _prepare_voice(user_id, lang)
            if voice is not None:
                sentence_id, audio_path = voice
                path = f"{AUDIO_DIR}/{audio_path}"
                await bot.send_audio(chat_id, FSInputFile(path))
                await bot.send_message(chat_id, bot_render.voice_prompt(lang), parse_mode=ParseMode.MARKDOWN_V2)
                await state.set_state(Flow.awaiting_voice)
                await state.update_data(sentence_id=sentence_id)
                return
        else:
            card = _prepare_text(user_id, lang)
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


@router.message(Command("lang"))
async def on_lang(msg: Message, state: FSMContext) -> None:
    user_id = _ensure_user(msg.chat.id)
    await msg.answer(
        "pick languages:",
        reply_markup=bot_render.lang_keyboard(_user_langs(user_id)),
    )


@router.callback_query(F.data.startswith("lang:"))
async def on_lang_pick(cq: CallbackQuery, state: FSMContext) -> None:
    user_id = _ensure_user(cq.message.chat.id)
    _, code = cq.data.split(":", 1)
    chosen = _user_langs(user_id)
    if code == "done":
        if not chosen:
            await cq.answer("pick at least one language")
            return
        await cq.answer()
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await state.clear()
        await _send_next(cq.bot, cq.message.chat.id, user_id, state)
        return
    if code not in LANGS:
        await cq.answer()
        return
    new = [c for c in chosen if c != code] if code in chosen else chosen + [code]
    if not new:
        await cq.answer("keep at least one language")
        return
    _set_user_langs(user_id, new)
    await cq.answer()
    try:
        await cq.message.edit_reply_markup(reply_markup=bot_render.lang_keyboard(new))
    except Exception:
        pass


@router.message(Command("login"))
async def on_login(msg: Message, state: FSMContext) -> None:
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("usage: /login <web username>")
        return
    name = parts[1].strip()
    _link_user(msg.chat.id, name)
    await state.clear()
    await msg.answer(f"linked to '{name}'. use /start.")


@router.message(Command("words"))
async def on_words(msg: Message) -> None:
    user_id = _ensure_user(msg.chat.id)
    conn = db.connect()
    try:
        chosen = db.get_langs(conn, user_id)
        marks = ",".join("?" * len(chosen))
        rows = conn.execute(
            f"""
            SELECT w.lang, w.lemma, uw.status, uw.due_at
            FROM user_words uw JOIN words w ON w.id = uw.word_id
            WHERE uw.user_id = ? AND w.lang IN ({marks})
            ORDER BY uw.due_at IS NULL, uw.due_at ASC
            LIMIT 50
            """,
            (user_id, *chosen),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        await msg.answer("no words yet. use /start.")
        return
    lines = []
    for r in rows:
        due = f" (due {r['due_at'][:10]})" if r["due_at"] else ""
        lines.append(f"{LANGS[r['lang']]['flag']} {r['lemma']} — {r['status']}{due}")
    await msg.answer("\n".join(lines))


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
    user_id = _ensure_user(msg.chat.id)
    langs = _user_langs(user_id) or list(LANGS)
    await state.clear()
    if len(langs) > 1:
        await msg.answer("which language?", reply_markup=bot_render.add_lang_keyboard(langs))
        return
    await _start_add(msg.answer, state, langs[0])


@router.callback_query(F.data.startswith("addlang:"))
async def on_add_lang_pick(cq: CallbackQuery, state: FSMContext) -> None:
    _, code = cq.data.split(":", 1)
    await cq.answer()
    if code not in LANGS:
        return
    chat_id = cq.message.chat.id
    await _start_add(lambda text: cq.bot.send_message(chat_id, text), state, code)


async def _start_add(reply, state: FSMContext, lang: str) -> None:
    await state.set_data({"add_lang": lang})
    if lang == "ru":
        await state.set_state(Flow.add_latin)
        await reply("send the latin spelling:")
    else:
        await state.set_state(Flow.add_target)
        await reply(f"send the {LANGS[lang]['name']} word:")


@router.message(Flow.add_latin)
async def on_add_latin(msg: Message, state: FSMContext) -> None:
    await state.update_data(latin=(msg.text or "").strip())
    await state.set_state(Flow.add_english)
    await msg.answer("send the english gloss:")


@router.message(Flow.add_target)
async def on_add_target(msg: Message, state: FSMContext) -> None:
    await state.update_data(target=(msg.text or "").strip())
    await state.set_state(Flow.add_english)
    await msg.answer("send the english gloss:")


@router.message(Flow.add_english)
async def on_add_english(msg: Message, state: FSMContext) -> None:
    await state.update_data(english=(msg.text or "").strip())
    data = await state.get_data()
    lang = data.get("add_lang", "ru")
    if lang == "ru":
        await state.set_state(Flow.add_cyrillic)
        await msg.answer("send the cyrillic (or '-' to auto-transliterate):")
        return
    await _finish_add(msg, state, lang, data.get("target", ""), "", data["english"])


@router.message(Flow.add_cyrillic)
async def on_add_cyrillic(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw = (msg.text or "").strip()
    target = "" if raw in ("", "-") else raw
    latin = data.get("latin", "")
    await _finish_add(msg, state, "ru", target, latin, data.get("english", ""))


async def _finish_add(msg: Message, state: FSMContext, lang: str, target: str, romanized: str, english: str) -> None:
    await state.clear()
    target, romanized = text_study.normalize_known_word(lang, target, romanized)
    english = english.strip()
    if not target or not english:
        await msg.answer("word and english are required. try /add again.")
        return
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO known_words(lang, target, romanized, english) VALUES (?, ?, ?, ?)",
            (lang, target, romanized, english),
        )
        if cur.rowcount:
            await msg.answer(f"added {target}")
        else:
            await msg.answer(f"already exists: {target}")
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
            SELECT s.text, s.gloss_en, w.lemma, w.lang FROM sentences s
            JOIN words w ON w.id = s.word_id
            WHERE s.id = ?
            """,
            (sentence_id,),
        ).fetchone()
        if row is None:
            await state.clear()
            await _send_next(msg.bot, msg.chat.id, user_id, state)
            return
        user_lemmas = study.user_word_lemmas(conn, user_id, row["lang"])
    finally:
        conn.close()
    tokens = study.diff_and_highlight(row["text"], typed, row["lemma"], row["lang"], user_lemmas)
    text = bot_render.voice_reveal(tokens, row["gloss_en"], row["lang"])
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
        card = text_study.load_card(conn, known_word_id, direction)
    finally:
        conn.close()
    if card is None:
        await state.clear()
        await _send_next(msg.bot, msg.chat.id, user_id, state)
        return
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
            if direction not in text_study.DIRECTIONS:
                # Buttons sent before the direction rename; drop the grade.
                await cq.answer()
                await _send_next(cq.bot, cq.message.chat.id, user_id, state)
                return
            text_study.record_grade(conn, user_id, known_word_id, direction, grade)
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
