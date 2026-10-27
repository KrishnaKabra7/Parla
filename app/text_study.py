import random
import re
from dataclasses import dataclass
from sqlite3 import Connection

from app import srs
from app.langs import LANGS
from app.translit import has_cyrillic, to_cyrillic, to_latin

EN_TO_TL = "en_to_tl"
TL_TO_EN = "tl_to_en"
DIRECTIONS = (EN_TO_TL, TL_TO_EN)
EN_TO_TL_WEIGHT = 0.75
REQUEUE_MIN, REQUEUE_MAX = 5, 10


@dataclass
class TextCard:
    known_word_id: int
    lang: str
    target: str
    romanized: str
    english: str
    direction: str  # EN_TO_TL or TL_TO_EN


def pick_direction(rng: random.Random | None = None) -> str:
    r = rng or random
    return EN_TO_TL if r.random() < EN_TO_TL_WEIGHT else TL_TO_EN


def _card(row, direction: str) -> TextCard:
    return TextCard(
        known_word_id=row["id"],
        lang=row["lang"],
        target=row["target"],
        romanized=row["romanized"],
        english=row["english"],
        direction=direction,
    )


def _review_count(conn: Connection, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM reviews WHERE user_id = ?", (user_id,)).fetchone()[0]


def _pick_requeued(conn: Connection, user_id: int, lang: str, ready_only: bool = True) -> TextCard | None:
    limit = _review_count(conn, user_id) if ready_only else 2**62
    row = conn.execute(
        """
        SELECT kw.id, kw.lang, kw.target, kw.romanized, kw.english, ukw.direction FROM user_known_words ukw
        JOIN known_words kw ON kw.id = ukw.known_word_id
        WHERE ukw.user_id = ? AND kw.lang = ? AND ukw.requeue_after <= ?
        ORDER BY ukw.requeue_after ASC LIMIT 1
        """,
        (user_id, lang, limit),
    ).fetchone()
    return _card(row, row["direction"]) if row else None


def _pick_in_direction(conn: Connection, user_id: int, lang: str, direction: str) -> TextCard | None:
    due = conn.execute(
        """
        SELECT kw.id, kw.lang, kw.target, kw.romanized, kw.english FROM user_known_words ukw
        JOIN known_words kw ON kw.id = ukw.known_word_id
        WHERE ukw.user_id = ? AND ukw.direction = ? AND kw.lang = ?
          AND ukw.requeue_after IS NULL
          AND ukw.due_at IS NOT NULL AND datetime(ukw.due_at) <= datetime('now')
        ORDER BY ukw.due_at ASC LIMIT 1
        """,
        (user_id, direction, lang),
    ).fetchone()
    if due:
        return _card(due, direction)
    new = conn.execute(
        """
        SELECT kw.id, kw.lang, kw.target, kw.romanized, kw.english FROM known_words kw
        WHERE kw.lang = ? AND kw.id NOT IN (
            SELECT known_word_id FROM user_known_words
            WHERE user_id = ? AND direction = ?
        )
        ORDER BY kw.created_at DESC, kw.id ASC LIMIT 1
        """,
        (lang, user_id, direction),
    ).fetchone()
    if new:
        return _card(new, direction)
    return None


def pick_card(conn: Connection, user_id: int, lang: str, rng: random.Random | None = None) -> TextCard | None:
    requeued = _pick_requeued(conn, user_id, lang)
    if requeued is not None:
        return requeued
    direction = pick_direction(rng)
    card = _pick_in_direction(conn, user_id, lang, direction)
    if card is not None:
        return card
    other = TL_TO_EN if direction == EN_TO_TL else EN_TO_TL
    card = _pick_in_direction(conn, user_id, lang, other)
    if card is not None:
        return card
    return _pick_requeued(conn, user_id, lang, ready_only=False)


def load_card(conn: Connection, known_word_id: int, direction: str) -> TextCard | None:
    row = conn.execute(
        "SELECT id, lang, target, romanized, english FROM known_words WHERE id = ?",
        (known_word_id,),
    ).fetchone()
    return _card(row, direction) if row else None


def normalize_known_word(lang: str, target: str, romanized: str) -> tuple[str, str]:
    """Fill whichever script form is missing for Russian; Latin-script
    languages store an empty romanized field."""
    target, romanized = target.strip(), romanized.strip()
    if lang != "ru":
        return target, ""
    if target and not has_cyrillic(target):
        romanized = romanized or target
        target = to_cyrillic(target)
    target = target or to_cyrillic(romanized)
    return target, romanized or (to_latin(target) if target else "")


def record_grade(
    conn: Connection,
    user_id: int,
    known_word_id: int,
    direction: str,
    grade: int,
    typed: str | None = None,
    rng: random.Random | None = None,
) -> None:
    """Apply SM-2 and log the review. "Again" brings the card back after
    5-10 more graded cards instead of tomorrow; the next pass then
    schedules it from a zero interval."""
    ukw = conn.execute(
        "SELECT ef, interval_days FROM user_known_words WHERE user_id=? AND known_word_id=? AND direction=?",
        (user_id, known_word_id, direction),
    ).fetchone()
    ef = ukw["ef"] if ukw else srs.DEFAULT_EF
    interval = ukw["interval_days"] if ukw else 0.0
    ef_new, interval_new, due_at, status_new = srs.review(ef, interval, grade)
    conn.execute(
        "INSERT INTO reviews(user_id, sentence_id, grade, typed_answer) VALUES (?, NULL, ?, ?)",
        (user_id, grade, typed),
    )
    requeue_after = None
    if grade == srs.Grade.AGAIN:
        interval_new = 0.0
        requeue_after = _review_count(conn, user_id) + (rng or random).randint(REQUEUE_MIN, REQUEUE_MAX)
    conn.execute(
        """
        INSERT INTO user_known_words(user_id, known_word_id, direction, status, ef, interval_days, due_at, requeue_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, known_word_id, direction) DO UPDATE SET
            status = excluded.status,
            ef = excluded.ef,
            interval_days = excluded.interval_days,
            due_at = excluded.due_at,
            requeue_after = excluded.requeue_after
        """,
        (user_id, known_word_id, direction, status_new, ef_new, interval_new, due_at.isoformat(), requeue_after),
    )


def ensure_user_known_word(conn: Connection, user_id: int, known_word_id: int, direction: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_known_words(user_id, known_word_id, direction) VALUES (?, ?, ?)",
        (user_id, known_word_id, direction),
    )


_GLOSS_STRIP = re.compile(r"\s*\([^)]*\)")
_PUNCT = ".,!?;:'\"¿¡"


def _norm_english(s: str) -> str:
    # Drop parenthetical hints like "me (dat)" → "me", lowercase, strip punct edges.
    s = _GLOSS_STRIP.sub("", s)
    return s.strip().strip(_PUNCT).lower()


def _norm_target(lang: str, s: str) -> str:
    return LANGS[lang]["fold"](s.strip().strip(_PUNCT))


def _accepted_english(gloss_field: str) -> set[str]:
    return {_norm_english(g) for g in gloss_field.split(",") if _norm_english(g)}


def check_answer(card: TextCard, typed: str) -> bool:
    if not typed.strip():
        return False
    if card.direction == EN_TO_TL:
        return _norm_target(card.lang, typed) == _norm_target(card.lang, card.target)
    return _norm_english(typed) in _accepted_english(card.english)
